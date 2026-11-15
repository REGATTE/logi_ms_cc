#!/bin/sh
# Run the source checkout with its own Qt runtime, from any working directory.
set -eu
project_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
python_runtime="$project_root/.venv/bin/python"
if [ ! -x "$python_runtime" ]; then
    echo "Create the development virtualenv described in README.md first." >&2
    exit 1
fi
export PYTHONPATH="$project_root/src"
# Optional local extraction used when developing without installing Qt's XCB
# dependency system-wide. The .deb declares this dependency through apt.
cursor_libraries="$project_root/build/host-libs/extracted/usr/lib/$(uname -m)-linux-gnu"
if [ -d "$cursor_libraries" ]; then
    export LD_LIBRARY_PATH="$cursor_libraries${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
exec "$python_runtime" -m mxcc "$@"
