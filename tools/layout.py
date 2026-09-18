"""MultiFirm v1 fixed layout, metadata record and naming rules.

Keep in sync with src/multifirm_layout.h (tests/test_multifirm.py checks the constants).
"""
from __future__ import annotations

import hashlib
import struct
import unicodedata
import zlib
from dataclasses import dataclass

FLASH_SIZE = 0x1000000
BOOTLOADER_OFFSET = 0x0
BOOTLOADER_MAX_SIZE = 0x8000
PARTITION_TABLE_OFFSET = 0x8000
PARTITION_TABLE_SIZE = 0xC00  # compared byte-for-byte, including 0xFF padding
SECTOR_SIZE = 0x1000

TYPE_APP = 0x00
TYPE_DATA = 0x01
SUBTYPE_OTA_DATA = 0x00
SUBTYPE_PHY = 0x01
SUBTYPE_NVS = 0x02
SUBTYPE_COREDUMP = 0x03
SUBTYPE_FAT = 0x81
SUBTYPE_MULTIFIRM_META = 0x40
SUBTYPE_OTA_0 = 0x10


@dataclass(frozen=True)
class Partition:
    label: str
    type: int
    subtype: int
    offset: int
    size: int
    flags: int = 0

    @property
    def end(self) -> int:
        return self.offset + self.size


# Order matters: Arduino's initArduino() erases the FIRST data/nvs partition, so
# `nvs` must stay before `multifirm_nvs` (docs/phase0-results.md 5.3).
PARTITIONS: tuple[Partition, ...] = (
    Partition("nvs", TYPE_DATA, SUBTYPE_NVS, 0x9000, 0x10000),
    Partition("otadata", TYPE_DATA, SUBTYPE_OTA_DATA, 0x19000, 0x2000),
    Partition("phy_init", TYPE_DATA, SUBTYPE_PHY, 0x1B000, 0x1000),
    Partition("multifirm_meta", TYPE_DATA, SUBTYPE_MULTIFIRM_META, 0x1C000, 0x4000),
    Partition("ota_0", TYPE_APP, 0x10, 0x20000, 0x400000),
    Partition("ota_1", TYPE_APP, 0x11, 0x420000, 0x1F0000),
    Partition("ota_2", TYPE_APP, 0x12, 0x610000, 0x1F0000),
    Partition("ota_3", TYPE_APP, 0x13, 0x800000, 0x1F0000),
    Partition("multifirm_nvs", TYPE_DATA, SUBTYPE_NVS, 0x9F0000, 0x10000),
    Partition("storage", TYPE_DATA, SUBTYPE_FAT, 0xA00000, 0x400000),
    Partition("coredump", TYPE_DATA, SUBTYPE_COREDUMP, 0xE00000, 0x10000),
)
BY_LABEL = {p.label: p for p in PARTITIONS}

HOST = BY_LABEL["ota_0"]
HOST_MAX_SIZE = HOST.size
GUEST_SLOTS = (1, 2, 3)
GUEST_MAX_SIZE = 0x1F0000
NVS = BY_LABEL["nvs"]
OTADATA = BY_LABEL["otadata"]
PHY_INIT = BY_LABEL["phy_init"]
META = BY_LABEL["multifirm_meta"]
MULTIFIRM_NVS = BY_LABEL["multifirm_nvs"]
STORAGE = BY_LABEL["storage"]
COREDUMP = BY_LABEL["coredump"]


def guest_partition(slot: int) -> Partition:
    if slot not in GUEST_SLOTS:
        raise ValueError(f"slot must be 1, 2 or 3 (got {slot})")
    return BY_LABEL[f"ota_{slot}"]


def meta_sector_offset(slot: int) -> int:
    guest_partition(slot)
    return META.offset + (slot - 1) * SECTOR_SIZE


# The last metadata sector (0x1f000) is reserved and never written.
META_RESERVED_OFFSET = META.offset + 3 * SECTOR_SIZE


# ---------------------------------------------------------------- partition table

ENTRY_MAGIC = b"\xAA\x50"
MD5_MAGIC = b"\xEB\xEB"


def encode_partition_table(parts: tuple[Partition, ...] | list[Partition]) -> bytes:
    """Same bytes as ESP-IDF gen_esp32part.py (MD5 entry on, padded to 0xC00)."""
    out = bytearray()
    for p in parts:
        label = p.label.encode()
        if len(label) > 16:
            raise ValueError(f"label too long: {p.label}")
        out += ENTRY_MAGIC + struct.pack("<BBII", p.type, p.subtype, p.offset, p.size)
        out += label.ljust(16, b"\0") + struct.pack("<I", p.flags)
    out += MD5_MAGIC + b"\xFF" * 14 + hashlib.md5(out).digest()
    if len(out) > PARTITION_TABLE_SIZE:
        raise ValueError("partition table too large")
    return bytes(out + b"\xFF" * (PARTITION_TABLE_SIZE - len(out)))


def decode_partition_table(data: bytes) -> list[Partition]:
    """Parse a table; raises ValueError on bad magic or MD5."""
    parts: list[Partition] = []
    for off in range(0, min(len(data), PARTITION_TABLE_SIZE), 32):
        e = data[off:off + 32]
        if e[:2] == MD5_MAGIC:
            if e[16:32] != hashlib.md5(data[:off]).digest():
                raise ValueError("partition table MD5 mismatch")
            return parts
        if e[:2] == b"\xFF\xFF":
            raise ValueError("partition table has no MD5 entry")
        if e[:2] != ENTRY_MAGIC:
            raise ValueError(f"bad partition entry at +0x{off:x}")
        t, st, o, s = struct.unpack_from("<BBII", e, 2)
        label = e[12:28].split(b"\0", 1)[0].decode("utf-8", "replace")
        parts.append(Partition(label, t, st, o, s, struct.unpack_from("<I", e, 28)[0]))
    raise ValueError("partition table not terminated")


PARTITION_TABLE = encode_partition_table(PARTITIONS)

# Known layouts that change commands must refuse (only `initial` may replace them).
LEGACY_3GUEST = encode_partition_table((
    Partition("nvs", TYPE_DATA, SUBTYPE_NVS, 0x9000, 0x4000),
    Partition("otadata", TYPE_DATA, SUBTYPE_OTA_DATA, 0xD000, 0x2000),
    Partition("phy_init", TYPE_DATA, SUBTYPE_PHY, 0xF000, 0x1000),
    Partition("ota_0", TYPE_APP, 0x10, 0x20000, 0x4F0000),
    Partition("ota_1", TYPE_APP, 0x11, 0x510000, 0x190000),
    Partition("ota_2", TYPE_APP, 0x12, 0x6A0000, 0x190000),
    Partition("ota_3", TYPE_APP, 0x13, 0x830000, 0x190000),
    Partition("storage", TYPE_DATA, SUBTYPE_FAT, 0xA00000, 0x400000),
    Partition("coredump", TYPE_DATA, SUBTYPE_COREDUMP, 0xE00000, 0x10000),
))
LEGACY_2APP = encode_partition_table((
    Partition("nvs", TYPE_DATA, SUBTYPE_NVS, 0x9000, 0x4000),
    Partition("otadata", TYPE_DATA, SUBTYPE_OTA_DATA, 0xD000, 0x2000),
    Partition("phy_init", TYPE_DATA, SUBTYPE_PHY, 0xF000, 0x1000),
    Partition("ota_0", TYPE_APP, 0x10, 0x20000, 0x4F0000),
    Partition("ota_1", TYPE_APP, 0x11, 0x510000, 0x4F0000),
    Partition("storage", TYPE_DATA, SUBTYPE_FAT, 0xA00000, 0x400000),
    Partition("coredump", TYPE_DATA, SUBTYPE_COREDUMP, 0xE00000, 0x10000),
))


def classify_table(data: bytes) -> str:
    """'multifirm-v1', 'legacy-3guest', 'legacy-2app', 'other' or 'invalid'."""
    data = bytes(data[:PARTITION_TABLE_SIZE])
    if data == PARTITION_TABLE:
        return "multifirm-v1"
    if data == LEGACY_3GUEST:
        return "legacy-3guest"
    if data == LEGACY_2APP:
        return "legacy-2app"
    try:
        decode_partition_table(data)
    except ValueError:
        return "invalid"
    return "other"


def parse_csv(text: str) -> list[Partition]:
    """Minimal parser for the fully specified CSV in docs/ (explicit offsets only)."""
    parts = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, typ, sub, off, size, *flags = [c.strip() for c in line.split(",")]
        t = {"app": TYPE_APP, "data": TYPE_DATA}[typ]
        subtypes = {"ota": SUBTYPE_OTA_DATA, "phy": SUBTYPE_PHY, "nvs": SUBTYPE_NVS,
                    "coredump": SUBTYPE_COREDUMP, "fat": SUBTYPE_FAT}
        if sub.startswith("ota_"):
            st = 0x10 + int(sub[4:])
        elif sub in subtypes:
            st = subtypes[sub]
        else:
            st = int(sub, 0)
        parts.append(Partition(name, t, st, int(off, 0), int(size, 0)))
    return parts


# ---------------------------------------------------------------- metadata record

META_MAGIC = 0x4D52464D  # bytes "MFRM"
META_VERSION = 1
META_LENGTH = 0x74
META_RECORD_SIZE = 0x78
NAME_FIELD_SIZE = 32
NAME_MAX_BYTES = 31
_META_FMT = "<IHH32sI32s32sQ"
assert struct.calcsize(_META_FMT) == META_LENGTH


def crc32(data: bytes) -> int:
    """CRC-32/ISO-HDLC (zlib.crc32)."""
    return zlib.crc32(data) & 0xFFFFFFFF


def validate_name(name: str) -> str | None:
    """Return None when valid, else a reason."""
    raw = name.encode("utf-8")
    if not raw:
        return "empty"
    if len(raw) > NAME_MAX_BYTES:
        return f"longer than {NAME_MAX_BYTES} bytes ({len(raw)})"
    for ch in name:
        if ch == "\0":
            return "contains NUL"
        if unicodedata.category(ch) == "Cc":
            return f"contains control character U+{ord(ch):04X}"
    return None


# Stable reason codes shared with src/multifirm_layout.h (metaErrorName).
META_OK = "ok"
META_ERRORS = ("short_read", "empty", "bad_magic", "unknown_version", "bad_length",
               "crc_mismatch", "name_not_terminated", "name_padding_not_zero",
               "name_invalid_utf8", "name_empty", "name_control_char", "image_size_out_of_range")


def decode_name_field(field: bytes) -> tuple[str | None, str | None]:
    """Decode a 32-byte NUL-terminated name field: (name, error code)."""
    nul = field.find(b"\0")
    if nul < 0:
        return None, "name_not_terminated"
    if any(field[nul:]):
        return None, "name_padding_not_zero"
    try:
        name = field[:nul].decode("utf-8", "strict")
    except UnicodeDecodeError:
        return None, "name_invalid_utf8"
    if not name:
        return None, "name_empty"
    if any(unicodedata.category(ch) == "Cc" for ch in name):
        return None, "name_control_char"
    return name, None


@dataclass(frozen=True)
class MetaRecord:
    name: str
    image_size: int
    elf_sha256: bytes
    app_digest: bytes
    installed_at: int


def encode_meta(rec: MetaRecord) -> bytes:
    reason = validate_name(rec.name)
    if reason:
        raise ValueError(f"invalid display name: {reason}")
    if len(rec.elf_sha256) != 32 or len(rec.app_digest) != 32:
        raise ValueError("digests must be 32 bytes")
    return encode_meta_raw(rec.name.encode("utf-8").ljust(NAME_FIELD_SIZE, b"\0"), rec.image_size,
                           rec.elf_sha256, rec.app_digest, rec.installed_at)


def encode_meta_raw(name_field: bytes, image_size: int, elf: bytes, digest: bytes, installed_at: int,
                    magic: int = META_MAGIC, version: int = META_VERSION, length: int = META_LENGTH,
                    crc: int | None = None) -> bytes:
    """No validation; tests use it to build malformed records."""
    body = struct.pack(_META_FMT, magic, version, length, name_field, image_size, elf, digest, installed_at)
    return body + struct.pack("<I", crc32(body) if crc is None else crc)


def meta_sector(rec: MetaRecord) -> bytes:
    record = encode_meta(rec)
    return record + b"\xFF" * (SECTOR_SIZE - len(record))


def decode_meta(data: bytes, capacity: int = GUEST_MAX_SIZE) -> tuple[MetaRecord | None, str | None]:
    """Structural check (steps 1-2 of the shared rule): (record, error code)."""
    if len(data) < META_RECORD_SIZE:
        return None, "short_read"
    if data[:META_RECORD_SIZE] == b"\xFF" * META_RECORD_SIZE:
        return None, "empty"
    magic, version, length = struct.unpack_from("<IHH", data, 0)
    if magic != META_MAGIC:
        return None, "bad_magic"
    if version != META_VERSION:
        return None, "unknown_version"
    if length != META_LENGTH:
        return None, "bad_length"
    (crc,) = struct.unpack_from("<I", data, META_LENGTH)
    if crc32(data[:META_LENGTH]) != crc:
        return None, "crc_mismatch"
    _, _, _, name_field, image_size, elf, digest, installed = struct.unpack_from(_META_FMT, data, 0)
    name, error = decode_name_field(name_field)
    if error:
        return None, error
    if image_size == 0 or image_size > capacity:
        return None, "image_size_out_of_range"
    return MetaRecord(name, image_size, elf, digest, installed), None


# ---------------------------------------------------------------- display names

DEFAULT_PROJECT_NAMES = frozenset({"firmware", "arduino-lib-builder", "app-template"})


def usable_project_name(project_name: str | None) -> str | None:
    if not project_name or project_name in DEFAULT_PROJECT_NAMES:
        return None
    return None if validate_name(project_name) else project_name


def fallback_name(slot: int) -> str:
    return f"App{slot}"
