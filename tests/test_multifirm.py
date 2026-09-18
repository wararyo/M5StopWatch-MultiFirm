"""PC tests for tools/ (no device, no esptool needed).

python -m unittest discover -s tests -v
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import device as D  # noqa: E402
import esp_image as E  # noqa: E402
import layout as L  # noqa: E402
import layout_fixtures  # noqa: E402
import multifirm as M  # noqa: E402

NOW = 1_790_000_000.0


# ---------------------------------------------------------------- synthetic images

def app_desc(project: str = "TestApp", version: str = "1.0", elf: bytes = b"\x11" * 32,
             idf: str = "v5.5.4") -> bytes:
    d = struct.pack("<II", E.APP_DESC_MAGIC, 0) + b"\0" * 8
    d += version.encode().ljust(32, b"\0") + project.encode().ljust(32, b"\0")
    d += b"12:00:00".ljust(16, b"\0") + b"Sep 18 2026".ljust(16, b"\0")
    d += idf.encode().ljust(32, b"\0") + elf
    return d.ljust(256, b"\0")


def bootloader_desc() -> bytes:
    return (bytes([E.BOOTLOADER_DESC_MAGIC, 0, 0, 0]) + struct.pack("<I", 1)
            + b"v5.5.4".ljust(32, b"\0") + b"Sep 18 2026 12:00:00".ljust(24, b"\0") + b"\0" * 16)


def make_image(size: int | None = None, *, desc: bytes | None = None, chip_id: int = 9,
               hash_appended: bool = True, mode: int = 2, seed: int = 1, max_rev: int = 99) -> bytes:
    """Valid ESP image. size (multiple of 16, with hash) sets the exact total length."""
    desc = app_desc() if desc is None else desc
    seg0 = desc + bytes((seed * 7 + i) & 0xFF for i in range(64))
    seg1_len = 1000
    if size is not None:
        assert hash_appended and size % 16 == 0
        seg1_len = size - 33 - (24 + 8 + len(seg0) + 8)
    seg1 = bytes((seed + i * 13) & 0xFF for i in range(seg1_len))
    header = struct.pack("<BBBBI", E.IMAGE_MAGIC, 2, mode, 0x4F, 0x40380000)
    header += struct.pack("<BBBBHBHH4sB", 0xEE, 0, 0, 0, chip_id, 0, 0, max_rev, b"\0" * 4,
                          1 if hash_appended else 0)
    body = header
    csum = E.CHECKSUM_INIT
    for load, data in ((0x3C000020, seg0), (0x42000020, seg1)):
        body += struct.pack("<II", load, len(data)) + data
        for b in data:
            csum ^= b
    body += b"\0" * (15 - len(body) % 16) + bytes([csum])
    if hash_appended:
        body += hashlib.sha256(body).digest()
    if size is not None:
        assert len(body) == size, (len(body), size)
    return body


def make_bootloader() -> bytes:
    return make_image(desc=bootloader_desc(), max_rev=99)


def multifirm_flash() -> bytearray:
    flash = bytearray(b"\xFF" * L.FLASH_SIZE)
    boot = make_bootloader()
    flash[0:len(boot)] = boot
    flash[L.PARTITION_TABLE_OFFSET:L.PARTITION_TABLE_OFFSET + L.PARTITION_TABLE_SIZE] = L.PARTITION_TABLE
    host = make_image(desc=app_desc("StopWatch-UserDemo"), seed=9)
    flash[L.HOST.offset:L.HOST.offset + len(host)] = host
    flash[L.NVS.offset:L.NVS.offset + 64] = bytes(range(64))  # pretend settings
    flash[L.OTADATA.offset:L.OTADATA.offset + 4] = b"\x01\0\0\0"
    flash[L.MULTIFIRM_NVS.offset:L.MULTIFIRM_NVS.offset + 8] = b"SHARED!!"
    flash[L.STORAGE.offset:L.STORAGE.offset + 8] = b"FATFSFAT"
    return flash


def legacy_flash(table: bytes) -> bytearray:
    flash = bytearray(b"\xFF" * L.FLASH_SIZE)
    flash[L.PARTITION_TABLE_OFFSET:L.PARTITION_TABLE_OFFSET + L.PARTITION_TABLE_SIZE] = table
    flash[L.STORAGE.offset:L.STORAGE.offset + 8] = b"FATFSFAT"
    flash[L.COREDUMP.offset:L.COREDUMP.offset + 4] = b"CORE"
    return flash


class ToolCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.dev = D.MemoryDevice(bytes(multifirm_flash()))
        self.factory_calls = 0

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def file(self, name: str, data: bytes) -> str:
        path = self.dir / name
        path.write_bytes(data)
        return str(path)

    def factory(self, args, log):
        self.factory_calls += 1
        return self.dev

    def run_tool(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = M.main(list(argv), factory=self.factory, state_dir=self.dir / "state", now=lambda: NOW)
        return code, out.getvalue()


# ---------------------------------------------------------------- layout

class LayoutTest(unittest.TestCase):
    def test_partitions_are_contiguous_and_ordered(self):
        end = 0x9000
        for p in L.PARTITIONS:
            self.assertGreaterEqual(p.offset, end)
            self.assertEqual(p.offset % L.SECTOR_SIZE, 0)
            self.assertEqual(p.size % L.SECTOR_SIZE, 0)
            end = p.end
        self.assertLessEqual(end, L.FLASH_SIZE)
        labels = [p.label for p in L.PARTITIONS]
        self.assertLess(labels.index("nvs"), labels.index("multifirm_nvs"))
        nvs = [p.label for p in L.PARTITIONS if p.type == L.TYPE_DATA and p.subtype == L.SUBTYPE_NVS]
        self.assertEqual(nvs[0], "nvs")

    def test_meta_sectors(self):
        self.assertEqual([L.meta_sector_offset(s) for s in L.GUEST_SLOTS], [0x1C000, 0x1D000, 0x1E000])
        self.assertEqual(L.META_RESERVED_OFFSET, 0x1F000)
        with self.assertRaises(ValueError):
            L.meta_sector_offset(4)

    def test_csv_matches_definition(self):
        text = (ROOT / "docs" / "partitions.multifirm.csv").read_text(encoding="utf-8")
        self.assertEqual(L.parse_csv(text), list(L.PARTITIONS))

    def test_table_roundtrip_and_classification(self):
        self.assertEqual(len(L.PARTITION_TABLE), L.PARTITION_TABLE_SIZE)
        self.assertEqual(L.decode_partition_table(L.PARTITION_TABLE), list(L.PARTITIONS))
        self.assertEqual(L.classify_table(L.PARTITION_TABLE), "multifirm-v1")
        self.assertEqual(L.classify_table(L.LEGACY_3GUEST), "legacy-3guest")
        self.assertEqual(L.classify_table(L.LEGACY_2APP), "legacy-2app")
        standalone = L.encode_partition_table((
            L.Partition("nvs", 1, 2, 0x9000, 0x5000), L.Partition("app0", 0, 0, 0x10000, 0xC80000)))
        self.assertEqual(L.classify_table(standalone), "other")
        broken = bytearray(L.PARTITION_TABLE)
        broken[40] ^= 1
        self.assertEqual(L.classify_table(bytes(broken)), "invalid")
        padded = bytearray(L.PARTITION_TABLE)
        padded[-1] = 0  # padding is part of the comparison
        self.assertNotEqual(L.classify_table(bytes(padded)), "multifirm-v1")

    def test_table_matches_esp_idf_generator(self):
        gen = find_gen_esp32part()
        if not gen:
            self.skipTest("ESP-IDF gen_esp32part.py not found (set IDF_PATH)")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "pt.bin"
            subprocess.run([sys.executable, str(gen), "--quiet", "--flash-size", "16MB",
                            str(ROOT / "docs" / "partitions.multifirm.csv"), str(out)], check=True)
            self.assertEqual(out.read_bytes(), L.PARTITION_TABLE)

    def test_python_and_cpp_constants_agree(self):
        header = (ROOT / "src" / "multifirm_layout.h").read_text(encoding="utf-8")
        consts = {m.group(1): int(m.group(2), 0) for m in
                  re.finditer(r"constexpr \w+ (k\w+) = (0x[0-9A-Fa-f]+|\d+);", header)}
        self.assertEqual(consts["kFlashSize"], L.FLASH_SIZE)
        self.assertEqual(consts["kPartitionTableOffset"], L.PARTITION_TABLE_OFFSET)
        self.assertEqual(consts["kPartitionTableSize"], L.PARTITION_TABLE_SIZE)
        self.assertEqual(consts["kHostOffset"], L.HOST.offset)
        self.assertEqual(consts["kHostMaxSize"], L.HOST_MAX_SIZE)
        self.assertEqual(consts["kGuestMaxSize"], L.GUEST_MAX_SIZE)
        self.assertEqual(consts["kMetaOffset"], L.META.offset)
        self.assertEqual(consts["kMetaSize"], L.META.size)
        self.assertEqual(consts["kSubtypeMultifirmMeta"], L.SUBTYPE_MULTIFIRM_META)
        self.assertEqual(consts["kMetaMagic"], L.META_MAGIC)
        self.assertEqual(consts["kMetaVersion"], L.META_VERSION)
        self.assertEqual(consts["kMetaLength"], L.META_LENGTH)
        self.assertEqual(consts["kMetaRecordSize"], L.META_RECORD_SIZE)
        self.assertEqual(consts["kNameMaxBytes"], L.NAME_MAX_BYTES)
        rows = re.findall(r'\{"(\w+)", (kType\w+), (0x[0-9A-Fa-f]+|kSubtype\w+), (0x[0-9A-Fa-f]+), (0x[0-9A-Fa-f]+)\}',
                          header)
        symbols = {"kTypeApp": L.TYPE_APP, "kTypeData": L.TYPE_DATA,
                   "kSubtypeMultifirmMeta": L.SUBTYPE_MULTIFIRM_META}
        parsed = [L.Partition(label, symbols[t], symbols.get(st) if st in symbols else int(st, 0),
                              int(o, 0), int(s, 0)) for label, t, st, o, s in rows]
        self.assertEqual(parsed, list(L.PARTITIONS))
        defaults = set(re.findall(r'"([^"]+)"', header[header.index("kDefaults[]"):].split(";")[0]))
        self.assertEqual(defaults, set(L.DEFAULT_PROJECT_NAMES))
        codes = re.findall(r'return "(\w+)";', header[header.index("metaErrorName"):])
        self.assertEqual(set(codes) - {"ok"}, set(L.META_ERRORS))


def find_gen_esp32part() -> Path | None:
    candidates = []
    if os.environ.get("IDF_PATH"):
        candidates.append(Path(os.environ["IDF_PATH"]))
    candidates.append(ROOT.parent / "M5StopWatch-UserDemo" / ".tools" / "esp-idf")
    for idf in candidates:
        gen = idf / "components" / "partition_table" / "gen_esp32part.py"
        if gen.exists():
            return gen
    return None


# ---------------------------------------------------------------- metadata

class MetaTest(unittest.TestCase):
    def test_crc_vector(self):
        self.assertEqual(L.crc32(b"123456789"), 0xCBF43926)

    def test_roundtrip(self):
        rec = L.MetaRecord("Kantan プレイ", 826_784, bytes(range(32)), bytes(32), 123)
        sector = L.meta_sector(rec)
        self.assertEqual(len(sector), L.SECTOR_SIZE)
        self.assertEqual(sector[:4], b"MFRM")
        self.assertEqual(sector[L.META_RECORD_SIZE:], b"\xFF" * (L.SECTOR_SIZE - L.META_RECORD_SIZE))
        self.assertEqual(L.decode_meta(sector), (rec, None))

    def test_fixtures_are_current(self):
        rendered = layout_fixtures.render()
        on_disk = {p.name: p.read_bytes() for p in layout_fixtures.FIXTURE_DIR.glob("*") if p.is_file()
                   and p.name != ".gitattributes"}
        self.assertEqual(set(on_disk), set(rendered), "run python tests/layout_fixtures.py")
        for name, data in rendered.items():
            self.assertEqual(on_disk[name].replace(b"\r\n", b"\n") if name.endswith(".txt") else on_disk[name],
                             data, f"{name} is stale; run python tests/layout_fixtures.py")

    def test_name_rules(self):
        self.assertIsNone(L.validate_name("A" * 31))
        self.assertIsNotNone(L.validate_name("A" * 32))
        self.assertIsNone(L.validate_name("あいうえおかきくけこa"))  # 30 + 1 = 31 bytes
        self.assertIsNotNone(L.validate_name("あ" * 11))  # 33 bytes
        self.assertIsNotNone(L.validate_name(""))
        self.assertIsNotNone(L.validate_name("a\0b"))
        self.assertIsNotNone(L.validate_name("tab\there"))
        self.assertIsNotNone(L.validate_name("c1"))
        with self.assertRaises(ValueError):
            L.encode_meta(L.MetaRecord("A" * 32, 1, bytes(32), bytes(32), 0))

    def test_default_project_names(self):
        for name in ("firmware", "arduino-lib-builder", "", None):
            self.assertIsNone(L.usable_project_name(name))
        self.assertEqual(L.usable_project_name("VibeWatch"), "VibeWatch")


# ---------------------------------------------------------------- images

class ImageTest(unittest.TestCase):
    def test_valid_image(self):
        data = make_image()
        info = E.verify_app_file(data, L.GUEST_MAX_SIZE)
        self.assertTrue(info.ok, info.errors)
        self.assertTrue(info.digest_ok)
        self.assertEqual(info.image_size, len(data))
        self.assertEqual(info.project_name, "TestApp")
        self.assertEqual(info.appended_digest, hashlib.sha256(data[:-32]).digest())
        self.assertNotEqual(info.appended_digest.hex(), hashlib.sha256(data).hexdigest())

    def test_capacity_limits(self):
        for cap in (L.GUEST_MAX_SIZE, L.HOST_MAX_SIZE):
            exact = make_image(size=cap)
            self.assertTrue(E.verify_app_file(exact, cap).ok)
            over = make_image(size=cap + 16)
            self.assertFalse(E.verify_app_file(over, cap).ok)
            # One byte over: trailing byte after the digest is rejected too.
            self.assertFalse(E.verify_app_file(exact + b"\xFF", cap).ok)

    def rejects(self, data: bytes, pattern: str, cap: int = L.GUEST_MAX_SIZE):
        info = E.verify_app_file(data, cap)
        self.assertFalse(info.ok)
        self.assertRegex("; ".join(info.errors), pattern)

    def test_rejections(self):
        good = make_image()
        self.rejects(make_image(hash_appended=False), "no appended SHA")
        self.rejects(good + b"\xFF" * 16, "extra bytes")
        self.rejects(make_image(chip_id=5), "chip_id")
        self.rejects(good[:len(good) // 2], "truncated")
        corrupt = bytearray(good)
        corrupt[500] ^= 0x40
        self.rejects(bytes(corrupt), "checksum mismatch|SHA-256 mismatch")
        bad_digest = bytearray(good)
        bad_digest[-1] ^= 1
        self.rejects(bytes(bad_digest), "SHA-256 mismatch")
        bad_len = bytearray(good)
        struct.pack_into("<I", bad_len, 24 + 4, 0x7FFFFFFF)
        self.rejects(bytes(bad_len), "exceeds capacity")
        self.rejects(b"\x00" * 64, "magic")
        self.rejects(make_bootloader(), "not a standalone app")
        merged = make_bootloader().ljust(0x8000, b"\xFF") + L.PARTITION_TABLE.ljust(0x18000, b"\xFF") + good
        self.rejects(merged, "not a standalone app")

    def test_signed_like_trailer_rejected(self):
        # Secure Boot v2 appends a 4 KiB signature block after the digest.
        self.rejects(make_image() + b"\xE7" + b"\0" * 4095, "extra bytes")

    def test_header_warnings(self):
        info = E.verify_app_file(make_image(mode=0), L.GUEST_MAX_SIZE)
        self.assertTrue(info.ok)
        self.assertTrue(any("QIO" in w for w in info.warnings))

    def test_bootloader(self):
        info = E.verify_bootloader_file(make_bootloader(), L.BOOTLOADER_MAX_SIZE)
        self.assertTrue(info.ok, info.errors)
        self.assertEqual(info.idf_ver, "v5.5.4")
        self.assertFalse(E.verify_bootloader_file(make_image(), L.BOOTLOADER_MAX_SIZE).ok)

    def test_need_more_never_exceeds_capacity(self):
        data = make_image()
        with self.assertRaises(E.NeedMore) as ctx:
            E.parse_image(data[:0x40], 0x100000, complete=False)
        self.assertLessEqual(ctx.exception.size, 0x100000)
        info = E.parse_image(data[:0x40], 0x100, complete=False)
        self.assertFalse(info.ok)

    def test_chip_rev(self):
        info = E.verify_app_file(make_image(max_rev=99), L.GUEST_MAX_SIZE)
        self.assertTrue(E.chip_rev_supported(info, 2))
        self.assertFalse(E.chip_rev_supported(info, 100))
        self.assertTrue(E.chip_rev_supported(E.verify_app_file(make_image(max_rev=0xFFFF), L.GUEST_MAX_SIZE), 300))

    def test_real_builds_when_available(self):
        bins = ROOT / ".backup" / "phase0" / "bins"
        if not bins.exists():
            self.skipTest("Phase 0 local binaries are not present")
        for name in ("kantanplay-std.bin", "mutehid-std.bin", "vibewatch-std.bin"):
            self.assertTrue(E.verify_app_file((bins / name).read_bytes(), L.GUEST_MAX_SIZE).ok, name)
        self.assertTrue(E.verify_app_file((bins / "userdemo-rebuild.bin").read_bytes(), L.HOST_MAX_SIZE).ok)


# ---------------------------------------------------------------- commands

class DryRunTest(ToolCase):
    def test_change_commands_do_not_connect_without_execute(self):
        guest = self.file("g.bin", make_image(desc=app_desc("MyGuest")))
        host = self.file("h.bin", make_image(desc=app_desc("MyHost")))
        build = make_host_build(self.dir / "build")
        for argv in (["install", "--slot", "1", guest, "--port", "COM99"],
                     ["install-host", host, "--port", "COM99"],
                     ["recover", "--port", "COM99"],
                     ["initial", "--host-build", str(build), "--port", "COM99"]):
            code, out = self.run_tool(*argv)
            self.assertEqual(code, 0, out)
            self.assertIn("DRY RUN", out)
            self.assertIn("未検証", out)
        self.assertEqual(self.factory_calls, 0)
        self.assertEqual(self.dev.ops, [])
        self.assertFalse((self.dir / "state").exists())

    def test_inspect(self):
        code, out = self.run_tool("inspect", self.file("g.bin", make_image(desc=app_desc("firmware"))))
        self.assertEqual(code, 0)
        self.assertIn("--name が必要", out)
        code, _ = self.run_tool("inspect", self.file("bad.bin", make_image() + b"\0"))
        self.assertEqual(code, 1)
        big = self.file("big.bin", make_image(size=L.GUEST_MAX_SIZE + 16))
        self.assertEqual(self.run_tool("inspect", big)[0], 1)
        self.assertEqual(self.run_tool("inspect", big, "--role", "host")[0], 0)

    def test_name_resolution(self):
        default = self.file("fw.bin", make_image(desc=app_desc("arduino-lib-builder")))
        code, out = self.run_tool("install", "--slot", "1", default)
        self.assertEqual(code, 1)
        self.assertIn("--name", out)
        code, out = self.run_tool("install", "--slot", "1", default, "--name", "Vibe")
        self.assertEqual(code, 0, out)
        self.assertIn("表示名: Vibe", out)
        code, out = self.run_tool("install", "--slot", "1", default, "--name", "あ" * 11)
        self.assertEqual(code, 1)
        named = self.file("n.bin", make_image(desc=app_desc("VibeWatch")))
        self.assertIn("表示名: Override", self.run_tool("install", "--slot", "1", named, "--name", "Override")[1])
        self.assertIn("表示名: VibeWatch", self.run_tool("install", "--slot", "1", named)[1])

    def test_execute_and_check_device_are_exclusive(self):
        guest = self.file("g.bin", make_image())
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                self.run_tool("install", "--slot", "1", guest, "--execute", "--check-device", "--port", "X")


class InstallTest(ToolCase):
    def install(self, slot: int = 2, image: bytes | None = None, *extra: str) -> tuple[int, str, bytes]:
        image = image or make_image(desc=app_desc("MyGuest"), seed=slot)
        path = self.file(f"guest{slot}.bin", image)
        code, out = self.run_tool("install", "--slot", str(slot), path, "--port", "COM99", "--execute", *extra)
        return code, out, image

    def test_install_writes_only_planned_ranges(self):
        before = bytes(self.dev.flash)
        code, out, image = self.install(2)
        self.assertEqual(code, 0, out)
        part = L.guest_partition(2)
        meta = L.meta_sector_offset(2)
        self.assertEqual(self.dev.mutating_ops(), [
            ("erase", meta, L.SECTOR_SIZE), ("erase", part.offset, part.size),
            ("write", part.offset, len(image)), ("write", meta, L.SECTOR_SIZE)])
        self.assertTrue(self.dev.was_reset)
        after = bytes(self.dev.flash)
        self.assertEqual(after[part.offset:part.offset + len(image)], image)
        # Everything outside the slot and its metadata sector is byte-identical.
        self.assertEqual(after[:meta], before[:meta])
        self.assertEqual(after[meta + L.SECTOR_SIZE:part.offset], before[meta + L.SECTOR_SIZE:part.offset])
        self.assertEqual(after[part.end:], before[part.end:])
        rec, err = L.decode_meta(after[meta:meta + L.SECTOR_SIZE])
        self.assertIsNone(err)
        self.assertEqual(rec.name, "MyGuest")
        self.assertEqual(rec.image_size, len(image))
        self.assertEqual(rec.app_digest, image[-32:])
        self.assertEqual(rec.installed_at, int(NOW))
        logs = list((self.dir / "state" / "logs").glob("*install-slot2.log"))
        self.assertEqual(len(logs), 1)
        text = logs[0].read_text(encoding="utf-8")
        for needle in ("SHA-256", "読み戻し一致", "リセット方針", "不変を確認", image[-32:].hex()):
            self.assertIn(needle, text)

    def test_slot_is_fully_erased_before_write(self):
        part = L.guest_partition(1)
        self.dev.flash[part.end - 16:part.end] = b"\x00" * 16  # leftovers from an older image
        code, out, _ = self.install(1)
        self.assertEqual(code, 0, out)
        self.assertEqual(bytes(self.dev.flash[part.end - 16:part.end]), b"\xFF" * 16)

    def test_rejects_other_layouts_without_writing(self):
        for table in (L.LEGACY_3GUEST, L.LEGACY_2APP,
                      L.encode_partition_table((L.Partition("app0", 0, 0, 0x10000, 0xC80000),))):
            self.dev = D.MemoryDevice(bytes(legacy_flash(table)))
            code, out, _ = self.install(1)
            self.assertEqual(code, 1)
            self.assertIn("MultiFirm v1 ではありません", out)
            self.assertEqual(self.dev.mutating_ops(), [])
            self.assertTrue(self.dev.was_reset, "nothing was written, so return to the app")

    def test_check_device_reads_only(self):
        code, out, _ = self.install(3)
        self.assertEqual(code, 0)
        self.dev.ops.clear()
        self.dev.was_reset = False
        path = self.file("g.bin", make_image(desc=app_desc("Other")))
        code, out = self.run_tool("install", "--slot", "3", path, "--port", "COM99", "--check-device")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.dev.mutating_ops(), [])
        self.assertIn("MyGuest", out)
        self.assertTrue(self.dev.was_reset)

    def test_readback_mismatch_is_failure_without_reset(self):
        part = L.guest_partition(1)

        def corrupt(offset, data):
            if offset == part.offset:
                data = bytearray(data)
                data[100] ^= 1
            return bytes(data)

        self.dev.corrupt_write = corrupt
        code, out, _ = self.install(1)
        self.assertEqual(code, 1)
        self.assertIn("読み戻しが一致しません", out)
        self.assertFalse(self.dev.was_reset)
        self.assertIn("リセットしていません", out)

    def test_erase_not_effective_is_failure(self):
        original = self.dev.erase
        self.dev.erase = lambda off, size: None if off == L.guest_partition(1).offset else original(off, size)
        self.dev.flash[L.guest_partition(1).offset] = 0
        code, out, _ = self.install(1)
        self.assertEqual(code, 1)
        self.assertIn("消去を確認できません", out)
        self.assertFalse(self.dev.was_reset)

    def test_communication_failure_then_rerun(self):
        part = L.guest_partition(1)
        self.dev.fail_on = lambda op, off, size: op == "write" and off == part.offset
        code, out, image = self.install(1)
        self.assertEqual(code, 1)
        self.assertFalse(self.dev.was_reset)
        # Interrupted state: metadata erased, slot empty. The same install recovers.
        self.assertEqual(bytes(self.dev.flash[L.meta_sector_offset(1):L.meta_sector_offset(1) + 4]), b"\xFF" * 4)
        self.dev.fail_on = lambda op, off, size: False
        code, out, _ = self.install(1, image)
        self.assertEqual(code, 0, out)
        self.assertTrue(self.dev.was_reset)

    def test_meta_write_failure_leaves_image_without_name(self):
        meta = L.meta_sector_offset(2)
        self.dev.fail_on = lambda op, off, size: op == "write" and off == meta
        code, _, image = self.install(2)
        self.assertEqual(code, 1)
        report = M.read_slot_image(self.dev, L.guest_partition(2))
        self.assertEqual(report.state, M.READY)  # valid image even without metadata

    def test_rejects_unsupported_chip_revision(self):
        self.dev.info.chip_rev_full = 100
        code, out, _ = self.install(1, make_image(max_rev=99, desc=app_desc("X")))
        self.assertEqual(code, 1)
        self.assertIn("revision", out)
        self.assertEqual(self.dev.mutating_ops(), [])


class StatusTest(ToolCase):
    def install(self, slot, project, name=None):
        path = self.file(f"s{slot}.bin", make_image(desc=app_desc(project), seed=slot + 20))
        argv = ["install", "--slot", str(slot), path, "--port", "COM99", "--execute"]
        if name:
            argv += ["--name", name]
        self.assertEqual(self.run_tool(*argv)[0], 0)

    def test_status_default_is_tentative_and_verify_checks(self):
        self.install(1, "firmware", name="Named")
        self.install(2, "ProjectOnly")
        # slot 2: drop metadata; slot 3: default project name without metadata
        self.dev.flash[L.meta_sector_offset(2):L.meta_sector_offset(2) + L.SECTOR_SIZE] = b"\xFF" * L.SECTOR_SIZE
        img3 = make_image(desc=app_desc("arduino-lib-builder"), seed=3)
        p3 = L.guest_partition(3)
        self.dev.flash[p3.offset:p3.offset + len(img3)] = img3
        self.dev.ops.clear()
        code, out = self.run_tool("status", "--port", "COM99")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.dev.mutating_ops(), [])
        self.assertIn("未検証", out)
        self.assertIn("表示名: Named (採用元 metadata、暫定)", out)
        self.assertIn("表示名: ProjectOnly (採用元 project_name、暫定)", out)
        self.assertIn("表示名: App3 (採用元 fallback、暫定)", out)
        code, out = self.run_tool("status", "--port", "COM99", "--verify")
        self.assertEqual(code, 0, out)
        self.assertIn("表示名: Named (採用元 metadata)", out)
        self.assertIn("ota_1 [slot 1]", out)
        self.assertRegex(out, r"ota_1 \[slot 1\].*: Ready")

    def test_replaced_image_does_not_keep_old_name(self):
        self.install(1, "Original", name="OldName")
        other = make_image(desc=app_desc("Replacement"), seed=77)
        p1 = L.guest_partition(1)
        self.dev.flash[p1.offset:p1.end] = b"\xFF" * p1.size
        self.dev.flash[p1.offset:p1.offset + len(other)] = other
        code, out = self.run_tool("status", "--port", "COM99", "--verify")
        self.assertIn("metadata: 無効 name 'OldName' (app_digest does not match", out)
        self.assertIn("表示名: Replacement (採用元 project_name)", out)

    def test_corrupted_image_detected_with_intact_desc(self):
        self.install(1, "Victim")
        p1 = L.guest_partition(1)
        self.dev.flash[p1.offset + 700] ^= 0xFF  # after app_desc
        code, out = self.run_tool("status", "--port", "COM99", "--verify")
        self.assertRegex(out, r"ota_1 \[slot 1\].*: Invalid")
        self.assertIn("metadata: 無効", out)

    def test_status_on_legacy_layout(self):
        self.dev = D.MemoryDevice(bytes(legacy_flash(L.LEGACY_3GUEST)))
        code, out = self.run_tool("status", "--port", "COM99")
        self.assertEqual(code, 0)
        self.assertIn("legacy-3guest", out)

    def test_boot_selection(self):
        def entry(seq, state=0xFFFFFFFF):
            import zlib
            return (struct.pack("<I", seq) + b"\xFF" * 20 + struct.pack("<I", state)
                    + struct.pack("<I", zlib.crc32(struct.pack("<I", seq), 0xFFFFFFFF))).ljust(L.SECTOR_SIZE, b"\xFF")
        self.assertIn("未設定", M.boot_selection(b"\xFF" * 0x2000))
        self.assertTrue(M.boot_selection(entry(20) + entry(21, 0)).startswith("ota_0"))
        self.assertTrue(M.boot_selection(entry(20) + entry(19)).startswith("ota_3"))
        self.assertTrue(M.boot_selection(entry(20) + entry(21, 3)).startswith("ota_3"))  # INVALID ignored


class HostAndRecoverTest(ToolCase):
    def test_install_host_takes_backup_and_preserves_everything_else(self):
        guest = make_image(desc=app_desc("Guest"), seed=5)
        p1 = L.guest_partition(1)
        self.dev.flash[p1.offset:p1.offset + len(guest)] = guest
        before = bytes(self.dev.flash)
        host = make_image(desc=app_desc("NewHost"), seed=33)
        code, out = self.run_tool("install-host", self.file("h.bin", host), "--port", "COM99", "--execute")
        self.assertEqual(code, 0, out)
        backups = list((self.dir / "state" / "backups").glob("backup-*.bin"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)
        manifest = json.loads(backups[0].with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(manifest["mac"], "28:84:85:43:a7:c0")
        self.assertEqual(manifest["layout"], "multifirm-v1")
        after = bytes(self.dev.flash)
        self.assertEqual(after[:L.HOST.offset], before[:L.HOST.offset])
        self.assertEqual(after[L.HOST.end:], before[L.HOST.end:])
        self.assertEqual(after[L.HOST.offset:L.HOST.offset + len(host)], host)
        self.assertEqual([op for op in self.dev.mutating_ops()],
                         [("erase", L.HOST.offset, L.HOST.size), ("write", L.HOST.offset, len(host))])

        # Second run reuses the matching backup (no new 16 MiB read).
        self.dev.ops.clear()
        code, out = self.run_tool("install-host", self.file("h2.bin", make_image(desc=app_desc("Host3"), seed=34)),
                                  "--port", "COM99", "--execute")
        self.assertEqual(code, 0, out)
        self.assertIn("現状と一致", out)
        self.assertNotIn(("read", 0, L.FLASH_SIZE), self.dev.ops)

    def test_install_host_stale_backup_triggers_new_backup(self):
        host = self.file("h.bin", make_image(desc=app_desc("NewHost"), seed=33))
        self.assertEqual(self.run_tool("install-host", host, "--port", "COM99", "--execute")[0], 0)
        p2 = L.guest_partition(2)
        g = make_image(desc=app_desc("Late"), seed=8)
        self.dev.flash[p2.offset:p2.offset + len(g)] = g
        code, out = self.run_tool("install-host", host, "--port", "COM99", "--execute")
        self.assertEqual(code, 0, out)
        self.assertIn("現状と不一致 (ota_2)", out)
        self.assertIn("全体バックアップを読み取り中", out)

    def test_install_host_rejects_oversize(self):
        big = self.file("big.bin", make_image(size=L.HOST_MAX_SIZE + 16))
        code, out = self.run_tool("install-host", big, "--port", "COM99", "--execute")
        self.assertEqual(code, 1)
        self.assertEqual(self.factory_calls, 0)

    def test_recover_erases_only_otadata(self):
        before = bytes(self.dev.flash)
        code, out = self.run_tool("recover", "--port", "COM99", "--execute")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.dev.mutating_ops(), [("erase", L.OTADATA.offset, L.OTADATA.size)])
        after = bytes(self.dev.flash)
        self.assertEqual(after[L.OTADATA.offset:L.OTADATA.end], b"\xFF" * L.OTADATA.size)
        self.assertEqual(after[:L.OTADATA.offset], before[:L.OTADATA.offset])
        self.assertEqual(after[L.OTADATA.end:], before[L.OTADATA.end:])

    def test_recover_refuses_broken_host(self):
        self.dev.flash[L.HOST.offset + 900] ^= 0xFF
        code, out = self.run_tool("recover", "--port", "COM99", "--execute")
        self.assertEqual(code, 1)
        self.assertIn("install-host", out)
        self.assertEqual(self.dev.mutating_ops(), [])
        self.assertFalse(self.dev.was_reset)

    def test_backup_command(self):
        code, out = self.run_tool("backup", "--port", "COM99", "--out", str(self.dir / "bk"))
        self.assertEqual(code, 0, out)
        files = list((self.dir / "bk").glob("backup-28848543a7c0-*.bin"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].stat().st_size, L.FLASH_SIZE)
        self.assertEqual(self.dev.mutating_ops(), [])


def make_host_build(root: Path, *, table: bytes = L.PARTITION_TABLE, rollback: bool = False,
                    phy_in_partition: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "bootloader").mkdir(exist_ok=True)
    (root / "partition_table").mkdir(exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    (root / "bootloader" / "bootloader.bin").write_bytes(make_bootloader())
    (root / "partition_table" / "partition-table.bin").write_bytes(table)
    (root / "Host.bin").write_bytes(make_image(desc=app_desc("StopWatch-UserDemo"), seed=44))
    flash_files = {"0x0": "bootloader/bootloader.bin", "0x8000": "partition_table/partition-table.bin",
                   "0x20000": "Host.bin", "0x19000": "ota_data_initial.bin"}
    if phy_in_partition:
        (root / "phy_init_data.bin").write_bytes(b"PHYDATA" * 20)
        flash_files["0x1b000"] = "phy_init_data.bin"
    flasher = {"flash_files": flash_files,
               "bootloader": {"offset": "0x0", "file": "bootloader/bootloader.bin"},
               "partition-table": {"offset": "0x8000", "file": "partition_table/partition-table.bin"},
               "app": {"offset": "0x20000", "file": "Host.bin"}}
    (root / "flasher_args.json").write_text(json.dumps(flasher))
    cfg = {"PARTITION_TABLE_OFFSET": 0x8000, "ESPTOOLPY_FLASHSIZE": "16MB",
           "BOOTLOADER_APP_ROLLBACK_ENABLE": rollback, "ESP_PHY_INIT_DATA_IN_PARTITION": phy_in_partition}
    (root / "config" / "sdkconfig.json").write_text(json.dumps(cfg))
    return root


class InitialTest(ToolCase):
    def setUp(self):
        super().setUp()
        flash = legacy_flash(L.LEGACY_3GUEST)
        flash[0x9000:0xD000] = b"\x12" * 0x4000  # old NVS
        flash[0x510000:0x510010] = b"OLDGUEST" * 2
        self.dev = D.MemoryDevice(bytes(flash))

    def test_initial_from_legacy_layout(self):
        before = bytes(self.dev.flash)
        build = make_host_build(self.dir / "build")
        code, out = self.run_tool("initial", "--host-build", str(build), "--port", "COM99", "--execute")
        self.assertEqual(code, 0, out)
        after = bytes(self.dev.flash)
        backups = list((self.dir / "state" / "backups").glob("*.bin"))
        self.assertEqual(backups[0].read_bytes(), before)
        # Backup finished before the first write.
        first_mut = next(i for i, op in enumerate(self.dev.ops) if op[0] in ("write", "erase"))
        self.assertIn(("read", 0, L.FLASH_SIZE), self.dev.ops[:first_mut])
        self.assertEqual(L.classify_table(after[0x8000:0x8C00]), "multifirm-v1")
        self.assertEqual(after[L.STORAGE.offset:L.STORAGE.end], before[L.STORAGE.offset:L.STORAGE.end])
        self.assertEqual(after[L.COREDUMP.offset:L.COREDUMP.end], before[L.COREDUMP.offset:L.COREDUMP.end])
        for p in (L.NVS, L.OTADATA, L.PHY_INIT, L.META, L.MULTIFIRM_NVS) + tuple(
                L.guest_partition(s) for s in L.GUEST_SLOTS):
            self.assertEqual(after[p.offset:p.end], b"\xFF" * p.size, p.label)
        host = (build / "Host.bin").read_bytes()
        self.assertEqual(after[L.HOST.offset:L.HOST.offset + len(host)], host)
        for op in self.dev.mutating_ops():
            end = op[1] + op[2]
            self.assertTrue(end <= L.STORAGE.offset, op)  # never touches storage/coredump
        self.assertTrue(self.dev.was_reset)

    def test_initial_writes_phy_data_when_configured(self):
        build = make_host_build(self.dir / "build", phy_in_partition=True)
        code, out = self.run_tool("initial", "--host-build", str(build), "--port", "COM99", "--execute")
        self.assertEqual(code, 0, out)
        self.assertEqual(bytes(self.dev.flash[L.PHY_INIT.offset:L.PHY_INIT.offset + 7]), b"PHYDATA")

    def test_initial_rejects_bad_builds_before_connecting(self):
        cases = [make_host_build(self.dir / "a", table=L.LEGACY_3GUEST),
                 make_host_build(self.dir / "b", rollback=True)]
        for build in cases:
            code, out = self.run_tool("initial", "--host-build", str(build), "--port", "COM99", "--execute")
            self.assertEqual(code, 1, out)
        self.assertEqual(self.factory_calls, 0)

    def test_initial_with_stale_backup_refuses(self):
        stale = self.dir / "stale.bin"
        stale.write_bytes(b"\x00" * L.FLASH_SIZE)
        build = make_host_build(self.dir / "build")
        code, out = self.run_tool("initial", "--host-build", str(build), "--port", "COM99", "--execute",
                                  "--backup", str(stale))
        self.assertEqual(code, 1)
        self.assertEqual(self.dev.mutating_ops(), [])

    def test_initial_check_device(self):
        build = make_host_build(self.dir / "build")
        code, out = self.run_tool("initial", "--host-build", str(build), "--port", "COM99", "--check-device")
        self.assertEqual(code, 0, out)
        self.assertIn("legacy-3guest", out)
        self.assertEqual(self.dev.mutating_ops(), [])


# ---------------------------------------------------------------- esptool backend

class FakeProc:
    def __init__(self, code, out):
        self.returncode, self.stdout, self.stderr = code, out, ""


class EsptoolDeviceTest(unittest.TestCase):
    def make(self, responses):
        self.calls = []

        def runner(cmd):
            self.calls.append(cmd)
            op = cmd[cmd.index("--after") + 2]
            code, out, write = responses.get(op, (0, "", None))
            if write:
                Path(cmd[-1]).write_bytes(write)
            return FakeProc(code, out)

        return D.EsptoolDevice("COM7", 460800, lambda msg: None, runner=runner, python="py")

    def test_commands(self):
        dev = self.make({
            "flash_id": (0, "Chip is ESP32-S3 (QFN56) (revision v0.2)\nMAC: 28:84:85:43:a7:c0\n"
                            "Detected flash size: 16MB\n", None),
            "read_flash": (0, "", b"\x01" * 16),
            "write_flash": (0, "Hash of data verified.\n", None),
            "verify_flash": (0, "-- verify OK (digest matched)\n", None),
        })
        info = dev.connect()
        self.assertEqual((info.mac, info.chip_rev_full, info.flash_size), ("28:84:85:43:a7:c0", 2, "16MB"))
        self.assertEqual(dev.read(0x8000, 16), b"\x01" * 16)
        dev.write(0x420000, b"abc")
        self.assertTrue(dev.verify(0x420000, b"abc"))
        dev.erase(0x1C000, 0x1000)
        dev.reset()
        for cmd in self.calls[:-1]:
            self.assertEqual(cmd[cmd.index("--before") + 1], "default_reset")
            self.assertEqual(cmd[cmd.index("--after") + 1], "no_reset")
        self.assertEqual(self.calls[-1][self.calls[-1].index("--after") + 1], "hard_reset")
        write = next(c for c in self.calls if "write_flash" in c)
        self.assertEqual(write[write.index("write_flash") + 1:write.index("write_flash") + 7], D.KEEP_FLASH_PARAMS)
        erase = next(c for c in self.calls if "erase_region" in c)
        self.assertEqual(erase[-2:], ["0x1c000", "0x1000"])
        dev.close()

    def test_failures(self):
        dev = self.make({
            "verify_flash": (2, "-- verify FAILED (digest mismatch)\n", None),
            "write_flash": (0, "no hash line\n", None),
            "read_flash": (0, "", b"\x01" * 8),
            "flash_id": (0, "Chip is ESP32-C3 (revision v0.4)\nMAC: 00:00:00:00:00:00\n", None),
        })
        self.assertFalse(dev.verify(0, b"x"))
        with self.assertRaises(D.DeviceError):
            dev.write(0, b"x")
        with self.assertRaises(D.DeviceError):
            dev.read(0, 16)  # short read
        with self.assertRaises(D.DeviceError):
            dev.connect()
        with self.assertRaises(D.DeviceError):
            dev.erase(0x1001, 0x1000)
        dev2 = self.make({"verify_flash": (1, "A fatal error occurred: port busy\n", None)})
        with self.assertRaises(D.DeviceError):
            dev2.verify(0, b"x")
        dev.close()
        dev2.close()


if __name__ == "__main__":
    unittest.main()
