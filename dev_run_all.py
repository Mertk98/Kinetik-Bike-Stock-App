#!/usr/bin/env python3
"""DEV-ONLY helper: run the Norco, Transition and Devinci scrapes back to back.

Meant for the development process only - the GUI will replace it. Each brand
runs through its own existing entrypoint in a separate process, so one
brand failing doesn't stop the others; a summary (status + time per brand)
is printed at the end and the exit code is non-zero if any brand failed.

Usage:
    python dev_run_all.py
    python dev_run_all.py --no-headless -v
    python dev_run_all.py --only norco devinci
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# name -> (entrypoint args, output file it writes)
BRANDS: dict[str, tuple[list[str], str]] = {
    "norco": (["norco_report.py"], "output/norco_report.csv"),
    "transition": (
        ["run.py", "--brands", "transition_bikes"],
        "output/stock_transition_bikes.csv",
    ),
    "devinci": (["devinci_report.py"], "output/devinci_report.csv"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only", nargs="+", choices=list(BRANDS), help="Run just these brands"
    )
    parser.add_argument("--no-headless", action="store_true", help="Show the browser")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    extra = (["--no-headless"] if args.no_headless else []) + (["-v"] if args.verbose else [])

    results = []
    for name in args.only or list(BRANDS):
        cmd_args, output = BRANDS[name]
        print(f"\n===== {name} =====", flush=True)
        start = time.monotonic()
        code = subprocess.run([sys.executable, *cmd_args, *extra], cwd=ROOT).returncode
        results.append((name, code, time.monotonic() - start, output))

    print("\n===== Summary =====")
    for name, code, secs, output in results:
        status = "OK    " if code == 0 else f"FAILED (exit {code})"
        print(f"{name:<11} {status:<18} {secs:6.1f}s  {output if code == 0 else ''}")
    return 0 if all(code == 0 for _, code, _, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
