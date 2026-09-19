"""Generate native verification inputs using the existing PC image fixtures."""
from pathlib import Path
import sys
from test_multifirm import app_desc, make_image
from layout import (PARTITION_TABLE, LEGACY_2APP, LEGACY_3GUEST, MetaRecord,
                    Partition, encode_meta, encode_partition_table)

if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    (out / "table.bin").write_bytes(PARTITION_TABLE)
    app = make_image()
    (out / "app.bin").write_bytes(app)
    meta = encode_meta(MetaRecord("Installed Name", len(app), b"\x11" * 32, app[-32:], 1790000000))
    (out / "meta.bin").write_bytes(meta)
    (out / "meta-bad-crc.bin").write_bytes(meta[:-1] + bytes([meta[-1] ^ 1]))
    (out / "meta-size-mismatch.bin").write_bytes(encode_meta(
        MetaRecord("Old Name", len(app) + 16, b"\x11" * 32, app[-32:], 1790000000)))
    (out / "meta-digest-mismatch.bin").write_bytes(encode_meta(
        MetaRecord("Old Name", len(app), b"\x11" * 32, b"\x22" * 32, 1790000000)))
    (out / "meta-elf-mismatch.bin").write_bytes(encode_meta(
        MetaRecord("Old Name", len(app), b"\x22" * 32, app[-32:], 1790000000)))
    (out / "meta-zero-elf.bin").write_bytes(encode_meta(
        MetaRecord("Zero ELF", len(app), b"\0" * 32, app[-32:], 1790000000)))
    (out / "default-app.bin").write_bytes(make_image(desc=app_desc("firmware")))
    invalid_desc = bytearray(app_desc("Project"))
    invalid_desc[48:80] = b"\xff\0" + b"\0" * 30
    (out / "invalid-project-app.bin").write_bytes(make_image(desc=bytes(invalid_desc)))
    (out / "legacy2.bin").write_bytes(LEGACY_2APP)
    (out / "legacy3.bin").write_bytes(LEGACY_3GUEST)
    # Standalone OTA layout with an ota_0: its presence alone must not mean host.
    (out / "standalone.bin").write_bytes(encode_partition_table((
        Partition("nvs", 1, 2, 0x9000, 0x5000),
        Partition("otadata", 1, 0, 0xE000, 0x2000),
        Partition("ota_0", 0, 0x10, 0x10000, 0x650000),
        Partition("ota_1", 0, 0x11, 0x660000, 0x650000),
    )))
