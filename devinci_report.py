#!/usr/bin/env python3
"""CLI entrypoint: log into Devinci's B2B portal and produce the user's own
availability report - the input CSV's columns (System ID, Manufact. SKU,
Description) plus Status/ETA, expanded to one row per size, distinct
from the shared multi-brand CSV produced by run.py.

Both login() and fetch_stock() are now implemented against real captured
portal pages (see src/kinetik_stock/scrapers/devinci.py) and covered by
their own local fixture tests, but neither has actually been run live
against transac.devinci.com yet - this dev sandbox's network policy blocks
outbound access to that host entirely. Try this for real before trusting
its output; this mirrors norco_report.py's shape.

Usage:
    python devinci_report.py
    python devinci_report.py --input config/devinci_items.csv --output output/devinci_report.csv
    python devinci_report.py --no-headless   # show the browser (debugging)
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
from kinetik_stock.scrapers.devinci import (  # noqa: E402
    DEVINCI_ITEMS_CSV,
    DevinciScraper,
    write_devinci_report_csv,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=DEVINCI_ITEMS_CSV, help="Input CSV path"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output") / "devinci_report.csv",
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

    brand = next(b for b in load_brand_configs() if b.key == "devinci")
    if not brand.credentials_present():
        missing = ", ".join(brand.missing_credential_envs())
        print(f"Missing credentials for devinci: set {missing} in .env", file=sys.stderr)
        return 1

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=not args.no_headless)
        try:
            scraper = DevinciScraper(brand, browser, headless=not args.no_headless)
            scraper.login()
            try:
                rows = scraper.generate_availability_report(args.input)
            finally:
                scraper.close()
        finally:
            browser.close()

    write_devinci_report_csv(rows, args.output)
    print(f"Wrote {len(rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
