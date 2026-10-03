#!/bin/sh
# MultiFirm tool launcher for macOS and Linux (multifirm.ps1 on Windows).
#
# Python is chosen in this order:
#   1. $MULTIFIRM_PYTHON (used even if its esptool differs; multifirm.py then refuses device access)
#   2. tools/.venv/bin/python
#   3. ../M5StopWatch-UserDemo/.tools/idf-tools/python_env/*/bin/python
# Candidates 2 and 3 are used only when they have the verified esptool version.
set -eu
required=4.12.0
dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
script="$dir/multifirm.py"

esptool_version() {
    # tr drops the CR printed by a Windows Python (e.g. when run from Git Bash).
    "$1" -c 'import esptool; print(esptool.__version__)' 2>/dev/null | tr -d '\r'
}

python=
if [ -n "${MULTIFIRM_PYTHON:-}" ]; then
    if [ ! -e "$MULTIFIRM_PYTHON" ]; then
        echo "MULTIFIRM_PYTHON not found: $MULTIFIRM_PYTHON" >&2
        exit 1
    fi
    python=$MULTIFIRM_PYTHON
    version=$(esptool_version "$python")
    if [ "$version" != "$required" ]; then
        echo "WARNING: esptool $version in $python is not the verified $required" >&2
    fi
else
    for candidate in "$dir/.venv/bin/python" \
            "$dir/../../M5StopWatch-UserDemo/.tools/idf-tools/python_env"/*/bin/python; do
        if [ -x "$candidate" ] && [ "$(esptool_version "$candidate")" = "$required" ]; then
            python=$candidate
            break
        fi
    done
fi
if [ -z "$python" ]; then
    cat >&2 <<EOF
No Python with esptool $required found. Set up the MultiFirm environment:
  python3.11 -m venv "$dir/.venv"
  "$dir/.venv/bin/python" -m pip install -r "$dir/requirements.txt"
or set MULTIFIRM_PYTHON to a Python that has esptool $required.
EOF
    exit 1
fi

exec "$python" "$script" "$@"
