#!/bin/sh
set -eu
ave_toolkit_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec python3 "$ave_toolkit_root/avtool.py" "$@"
