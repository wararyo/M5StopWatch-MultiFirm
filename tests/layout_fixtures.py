"""Generate metadata fixtures shared by the Python and C++ tests.

usage: python tests/layout_fixtures.py   (rewrites tests/fixtures/meta/)
tests/test_multifirm.py fails when the committed files are out of date.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import layout as L  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "meta"
CAP = L.GUEST_MAX_SIZE
ELF = bytes(range(32))
DIGEST = bytes(range(0xA0, 0xC0))
INSTALLED = 1_790_000_000


def _name(raw: bytes) -> bytes:
    return raw.ljust(L.NAME_FIELD_SIZE, b"\0")


def _sector(record: bytes) -> bytes:
    return record + b"\xFF" * (L.SECTOR_SIZE - len(record))


def cases() -> list[tuple[str, bytes, str]]:
    def rec(name: bytes = b"MyApp", size: int = 826_784, elf: bytes = ELF, **kw) -> bytes:
        return L.encode_meta_raw(_name(name) if len(name) < 32 else name, size, elf, DIGEST, INSTALLED, **kw)

    good = rec()
    bad_crc = good[:-4] + bytes([good[-4] ^ 1]) + good[-3:]
    return [
        ("ok_ascii", _sector(good), "ok"),
        ("ok_utf8_31bytes", _sector(rec("かんたんプレイ".encode() + b"ABCDEFGHIJ")), "ok"),
        ("ok_zero_elf", _sector(rec(elf=bytes(32))), "ok"),
        ("ok_size_equals_capacity", _sector(rec(size=CAP)), "ok"),
        ("short_read", good[:L.META_RECORD_SIZE - 1], "short_read"),
        ("empty", b"\xFF" * L.SECTOR_SIZE, "empty"),
        ("bad_magic", _sector(rec(magic=0x4D52464E)), "bad_magic"),
        ("unknown_version", _sector(rec(version=2)), "unknown_version"),
        ("bad_length", _sector(rec(length=0x78)), "bad_length"),
        ("crc_mismatch", _sector(bad_crc), "crc_mismatch"),
        ("name_not_terminated", _sector(rec(b"A" * 32)), "name_not_terminated"),
        ("name_padding_not_zero", _sector(rec(b"AB\0C" + b"\0" * 28)), "name_padding_not_zero"),
        ("name_invalid_byte", _sector(rec(b"\xFF\xFE")), "name_invalid_utf8"),
        ("name_overlong", _sector(rec(b"\xC0\xAF")), "name_invalid_utf8"),
        ("name_surrogate", _sector(rec(b"\xED\xA0\x80")), "name_invalid_utf8"),
        ("name_truncated", _sector(rec(b"A\xE3\x81")), "name_invalid_utf8"),
        ("name_too_high", _sector(rec(b"\xF4\x90\x80\x80")), "name_invalid_utf8"),
        ("name_empty", _sector(rec(b"")), "name_empty"),
        ("name_c0_control", _sector(rec(b"A\x07")), "name_control_char"),
        ("name_c1_control", _sector(rec(b"A\xC2\x85")), "name_control_char"),
        ("image_size_zero", _sector(rec(size=0)), "image_size_out_of_range"),
        ("image_size_over", _sector(rec(size=CAP + 1)), "image_size_out_of_range"),
    ]


def render() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    lines = ["# file capacity expected name_hex image_size elf_sha256 app_digest installed_at"]
    for name, data, expected in cases():
        files[f"{name}.bin"] = data
        rec, error = L.decode_meta(data, CAP)
        actual = error or "ok"
        if actual != expected:
            raise AssertionError(f"{name}: Python decodes {actual}, fixture expects {expected}")
        line = f"{name}.bin 0x{CAP:x} {expected}"
        if rec:
            line += (f" {rec.name.encode().hex()} {rec.image_size} {rec.elf_sha256.hex()}"
                     f" {rec.app_digest.hex()} {rec.installed_at}")
        lines.append(line)
    files["cases.txt"] = ("\n".join(lines) + "\n").encode()
    return files


def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for old in FIXTURE_DIR.iterdir():
        old.unlink()
    for name, data in render().items():
        (FIXTURE_DIR / name).write_bytes(data)
    print(f"wrote {FIXTURE_DIR}")


if __name__ == "__main__":
    main()
