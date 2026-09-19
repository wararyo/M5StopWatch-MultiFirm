"""Copy into your project as tools/upload_guard.py; use as a PRE extra_script.

Self-contained: it must run before PlatformIO fetches any lib_deps.
"""
from pathlib import Path

GUEST_MAX_SIZE = 0x1F0000
BLOCKED_TARGETS = frozenset(("upload", "uploadfs", "uploadfsota", "erase"))


def check_targets(targets, allow="no"):
    if allow not in ("yes", "no"):
        raise ValueError("custom_allow_full_upload must be exactly yes or no")
    blocked = sorted(BLOCKED_TARGETS.intersection(targets))
    if blocked and allow != "yes":
        raise RuntimeError(
            "MultiFirm blocks " + ", ".join(blocked) +
            ". Use multifirm.py install for a guest slot. Standalone full-device "
            "upload requires custom_allow_full_upload = yes.")
    if blocked:
        print("WARNING: standalone full upload/erase can overwrite the coexistence "
              "partition table, host, other apps and shared settings.")


def check_size(path):
    size = Path(path).stat().st_size
    if size > GUEST_MAX_SIZE:
        raise RuntimeError(
            f"MultiFirm guest image is {size} bytes; limit is {GUEST_MAX_SIZE} (0x1f0000)")


def configure(env, targets):
    check_targets(targets, env.GetProjectOption("custom_allow_full_upload", "no"))
    # Match espressif32's default before creating file nodes in this PRE script.
    if env.get("PROGNAME", "program") == "program":
        env.Replace(PROGNAME="firmware")
    image = env.subst("$BUILD_DIR/${PROGNAME}.bin")

    def verify_size(source, target, env):
        check_size(image)

    # checkprogsize precedes ElfToBin, so checking the .bin there is too early.
    env.AddPostAction(image, verify_size)
    # Use a separate alias: the platform replaces actions on buildprog/upload.
    # AlwaysBuild covers a cached image that SCons does not regenerate.
    check = env.Alias("multifirm_check_size", image, verify_size)
    env.AlwaysBuild(check)
    env.Depends(env.Alias("buildprog"), check)
    env.Depends(env.Alias("upload"), check)


if "Import" in globals():
    Import("env")
    from SCons.Script import COMMAND_LINE_TARGETS
    configure(env, COMMAND_LINE_TARGETS)
elif __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Check the MultiFirm guest binary size")
    parser.add_argument("image")
    check_size(parser.parse_args().image)
