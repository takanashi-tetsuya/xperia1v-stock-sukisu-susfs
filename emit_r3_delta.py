#!/usr/bin/env python3
"""Generate only the source/config audit; never generate device PASS or build PASS."""
import argparse
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, required=True)
parser.add_argument('--previous-r2-root', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
sys.path.append(str(args.root.resolve()))
from pipeline_common import atomic_json, cli_error
from r3_delivery_checks import calculate_r3_delta


def main():
    args.output.unlink(missing_ok=True)
    report = calculate_r3_delta(args.root.resolve(), args.previous_r2_root.resolve())
    atomic_json(args.output, report)
    print(report['status'])


sys.exit(cli_error(main))
