#!/bin/sh
# Explicit dependency-fetch step; never run by package installation.
set -eu

project_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
wheelhouse="$project_root/build/wheelhouse"
runtime="$project_root/build/runtime"
case "${1:-}" in
    --download)
        mkdir -p "$wheelhouse"
        python3 -m pip download --only-binary=:all: --no-deps \
            --dest "$wheelhouse" -r "$project_root/packaging/requirements-runtime.txt"
        (cd "$wheelhouse" && sha256sum ./*.whl > SHA256SUMS)
        ;;
    "")
        if [ ! -f "$wheelhouse/SHA256SUMS" ]; then
            echo 'No wheelhouse found. First run scripts/prepare-runtime.sh --download.' >&2
            exit 1
        fi
        ;;
    *)
        echo 'Usage: scripts/prepare-runtime.sh [--download]' >&2
        exit 2
        ;;
esac
(cd "$wheelhouse" && sha256sum --check SHA256SUMS)
mkdir -p "$runtime"
python3 -m pip install --no-index --find-links "$wheelhouse" --only-binary=:all: \
    --no-deps --no-compile --upgrade --target "$runtime" \
    -r "$project_root/packaging/requirements-runtime.txt"
echo "Private Qt runtime prepared in $runtime"
