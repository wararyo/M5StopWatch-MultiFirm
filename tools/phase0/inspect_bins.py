"""Summarize standalone app images (offline). usage: python inspect_bins.py a.bin [b.bin ...]"""
from __future__ import annotations

import hashlib
import json
import os
import sys

from espimage import CHIP_IDS, parse_image

GUEST_LIMIT = 0x1F0000
HOST_LIMIT = 0x400000


def summarize(path: str) -> dict:
    data = open(path, "rb").read()
    img = parse_image(data)
    d = img.app_desc or {}
    return {
        "path": path,
        "file_size": len(data),
        "file_sha256": hashlib.sha256(data).hexdigest(),
        "ok": img.ok,
        "errors": img.errors,
        "image_size": img.image_size,
        "extra_bytes_after_digest": len(data) - img.image_size if img.image_size else None,
        "chip": CHIP_IDS.get(img.chip_id, img.chip_id),
        "flash": f"{img.spi_mode}/{img.spi_speed}/{img.spi_size}",
        "max_rev_full": img.max_rev_full,
        "hash_appended": img.hash_appended,
        "appended_digest": img.appended_digest.hex(),
        "digest_match": img.appended_digest == img.computed_digest and img.hash_appended,
        "project_name": d.get("project_name"),
        "version": d.get("version"),
        "idf_ver": d.get("idf_ver"),
        "app_elf_sha256": d.get("app_elf_sha256"),
        "mmu_page_size": d.get("mmu_page_size"),
        "fits_guest": len(data) <= GUEST_LIMIT,
        "fits_host": len(data) <= HOST_LIMIT,
        "mtime": os.path.getmtime(path),
    }


if __name__ == "__main__":
    print(json.dumps([summarize(p) for p in sys.argv[1:]], indent=2, ensure_ascii=False))
