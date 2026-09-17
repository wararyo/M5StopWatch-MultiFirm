"""Phase 0 investigation helpers: parse ESP32-S3 flash dumps and app images.

Independent of esptool so the results can be cross-checked against
`esptool image_info`. Not the Phase 1 tool; kept for reproducibility.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

IMAGE_MAGIC = 0xE9
APP_DESC_MAGIC = 0xABCD5432
BOOTLOADER_DESC_MAGIC = 0x50
CHECKSUM_INIT = 0xEF

SPI_MODES = {0: "QIO", 1: "QOUT", 2: "DIO", 3: "DOUT", 4: "FAST_READ", 5: "SLOW_READ"}
SPI_SIZES = {0: "1MB", 1: "2MB", 2: "4MB", 3: "8MB", 4: "16MB", 5: "32MB", 6: "64MB", 7: "128MB"}
SPI_FREQS = {0x0: "40m", 0x1: "26m", 0x2: "20m", 0xF: "80m"}
CHIP_IDS = {0: "esp32", 2: "esp32s2", 9: "esp32s3", 5: "esp32c3"}


def cstr(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("utf-8", "replace")


@dataclass
class Segment:
    offset: int
    load_addr: int
    length: int


@dataclass
class ImageInfo:
    ok: bool
    errors: list[str] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    spi_mode: str = ""
    spi_speed: str = ""
    spi_size: str = ""
    entry: int = 0
    chip_id: int = -1
    min_rev_full: int = 0
    max_rev_full: int = 0
    hash_appended: bool = False
    checksum_ok: bool = False
    checksum_end: int = 0  # offset just past checksum byte
    image_size: int = 0  # including appended digest
    appended_digest: bytes = b""
    computed_digest: bytes = b""
    app_desc: dict | None = None
    bootloader_desc: dict | None = None


def parse_app_desc(b: bytes) -> dict | None:
    if len(b) < 256:
        return None
    magic, secure_version = struct.unpack_from("<II", b, 0)
    if magic != APP_DESC_MAGIC:
        return None
    return {
        "secure_version": secure_version,
        "version": cstr(b[16:48]),
        "project_name": cstr(b[48:80]),
        "time": cstr(b[80:96]),
        "date": cstr(b[96:112]),
        "idf_ver": cstr(b[112:144]),
        "app_elf_sha256": b[144:176].hex(),
        "min_efuse_blk_rev_full": struct.unpack_from("<H", b, 176)[0],
        "max_efuse_blk_rev_full": struct.unpack_from("<H", b, 178)[0],
        "mmu_page_size": 1 << b[180] if b[180] else 0,
    }


def parse_bootloader_desc(b: bytes) -> dict | None:
    # esp_bootloader_desc_t (IDF >= 5.2)
    if len(b) < 80 or b[0] != BOOTLOADER_DESC_MAGIC:
        return None
    return {
        "secure_version": b[1],
        "version": struct.unpack_from("<I", b, 4)[0],
        "idf_ver": cstr(b[8:40]),
        "date_time": cstr(b[40:64]),
    }


def parse_image(buf: bytes, limit: int | None = None) -> ImageInfo:
    """Parse an ESP image at buf[0:]. `limit` bounds the region (partition size)."""
    limit = len(buf) if limit is None else min(limit, len(buf))
    info = ImageInfo(ok=False)
    if limit < 24:
        info.errors.append("too short")
        return info
    magic, nseg, mode, speed_size, entry = struct.unpack_from("<BBBBI", buf, 0)
    if magic != IMAGE_MAGIC:
        info.errors.append(f"bad magic 0x{magic:02x}")
        return info
    info.spi_mode = SPI_MODES.get(mode, f"0x{mode:x}")
    info.spi_size = SPI_SIZES.get(speed_size >> 4, f"0x{speed_size >> 4:x}")
    info.spi_speed = SPI_FREQS.get(speed_size & 0xF, f"0x{speed_size & 0xF:x}")
    info.entry = entry
    (wp, d0, d1, d2, chip_id, min_rev, min_rev_full, max_rev_full) = struct.unpack_from(
        "<BBBBHBHH", buf, 8)
    info.chip_id = chip_id
    info.min_rev_full = min_rev_full
    info.max_rev_full = max_rev_full
    info.hash_appended = buf[23] == 1
    if nseg == 0 or nseg > 16:
        info.errors.append(f"bad segment count {nseg}")
        return info
    off = 24
    csum = CHECKSUM_INIT
    for i in range(nseg):
        if off + 8 > limit:
            info.errors.append(f"segment {i} header beyond limit")
            return info
        load, length = struct.unpack_from("<II", buf, off)
        data_off = off + 8
        if length > limit or data_off + length > limit:
            info.errors.append(f"segment {i} length 0x{length:x} beyond limit")
            return info
        info.segments.append(Segment(data_off, load, length))
        for byte in buf[data_off:data_off + length]:
            csum ^= byte
        off = data_off + length
    # padding: checksum is the last byte of a 16-byte aligned block
    pad = 15 - (off % 16)
    csum_off = off + pad
    if csum_off >= limit:
        info.errors.append("checksum beyond limit")
        return info
    info.checksum_ok = buf[csum_off] == csum
    if not info.checksum_ok:
        info.errors.append(f"checksum mismatch stored=0x{buf[csum_off]:02x} calc=0x{csum:02x}")
    info.checksum_end = csum_off + 1
    info.image_size = info.checksum_end
    if info.hash_appended:
        if info.checksum_end + 32 > limit:
            info.errors.append("appended digest beyond limit")
            return info
        info.appended_digest = bytes(buf[info.checksum_end:info.checksum_end + 32])
        info.computed_digest = hashlib.sha256(buf[:info.checksum_end]).digest()
        info.image_size += 32
        if info.appended_digest != info.computed_digest:
            info.errors.append("appended SHA-256 mismatch")
    first = info.segments[0]
    seg0 = bytes(buf[first.offset:first.offset + min(first.length, 256)])
    info.app_desc = parse_app_desc(seg0)
    info.bootloader_desc = parse_bootloader_desc(seg0)
    info.ok = not info.errors
    return info


@dataclass
class PartEntry:
    label: str
    type: int
    subtype: int
    offset: int
    size: int
    flags: int


def parse_partition_table(buf: bytes) -> tuple[list[PartEntry], dict]:
    """buf = 0xC00 bytes at 0x8000. Returns entries and md5 info."""
    entries: list[PartEntry] = []
    md5 = {"present": False}
    for i in range(0, 0xC00, 32):
        e = buf[i:i + 32]
        if e[:2] == b"\xff\xff":
            md5["end_offset"] = i
            break
        if e[:2] == b"\xeb\xeb":
            md5["present"] = True
            md5["stored"] = e[16:32].hex()
            md5["computed"] = hashlib.md5(buf[:i]).hexdigest()
            md5["ok"] = md5["stored"] == md5["computed"]
            md5["md5_entry_offset"] = i
            continue
        if e[:2] != b"\xaa\x50":
            md5["error"] = f"bad entry magic at +0x{i:x}"
            break
        t, st, off, size = struct.unpack_from("<BBII", e, 2)
        label = cstr(e[12:28])
        flags = struct.unpack_from("<I", e, 28)[0]
        entries.append(PartEntry(label, t, st, off, size, flags))
    return entries, md5
