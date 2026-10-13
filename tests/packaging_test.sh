#!/usr/bin/env bash
# CI entry point: isolated fixtures only; no kernel compilation, network or devices.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/.." && pwd -P)"
cd "$ROOT"
bash -n build.sh prepare-sources.sh
"${PYTHON:-python3}" -m unittest discover -s tests -p 'packaging*_test.py' -v
PYTHONOPTIMIZE=1 "${PYTHON:-python3}" -m unittest discover -s tests -p 'packaging*_test.py' -v
