from __future__ import annotations

import importlib
from abc import ABC, abstractmethod

from playwright.sync_api import Browser, Page

from kinetik_stock.config import BrandConfig
from kinetik_stock.models import StockItem


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
