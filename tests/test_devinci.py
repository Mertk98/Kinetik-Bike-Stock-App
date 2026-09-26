import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.sync_api import sync_playwright

from kinetik_stock.browser import launch_chromium
from kinetik_stock.config import BrandConfig
from kinetik_stock.models import StockStatus
from kinetik_stock.scrapers.devinci import DevinciScraper, split_description

FIXTURE_PAGE = (
    (Path(__file__).parent / "fixtures" / "devinci_product" / "achats_treeview.html")
    .resolve()
    .as_uri()
)


def make_brand_config() -> BrandConfig:
    return BrandConfig(
        key="devinci",
        name="Devinci",
        portal_url="https://example.invalid/devinci",
        scraper="kinetik_stock.scrapers.devinci.DevinciScraper",
        username_env="TEST_DEVINCI_USERNAME",
        password_env="TEST_DEVINCI_PASSWORD",
    )


def test_split_description():
    assert split_description("Bike Wilson 40 | GX DH | Sepia Green") == (
        "Bike Wilson 40 | GX DH",
        "Sepia Green",
    )
    assert split_description("No pipes here") == ("No pipes here", None)


def test_login_is_unverified():
    brand_config = make_brand_config()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = DevinciScraper(brand_config, browser)
            try:
                with pytest.raises(NotImplementedError):
                    scraper.login()
            finally:
                scraper.close()
        finally:
            browser.close()


def test_fetch_stock_is_unverified():
    brand_config = make_brand_config()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = DevinciScraper(brand_config, browser)
            try:
                with pytest.raises(NotImplementedError):
                    scraper.fetch_stock()
            finally:
                scraper.close()
        finally:
            browser.close()


def test_extract_stock_items_from_page_matches_real_grid_structure():
    brand_config = make_brand_config()

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = DevinciScraper(brand_config, browser)
            try:
                scraper.page.goto(FIXTURE_PAGE)
                items = scraper._extract_stock_items_from_page()
            finally:
                scraper.close()
        finally:
            browser.close()

    by_sku_size = {(i.sku, i.size): i for i in items}

    # Exactly one StockItem per (SKU, size) - one of only 3 statuses, never
    # split by period.
    assert len(items) == len(by_sku_size)

    # FV27122-21: sold out (disabled) in both periods for every size ->
    # OUT_OF_STOCK, quantity 0, no ETA.
    sold_out = [i for i in items if i.sku == "FV27122-21"]
    assert len(sold_out) == 5
    assert all(i.status == StockStatus.OUT_OF_STOCK for i in sold_out)
    assert all(i.quantity == 0 for i in sold_out)
    assert all(i.eta_date is None for i in sold_out)
    assert all(i.color == "Sepia Green" for i in sold_out)
    assert all(i.product_title == "Bike Wilson 40 | GX DH" for i in sold_out)

    # FE26100-11: XS/S never offered (gray/zero in both periods) ->
    # OUT_OF_STOCK. M/L/XL in stock now, with the real (uncapped) quantity
    # read from onchange even though the label shows the "10+" cap.
    assert by_sku_size[("FE26100-11", "XS")].status == StockStatus.OUT_OF_STOCK
    assert by_sku_size[("FE26100-11", "S")].status == StockStatus.OUT_OF_STOCK
    m = by_sku_size[("FE26100-11", "M")]
    assert (m.status, m.quantity, m.eta_date) == (StockStatus.IN_STOCK, 25, None)
    assert by_sku_size[("FE26100-11", "L")].quantity == 28
    assert by_sku_size[("FE26100-11", "XL")].quantity == 32

    # FE26100-22: plain (non-capped) in-stock-now quantities.
    fe22_m = by_sku_size[("FE26100-22", "M")]
    assert (fe22_m.status, fe22_m.quantity) == (StockStatus.IN_STOCK, 7)
    assert fe22_m.color == "Deep Olive"
    assert fe22_m.regular_retail_price == 9999.0
    assert by_sku_size[("FE26100-22", "L")].quantity == 4
    assert by_sku_size[("FE26100-22", "XL")].quantity == 18

    # FV27105-32: confirmed live by the user - 9 Smalls and 5 XLs in stock
    # now (period 1), and M/L have zero stock *now* (gray period-1 cells)
    # but ARE available as future production (period 2) -> PRE_ORDER, not
    # OUT_OF_STOCK, since a gray period-1 cell doesn't mean the size is
    # unoffered when period 2 has real stock. XS is gray/zero in both
    # periods -> genuinely OUT_OF_STOCK.
    s = by_sku_size[("FV27105-32", "S")]
    assert (s.status, s.quantity, s.eta_date) == (StockStatus.IN_STOCK, 9, None)
    xl = by_sku_size[("FV27105-32", "XL")]
    assert (xl.status, xl.quantity, xl.eta_date) == (StockStatus.IN_STOCK, 5, None)

    m = by_sku_size[("FV27105-32", "M")]
    assert (m.status, m.quantity, m.eta_date) == (StockStatus.PRE_ORDER, 15, "2026-08-16")
    l = by_sku_size[("FV27105-32", "L")]
    assert (l.status, l.quantity, l.eta_date) == (StockStatus.PRE_ORDER, 12, "2026-08-16")

    xs = by_sku_size[("FV27105-32", "XS")]
    assert (xs.status, xs.quantity, xs.eta_date) == (StockStatus.OUT_OF_STOCK, 0, None)
