#!/usr/bin/env python3
"""CLI entrypoint: log into Norco's B2B portal (LTP Dealer) and produce the
user's own availability report - the input CSV's columns (System ID,
Manufact. SKU, Item) plus Status/ETA in their own wording, distinct from the
shared multi-brand CSV produced by run.py.

Usage:
    python norco_report.py
    python norco_report.py --input config/norco_items.csv --output output/norco_report.csv
    python norco_report.py --no-headless   # show the browser (debugging)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from playwright.sync_api import sync_playwright  # noqa: E402

from kinetik_stock.browser import launch_chromium  # noqa: E402
from kinetik_stock.config import load_brand_configs  # noqa: E402
from kinetik_stock.scrapers.norco import (  # noqa: E402
    NORCO_ITEMS_CSV,
    NorcoScraper,
    write_norco_report_csv,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=NORCO_ITEMS_CSV, help="Input CSV path"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output") / "norco_report.csv",
        help="Output CSV path",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run the browser with a visible window (useful when debugging)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    brand = next(b for b in load_brand_configs() if b.key == "norco")
    if not brand.credentials_present():
        missing = ", ".join(brand.missing_credential_envs())
        print(f"Missing credentials for norco: set {missing} in .env", file=sys.stderr)
        return 1

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=not args.no_headless)
        try:
            scraper = NorcoScraper(brand, browser, headless=not args.no_headless)
            scraper.login()
            try:
                rows = scraper.generate_availability_report(args.input)
            finally:
                scraper.close()
        finally:
            browser.close()

    write_norco_report_csv(rows, args.output)
    print(f"Wrote {len(rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
