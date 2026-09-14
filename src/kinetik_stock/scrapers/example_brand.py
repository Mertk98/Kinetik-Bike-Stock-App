from __future__ import annotations

import re

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from kinetik_stock.models import StockItem, StockStatus
from kinetik_stock.scrapers.base import BaseScraper

# Maps substrings of a portal's raw status text to our normalized StockStatus.
# Real brand scrapers will have their own version of this tuned to that
# portal's exact wording (e.g. "Backorder", "Pre-Order", "Sold Out").
STATUS_KEYWORDS: list[tuple[str, StockStatus]] = [
    ("discontinued", StockStatus.DISCONTINUED),
    ("eta", StockStatus.ETA),
    ("out of stock", StockStatus.OUT_OF_STOCK),
    ("in stock", StockStatus.IN_STOCK),
]

ETA_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def normalize_status(raw_text: str) -> tuple[StockStatus, str | None]:
    lowered = raw_text.strip().lower()
    for keyword, status in STATUS_KEYWORDS:
        if keyword in lowered:
            eta_date = None
            if status == StockStatus.ETA:
                match = ETA_DATE_RE.search(raw_text)
                eta_date = match.group(1) if match else None
            return status, eta_date
    return StockStatus.UNKNOWN, None


class ExampleBrandScraper(BaseScraper):
    """Fully working reference implementation.

    Runs against the local test fixture in tests/fixtures/example_b2b/ so the
    whole login -> scrape -> normalize -> CSV pipeline can be exercised
    without real B2B credentials. Copy this file as a starting point for a
    real brand's scraper.
    """

    def login(self) -> None:
        self.page.goto(self.brand_config.portal_url)
        self.page.fill("#username", self.brand_config.username or "")
        self.page.fill("#password", self.brand_config.password or "")
        self.page.click("#login-button")

        try:
            self.page.wait_for_url("**/stock.html*", timeout=5000)
        except PlaywrightTimeoutError:
            pass  # stayed on the login page - credentials were rejected

        if "stock.html" not in self.page.url:
            raise RuntimeError(
                f"Login to {self.brand_config.name} failed: check "
                f"{self.brand_config.username_env}/{self.brand_config.password_env}"
            )

        self.page.wait_for_selector("#content", state="visible", timeout=5000)

    def fetch_stock(self) -> list[StockItem]:
        rows = self.page.query_selector_all("#stock-table tbody tr")
        items: list[StockItem] = []
        source_url = self.page.url

        for row in rows:
            sku = row.query_selector(".sku").inner_text().strip()
            model = row.query_selector(".model").inner_text().strip()
            variant = row.query_selector(".variant").inner_text().strip()
            raw_status = row.query_selector(".status").inner_text().strip()
            raw_qty = row.query_selector(".qty").inner_text().strip()

            status, eta_date = normalize_status(raw_status)
            quantity = int(raw_qty) if raw_qty.isdigit() else None

            items.append(
                StockItem(
                    brand=self.brand_config.name,
                    sku=sku,
                    product_title=model,
                    variant=variant,
                    status=status,
                    quantity=quantity,
                    eta_date=eta_date,
                    raw_status_text=raw_status,
                    source_url=source_url,
                )
            )

        return items
