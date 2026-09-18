"""Strict ESP32-S3 app image verification (unsigned images with appended SHA-256).

The appended digest covers bytes [0, checksum byte] of the image; the file SHA-256
is only for logs and read-back comparison.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

IMAGE_MAGIC = 0xE9
APP_DESC_MAGIC = 0xABCD5432
BOOTLOADER_DESC_MAGIC = 0x50
ESP32S3_CHIP_ID = 9
CHECKSUM_INIT = 0xEF
HEADER_SIZE = 24
SEGMENT_HEADER_SIZE = 8
MAX_SEGMENTS = 16
DIGEST_SIZE = 32

SPI_MODES = {0: "QIO", 1: "QOUT", 2: "DIO", 3: "DOUT", 4: "FAST_READ", 5: "SLOW_READ"}
SPI_SIZES = {0: "1MB", 1: "2MB", 2: "4MB", 3: "8MB", 4: "16MB", 5: "32MB", 6: "64MB", 7: "128MB"}
SPI_FREQS = {0x0: "40m", 0x1: "26m", 0x2: "20m", 0xF: "80m"}


class NeedMore(Exception):
    """Parsing needs `size` bytes from the start of the image."""

    def __init__(self, size: int):
        super().__init__(size)
        self.size = size


def _cstr(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("utf-8", "replace")


@dataclass
class ImageInfo:
    ok: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    kind: str = ""  # "app" / "bootloader"
    chip_id: int = -1
    spi_mode: str = ""
    spi_speed: str = ""
    spi_size: str = ""
    min_rev_full: int = 0
    max_rev_full: int = 0
    segments: int = 0
    hash_appended: bool = False
    image_size: int = 0  # up to the end of the appended digest
    appended_digest: bytes = b""
    computed_digest: bytes = b""
    file_size: int = 0
    file_sha256: str = ""
    project_name: str = ""
    version: str = ""
    idf_ver: str = ""
    date_time: str = ""
    elf_sha256: bytes = b""
    secure_version: int = 0

    @property
    def digest_ok(self) -> bool:
        return self.hash_appended and bool(self.appended_digest) and self.appended_digest == self.computed_digest

    @property
    def elf_sha256_set(self) -> bool:
        return any(self.elf_sha256)


def _xor_bytes(data: bytes) -> int:
    """XOR of all bytes (values that occur an odd number of times)."""
    result = 0
    for value in range(256):
        if data.count(value) & 1:
            result ^= value
    return result


def _header_fields(info: ImageInfo, buf: bytes) -> int:
    magic, nseg, mode, speed_size, _entry = struct.unpack_from("<BBBBI", buf, 0)
    if magic != IMAGE_MAGIC:
        raise ValueError(f"bad image magic 0x{magic:02x}")
    info.spi_mode = SPI_MODES.get(mode, f"0x{mode:x}")
    info.spi_size = SPI_SIZES.get(speed_size >> 4, f"0x{speed_size >> 4:x}")
    info.spi_speed = SPI_FREQS.get(speed_size & 0xF, f"0x{speed_size & 0xF:x}")
    info.chip_id, _min_rev = struct.unpack_from("<HB", buf, 12)
    info.min_rev_full, info.max_rev_full = struct.unpack_from("<HH", buf, 15)
    if buf[23] not in (0, 1):
        raise ValueError(f"bad hash_appended byte 0x{buf[23]:02x}")
    info.hash_appended = buf[23] == 1
    if nseg == 0 or nseg > MAX_SEGMENTS:
        raise ValueError(f"bad segment count {nseg}")
    return nseg


def parse_image(buf: bytes, capacity: int, *, complete: bool = True) -> ImageInfo:
    """Parse and verify an image at buf[0:].

    capacity: maximum image size allowed (partition size).
    complete: buf holds everything available; when False, raise NeedMore(n) if the
      image extends past len(buf) but not past capacity.
    Never reads beyond `capacity`, regardless of what the headers claim.
    """
    info = ImageInfo()

    def need(end: int) -> None:
        if end > capacity:
            raise ValueError(f"image exceeds capacity 0x{capacity:x}")
        if end > len(buf):
            if complete:
                raise ValueError("image truncated")
            raise NeedMore(end)

    try:
        need(HEADER_SIZE)
        nseg = _header_fields(info, buf)
        info.segments = nseg
        off = HEADER_SIZE
        csum = CHECKSUM_INIT
        first_data = None
        for i in range(nseg):
            need(off + SEGMENT_HEADER_SIZE)
            _load, length = struct.unpack_from("<II", buf, off)
            data_off = off + SEGMENT_HEADER_SIZE
            if length > capacity:
                raise ValueError(f"segment {i} length 0x{length:x} exceeds capacity")
            need(data_off + length)
            if first_data is None:
                first_data = data_off
            csum ^= _xor_bytes(bytes(buf[data_off:data_off + length]))
            off = data_off + length
        csum_off = off + (15 - off % 16)
        need(csum_off + 1)
        if buf[csum_off] != csum:
            info.errors.append(f"checksum mismatch (stored 0x{buf[csum_off]:02x}, computed 0x{csum:02x})")
        info.image_size = csum_off + 1
        if info.hash_appended:
            need(info.image_size + DIGEST_SIZE)
            info.appended_digest = bytes(buf[info.image_size:info.image_size + DIGEST_SIZE])
            info.computed_digest = hashlib.sha256(buf[:info.image_size]).digest()
            info.image_size += DIGEST_SIZE
            if not info.digest_ok:
                info.errors.append("appended SHA-256 mismatch")
        # Descriptor at the start of the first segment identifies the image kind.
        seg0 = bytes(buf[first_data:first_data + 256])
        if len(seg0) >= 256 and struct.unpack_from("<I", seg0, 0)[0] == APP_DESC_MAGIC:
            info.kind = "app"
            info.secure_version = struct.unpack_from("<I", seg0, 4)[0]
            info.version = _cstr(seg0[16:48])
            info.project_name = _cstr(seg0[48:80])
            info.date_time = f"{_cstr(seg0[96:112])} {_cstr(seg0[80:96])}"
            info.idf_ver = _cstr(seg0[112:144])
            info.elf_sha256 = seg0[144:176]
        elif seg0[:1] == bytes([BOOTLOADER_DESC_MAGIC]) and len(seg0) >= 64:
            info.kind = "bootloader"
            info.idf_ver = _cstr(seg0[8:40])
            info.date_time = _cstr(seg0[40:64])
    except NeedMore:
        raise
    except (ValueError, struct.error) as e:
        info.errors.append(str(e))
    if info.chip_id not in (-1, ESP32S3_CHIP_ID):
        info.errors.append(f"chip_id {info.chip_id} is not ESP32-S3 ({ESP32S3_CHIP_ID})")
    info.ok = not info.errors
    return info


def peek_image(head: bytes) -> ImageInfo:
    """Header and descriptor from the first sector only. NOT a verification."""
    info = ImageInfo()
    try:
        if len(head) < HEADER_SIZE + SEGMENT_HEADER_SIZE:
            raise ValueError("short read")
        info.segments = _header_fields(info, head)
        seg0 = head[HEADER_SIZE + SEGMENT_HEADER_SIZE:HEADER_SIZE + SEGMENT_HEADER_SIZE + 256]
        if len(seg0) == 256 and struct.unpack_from("<I", seg0, 0)[0] == APP_DESC_MAGIC:
            info.kind = "app"
            info.version = _cstr(seg0[16:48])
            info.project_name = _cstr(seg0[48:80])
            info.date_time = f"{_cstr(seg0[96:112])} {_cstr(seg0[80:96])}"
            info.idf_ver = _cstr(seg0[112:144])
            info.elf_sha256 = seg0[144:176]
        else:
            info.errors.append("no esp_app_desc_t at the first segment")
    except (ValueError, struct.error) as e:
        info.errors.append(str(e))
    return info


def verify_app_file(data: bytes, capacity: int) -> ImageInfo:
    """Rules for an input .bin: standalone app, appended SHA, no trailing bytes."""
    info = parse_image(data, capacity=max(capacity, len(data)))
    info.file_size = len(data)
    info.file_sha256 = hashlib.sha256(data).hexdigest()
    if len(data) > capacity:
        info.errors.append(f"file is {len(data):,} bytes; capacity is {capacity:,} (0x{capacity:x})")
    if not info.errors:
        if info.kind != "app":
            info.errors.append("not a standalone app image (no esp_app_desc_t; bootloader or merged bin?)")
        if not info.hash_appended:
            info.errors.append("no appended SHA-256 digest")
        if info.image_size and len(data) != info.image_size:
            info.errors.append(
                f"{len(data) - info.image_size:,} extra bytes after the appended digest "
                "(merged or signed image?)")
    _warn_flash_header(info)
    info.ok = not info.errors
    return info


def verify_bootloader_file(data: bytes, capacity: int) -> ImageInfo:
    info = parse_image(data, capacity=max(capacity, len(data)))
    info.file_size = len(data)
    info.file_sha256 = hashlib.sha256(data).hexdigest()
    if len(data) > capacity:
        info.errors.append(f"bootloader is {len(data):,} bytes; limit is {capacity:,}")
    if not info.errors:
        if info.kind != "bootloader":
            info.errors.append("not a bootloader image (no esp_bootloader_desc_t)")
        if not info.hash_appended:
            info.errors.append("bootloader has no appended SHA-256 digest")
        if info.image_size and len(data) != info.image_size:
            info.errors.append(f"{len(data) - info.image_size:,} extra bytes after the bootloader image")
    info.ok = not info.errors
    return info


def _warn_flash_header(info: ImageInfo) -> None:
    # Phase 0 verified DIO/80m/16MB headers only. Other values are not rejected.
    if info.spi_mode and info.spi_mode != "DIO":
        info.warnings.append(f"flash mode header {info.spi_mode} is not the verified DIO")
    if info.spi_speed and info.spi_speed != "80m":
        info.warnings.append(f"flash frequency header {info.spi_speed} is not the verified 80m")
    if info.spi_size and info.spi_size != "16MB":
        info.warnings.append(f"flash size header {info.spi_size} is not 16MB")


def chip_rev_supported(info: ImageInfo, chip_rev_full: int) -> bool:
    """chip_rev_full = major*100 + minor (v0.2 -> 2). max 0xFFFF means no limit."""
    if chip_rev_full < info.min_rev_full:
        return False
    return info.max_rev_full == 0xFFFF or chip_rev_full <= info.max_rev_full


def describe(info: ImageInfo) -> list[str]:
    lines = [
        f"kind: {info.kind or '?'}  chip_id: {info.chip_id}  segments: {info.segments}",
        f"flash header: {info.spi_mode}/{info.spi_speed}/{info.spi_size}",
        f"chip rev: v{info.min_rev_full // 100}.{info.min_rev_full % 100}"
        f" .. v{info.max_rev_full // 100}.{info.max_rev_full % 100}",
        f"image size: {info.image_size:,} bytes",
    ]
    if info.file_size:
        lines.append(f"file: {info.file_size:,} bytes, SHA-256 {info.file_sha256}")
    lines.append(f"appended digest: {info.appended_digest.hex() or '(none)'}"
                 f" [{'valid' if info.digest_ok else 'INVALID'}]")
    if info.kind == "app":
        lines += [f"project_name: {info.project_name!r}  version: {info.version!r}",
                  f"idf_ver: {info.idf_ver}  built: {info.date_time}",
                  f"elf_sha256: {info.elf_sha256.hex()}"]
    elif info.kind == "bootloader":
        lines.append(f"idf_ver: {info.idf_ver}  built: {info.date_time}")
    return lines
