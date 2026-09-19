import importlib.util
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import layout

spec = importlib.util.spec_from_file_location("upload_guard", ROOT / "examples/upload_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GuestSupportTest(unittest.TestCase):
    def test_canonical_table(self):
        text = (ROOT / "src/detail/partition_table_bytes.h").read_text()
        body = text.split("kPartitionTablePrefix[] = {")[1].split("};")[0]
        prefix = bytes(int(x, 16) for x in re.findall(r"0x([0-9A-F]{2})", body))
        self.assertEqual(prefix.ljust(layout.PARTITION_TABLE_SIZE, b"\xff"), layout.PARTITION_TABLE)

    def test_targets(self):
        guard.check_targets(["build"])
        for target in guard.BLOCKED_TARGETS:
            with self.subTest(target=target):
                with self.assertRaises(RuntimeError):
                    guard.check_targets(["build", target])
                guard.check_targets(["build", target], "yes")
        with self.assertRaises(RuntimeError):
            guard.check_targets(["upload", "erase"])
        for invalid in ("", "true", "YES", "1", " yes "):
            with self.assertRaises(ValueError):
                guard.check_targets([], invalid)

    def test_size(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "firmware.bin"
            for size in (guard.GUEST_MAX_SIZE - 1, guard.GUEST_MAX_SIZE):
                image.write_bytes(b"\xff" * size)
                guard.check_size(image)
            image.write_bytes(b"\xff" * (guard.GUEST_MAX_SIZE + 1))
            with self.assertRaises(RuntimeError):
                guard.check_size(image)

    def test_hooks(self):
        class Env:
            def __init__(self): self.actions = []
            def GetProjectOption(self, key, default): return default
            def get(self, key, default): return default
            def Replace(self, **values): self.actions.append(("replace", values))
            def subst(self, value): return "build/firmware.bin"
            def Alias(self, name, *args): return name
            def AlwaysBuild(self, name): self.actions.append(("always", name))
            def Depends(self, name, dependency): self.actions.append(("depends", name, dependency))
            def AddPostAction(self, name, callback): self.actions.append(("post", name))
            def AddPreAction(self, name, callback): self.actions.append(("pre", name))
        env = Env()
        guard.configure(env, [])
        self.assertEqual(env.actions, [("replace", {"PROGNAME": "firmware"}),
            ("post", "build/firmware.bin"), ("always", "multifirm_check_size"),
            ("depends", "buildprog", "multifirm_check_size"),
            ("depends", "upload", "multifirm_check_size")])

    def test_sample_guard_copies(self):
        for name in ("guest-arduino", "guest-espidf"):
            self.assertEqual((ROOT / "examples" / name / "tools/upload_guard.py").read_bytes(),
                             (ROOT / "examples/upload_guard.py").read_bytes())
