from __future__ import annotations

import logging

from playwright.sync_api import sync_playwright

from kinetik_stock.browser import launch_chromium
from kinetik_stock.config import BrandConfig, load_brand_configs
from kinetik_stock.models import StockItem
from kinetik_stock.scrapers.base import load_scraper_class

logger = logging.getLogger(__name__)


def select_brands(
    brands: list[BrandConfig], only_keys: list[str] | None
) -> list[BrandConfig]:
    # Explicitly requesting a brand by key (--brands foo) is deliberate intent
    # and overrides `enabled: false` - useful for testing a brand that isn't
    # ready for full/bulk runs yet. A bare run (no --brands) only picks up
    # enabled brands.
    if only_keys:
        wanted = set(only_keys)
        return [b for b in brands if b.key in wanted]
    return [b for b in brands if b.enabled]


def run_brand(brand: BrandConfig, browser, *, headless: bool) -> list[StockItem]:
    if not brand.credentials_present():
        raise RuntimeError(
            f"Missing credentials for '{brand.key}': set {brand.username_env} "
            f"and {brand.password_env} in .env"
        )

    scraper_class = load_scraper_class(brand.scraper)
    scraper = scraper_class(brand, browser, headless=headless)
    logger.info("Scraping %s...", brand.name)
    items = scraper.run()
    logger.info("Got %d items for %s", len(items), brand.name)
    return items


def run_all(
    only_keys: list[str] | None = None, *, headless: bool = True
) -> tuple[list[StockItem], list[tuple[str, str]]]:
    """Returns (all stock items, list of (brand_key, error_message) failures)."""
    brands = select_brands(load_brand_configs(), only_keys)
    if not brands:
        raise RuntimeError("No enabled brands matched the given selection.")

    all_items: list[StockItem] = []
    errors: list[tuple[str, str]] = []

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=headless)
        try:
            for brand in brands:
                try:
                    all_items.extend(run_brand(brand, browser, headless=headless))
                except Exception as exc:  # keep going so one bad brand doesn't kill the run
                    logger.exception("Failed to scrape %s", brand.name)
                    errors.append((brand.key, str(exc)))
        finally:
            browser.close()

    return all_items, errors
