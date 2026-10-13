#!/usr/bin/env bash
set -euo pipefail
TASK_DIR="$(cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")" && pwd -P)"
exec "${PYTHON:-python3}" "$TASK_DIR/build_pipeline.py" "$@"
