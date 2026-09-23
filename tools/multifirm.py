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
RESET_POLICY = ("接続時は default_reset でダウンロードモードへ入り、各操作の間は no_reset で"
                "ダウンロードモードのまま続ける。成功時だけ hard_reset でアプリへ戻す")

EMPTY, INVALID, READY, READ_ERROR = "Empty", "Invalid", "Ready", "ReadError"


class ToolError(Exception):
    pass


# ---------------------------------------------------------------- logging

class Log:
    def __init__(self, path: Path | None = None):
        self.path = path
        self._fh = None
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("a", encoding="utf-8")

    def __call__(self, msg: str = "") -> None:
        print(msg, flush=True)
        self.detail(msg)

    def detail(self, msg: str) -> None:
        if self._fh:
            self._fh.write(msg + "\n")
            self._fh.flush()

    def close(self) -> None:
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
        raise ToolError("--port が必要です")
    log(f"MultiFirm {TOOL_VERSION} / Python {sys.version.split()[0]}")
    log(f"ログ: {log.path}")
    log(f"リセット方針: {RESET_POLICY}")
    dev = ctx.dev = TrackedDevice(ctx.factory(ctx.args, log))
    info = dev.connect()
    log(f"接続: {ctx.args.port} {info.chip} MAC {info.mac} flash {info.flash_size} "
        f"esptool {info.esptool_version}")
    return dev, info


def require_layout(dev: Device, log: Log) -> None:
    table = dev.read(L.PARTITION_TABLE_OFFSET, L.PARTITION_TABLE_SIZE)
    kind = L.classify_table(table)
    log(f"パーティション表: {kind} (SHA-256 {hashlib.sha256(table).hexdigest()})")
    if kind != "multifirm-v1":
        raise ToolError(f"実機の表が MultiFirm v1 ではありません ({kind})。書き込みません。"
                        "新配置の導入は initial を使います")


def check_chip_rev(info: img.ImageInfo, dev_info: DeviceInfo, what: str) -> None:
    if not img.chip_rev_supported(info, dev_info.chip_rev_full):
        raise ToolError(f"{what} はこのチップ revision ({dev_info.chip_rev_full}) に対応しません "
                        f"(min {info.min_rev_full}, max {info.max_rev_full})")


# ---------------------------------------------------------------- verified primitives

def erase_verified(dev: Device, log: Log, offset: int, size: int, what: str) -> None:
    log(f"  消去 {what}: {rng(offset, size)}")
    dev.erase(offset, size)
    if not dev.verify(offset, ff(size)):
        raise ToolError(f"{what} の消去を確認できません ({rng(offset, size)})")
    log("    消去済みを確認")


def write_verified(dev: Device, log: Log, offset: int, data: bytes, what: str) -> None:
    log(f"  書き込み {what}: {rng(offset, len(data))} SHA-256 {hashlib.sha256(data).hexdigest()}")
    dev.write(offset, data)
    back = dev.read(offset, len(data))
    if back != data:
        raise ToolError(f"{what} の読み戻しが一致しません")
    log("    読み戻し一致")


def snapshot(dev: Device, regions: list[tuple[str, int, int]]) -> list[tuple[str, int, bytes]]:
    return [(name, off, dev.read(off, size)) for name, off, size in regions]


def check_unchanged(dev: Device, log: Log, snaps: list[tuple[str, int, bytes]]) -> None:
    changed = [name for name, off, data in snaps if not dev.verify(off, data)]
    if changed:
        raise ToolError(f"書き込み対象外の領域が変化しました: {', '.join(changed)}")
    log(f"  不変を確認: {', '.join(name for name, _, _ in snaps)}")


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
        return "未設定 (ブートローダは ota_0 を起動)"
    return f"ota_{(best[0] - 1) % 4} (seq {best[0]}, state 0x{best[1]:x})"


# ---------------------------------------------------------------- input files

def load_guest(args: argparse.Namespace, log: Log) -> tuple[bytes, img.ImageInfo, str]:
    data = Path(args.firmware).read_bytes()
    info = img.verify_app_file(data, L.GUEST_MAX_SIZE)
    log(f"入力: {Path(args.firmware).resolve()}")
    for line in img.describe(info):
        log(f"  {line}")
    for w in info.warnings:
        log(f"  警告: {w}")
    if not info.ok:
        raise ToolError("イメージを受け付けられません: " + "; ".join(info.errors))
    if args.name is not None:
        reason = L.validate_name(args.name)
        if reason:
            raise ToolError(f"--name が不正です: {reason}")
        name = args.name
    else:
        name = L.usable_project_name(info.project_name)
        if not name:
            raise ToolError(f"project_name {info.project_name!r} は表示名に使えません。--name を指定してください")
    log(f"  表示名: {name}")
    return data, info, name


def load_host_image(path: str, log: Log) -> tuple[bytes, img.ImageInfo]:
    data = Path(path).read_bytes()
    info = img.verify_app_file(data, L.HOST_MAX_SIZE)
    log(f"入力 (ホスト): {Path(path).resolve()}")
    for line in img.describe(info):
        log(f"  {line}")
    for w in info.warnings:
        log(f"  警告: {w}")
    if not info.ok:
        raise ToolError("ホストイメージを受け付けられません: " + "; ".join(info.errors))
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


def load_host_build(directory: str, log: Log) -> HostBuild:
    root = Path(directory)
    try:
        flasher = json.loads((root / "flasher_args.json").read_text(encoding="utf-8"))
        cfg = json.loads((root / "config" / "sdkconfig.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ToolError(f"ESP-IDF のビルドディレクトリとして読めません: {e}")
    log(f"ホストビルド: {root.resolve()}")

    def entry(key: str, offset: int) -> Path:
        item = flasher.get(key)
        if not item or int(item["offset"], 0) != offset:
            raise ToolError(f"flasher_args.json の {key} が 0x{offset:x} にありません")
        return root / item["file"]

    problems = []
    if cfg.get("PARTITION_TABLE_OFFSET") != L.PARTITION_TABLE_OFFSET:
        problems.append("CONFIG_PARTITION_TABLE_OFFSET が 0x8000 ではない")
    if cfg.get("ESPTOOLPY_FLASHSIZE") != "16MB":
        problems.append("CONFIG_ESPTOOLPY_FLASHSIZE が 16MB ではない")
    if _truthy(cfg, "BOOTLOADER_APP_ROLLBACK_ENABLE"):
        problems.append("CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE は v1 の前提外")
    for key in ("SECURE_BOOT", "SECURE_FLASH_ENC_ENABLED"):
        if _truthy(cfg, key):
            problems.append(f"CONFIG_{key} は v1 の対象外")
    if problems:
        raise ToolError("ホストビルドの設定が対応外です: " + "; ".join(problems))

    bl = entry("bootloader", L.BOOTLOADER_OFFSET).read_bytes()
    bl_info = img.verify_bootloader_file(bl, L.BOOTLOADER_MAX_SIZE)
    log("  bootloader:")
    for line in img.describe(bl_info):
        log(f"    {line}")
    if not bl_info.ok:
        raise ToolError("ブートローダを受け付けられません: " + "; ".join(bl_info.errors))

    table = entry("partition-table", L.PARTITION_TABLE_OFFSET).read_bytes()
    if table.ljust(L.PARTITION_TABLE_SIZE, FF)[:L.PARTITION_TABLE_SIZE] != L.PARTITION_TABLE \
            or len(table) > L.PARTITION_TABLE_SIZE:
        raise ToolError("ビルドのパーティション表が MultiFirm v1 と一致しません")
    log(f"  partition-table: MultiFirm v1 と一致 ({len(table)} bytes)")

    app_path = entry("app", L.HOST.offset)
    app, app_info = load_host_image(str(app_path), log)

    phy = None
    if _truthy(cfg, "ESP_PHY_INIT_DATA_IN_PARTITION"):
        files = {int(k, 0): v for k, v in flasher.get("flash_files", {}).items()}
        if L.PHY_INIT.offset not in files:
            raise ToolError("PHY 初期化データをパーティションに置く設定ですが、phy_init のファイルがありません")
        phy = (root / files[L.PHY_INIT.offset]).read_bytes()
        if not 0 < len(phy) <= L.PHY_INIT.size:
            raise ToolError("phy_init データのサイズが不正です")
        log(f"  phy_init: {len(phy)} bytes を書き込む")
    else:
        log("  phy_init: パーティション不使用の設定のため消去する")
    return HostBuild(bl, bl_info, table.ljust(L.PARTITION_TABLE_SIZE, FF), app, app_info, phy)


# ---------------------------------------------------------------- backups

def backup_dir(ctx: Ctx) -> Path:
    return Path(ctx.args.out) if getattr(ctx.args, "out", None) else ctx.state_dir / "backups"


def take_backup(ctx: Ctx, dev: Device, info: DeviceInfo, log: Log) -> tuple[Path, bytes]:
    log(f"全体バックアップを読み取り中: {rng(0, L.FLASH_SIZE)}")
    data = dev.read(0, L.FLASH_SIZE)
    if len(data) != L.FLASH_SIZE:
        raise ToolError("バックアップのサイズが 16 MiB ではありません")
    if not dev.verify(0, data):
        raise ToolError("バックアップと実機の MD5 が一致しません")
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
    log(f"バックアップ: {path}")
    log(f"  SHA-256 {sha}、実機 MD5 と一致")
    return path, data


def load_backup(path: Path) -> bytes:
    data = path.read_bytes()
    if len(data) != L.FLASH_SIZE:
        raise ToolError(f"{path} は 16 MiB の全体バックアップではありません")
    meta = path.with_suffix(".json")
    if meta.exists():
        expected = json.loads(meta.read_text(encoding="utf-8")).get("sha256")
        if expected != hashlib.sha256(data).hexdigest():
            raise ToolError(f"{path} の SHA-256 がマニフェストと一致しません")
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

def plan_header(log: Log, lines: list[str]) -> None:
    log("書き込み計画:")
    for line in lines:
        log(f"  {line}")


def dry_run_note(log: Log) -> int:
    log("DRY RUN: 実機には接続していません。実機のレイアウトは未検証です。")
    log("  --check-device で実機を読み取り検証、--execute で実行します。")
    return 0


def finish_ok(dev: Device, log: Log, message: str) -> int:
    dev.reset()
    log("hard reset でアプリへ戻しました")
    log(message)
    return 0


def run_logged(ctx: Ctx, log: Log, body: Callable[[], int], hint: str = "") -> int:
    """Failure policy: after any erase/write, never reset into the application."""
    try:
        return body()
    except (ToolError, DeviceError, OSError) as e:
        log(f"失敗: {e}")
        dev = ctx.dev
        if dev is None:
            return 1
        if dev.mutated:
            log("実機はリセットしていません (ダウンロードモードのまま)。" + hint)
            return 1
        try:
            dev.reset()
            log("書き込み・消去の前に中止しました。hard reset でアプリへ戻しました")
        except DeviceError as reset_error:
            log(f"リセットにも失敗しました: {reset_error}")
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
    log(f"{ctx.args.firmware} ({ctx.args.role}, 上限 {cap:,} bytes)")
    for line in img.describe(info):
        log(f"  {line}")
    log(f"  ゲスト上限 0x{L.GUEST_MAX_SIZE:x}: {'可' if len(data) <= L.GUEST_MAX_SIZE else '超過'} / "
        f"ホスト上限 0x{L.HOST_MAX_SIZE:x}: {'可' if len(data) <= L.HOST_MAX_SIZE else '超過'}")
    usable = L.usable_project_name(info.project_name)
    log(f"  表示名候補: {usable} (project_name)" if usable
        else "  表示名候補: なし (install には --name が必要)")
    for w in info.warnings:
        log(f"  警告: {w}")
    for e in info.errors:
        log(f"  拒否: {e}")
    log("結果: " + ("受け付け可能" if info.ok else "受け付けられません"))
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
            report = SlotReport(part.label, "app (未検証)" if peek.kind == "app" else INVALID,
                                peek, "; ".join(peek.errors))
    im = report.info
    log(f"{part.label} [{role}] {rng(part.offset, part.size)}: {report.state}"
        + (f" - {report.error}" if report.error else ""))
    if im and im.kind == "app":
        log(f"  project_name {im.project_name!r} version {im.version!r} idf {im.idf_ver}")
        log(f"  elf_sha256 {im.elf_sha256.hex()}")
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
        log(f"  metadata: {'有効' if meta_name else '無効'} name {rec.name!r}"
            + (f" ({reason})" if reason else ""))
    elif rec:
        meta_name = rec.name
        log(f"  metadata: name {rec.name!r} (暫定・イメージと未照合)")
    else:
        log(f"  metadata: なし ({reason})")
    name, source = display_name(index, meta_name, im.project_name if im else None)
    log(f"  表示名: {name} (採用元 {source}{'' if verify else '、暫定'})")


def cmd_status(ctx: Ctx) -> int:
    log = ctx.open_log("status")

    def body() -> int:
        dev, _ = connect(ctx, log)
        table = dev.read(L.PARTITION_TABLE_OFFSET, L.PARTITION_TABLE_SIZE)
        kind = L.classify_table(table)
        log(f"パーティション表: {kind}")
        if kind != "multifirm-v1":
            try:
                for p in L.decode_partition_table(table):
                    log(f"  {p.label:<16} type {p.type} subtype 0x{p.subtype:02x} {rng(p.offset, p.size)}")
            except ValueError as e:
                log(f"  解析できません: {e}")
            return finish_ok(dev, log, "MultiFirm v1 ではないため詳細は表示しません")
        log(f"起動先 (otadata): {boot_selection(dev.read(L.OTADATA.offset, L.OTADATA.size))}")
        meta = dev.read(L.META.offset, 3 * L.SECTOR_SIZE)
        log("イメージ内容: " + ("読み戻して検証" if ctx.args.verify
                              else "未検証 (先頭セクタのみ。--verify で検証)"))
        _status_slot(dev, log, 0, L.HOST, meta, ctx.args.verify)
        for slot in L.GUEST_SLOTS:
            _status_slot(dev, log, slot, L.guest_partition(slot), meta, ctx.args.verify)
        return finish_ok(dev, log, "status 完了")

    return run_logged(ctx, log, body)


def cmd_backup(ctx: Ctx) -> int:
    log = ctx.open_log("backup")

    def body() -> int:
        dev, info = connect(ctx, log)
        take_backup(ctx, dev, info, log)
        return finish_ok(dev, log, "backup 完了")

    return run_logged(ctx, log, body)


def device_mode(args: argparse.Namespace) -> bool:
    return bool(args.execute or args.check_device)


def check_device_done(dev: Device, log: Log) -> int:
    return finish_ok(dev, log, "--check-device: 読み取り検証のみ行いました。書き込み・消去はしていません")


def cmd_install(ctx: Ctx) -> int:
    args = ctx.args
    log = ctx.open_log(f"install-slot{args.slot}") if device_mode(args) else Log()

    def body() -> int:
        data, info, name = load_guest(args, log)
        part = L.guest_partition(args.slot)
        meta_off = L.meta_sector_offset(args.slot)
        record = L.MetaRecord(name, info.image_size, info.elf_sha256, info.appended_digest, int(ctx.now()))
        sector = L.meta_sector(record)
        plan_header(log, [
            f"1. メタデータ[{args.slot}] 消去 {rng(meta_off, L.SECTOR_SIZE)}",
            f"2. {part.label} 全域消去 {rng(part.offset, part.size)}",
            f"3. イメージ書き込み・読み戻し検証 {rng(part.offset, len(data))}",
            f"4. メタデータ[{args.slot}] 書き込み・読み戻し検証 {rng(meta_off, L.SECTOR_SIZE)}",
            "書き込まない: NVS / multifirm_nvs / otadata / phy_init / 他スロット / 他メタデータ",
        ])
        if not device_mode(args):
            return dry_run_note(log)
        dev, dinfo = connect(ctx, log)
        require_layout(dev, log)
        check_chip_rev(info, dinfo, "イメージ")
        if args.check_device:
            head = dev.read(part.offset, L.SECTOR_SIZE)
            peek = None if head == ff(L.SECTOR_SIZE) else img.peek_image(head)
            log(f"現在の {part.label}: " + ("Empty" if peek is None
                                             else f"project_name {peek.project_name!r} (未検証)"))
            return check_device_done(dev, log)
        snaps = snapshot(dev, preserved_regions(exclude_meta_slot=args.slot))
        log("実行:")
        erase_verified(dev, log, meta_off, L.SECTOR_SIZE, f"メタデータ[{args.slot}]")
        erase_verified(dev, log, part.offset, part.size, part.label)
        write_verified(dev, log, part.offset, data, part.label)
        back = read_slot_image(dev, part)
        if back.state != READY or back.info.appended_digest != info.appended_digest:
            raise ToolError(f"書き込んだイメージを検証できません: {back.state} {back.error}")
        log(f"    イメージ検証 OK (digest {info.appended_digest.hex()})")
        write_verified(dev, log, meta_off, sector, f"メタデータ[{args.slot}]")
        rec, reason = L.decode_meta(dev.read(meta_off, L.SECTOR_SIZE), part.size)
        reason = reason or match_meta(rec, back.info)
        if reason:
            raise ToolError(f"メタデータを検証できません: {reason}")
        log("    メタデータ検証 OK")
        check_unchanged(dev, log, snaps)
        return finish_ok(dev, log, f"install 完了: slot {args.slot} = {name}")

    return run_logged(ctx, log, body, "同じ install を再実行するか、recover でホストへ戻してください")


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
        plan_header(log, [
            "1. 全体バックアップを確認 (現状と一致しなければ新規取得)",
            f"2. ota_0 全域消去 {rng(L.HOST.offset, L.HOST.size)}",
            f"3. ホスト書き込み・読み戻し検証 {rng(L.HOST.offset, len(data))}",
            "4. ブートローダ・表・メタデータ・ゲスト3スロットがバックアップと一致することを確認",
            "書き込まない: ブートローダ / NVS / multifirm_nvs / otadata / ゲスト",
        ])
        if not device_mode(args):
            return dry_run_note(log)
        dev, dinfo = connect(ctx, log)
        require_layout(dev, log)
        check_chip_rev(info, dinfo, "ホストイメージ")
        backup_path = Path(args.backup) if args.backup else latest_backup(ctx, dinfo.mac)
        backup = None
        if backup_path:
            backup = load_backup(backup_path)
            stale = _verify_backup_regions(dev, backup)
            log(f"バックアップ {backup_path}: "
                + (f"現状と不一致 ({', '.join(stale)})" if stale else "現状と一致"))
            if stale:
                backup = None
        if args.check_device:
            if backup is None:
                log("実行時は全体バックアップを新規取得します")
            return check_device_done(dev, log)
        if backup is None:
            _, backup = take_backup(ctx, dev, dinfo, log)
        snaps = snapshot(dev, preserved_regions()[:4])
        log("実行:")
        erase_verified(dev, log, L.HOST.offset, L.HOST.size, "ota_0")
        write_verified(dev, log, L.HOST.offset, data, "ota_0")
        back = read_slot_image(dev, L.HOST)
        if back.state != READY or back.info.appended_digest != info.appended_digest:
            raise ToolError(f"書き込んだホストを検証できません: {back.state} {back.error}")
        log(f"    イメージ検証 OK (digest {info.appended_digest.hex()})")
        changed = _verify_backup_regions(dev, backup)
        if changed:
            raise ToolError(f"保護領域がバックアップと一致しません: {', '.join(changed)}")
        log("  ブートローダ・表・メタデータ・ゲストがバックアップと一致")
        check_unchanged(dev, log, snaps)
        return finish_ok(dev, log, "install-host 完了")

    return run_logged(ctx, log, body, "install-host を再実行してください。ホストが壊れたままなら"
                      "バックアップから復元します (tools/README.md)")


def cmd_recover(ctx: Ctx) -> int:
    args = ctx.args
    log = ctx.open_log("recover") if device_mode(args) else Log()

    def body() -> int:
        plan_header(log, [
            "1. 表とホスト (ota_0) のイメージを検証",
            f"2. otadata 消去 {rng(L.OTADATA.offset, L.OTADATA.size)} → 次回起動はホスト",
            "書き込まない: NVS / メタデータ / 各スロット",
        ])
        if not device_mode(args):
            return dry_run_note(log)
        dev, _ = connect(ctx, log)
        require_layout(dev, log)
        host = read_slot_image(dev, L.HOST)
        log(f"ホスト: {host.state}" + (f" - {host.error}" if host.error else "")
            + (f" project_name {host.info.project_name!r}" if host.info else ""))
        if host.state != READY:
            log("ホストのイメージが正常ではないため、recover では戻れません。install-host で書き直してください")
            log("実機はリセットしていません (ダウンロードモードのまま)")
            return 1
        if args.check_device:
            return check_device_done(dev, log)
        erase_verified(dev, log, L.OTADATA.offset, L.OTADATA.size, "otadata")
        return finish_ok(dev, log, "recover 完了: 次回はホストが起動します")

    return run_logged(ctx, log, body, "recover を再実行してください")


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
        lines = ["0. 16 MiB 全体バックアップを保存し実機 MD5 と照合 (完了前は書かない)"]
        lines += [f"消去 {name} {rng(off, size)}" for name, off, size in erases]
        if build.phy_init:
            lines.append(f"書き込み phy_init {rng(L.PHY_INIT.offset, len(build.phy_init))}")
        lines += [f"消去して書き込み ota_0 (ホスト) {rng(L.HOST.offset, len(build.app))}",
                  f"書き込み パーティション表 {rng(L.PARTITION_TABLE_OFFSET, L.PARTITION_TABLE_SIZE)}",
                  f"消去して書き込み ブートローダ {rng(0, len(build.bootloader))}",
                  f"書き込まない: storage {rng(L.STORAGE.offset, L.STORAGE.size)}, "
                  f"coredump {rng(L.COREDUMP.offset, L.COREDUMP.size)}",
                  "NVS 設定と BLE ボンドは初期化されます。ゲストは空になり再インストールが必要です"]
        plan_header(log, lines)
        if not device_mode(args):
            return dry_run_note(log)
        dev, dinfo = connect(ctx, log)
        current = dev.read(L.PARTITION_TABLE_OFFSET, L.PARTITION_TABLE_SIZE)
        log(f"現在の表: {L.classify_table(current)} (SHA-256 {hashlib.sha256(current).hexdigest()})")
        check_chip_rev(build.app_info, dinfo, "ホストイメージ")
        check_chip_rev(build.bootloader_info, dinfo, "ブートローダ")
        if args.check_device:
            return check_device_done(dev, log)
        if args.backup:
            backup = load_backup(Path(args.backup))
            if not dev.verify(0, backup):
                raise ToolError("--backup が実機の現在の内容と一致しません。書き込みません")
            log(f"バックアップ {args.backup}: 実機と一致")
        else:
            take_backup(ctx, dev, dinfo, log)
        if not dev.verify(L.PARTITION_TABLE_OFFSET, current):
            raise ToolError("バックアップ中に表が変化しました。書き込みません")
        log("実行:")
        for name, off, size in erases:
            erase_verified(dev, log, off, size, name)
        if build.phy_init:
            write_verified(dev, log, L.PHY_INIT.offset, build.phy_init, "phy_init")
        erase_verified(dev, log, L.HOST.offset, L.HOST.size, "ota_0")
        write_verified(dev, log, L.HOST.offset, build.app, "ota_0")
        write_verified(dev, log, L.PARTITION_TABLE_OFFSET, build.table, "パーティション表")
        erase_verified(dev, log, 0, L.BOOTLOADER_MAX_SIZE, "ブートローダ領域")
        write_verified(dev, log, 0, build.bootloader, "ブートローダ")
        host = read_slot_image(dev, L.HOST)
        if host.state != READY or host.info.appended_digest != build.app_info.appended_digest:
            raise ToolError(f"ホストを検証できません: {host.state} {host.error}")
        require_layout(dev, log)
        return finish_ok(dev, log, "initial 完了: ホストのみの新配置になりました。ゲストを install してください")

    return run_logged(ctx, log, body, "initial を再実行するか、バックアップから復元してください "
                      "(tools/README.md)")


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="multifirm.py", description=__doc__)
    p.add_argument("--version", action="version", version=f"MultiFirm {TOOL_VERSION} (esptool {REQUIRED_ESPTOOL})")
    sub = p.add_subparsers(dest="command", required=True)

    def device_opts(sp: argparse.ArgumentParser, change: bool) -> None:
        sp.add_argument("--port", help="シリアルポート (例: COM11)")
        sp.add_argument("--baud", type=int, default=460800)
        sp.add_argument("--allow-untested-esptool", action="store_true")
        if change:
            mode = sp.add_mutually_exclusive_group()
            mode.add_argument("--execute", action="store_true", help="実機へ書き込む")
            mode.add_argument("--check-device", action="store_true", help="実機を読み取り検証のみ")

    sp = sub.add_parser("status", help="実機の表・スロット・メタデータを表示")
    device_opts(sp, change=False)
    sp.add_argument("--verify", action="store_true", help="イメージを読み戻して検証")

    sp = sub.add_parser("inspect", help="単体アプリイメージをオフライン検証")
    sp.add_argument("firmware")
    sp.add_argument("--role", choices=["guest", "host"], default="guest")

    sp = sub.add_parser("install", help="ゲストを ota_1..3 に書き込む")
    sp.add_argument("firmware")
    sp.add_argument("--slot", type=int, choices=L.GUEST_SLOTS, required=True)
    sp.add_argument("--name", help="表示名 (UTF-8 31 bytes 以内)")
    device_opts(sp, change=True)

    sp = sub.add_parser("install-host", help="ホストを ota_0 に書き込む")
    sp.add_argument("firmware")
    sp.add_argument("--backup", help="使用する全体バックアップ (.bin)")
    device_opts(sp, change=True)

    sp = sub.add_parser("recover", help="otadata を消してホストへ戻す")
    device_opts(sp, change=True)

    sp = sub.add_parser("backup", help="16 MiB 全体を保存")
    device_opts(sp, change=False)
    sp.add_argument("--out", help="保存先ディレクトリ")

    sp = sub.add_parser("initial", help="新配置を初期導入 (全体バックアップ後)")
    sp.add_argument("--host-build", required=True, help="ホストの ESP-IDF build ディレクトリ")
    sp.add_argument("--backup", help="取得済みの全体バックアップ (実機と一致する場合のみ使用)")
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
        print(f"失敗: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
