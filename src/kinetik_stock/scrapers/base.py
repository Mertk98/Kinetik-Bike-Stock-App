from __future__ import annotations

import importlib
import logging
import re
from abc import ABC, abstractmethod

from playwright.sync_api import Browser, Page

from kinetik_stock.config import REPO_ROOT, BrandConfig
from kinetik_stock.models import StockItem


logger = logging.getLogger(__name__)
DEBUG_DIR = REPO_ROOT / "output" / "debug"


class BaseScraper(ABC):
    """Base class for a brand's B2B portal scraper.

    Subclasses implement `login` and `fetch_stock` using the Playwright `Page`
    handed to them. One scraper instance handles exactly one brand.
    """

    def __init__(self, brand_config: BrandConfig, browser: Browser, *, headless: bool = True):
        self.brand_config = brand_config
        self.browser = browser
        self.headless = headless
        self.page: Page = browser.new_page()

    @abstractmethod
    def login(self) -> None:
        """Authenticate against the brand's B2B portal using self.brand_config
        credentials. Should raise on failure (e.g. bad credentials, portal
        layout changed) rather than continuing silently.
        """

    @abstractmethod
    def fetch_stock(self) -> list[StockItem]:
        """Navigate the authenticated session and return normalized StockItems
        for every bike model/variant this brand exposes.
        """

    def describe_page(self, label: str) -> str:
        """One-line summary of what the browser is looking at (URL, title,
        visible text) plus a screenshot saved under output/debug/. Meant for
        error messages when a step fails and the reason isn't obvious - e.g.
        a login that bounces back to the form, an error banner, or a bot check.
        """
        try:
            text = re.sub(r"\s+", " ", self.page.inner_text("body", timeout=3000)).strip()
        except Exception:
            text = "(couldn't read page text)"
        summary = f"url={self.page.url} title={self.page.title()!r} page text: {text[:300]!r}"
        try:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            shot = DEBUG_DIR / f"{label}.png"
            self.page.screenshot(path=str(shot))
            summary += f" (screenshot: {shot})"
        except Exception:
            logger.debug("Couldn't save debug screenshot", exc_info=True)
        return summary

    def close(self) -> None:
        self.page.close()

    def run(self) -> list[StockItem]:
        self.login()
        try:
            return self.fetch_stock()
        finally:
            self.close()


def load_scraper_class(dotted_path: str) -> type[BaseScraper]:
    """Import 'pkg.module.ClassName' and return the class object."""
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)
