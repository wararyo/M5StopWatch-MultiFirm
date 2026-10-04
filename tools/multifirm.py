"""MultiFirm PC tool: install guest/host apps into the fixed v1 layout.

Change commands (install, install-host, recover, initial) never touch the device
without --execute; --check-device only reads. See tools/README.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import struct
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import esp_image as img  # noqa: E402
import layout as L  # noqa: E402
from device import (REQUIRED_ESPTOOL, Device, DeviceError, DeviceInfo,  # noqa: E402
                    EsptoolDevice, check_esptool)

TOOL_VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parents[1]
FF = b"\xFF"


def detect_lang() -> str:
    """MULTIFIRM_LANG (ja/en) wins; otherwise Japanese only for a Japanese UI language or locale."""
    forced = os.environ.get("MULTIFIRM_LANG", "").strip().lower()
    if forced in ("ja", "en"):
        return forced
    if sys.platform == "win32":
        try:
            import ctypes
            return "ja" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x11 else "en"
        except (AttributeError, OSError):
            return "en"
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return "ja" if value.lower().startswith("ja") else "en"
    return "en"


LANG = detect_lang()


def tr(ja: str, en: str) -> str:
    return ja if LANG == "ja" else en


EMPTY, INVALID, READY, READ_ERROR = "Empty", "Invalid", "Ready", "ReadError"


class ToolError(Exception):
    pass


# ---------------------------------------------------------------- logging

class Log:
    """Console gets the outcome; the log file also gets hashes, image details and esptool output."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self._fh = None
        self._open_line = False
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("a", encoding="utf-8")

    def __call__(self, msg: str = "") -> None:
        self._close_line()
        print(msg, flush=True)
        self.detail(msg)

    def begin(self, msg: str) -> None:
        """Print a step without a newline; end() appends its result to the same line."""
        self._close_line()
        print(msg, end="", flush=True)
        self._open_line = True
        self.detail(msg)

    def end(self, result: str) -> None:
        print(f" {result}" if self._open_line else f"    {result}", flush=True)
        self._open_line = False
        self.detail(f"    {result}")

    def _close_line(self) -> None:
        if self._open_line:
            print(flush=True)
            self._open_line = False

    def detail(self, msg: str) -> None:
        if self._fh:
            self._fh.write(msg + "\n")
            self._fh.flush()

    def close(self) -> None:
        self._close_line()
        if self._fh:
            self._fh.close()


def rng(offset: int, size: int) -> str:
    return f"0x{offset:06x}-0x{offset + size:06x} ({size:,} bytes)"


def ff(size: int) -> bytes:
    return FF * size


# ---------------------------------------------------------------- context

DeviceFactory = Callable[[argparse.Namespace, Log], Device]


def esptool_factory(args: argparse.Namespace, log: Log) -> Device:
    version = check_esptool(args.allow_untested_esptool)
    return EsptoolDevice(args.port, args.baud, log.detail, version=version)


class TrackedDevice:
    """Delegates to a Device and remembers whether flash was modified."""

    def __init__(self, inner: Device):
        self.inner = inner
        self.mutated = False

    def connect(self) -> DeviceInfo:
        return self.inner.connect()

    def read(self, offset: int, size: int) -> bytes:
        return self.inner.read(offset, size)

    def verify(self, offset: int, data: bytes) -> bool:
        return self.inner.verify(offset, data)

    def write(self, offset: int, data: bytes) -> None:
        self.mutated = True
        self.inner.write(offset, data)

    def erase(self, offset: int, size: int) -> None:
        self.mutated = True
        self.inner.erase(offset, size)

    def reset(self) -> None:
        self.inner.reset()

    def close(self) -> None:
        close = getattr(self.inner, "close", None)
        if close:
            close()


@dataclass
class Ctx:
    args: argparse.Namespace
    factory: DeviceFactory
    state_dir: Path
    now: Callable[[], float]
    dev: TrackedDevice | None = None

    def stamp(self) -> str:
        return dt.datetime.fromtimestamp(self.now()).strftime("%Y%m%d-%H%M%S")

    def open_log(self, command: str) -> Log:
        return Log(self.state_dir / "logs" / f"{self.stamp()}-{command}.log")


def connect(ctx: Ctx, log: Log) -> tuple[Device, DeviceInfo]:
    if not ctx.args.port:
        raise ToolError(tr("--port が必要です", "--port is required"))
    log.detail(f"MultiFirm {TOOL_VERSION} / Python {sys.version.split()[0]}")
    log.detail(tr("リセット方針: 接続時は default_reset でダウンロードモードへ入り、各操作の間は no_reset で"
                  "ダウンロードモードのまま続ける。成功時だけ hard_reset でアプリへ戻す",
                  "Reset policy: default_reset into download mode on connect, no_reset between operations, "
                  "hard_reset into the app only on success"))
    log(tr("ログ", "Log") + f": {log.path}")
    dev = ctx.dev = TrackedDevice(ctx.factory(ctx.args, log))
    info = dev.connect()
    log(tr("接続", "Connected") + f": {ctx.args.port} {info.chip} MAC {info.mac} flash {info.flash_size} "
        f"esptool {info.esptool_version}")
    return dev, info


def require_layout(dev: Device, log: Log) -> None:
    table = dev.read(L.PARTITION_TABLE_OFFSET, L.PARTITION_TABLE_SIZE)
    kind = L.classify_table(table)
    log.detail(tr("パーティション表", "Partition table") + f": {kind} (SHA-256 {hashlib.sha256(table).hexdigest()})")
    if kind != "multifirm-v1":
        raise ToolError(tr(f"実機の表が MultiFirm v1 ではありません ({kind})。書き込みません。"
                           "新配置の導入は initial を使います",
                           f"the device's partition table is not MultiFirm v1 ({kind}); nothing written. "
                           "Use initial to install the new layout"))


def check_chip_rev(info: img.ImageInfo, dev_info: DeviceInfo, what: str) -> None:
    if not img.chip_rev_supported(info, dev_info.chip_rev_full):
        raise ToolError(tr(f"{what} はこのチップ revision ({dev_info.chip_rev_full}) に対応しません",
                           f"{what} does not support this chip revision ({dev_info.chip_rev_full})")
                        + f" (min {info.min_rev_full}, max {info.max_rev_full})")


# ---------------------------------------------------------------- verified primitives

def erase_verified(dev: Device, log: Log, offset: int, size: int, what: str) -> None:
    log.begin(tr(f"  消去 {what}", f"  Erase {what}") + f": {rng(offset, size)}")
    dev.erase(offset, size)
    if not dev.verify(offset, ff(size)):
        raise ToolError(tr(f"{what} の消去を確認できません", f"could not confirm that {what} was erased")
                        + f" ({rng(offset, size)})")
    log.end(tr("消去済みを確認", "erase verified"))


def write_verified(dev: Device, log: Log, offset: int, data: bytes, what: str,
                   done: bool = True) -> bytes:
    """With done=False the caller verifies the read-back further and ends the line itself."""
    log.begin(tr(f"  書き込み {what}", f"  Write {what}") + f": {rng(offset, len(data))}")
    log.detail(f"    SHA-256 {hashlib.sha256(data).hexdigest()}")
    dev.write(offset, data)
    back = dev.read(offset, len(data))
    if back != data:
        raise ToolError(tr(f"{what} の読み戻しが一致しません", f"{what} read-back does not match"))
    if done:
        log.end(tr("読み戻し一致", "read-back OK"))
    return back


def snapshot(dev: Device, regions: list[tuple[str, int, int]]) -> list[tuple[str, int, bytes]]:
    return [(name, off, dev.read(off, size)) for name, off, size in regions]


def check_unchanged(dev: Device, log: Log, snaps: list[tuple[str, int, bytes]]) -> None:
    changed = [name for name, off, data in snaps if not dev.verify(off, data)]
    if changed:
        raise ToolError(tr("書き込み対象外の領域が変化しました", "regions outside the write changed")
                        + f": {', '.join(changed)}")
    log(tr("  書き込み対象外の領域の不変を確認", "  Regions outside the write are unchanged"))
    log.detail(f"    {', '.join(name for name, _, _ in snaps)}")


def preserved_regions(exclude_meta_slot: int | None = None) -> list[tuple[str, int, int]]:
    regions = [("nvs", L.NVS.offset, L.NVS.size), ("otadata", L.OTADATA.offset, L.OTADATA.size),
               ("phy_init", L.PHY_INIT.offset, L.PHY_INIT.size),
               ("multifirm_nvs", L.MULTIFIRM_NVS.offset, L.MULTIFIRM_NVS.size)]
    for slot in L.GUEST_SLOTS:
        if slot != exclude_meta_slot:
            regions.append((f"meta[{slot}]", L.meta_sector_offset(slot), L.SECTOR_SIZE))
    regions.append(("meta[reserved]", L.META_RESERVED_OFFSET, L.SECTOR_SIZE))
    return regions


# ---------------------------------------------------------------- slot evaluation

@dataclass
class SlotReport:
    label: str
    state: str
    info: img.ImageInfo | None
    error: str = ""


def read_slot_image(dev: Device, part: L.Partition) -> SlotReport:
    try:
        buf = dev.read(part.offset, L.SECTOR_SIZE)
    except DeviceError as e:
        return SlotReport(part.label, READ_ERROR, None, str(e))
    if buf == ff(L.SECTOR_SIZE):
        return SlotReport(part.label, EMPTY, None)
    while True:
        try:
            info = img.parse_image(buf, part.size, complete=len(buf) >= part.size)
            break
        except img.NeedMore as more:
            want = min(part.size, max(more.size, len(buf) * 4))
            want += -want % L.SECTOR_SIZE
            want = min(want, part.size)
            try:
                buf += dev.read(part.offset + len(buf), want - len(buf))
            except DeviceError as e:
                return SlotReport(part.label, READ_ERROR, None, str(e))
    return slot_report(part, info)


def slot_report(part: L.Partition, info: img.ImageInfo) -> SlotReport:
    """Apply the same app/digest requirements to streamed and read-back images."""
    if info.ok and info.kind != "app":
        info.errors.append("not an app image")
    if info.ok and not info.digest_ok:
        info.errors.append("no appended SHA-256 digest")
    info.ok = not info.errors
    return SlotReport(part.label, READY if info.ok else INVALID, info, "; ".join(info.errors))


def match_meta(rec: L.MetaRecord, info: img.ImageInfo) -> str | None:
    """Steps 3-4 of the shared rule, against an already verified image."""
    if not (info.ok and info.kind == "app" and info.digest_ok):
        return "image is not verified"
    if rec.image_size != info.image_size:
        return f"image_size {rec.image_size} != verified {info.image_size}"
    if rec.app_digest != info.appended_digest:
        return "app_digest does not match the verified image"
    if any(rec.elf_sha256) and rec.elf_sha256 != info.elf_sha256:
        return "elf_sha256 does not match app_desc"
    return None


def display_name(slot: int, meta_name: str | None, project_name: str | None) -> tuple[str, str]:
    if meta_name:
        return meta_name, "metadata"
    usable = L.usable_project_name(project_name)
    if usable:
        return usable, "project_name"
    return L.fallback_name(slot), "fallback"


def boot_selection(otadata: bytes) -> str:
    best = None
    for i in range(2):
        seq, _label, state, crc = struct.unpack_from("<I20sII", otadata, i * L.SECTOR_SIZE)
        if seq == 0xFFFFFFFF or zlib.crc32(struct.pack("<I", seq), 0xFFFFFFFF) != crc:
            continue
        if state in (3, 4):  # INVALID, ABORTED
            continue
        if best is None or seq > best[0]:
            best = (seq, state)
    if best is None:
        return tr("未設定 (ブートローダは ota_0 を起動)", "not set (the bootloader starts ota_0)")
    return f"ota_{(best[0] - 1) % 4} (seq {best[0]}, state 0x{best[1]:x})"


# ---------------------------------------------------------------- input files

def log_image(log: Log, label: str, path: Path, info: img.ImageInfo) -> None:
    """One summary line on the console; the full description goes to the log file."""
    summary = f"{info.image_size:,} bytes" if info.image_size else tr("解析できません", "unparsable")
    if info.kind == "app":
        summary = f"project_name {info.project_name!r} version {info.version!r}, {summary}"
    log(f"{label}: {path.name} ({summary})")
    log.detail(f"  {path.resolve()}")
    for line in img.describe(info):
        log.detail(f"  {line}")
    for w in info.warnings:
        log(tr("  警告", "  Warning") + f": {w}")


def load_guest(args: argparse.Namespace, log: Log) -> tuple[bytes, img.ImageInfo, str]:
    data = Path(args.firmware).read_bytes()
    info = img.verify_app_file(data, L.GUEST_MAX_SIZE)
    log_image(log, tr("入力", "Input"), Path(args.firmware), info)
    if not info.ok:
        raise ToolError(tr("イメージを受け付けられません: ", "image rejected: ") + "; ".join(info.errors))
    if args.name is not None:
        reason = L.validate_name(args.name)
        if reason:
            raise ToolError(tr("--name が不正です", "invalid --name") + f": {reason}")
        name = args.name
    else:
        name = L.usable_project_name(info.project_name)
        if not name:
            raise ToolError(tr(f"project_name {info.project_name!r} は表示名に使えません。--name を指定してください",
                               f"project_name {info.project_name!r} cannot be used as the display name; "
                               "specify --name"))
    log(tr("  表示名", "  Display name") + f": {name}")
    return data, info, name


def load_host_image(path: str, log: Log) -> tuple[bytes, img.ImageInfo]:
    data = Path(path).read_bytes()
    info = img.verify_app_file(data, L.HOST_MAX_SIZE)
    log_image(log, tr("入力 (ホスト)", "Input (host)"), Path(path), info)
    if not info.ok:
        raise ToolError(tr("ホストイメージを受け付けられません: ", "host image rejected: ") + "; ".join(info.errors))
    return data, info


@dataclass
class HostBuild:
    bootloader: bytes
    bootloader_info: img.ImageInfo
    table: bytes
    app: bytes
    app_info: img.ImageInfo
    phy_init: bytes | None


def _truthy(cfg: dict, key: str) -> bool:
    return bool(cfg.get(key, False))


HOST_BUILD_KEYS = (("bootloader", L.BOOTLOADER_OFFSET), ("partition-table", L.PARTITION_TABLE_OFFSET),
                   ("app", L.HOST.offset))
# PlatformIO's espidf builder runs CMake only to configure, so flasher_args.json names
# files that SCons never writes; it writes these instead, next to flasher_args.json.
PIO_HOST_FILES = {"bootloader": "bootloader.bin", "partition-table": "partitions.bin", "app": "firmware.bin"}


def _is_platformio_build(root: Path) -> bool:
    return any(root.glob(".sconsign*.dblite"))


def load_host_build(directory: str, log: Log) -> HostBuild:
    root = Path(directory)
    try:
        flasher = json.loads((root / "flasher_args.json").read_text(encoding="utf-8"))
        cfg = json.loads((root / "config" / "sdkconfig.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ToolError(tr("ESP-IDF / PlatformIO のビルドディレクトリとして読めません",
                           "not readable as an ESP-IDF / PlatformIO build directory") + f": {e}")

    def entry(key: str, offset: int) -> str:
        item = flasher.get(key)
        if not item or int(item["offset"], 0) != offset:
            raise ToolError(tr(f"flasher_args.json の {key} が 0x{offset:x} にありません",
                               f"{key} in flasher_args.json is not at 0x{offset:x}"))
        return item["file"]

    # Decide the kind once for the whole directory, so files from the two kinds never mix.
    idf_files = {key: root / entry(key, offset) for key, offset in HOST_BUILD_KEYS}
    pio_files = {key: root / name for key, name in PIO_HOST_FILES.items()}
    idf_missing = [str(p) for p in idf_files.values() if not p.is_file()]
    pio_missing = [str(p) for p in pio_files.values() if not p.is_file()]
    if not idf_missing:
        kind, files = "ESP-IDF", idf_files
    elif _is_platformio_build(root) and not pio_missing:
        kind, files = "PlatformIO", pio_files
    elif _is_platformio_build(root):
        raise ToolError(tr("PlatformIO のビルド成果物が見つかりません。ビルドが完了しているか確認してください: ",
                           "PlatformIO build outputs not found; check that the build finished: ")
                        + ", ".join(pio_missing))
    else:
        raise ToolError(tr("flasher_args.json が示すファイルが見つかりません。ビルドが完了しているか確認してください: ",
                           "files listed in flasher_args.json not found; check that the build finished: ")
                        + ", ".join(idf_missing))
    log(tr("ホストビルド", "Host build") + f" ({kind}): {root.resolve()}")
    config_path = root / "config" / "sdkconfig.json"
    if config_path.stat().st_mtime > files["app"].stat().st_mtime:
        log(tr(f"  警告: config/sdkconfig.json が {files['app'].name} より新しく、ビルドが古い可能性があります",
               f"  Warning: config/sdkconfig.json is newer than {files['app'].name}; the build may be stale"))

    def read(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as e:
            raise ToolError(tr(f"{path} を読めません", f"cannot read {path}") + f": {e}")

    problems = []
    if cfg.get("PARTITION_TABLE_OFFSET") != L.PARTITION_TABLE_OFFSET:
        problems.append(tr("CONFIG_PARTITION_TABLE_OFFSET が 0x8000 ではない",
                           "CONFIG_PARTITION_TABLE_OFFSET is not 0x8000"))
    if cfg.get("ESPTOOLPY_FLASHSIZE") != "16MB":
        problems.append(tr("CONFIG_ESPTOOLPY_FLASHSIZE が 16MB ではない", "CONFIG_ESPTOOLPY_FLASHSIZE is not 16MB"))
    if _truthy(cfg, "BOOTLOADER_APP_ROLLBACK_ENABLE"):
        problems.append(tr("CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE は v1 の前提外",
                           "CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE is not supported by v1"))
    for key in ("SECURE_BOOT", "SECURE_FLASH_ENC_ENABLED"):
        if _truthy(cfg, key):
            problems.append(tr(f"CONFIG_{key} は v1 の対象外", f"CONFIG_{key} is not supported by v1"))
    if problems:
        raise ToolError(tr("ホストビルドの設定が対応外です: ", "unsupported host build configuration: ") + "; ".join(problems))

    bl = read(files["bootloader"])
    bl_info = img.verify_bootloader_file(bl, L.BOOTLOADER_MAX_SIZE)
    log.detail("  bootloader:")
    for line in img.describe(bl_info):
        log.detail(f"    {line}")
    if not bl_info.ok:
        raise ToolError(tr("ブートローダを受け付けられません: ", "bootloader rejected: ") + "; ".join(bl_info.errors))

    table = read(files["partition-table"])
    if table.ljust(L.PARTITION_TABLE_SIZE, FF)[:L.PARTITION_TABLE_SIZE] != L.PARTITION_TABLE \
            or len(table) > L.PARTITION_TABLE_SIZE:
        raise ToolError(tr("ビルドのパーティション表が MultiFirm v1 と一致しません",
                           "the build's partition table does not match MultiFirm v1"))
    log.detail(tr("  partition-table: MultiFirm v1 と一致", "  partition-table: matches MultiFirm v1")
               + f" ({len(table)} bytes)")

    app, app_info = load_host_image(str(files["app"]), log)

    phy = None
    if _truthy(cfg, "ESP_PHY_INIT_DATA_IN_PARTITION"):
        if kind == "PlatformIO":
            raise ToolError(tr("PHY 初期化データをパーティションに置く設定ですが、PlatformIO のビルドは "
                               "phy_init のファイルを出力しないため対応していません",
                               "PHY init data is configured to live in a partition, but PlatformIO builds "
                               "do not output the phy_init file, so this is not supported"))
        flash_files = {int(k, 0): v for k, v in flasher.get("flash_files", {}).items()}
        if L.PHY_INIT.offset not in flash_files:
            raise ToolError(tr("PHY 初期化データをパーティションに置く設定ですが、phy_init のファイルがありません",
                               "PHY init data is configured to live in a partition, but there is no phy_init file"))
        phy = read(root / flash_files[L.PHY_INIT.offset])
        if not 0 < len(phy) <= L.PHY_INIT.size:
            raise ToolError(tr("phy_init データのサイズが不正です", "invalid phy_init data size"))
        log.detail(tr(f"  phy_init: {len(phy)} bytes を書き込む", f"  phy_init: write {len(phy)} bytes"))
    else:
        log.detail(tr("  phy_init: パーティション不使用の設定のため消去する",
                       "  phy_init: erase (the configuration does not use the partition)"))
    return HostBuild(bl, bl_info, table.ljust(L.PARTITION_TABLE_SIZE, FF), app, app_info, phy)


# ---------------------------------------------------------------- backups

def backup_dir(ctx: Ctx) -> Path:
    return Path(ctx.args.out) if getattr(ctx.args, "out", None) else ctx.state_dir / "backups"


def take_backup(ctx: Ctx, dev: Device, info: DeviceInfo, log: Log) -> tuple[Path, bytes]:
    log(tr("全体バックアップを読み取り中", "Reading full backup") + f": {rng(0, L.FLASH_SIZE)}")
    data = dev.read(0, L.FLASH_SIZE)
    if len(data) != L.FLASH_SIZE:
        raise ToolError(tr("バックアップのサイズが 16 MiB ではありません", "backup is not 16 MiB"))
    if not dev.verify(0, data):
        raise ToolError(tr("バックアップと実機の MD5 が一致しません", "backup does not match the device MD5"))
    folder = backup_dir(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"backup-{info.mac.replace(':', '')}-{ctx.stamp()}.bin"
    path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    manifest = {
        "format": "multifirm-backup-v1", "size": len(data), "sha256": sha,
        "mac": info.mac, "chip": info.chip, "chip_rev_full": info.chip_rev_full,
        "flash_size": info.flash_size,
        "layout": L.classify_table(data[L.PARTITION_TABLE_OFFSET:L.PARTITION_TABLE_OFFSET + L.PARTITION_TABLE_SIZE]),
        "tool_version": TOOL_VERSION, "esptool_version": info.esptool_version,
        "created_at": dt.datetime.fromtimestamp(ctx.now()).isoformat(timespec="seconds"),
    }
    path.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    log(tr(f"バックアップ: {path} (実機 MD5 と一致)", f"Backup: {path} (matches the device MD5)"))
    log.detail(f"  SHA-256 {sha}")
    return path, data


def load_backup(path: Path) -> bytes:
    data = path.read_bytes()
    if len(data) != L.FLASH_SIZE:
        raise ToolError(tr(f"{path} は 16 MiB の全体バックアップではありません", f"{path} is not a 16 MiB full backup"))
    meta = path.with_suffix(".json")
    if meta.exists():
        expected = json.loads(meta.read_text(encoding="utf-8")).get("sha256")
        if expected != hashlib.sha256(data).hexdigest():
            raise ToolError(tr(f"{path} の SHA-256 がマニフェストと一致しません",
                               f"{path} SHA-256 does not match its manifest"))
    return data


def latest_backup(ctx: Ctx, mac: str) -> Path | None:
    folder = ctx.state_dir / "backups"
    found = []
    for meta in folder.glob("backup-*.json"):
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if m.get("mac") == mac and meta.with_suffix(".bin").exists():
            found.append((m.get("created_at", ""), meta.with_suffix(".bin")))
    return max(found)[1] if found else None


# ---------------------------------------------------------------- commands

def plan_header(log: Log, args: argparse.Namespace, lines: list[str]) -> None:
    """--execute reports each step as it runs, so the plan itself goes only to the log file."""
    out = log.detail if args.execute else log
    out(tr("書き込み計画:", "Write plan:"))
    for line in lines:
        out(f"  {line}")


def dry_run_note(log: Log) -> int:
    log(tr("DRY RUN: 実機には接続していません。実機のレイアウトは未検証です。",
           "DRY RUN: not connected to the device; its layout is unverified."))
    log(tr("  --check-device で実機を読み取り検証、--execute で実行します。",
           "  Use --check-device to read and verify the device, --execute to run."))
    return 0


def finish_ok(dev: Device, log: Log, message: str) -> int:
    dev.reset()
    log(tr(f"{message} (実機をリセットしました)", f"{message} (device reset)"))
    return 0


def run_logged(ctx: Ctx, log: Log, body: Callable[[], int], hint: str = "") -> int:
    """Failure policy: after any erase/write, never reset into the application."""
    try:
        return body()
    except (ToolError, DeviceError, OSError) as e:
        log(tr("失敗", "Failed") + f": {e}")
        dev = ctx.dev
        if dev is None:
            return 1
        if dev.mutated:
            log(tr("実機はリセットしていません (ダウンロードモードのまま)。",
                   "Device not reset (still in download mode). ") + hint)
            return 1
        try:
            dev.reset()
            log(tr("書き込み・消去の前に中止しました (実機をリセットしました)",
                   "Stopped before any write or erase (device reset)"))
        except DeviceError as reset_error:
            log(tr("リセットにも失敗しました", "Reset also failed") + f": {reset_error}")
        return 1
    finally:
        if ctx.dev is not None:
            ctx.dev.close()
        log.close()


def cmd_inspect(ctx: Ctx) -> int:
    log = Log()
    data = Path(ctx.args.firmware).read_bytes()
    cap = L.HOST_MAX_SIZE if ctx.args.role == "host" else L.GUEST_MAX_SIZE
    info = img.verify_app_file(data, cap)
    log(f"{ctx.args.firmware} ({ctx.args.role}, " + tr("上限", "max") + f" {cap:,} bytes)")
    for line in img.describe(info):
        log(f"  {line}")
    fits = {True: tr("可", "fits"), False: tr("超過", "too large")}
    log(tr("  ゲスト上限", "  Guest max") + f" 0x{L.GUEST_MAX_SIZE:x}: {fits[len(data) <= L.GUEST_MAX_SIZE]} / "
        + tr("ホスト上限", "host max") + f" 0x{L.HOST_MAX_SIZE:x}: {fits[len(data) <= L.HOST_MAX_SIZE]}")
    usable = L.usable_project_name(info.project_name)
    log(tr(f"  表示名候補: {usable} (project_name)", f"  Display name candidate: {usable} (project_name)")
        if usable else tr("  表示名候補: なし (install には --name が必要)",
                          "  Display name candidate: none (install needs --name)"))
    for w in info.warnings:
        log(tr("  警告", "  Warning") + f": {w}")
    for e in info.errors:
        log(tr("  拒否", "  Rejected") + f": {e}")
    log(tr("結果: ", "Result: ") + (tr("受け付け可能", "accepted") if info.ok
                                    else tr("受け付けられません", "rejected")))
    return 0 if info.ok else 1


def _status_slot(dev: Device, log: Log, index: int, part: L.Partition, meta: bytes, verify: bool) -> None:
    role = "host" if index == 0 else f"slot {index}"
    if verify:
        report = read_slot_image(dev, part)
    else:
        head = dev.read(part.offset, L.SECTOR_SIZE)
        if head == ff(L.SECTOR_SIZE):
            report = SlotReport(part.label, EMPTY, None)
        else:
            peek = img.peek_image(head)
            report = SlotReport(part.label, tr("app (未検証)", "app (unverified)") if peek.kind == "app" else INVALID,
                                peek, "; ".join(peek.errors))
    im = report.info
    log(f"{part.label} [{role}] {rng(part.offset, part.size)}: {report.state}"
        + (f" - {report.error}" if report.error else ""))
    if im and im.kind == "app":
        log(f"  project_name {im.project_name!r} version {im.version!r} idf {im.idf_ver}")
        (log if verify else log.detail)(f"  elf_sha256 {im.elf_sha256.hex()}")
        if report.state == READY:
            log(f"  image {im.image_size:,} bytes, digest {im.appended_digest.hex()}")
    if index == 0:
        return
    sector = meta[(index - 1) * L.SECTOR_SIZE:index * L.SECTOR_SIZE]
    rec, reason = L.decode_meta(sector, part.size)
    meta_name = None
    if rec and verify:
        reason = match_meta(rec, im) if (im and report.state == READY) else "image is not verified"
        meta_name = None if reason else rec.name
        log(f"  metadata: {tr('有効', 'valid') if meta_name else tr('無効', 'invalid')} name {rec.name!r}"
            + (f" ({reason})" if reason else ""))
    elif rec:
        meta_name = rec.name
        log(tr(f"  metadata: name {rec.name!r} (暫定・イメージと未照合)",
               f"  metadata: name {rec.name!r} (provisional, not matched to the image)"))
    else:
        log(tr(f"  metadata: なし ({reason})", f"  metadata: none ({reason})"))
    name, source = display_name(index, meta_name, im.project_name if im else None)
    log(tr(f"  表示名: {name} (採用元 {source}{'' if verify else '、暫定'})",
           f"  Display name: {name} (from {source}{'' if verify else ', provisional'})"))


def cmd_status(ctx: Ctx) -> int:
    log = ctx.open_log("status")

    def body() -> int:
        dev, _ = connect(ctx, log)
        table = dev.read(L.PARTITION_TABLE_OFFSET, L.PARTITION_TABLE_SIZE)
        kind = L.classify_table(table)
        log(tr("パーティション表", "Partition table") + f": {kind}")
        if kind != "multifirm-v1":
            try:
                for p in L.decode_partition_table(table):
                    log(f"  {p.label:<16} type {p.type} subtype 0x{p.subtype:02x} {rng(p.offset, p.size)}")
            except ValueError as e:
                log(tr("  解析できません", "  Cannot parse") + f": {e}")
            return finish_ok(dev, log, tr("MultiFirm v1 ではないため詳細は表示しません",
                                           "not MultiFirm v1, so no details are shown"))
        boot = boot_selection(dev.read(L.OTADATA.offset, L.OTADATA.size))
        log(tr("起動先 (otadata)", "Boot target (otadata)") + f": {boot}")
        meta = dev.read(L.META.offset, 3 * L.SECTOR_SIZE)
        log(tr("イメージ内容: ", "Image contents: ")
            + (tr("読み戻して検証", "read back and verified") if ctx.args.verify
               else tr("未検証 (先頭セクタのみ。--verify で検証)", "unverified (first sector only; use --verify)")))
        _status_slot(dev, log, 0, L.HOST, meta, ctx.args.verify)
        for slot in L.GUEST_SLOTS:
            _status_slot(dev, log, slot, L.guest_partition(slot), meta, ctx.args.verify)
        return finish_ok(dev, log, tr("status 完了", "status done"))

    return run_logged(ctx, log, body)


def cmd_backup(ctx: Ctx) -> int:
    log = ctx.open_log("backup")

    def body() -> int:
        dev, info = connect(ctx, log)
        take_backup(ctx, dev, info, log)
        return finish_ok(dev, log, tr("backup 完了", "backup done"))

    return run_logged(ctx, log, body)


def device_mode(args: argparse.Namespace) -> bool:
    return bool(args.execute or args.check_device)


def check_device_done(dev: Device, log: Log) -> int:
    return finish_ok(dev, log, tr("--check-device: 読み取り検証のみ行いました。書き込み・消去はしていません",
                                  "--check-device: read and verified only; nothing written or erased"))


def cmd_install(ctx: Ctx) -> int:
    args = ctx.args
    log = ctx.open_log(f"install-slot{args.slot}") if device_mode(args) else Log()

    def body() -> int:
        data, info, name = load_guest(args, log)
        part = L.guest_partition(args.slot)
        meta_off = L.meta_sector_offset(args.slot)
        record = L.MetaRecord(name, info.image_size, info.elf_sha256, info.appended_digest, int(ctx.now()))
        sector = L.meta_sector(record)
        plan_header(log, args, [
            tr(f"1. メタデータ[{args.slot}] 消去", f"1. Erase metadata[{args.slot}]")
            + f" {rng(meta_off, L.SECTOR_SIZE)}",
            (tr(f"2. {part.label} 全域消去", f"2. Erase all of {part.label}") + f" {rng(part.offset, part.size)}"
             if args.erase_slot else tr("2. 書き込み対象セクタのみ自動消去 (スロット末尾は保持)",
                                        "2. Erase only the sectors being written (rest of the slot kept)")),
            tr("3. イメージ書き込み・読み戻し検証", "3. Write the image, verify read-back")
            + f" {rng(part.offset, len(data))}",
            tr(f"4. メタデータ[{args.slot}] 書き込み・読み戻し検証", f"4. Write metadata[{args.slot}], verify read-back")
            + f" {rng(meta_off, L.SECTOR_SIZE)}",
            tr("書き込まない: NVS / multifirm_nvs / otadata / phy_init / 他スロット / 他メタデータ",
               "Not written: NVS / multifirm_nvs / otadata / phy_init / other slots / other metadata"),
        ])
        if not device_mode(args):
            return dry_run_note(log)
        dev, dinfo = connect(ctx, log)
        require_layout(dev, log)
        check_chip_rev(info, dinfo, tr("イメージ", "image"))
        if args.check_device:
            head = dev.read(part.offset, L.SECTOR_SIZE)
            peek = None if head == ff(L.SECTOR_SIZE) else img.peek_image(head)
            log(tr(f"現在の {part.label}: ", f"Current {part.label}: ")
                + ("Empty" if peek is None
                   else f"project_name {peek.project_name!r} " + tr("(未検証)", "(unverified)")))
            return check_device_done(dev, log)
        snaps = snapshot(dev, preserved_regions(exclude_meta_slot=args.slot))
        log(tr("実行:", "Running:"))
        erase_verified(dev, log, meta_off, L.SECTOR_SIZE, tr(f"メタデータ[{args.slot}]", f"metadata[{args.slot}]"))
        if args.erase_slot:
            erase_verified(dev, log, part.offset, part.size, part.label)
        readback = write_verified(dev, log, part.offset, data, part.label, done=False)
        back = slot_report(part, img.parse_image(readback, part.size))
        if back.state != READY or back.info.appended_digest != info.appended_digest:
            raise ToolError(tr("書き込んだイメージを検証できません", "cannot verify the written image")
                            + f": {back.state} {back.error}")
        log.end(tr("読み戻し一致、イメージ検証 OK", "read-back OK, image verified"))
        meta_readback = write_verified(dev, log, meta_off, sector,
                                       tr(f"メタデータ[{args.slot}]", f"metadata[{args.slot}]"), done=False)
        rec, reason = L.decode_meta(meta_readback, part.size)
        reason = reason or match_meta(rec, back.info)
        if reason:
            raise ToolError(tr("メタデータを検証できません", "cannot verify the metadata") + f": {reason}")
        log.end(tr("読み戻し一致、メタデータ検証 OK", "read-back OK, metadata verified"))
        check_unchanged(dev, log, snaps)
        return finish_ok(dev, log, tr(f"install 完了: slot {args.slot} = {name}",
                                         f"install done: slot {args.slot} = {name}"))

    return run_logged(ctx, log, body, tr("同じ install を再実行するか、recover でホストへ戻してください",
                                      "Run the same install again, or return to the host with recover"))


def _verify_backup_regions(dev: Device, backup: bytes) -> list[str]:
    regions = [("bootloader", 0, L.PARTITION_TABLE_OFFSET),
               ("partition table", L.PARTITION_TABLE_OFFSET, L.SECTOR_SIZE),
               ("multifirm_meta", L.META.offset, L.META.size)]
    regions += [(f"ota_{s}", L.guest_partition(s).offset, L.GUEST_MAX_SIZE) for s in L.GUEST_SLOTS]
    return [name for name, off, size in regions if not dev.verify(off, backup[off:off + size])]


def cmd_install_host(ctx: Ctx) -> int:
    args = ctx.args
    log = ctx.open_log("install-host") if device_mode(args) else Log()

    def body() -> int:
        data, info = load_host_image(args.firmware, log)
        plan_header(log, args, [
            tr("1. 全体バックアップを確認 (現状と一致しなければ新規取得)",
               "1. Check the full backup (take a new one if it does not match the device)"),
            (tr("2. ota_0 全域消去", "2. Erase all of ota_0") + f" {rng(L.HOST.offset, L.HOST.size)}"
             if args.erase_slot else tr("2. 書き込み対象セクタのみ自動消去 (スロット末尾は保持)",
                                        "2. Erase only the sectors being written (rest of the slot kept)")),
            tr("3. ホスト書き込み・読み戻し検証", "3. Write the host, verify read-back")
            + f" {rng(L.HOST.offset, len(data))}",
            tr("4. ブートローダ・表・メタデータ・ゲスト3スロットがバックアップと一致することを確認",
               "4. Check that the bootloader, table, metadata and 3 guest slots match the backup"),
            tr("書き込まない: ブートローダ / NVS / multifirm_nvs / otadata / ゲスト",
               "Not written: bootloader / NVS / multifirm_nvs / otadata / guests"),
        ])
        if not device_mode(args):
            return dry_run_note(log)
        dev, dinfo = connect(ctx, log)
        require_layout(dev, log)
        check_chip_rev(info, dinfo, tr("ホストイメージ", "host image"))
        backup_path = Path(args.backup) if args.backup else latest_backup(ctx, dinfo.mac)
        backup = None
        if backup_path:
            backup = load_backup(backup_path)
            stale = _verify_backup_regions(dev, backup)
            log(tr("バックアップ", "Backup") + f" {backup_path}: "
                + (tr("現状と不一致", "does not match the device") + f" ({', '.join(stale)})" if stale
                   else tr("現状と一致", "matches the device")))
            if stale:
                backup = None
        if args.check_device:
            if backup is None:
                log(tr("実行時は全体バックアップを新規取得します", "A new full backup will be taken on --execute"))
            return check_device_done(dev, log)
        if backup is None:
            _, backup = take_backup(ctx, dev, dinfo, log)
        snaps = snapshot(dev, preserved_regions()[:4])
        log(tr("実行:", "Running:"))
        if args.erase_slot:
            erase_verified(dev, log, L.HOST.offset, L.HOST.size, "ota_0")
        readback = write_verified(dev, log, L.HOST.offset, data, "ota_0", done=False)
        back = slot_report(L.HOST, img.parse_image(readback, L.HOST.size))
        if back.state != READY or back.info.appended_digest != info.appended_digest:
            raise ToolError(tr("書き込んだホストを検証できません", "cannot verify the written host")
                            + f": {back.state} {back.error}")
        log.end(tr("読み戻し一致、イメージ検証 OK", "read-back OK, image verified"))
        changed = _verify_backup_regions(dev, backup)
        if changed:
            raise ToolError(tr("保護領域がバックアップと一致しません", "protected regions do not match the backup")
                            + f": {', '.join(changed)}")
        log(tr("  ブートローダ・表・メタデータ・ゲストがバックアップと一致",
               "  Bootloader, table, metadata and guests match the backup"))
        check_unchanged(dev, log, snaps)
        return finish_ok(dev, log, tr("install-host 完了", "install-host done"))

    return run_logged(ctx, log, body, tr("install-host を再実行してください。ホストが壊れたままなら"
                                         "バックアップから復元します (tools/README.md)",
                                         "Run install-host again. If the host stays broken, "
                                         "restore from the backup (tools/README.md)"))


def cmd_recover(ctx: Ctx) -> int:
    args = ctx.args
    log = ctx.open_log("recover") if device_mode(args) else Log()

    def body() -> int:
        plan_header(log, args, [
            tr("1. 表とホスト (ota_0) のイメージを検証", "1. Verify the table and the host (ota_0) image"),
            tr(f"2. otadata 消去 {rng(L.OTADATA.offset, L.OTADATA.size)} → 次回起動はホスト",
               f"2. Erase otadata {rng(L.OTADATA.offset, L.OTADATA.size)} → the host boots next"),
            tr("書き込まない: NVS / メタデータ / 各スロット", "Not written: NVS / metadata / slots"),
        ])
        if not device_mode(args):
            return dry_run_note(log)
        dev, _ = connect(ctx, log)
        require_layout(dev, log)
        host = read_slot_image(dev, L.HOST)
        log(tr("ホスト", "Host") + f": {host.state}" + (f" - {host.error}" if host.error else "")
            + (f" project_name {host.info.project_name!r}" if host.info else ""))
        if host.state != READY:
            log(tr("ホストのイメージが正常ではないため、recover では戻れません。install-host で書き直してください",
                   "The host image is not valid, so recover cannot return to it. Rewrite it with install-host"))
            log(tr("実機はリセットしていません (ダウンロードモードのまま)", "Device not reset (still in download mode)"))
            return 1
        if args.check_device:
            return check_device_done(dev, log)
        erase_verified(dev, log, L.OTADATA.offset, L.OTADATA.size, "otadata")
        return finish_ok(dev, log, tr("recover 完了: 次回はホストが起動します", "recover done: the host boots next"))

    return run_logged(ctx, log, body, tr("recover を再実行してください", "Run recover again"))


def cmd_initial(ctx: Ctx) -> int:
    args = ctx.args
    log = ctx.open_log("initial") if device_mode(args) else Log()

    def body() -> int:
        build = load_host_build(args.host_build, log)
        erases = [("otadata", L.OTADATA.offset, L.OTADATA.size)]
        erases += [(f"ota_{s}", L.guest_partition(s).offset, L.GUEST_MAX_SIZE) for s in L.GUEST_SLOTS]
        erases += [("multifirm_nvs", L.MULTIFIRM_NVS.offset, L.MULTIFIRM_NVS.size),
                   ("multifirm_meta", L.META.offset, L.META.size),
                   ("nvs", L.NVS.offset, L.NVS.size),
                   ("phy_init", L.PHY_INIT.offset, L.PHY_INIT.size)]
        lines = [tr("0. 16 MiB 全体バックアップを保存し実機 MD5 と照合 (完了前は書かない)",
                    "0. Save a 16 MiB full backup and check it against the device MD5 (nothing written before)")]
        lines += [tr(f"消去 {name}", f"Erase {name}") + f" {rng(off, size)}" for name, off, size in erases]
        if build.phy_init:
            lines.append(tr("書き込み phy_init", "Write phy_init") + f" {rng(L.PHY_INIT.offset, len(build.phy_init))}")
        lines += [tr("消去して書き込み ota_0 (ホスト)", "Erase and write ota_0 (host)")
                  + f" {rng(L.HOST.offset, len(build.app))}",
                  tr("書き込み パーティション表", "Write partition table")
                  + f" {rng(L.PARTITION_TABLE_OFFSET, L.PARTITION_TABLE_SIZE)}",
                  tr("消去して書き込み ブートローダ", "Erase and write bootloader") + f" {rng(0, len(build.bootloader))}",
                  tr("書き込まない", "Not written") + f": storage {rng(L.STORAGE.offset, L.STORAGE.size)}, "
                  f"coredump {rng(L.COREDUMP.offset, L.COREDUMP.size)}",
                  tr("NVS 設定と BLE ボンドは初期化されます。ゲストは空になり再インストールが必要です",
                     "NVS settings and BLE bonds are reset. Guests are emptied and must be reinstalled")]
        plan_header(log, args, lines)
        if not device_mode(args):
            return dry_run_note(log)
        dev, dinfo = connect(ctx, log)
        current = dev.read(L.PARTITION_TABLE_OFFSET, L.PARTITION_TABLE_SIZE)
        log(tr("現在の表", "Current table") + f": {L.classify_table(current)}")
        log.detail(f"  SHA-256 {hashlib.sha256(current).hexdigest()}")
        check_chip_rev(build.app_info, dinfo, tr("ホストイメージ", "host image"))
        check_chip_rev(build.bootloader_info, dinfo, tr("ブートローダ", "bootloader"))
        if args.check_device:
            return check_device_done(dev, log)
        if args.backup:
            backup = load_backup(Path(args.backup))
            if not dev.verify(0, backup):
                raise ToolError(tr("--backup が実機の現在の内容と一致しません。書き込みません",
                                   "--backup does not match the current device contents; nothing written"))
            log(tr(f"バックアップ {args.backup}: 実機と一致", f"Backup {args.backup}: matches the device"))
        else:
            take_backup(ctx, dev, dinfo, log)
        if not dev.verify(L.PARTITION_TABLE_OFFSET, current):
            raise ToolError(tr("バックアップ中に表が変化しました。書き込みません",
                               "the partition table changed during the backup; nothing written"))
        log(tr("実行:", "Running:"))
        for name, off, size in erases:
            erase_verified(dev, log, off, size, name)
        if build.phy_init:
            write_verified(dev, log, L.PHY_INIT.offset, build.phy_init, "phy_init")
        erase_verified(dev, log, L.HOST.offset, L.HOST.size, "ota_0")
        write_verified(dev, log, L.HOST.offset, build.app, "ota_0")
        write_verified(dev, log, L.PARTITION_TABLE_OFFSET, build.table, tr("パーティション表", "partition table"))
        erase_verified(dev, log, 0, L.BOOTLOADER_MAX_SIZE, tr("ブートローダ領域", "bootloader region"))
        write_verified(dev, log, 0, build.bootloader, tr("ブートローダ", "bootloader"))
        host = read_slot_image(dev, L.HOST)
        if host.state != READY or host.info.appended_digest != build.app_info.appended_digest:
            raise ToolError(tr("ホストを検証できません", "cannot verify the host") + f": {host.state} {host.error}")
        require_layout(dev, log)
        return finish_ok(dev, log, tr("initial 完了: ホストのみの新配置になりました。ゲストを install してください",
                                         "initial done: the new layout has only the host. Install the guests next"))

    return run_logged(ctx, log, body, tr("initial を再実行するか、バックアップから復元してください "
                                         "(tools/README.md)",
                                         "Run initial again, or restore from the backup (tools/README.md)"))


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="multifirm.py", description=__doc__)
    p.add_argument("--version", action="version", version=f"MultiFirm {TOOL_VERSION} (esptool {REQUIRED_ESPTOOL})")
    sub = p.add_subparsers(dest="command", required=True)

    def device_opts(sp: argparse.ArgumentParser, change: bool) -> None:
        sp.add_argument("--port", help=tr("シリアルポート (例: COM11、/dev/ttyACM0、/dev/cu.usbmodem1101)",
                                           "serial port (e.g. COM11, /dev/ttyACM0, /dev/cu.usbmodem1101)"))
        sp.add_argument("--baud", type=int, default=460800)
        sp.add_argument("--allow-untested-esptool", action="store_true")
        if change:
            mode = sp.add_mutually_exclusive_group()
            mode.add_argument("--execute", action="store_true", help=tr("実機へ書き込む", "write to the device"))
            mode.add_argument("--check-device", action="store_true",
                              help=tr("実機を読み取り検証のみ", "only read and verify the device"))

    erase_slot_help = tr("書き込み前に対象スロット全体を消去・検証する", "erase and verify the whole slot before writing")

    sp = sub.add_parser("status", help=tr("実機の表・スロット・メタデータを表示", "show the device's table, slots and metadata"))
    device_opts(sp, change=False)
    sp.add_argument("--verify", action="store_true", help=tr("イメージを読み戻して検証", "read back and verify the images"))

    sp = sub.add_parser("inspect", help=tr("単体アプリイメージをオフライン検証", "verify a single app image offline"))
    sp.add_argument("firmware")
    sp.add_argument("--role", choices=["guest", "host"], default="guest")

    sp = sub.add_parser("install", help=tr("ゲストを ota_1..3 に書き込む", "write a guest to ota_1..3"))
    sp.add_argument("firmware")
    sp.add_argument("--slot", type=int, choices=L.GUEST_SLOTS, required=True)
    sp.add_argument("--name", help=tr("表示名 (UTF-8 31 bytes 以内)", "display name (up to 31 UTF-8 bytes)"))
    sp.add_argument("--erase-slot", action="store_true", help=erase_slot_help)
    device_opts(sp, change=True)

    sp = sub.add_parser("install-host", help=tr("ホストを ota_0 に書き込む", "write the host to ota_0"))
    sp.add_argument("firmware")
    sp.add_argument("--backup", help=tr("使用する全体バックアップ (.bin)", "full backup to use (.bin)"))
    sp.add_argument("--erase-slot", action="store_true", help=erase_slot_help)
    device_opts(sp, change=True)

    sp = sub.add_parser("recover", help=tr("otadata を消してホストへ戻す", "erase otadata to return to the host"))
    device_opts(sp, change=True)

    sp = sub.add_parser("backup", help=tr("16 MiB 全体を保存", "save the whole 16 MiB"))
    device_opts(sp, change=False)
    sp.add_argument("--out", help=tr("保存先ディレクトリ", "output directory"))

    sp = sub.add_parser("initial", help=tr("新配置を初期導入 (全体バックアップ後)", "install the new layout (after a full backup)"))
    sp.add_argument("--host-build", required=True,
                    help=tr("ホストの ESP-IDF build ディレクトリ、または PlatformIO の .pio/build/<env>",
                            "host ESP-IDF build directory, or PlatformIO .pio/build/<env>"))
    sp.add_argument("--backup", help=tr("取得済みの全体バックアップ (実機と一致する場合のみ使用)",
                                         "existing full backup (used only if it matches the device)"))
    device_opts(sp, change=True)
    return p


COMMANDS = {"status": cmd_status, "inspect": cmd_inspect, "install": cmd_install,
            "install-host": cmd_install_host, "recover": cmd_recover, "backup": cmd_backup,
            "initial": cmd_initial}


def main(argv: list[str] | None = None, factory: DeviceFactory | None = None,
         state_dir: Path | None = None, now: Callable[[], float] = time.time) -> int:
    args = build_parser().parse_args(argv)
    state = state_dir or Path(os.environ.get("MULTIFIRM_STATE_DIR", ROOT / ".multifirm"))
    ctx = Ctx(args, factory or esptool_factory, state, now)
    try:
        return COMMANDS[args.command](ctx)
    except (ToolError, DeviceError, OSError) as e:
        print(tr("失敗", "Failed") + f": {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
