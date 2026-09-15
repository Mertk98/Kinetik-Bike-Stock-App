#!/usr/bin/env python3
"""CLI entrypoint: log into each configured brand B2B portal, scrape bike
availability, and write a normalized CSV.

Usage:
    python run.py                          # scrape all enabled brands
    python run.py --brands example_brand   # scrape just one brand
    python run.py --no-headless            # show the browser (debugging)
    python run.py --output output/stock.csv
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from kinetik_stock.csv_export import write_csv  # noqa: E402
from kinetik_stock.runner import run_all  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--brands",
        nargs="+",
        default=None,
        help="Brand keys to scrape (default: all enabled brands in config/brands.yaml)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Output CSV path (default: output/stock_<brand keys>.csv, "
            "e.g. output/stock_transition_bikes.csv - overwritten on each run)"
        ),
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run the browser with a visible window (useful when debugging a scraper)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    output_path = args.output
    if output_path is None:
        brands_label = "_".join(sorted(args.brands)) if args.brands else "all"
        output_path = Path("output") / f"stock_{brands_label}.csv"

    items, errors = run_all(only_keys=args.brands, headless=not args.no_headless)

    write_csv(items, output_path)
    print(f"Wrote {len(items)} rows to {output_path}")

    if errors:
        print(f"\n{len(errors)} brand(s) failed:", file=sys.stderr)
        for key, message in errors:
            print(f"  - {key}: {message}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
