"""Device access through the esptool CLI.

Each operation is a separate esptool run with `--before default_reset --after no_reset`,
so the chip stays in the ROM download mode between steps and the application never
runs in the middle of a sequence. Only `reset()` returns to the application
(hard reset). The same pattern was used by the previous coexistence tools on this
hardware (USB-Serial/JTAG).
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

REQUIRED_ESPTOOL = "4.12.0"
KEEP_FLASH_PARAMS = ["--flash_mode", "keep", "--flash_freq", "keep", "--flash_size", "keep"]


class DeviceError(Exception):
    """Communication or tool failure (distinct from a verification mismatch)."""


@dataclass
class DeviceInfo:
    chip: str
    mac: str
    chip_rev_full: int
    flash_size: str
    esptool_version: str


class Device(Protocol):
    def connect(self) -> DeviceInfo: ...
    def read(self, offset: int, size: int) -> bytes: ...
    def verify(self, offset: int, data: bytes) -> bool: ...
    def write(self, offset: int, data: bytes) -> None: ...
    def erase(self, offset: int, size: int) -> None: ...
    def reset(self) -> None: ...


def esptool_version() -> str | None:
    try:
        import esptool  # noqa: PLC0415
    except ImportError:
        return None
    return getattr(esptool, "__version__", None)


def check_esptool(allow_other: bool) -> str:
    version = esptool_version()
    if version is None:
        raise DeviceError(
            f"esptool is not installed for {sys.executable}; "
            f"install tools/requirements.txt (esptool=={REQUIRED_ESPTOOL})")
    if version != REQUIRED_ESPTOOL and not allow_other:
        raise DeviceError(
            f"esptool {version} is not the verified {REQUIRED_ESPTOOL}; "
            "install tools/requirements.txt or pass --allow-untested-esptool")
    return version


def _align_ok(value: int) -> bool:
    return value % 0x1000 == 0


Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _default_runner(cmd: list[str]) -> "subprocess.CompletedProcess[str]":
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)


class EsptoolDevice:
    def __init__(self, port: str, baud: int, log: Callable[[str], None],
                 runner: Runner = _default_runner, python: str = sys.executable,
                 version: str = REQUIRED_ESPTOOL):
        self.port = port
        self.baud = baud
        self.log = log
        self.runner = runner
        self.python = python
        self.version = version
        self._tmp = tempfile.TemporaryDirectory(prefix="multifirm-")
        self._n = 0

    def _file(self, data: bytes | None = None) -> Path:
        self._n += 1
        path = Path(self._tmp.name) / f"io{self._n}.bin"
        if data is not None:
            path.write_bytes(data)
        return path

    def _run(self, args: list[str], after: str = "no_reset") -> tuple[int, str]:
        cmd = [self.python, "-m", "esptool", "--chip", "esp32s3", "--port", self.port,
               "--baud", str(self.baud), "--before", "default_reset", "--after", after, *args]
        self.log("$ esptool " + " ".join(cmd[3:]))
        proc = self.runner(cmd)
        out = (proc.stdout or "") + (proc.stderr or "")
        # Drop progress lines; keep the rest for the log.
        kept = [ln for ln in out.replace("\r", "\n").splitlines()
                if ln.strip() and not re.match(r"^(Writing|Reading) at 0x", ln.strip())
                and "%)" not in ln]
        for ln in kept:
            self.log("  | " + ln)
        self.log(f"  exit {proc.returncode}")
        return proc.returncode, out

    def connect(self) -> DeviceInfo:
        code, out = self._run(["flash_id"])
        if code:
            raise DeviceError("could not connect (esptool flash_id failed)")
        chip = re.search(r"Chip is (.+)", out)
        mac = re.search(r"MAC: ([0-9a-fA-F:]{17})", out)
        rev = re.search(r"revision v(\d+)\.(\d+)", out)
        size = re.search(r"Detected flash size: (\S+)", out)
        if not (chip and mac and rev):
            raise DeviceError("could not parse chip information from esptool output")
        if "ESP32-S3" not in chip.group(1):
            raise DeviceError(f"not an ESP32-S3: {chip.group(1).strip()}")
        return DeviceInfo(chip.group(1).strip(), mac.group(1).lower(),
                          int(rev.group(1)) * 100 + int(rev.group(2)),
                          size.group(1) if size else "?", self.version)

    def read(self, offset: int, size: int) -> bytes:
        path = self._file()
        code, _ = self._run(["read_flash", hex(offset), hex(size), str(path)])
        if code or not path.exists():
            raise DeviceError(f"read 0x{offset:x}+0x{size:x} failed")
        data = path.read_bytes()
        if len(data) != size:
            raise DeviceError(f"read 0x{offset:x}: got {len(data)} bytes, expected {size}")
        return data

    def verify(self, offset: int, data: bytes) -> bool:
        path = self._file(data)
        code, out = self._run(["verify_flash", *KEEP_FLASH_PARAMS, hex(offset), str(path)])
        if code == 0 and "verify OK" in out:
            return True
        if "verify FAILED" in out:
            return False
        raise DeviceError(f"verify 0x{offset:x}+0x{len(data):x} failed to run")

    def write(self, offset: int, data: bytes) -> None:
        if not _align_ok(offset):
            raise DeviceError(f"write offset 0x{offset:x} is not sector aligned")
        path = self._file(data)
        code, out = self._run(["write_flash", *KEEP_FLASH_PARAMS, hex(offset), str(path)])
        if code or "Hash of data verified" not in out:
            raise DeviceError(f"write 0x{offset:x}+0x{len(data):x} failed")

    def erase(self, offset: int, size: int) -> None:
        if not (_align_ok(offset) and _align_ok(size)):
            raise DeviceError(f"erase 0x{offset:x}+0x{size:x} is not sector aligned")
        code, _ = self._run(["erase_region", hex(offset), hex(size)])
        if code:
            raise DeviceError(f"erase 0x{offset:x}+0x{size:x} failed")

    def reset(self) -> None:
        code, _ = self._run(["read_mac"], after="hard_reset")
        if code:
            raise DeviceError("hard reset failed")

    def close(self) -> None:
        self._tmp.cleanup()


class MemoryDevice:
    """In-memory flash for tests. Failure hooks simulate faults."""

    def __init__(self, image: bytes | None = None, mac: str = "28:84:85:43:a7:c0",
                 chip_rev_full: int = 2):
        self.flash = bytearray(image if image is not None else b"\xFF" * 0x1000000)
        self.info = DeviceInfo("ESP32-S3 (QFN56) (revision v0.2)", mac, chip_rev_full, "16MB", "memory")
        self.ops: list[tuple] = []
        self.connected = False
        self.was_reset = False
        self.fail_on: Callable[[str, int, int], bool] = lambda op, off, size: False
        self.corrupt_write: Callable[[int, bytes], bytes] = lambda off, data: data

    def _check(self, op: str, offset: int, size: int) -> None:
        if offset < 0 or offset + size > len(self.flash):
            raise DeviceError(f"{op} out of range")
        if self.fail_on(op, offset, size):
            raise DeviceError(f"simulated {op} failure at 0x{offset:x}")

    def connect(self) -> DeviceInfo:
        self._check("connect", 0, 0)
        self.connected = True
        self.ops.append(("connect",))
        return self.info

    def read(self, offset: int, size: int) -> bytes:
        self._check("read", offset, size)
        self.ops.append(("read", offset, size))
        return bytes(self.flash[offset:offset + size])

    def verify(self, offset: int, data: bytes) -> bool:
        self._check("verify", offset, len(data))
        self.ops.append(("verify", offset, len(data)))
        return hashlib.md5(self.flash[offset:offset + len(data)]).digest() == hashlib.md5(data).digest()

    def write(self, offset: int, data: bytes) -> None:
        self._check("write", offset, len(data))
        self.ops.append(("write", offset, len(data)))
        # Like esptool write_flash: erase the covered sectors, then program.
        end = offset + len(data)
        end += -end % 0x1000
        self.flash[offset:end] = b"\xFF" * (end - offset)
        stored = self.corrupt_write(offset, data)
        self.flash[offset:offset + len(stored)] = stored

    def erase(self, offset: int, size: int) -> None:
        if not (_align_ok(offset) and _align_ok(size)):
            raise DeviceError("erase not aligned")
        self._check("erase", offset, size)
        self.ops.append(("erase", offset, size))
        self.flash[offset:offset + size] = b"\xFF" * size

    def reset(self) -> None:
        self.ops.append(("reset",))
        self.was_reset = True

    def mutating_ops(self) -> list[tuple]:
        return [op for op in self.ops if op[0] in ("write", "erase")]
