"""Generate native verification inputs using the existing PC image fixtures."""
from pathlib import Path
import sys
from test_multifirm import make_image
from layout import PARTITION_TABLE, LEGACY_2APP, LEGACY_3GUEST, Partition, encode_partition_table

if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    (out / "table.bin").write_bytes(PARTITION_TABLE)
    (out / "app.bin").write_bytes(make_image())
    (out / "legacy2.bin").write_bytes(LEGACY_2APP)
    (out / "legacy3.bin").write_bytes(LEGACY_3GUEST)
    # Standalone OTA layout with an ota_0: its presence alone must not mean host.
    (out / "standalone.bin").write_bytes(encode_partition_table((
        Partition("nvs", 1, 2, 0x9000, 0x5000),
        Partition("otadata", 1, 0, 0xE000, 0x2000),
        Partition("ota_0", 0, 0x10, 0x10000, 0x650000),
        Partition("ota_1", 0, 0x11, 0x660000, 0x650000),
    )))
