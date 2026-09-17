"""Analyze a full flash dump: partition table, bootloader, app slots, otadata, NVS.

usage: python analyze_dump.py <full.bin> [--json out.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import zlib

from espimage import CHIP_IDS, parse_image, parse_partition_table

TYPE_NAMES = {0: "app", 1: "data"}


def otadata_entries(buf: bytes) -> list[dict]:
    res = []
    for i in range(2):
        sec = buf[i * 0x1000:i * 0x1000 + 32]
        seq, label, state, crc = struct.unpack_from("<I20sII", sec, 0)
        blank = sec == b"\xff" * 32
        res.append({
            "sector": i,
            "blank": blank,
            "ota_seq": seq,
            "ota_state": state,
            "crc": crc,
            "crc_ok": zlib.crc32(struct.pack("<I", seq), 0xFFFFFFFF) == crc,
        })
    return res


def nvs_summary(buf: bytes) -> dict:
    pages = []
    for p in range(len(buf) // 0x1000):
        page = buf[p * 0x1000:(p + 1) * 0x1000]
        state, seq, ver = struct.unpack_from("<IIB", page, 0)
        pages.append({"page": p, "state": f"0x{state:08x}", "seq": seq,
                      "version": ver, "blank": page == b"\xff" * 0x1000})
    return {"pages": pages}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--json")
    a = ap.parse_args()
    data = open(a.dump, "rb").read()
    out: dict = {
        "file": a.dump,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }

    bl = parse_image(data, 0x8000)
    out["bootloader"] = {
        "ok": bl.ok, "errors": bl.errors, "image_size": bl.image_size,
        "sha256_of_image": hashlib.sha256(data[:bl.image_size]).hexdigest() if bl.image_size else None,
        "spi_mode": bl.spi_mode, "spi_speed": bl.spi_speed, "spi_size": bl.spi_size,
        "chip": CHIP_IDS.get(bl.chip_id, bl.chip_id), "hash_appended": bl.hash_appended,
        "desc": bl.bootloader_desc,
        "segments": [(hex(s.load_addr), s.length) for s in bl.segments],
    }

    pt_raw = data[0x8000:0x8000 + 0xC00]
    entries, md5 = parse_partition_table(pt_raw)
    out["partition_table"] = {
        "md5": md5,
        "sha256_0xC00": hashlib.sha256(pt_raw).hexdigest(),
        "entries": [
            {"label": e.label, "type": TYPE_NAMES.get(e.type, e.type), "subtype": f"0x{e.subtype:02x}",
             "offset": f"0x{e.offset:x}", "size": f"0x{e.size:x}", "flags": e.flags}
            for e in entries
        ],
    }

    slots = []
    for e in entries:
        region = data[e.offset:e.offset + e.size]
        item = {"label": e.label, "offset": f"0x{e.offset:x}", "size": e.size,
                "region_sha256": hashlib.sha256(region).hexdigest(),
                "blank": region.count(0xFF) == len(region)}
        if e.type == 0:
            head_blank = region[:0x1000] == b"\xff" * 0x1000
            img = parse_image(region, e.size)
            item.update({
                "first_sector_blank": head_blank,
                "image_ok": img.ok, "errors": img.errors,
                "image_size": img.image_size,
                "image_sha256_file": hashlib.sha256(region[:img.image_size]).hexdigest() if img.image_size else None,
                "hash_appended": img.hash_appended,
                "appended_digest": img.appended_digest.hex(),
                "computed_digest": img.computed_digest.hex(),
                "spi_mode": img.spi_mode, "spi_speed": img.spi_speed, "spi_size": img.spi_size,
                "chip": CHIP_IDS.get(img.chip_id, img.chip_id),
                "min_rev_full": img.min_rev_full, "max_rev_full": img.max_rev_full,
                "segments": len(img.segments),
                "app_desc": img.app_desc,
                "trailing_nonblank_after_image": (
                    region[img.image_size:].count(0xFF) != len(region) - img.image_size
                    if img.image_size else None),
            })
        elif e.type == 1 and e.subtype == 0x00:
            item["otadata"] = otadata_entries(region)
        elif e.type == 1 and e.subtype == 0x02:
            item["nvs"] = nvs_summary(region)
        slots.append(item)
    out["partitions"] = slots

    text = json.dumps(out, indent=2, ensure_ascii=False)
    if a.json:
        open(a.json, "w", encoding="utf-8").write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
