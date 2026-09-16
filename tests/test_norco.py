import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.sync_api import sync_playwright

from kinetik_stock.browser import launch_chromium
from kinetik_stock.config import BrandConfig
from kinetik_stock.models import StockStatus
from kinetik_stock.scrapers.norco import (
    NorcoScraper,
    _find_col_key,
    _status_and_eta,
)

FIXTURE_PRODUCT_PAGE = (
    (Path(__file__).parent / "fixtures" / "norco_product" / "sight_c1_160.html")
    .resolve()
    .as_uri()
)


def make_brand_config() -> BrandConfig:
    return BrandConfig(
        key="norco",
        name="Norco",
        portal_url="https://ltpdealer.com/",
        scraper="kinetik_stock.scrapers.norco.NorcoScraper",
        username_env="TEST_NORCO_USERNAME",
        password_env="TEST_NORCO_PASSWORD",
        extra_env={"dealer_id": "TEST_NORCO_DEALER_ID"},
    )


def test_find_col_key():
    label_to_col = {"colour": "col_1", "frame size": "col_6"}
    assert _find_col_key(label_to_col, ("colour", "color")) == "col_1"
    assert _find_col_key(label_to_col, ("frame size", "size")) == "col_6"
    assert _find_col_key(label_to_col, ("motor",)) is None


def test_status_and_eta_in_stock():
    row = {"qty_availh": 3, "qty_availnh": 0, "eta_h": "N", "eta_nh": "N"}
    assert _status_and_eta(row) == (StockStatus.IN_STOCK, None)


def test_status_and_eta_out_of_stock():
    row = {"qty_availh": 0, "qty_availnh": 0, "eta_h": "N", "eta_nh": "N"}
    assert _status_and_eta(row) == (StockStatus.OUT_OF_STOCK, None)


def test_status_and_eta_pre_order():
    row = {"qty_availh": 0, "qty_availnh": 0, "eta_h": "2026-12-01", "eta_nh": "N"}
    assert _status_and_eta(row) == (StockStatus.PRE_ORDER, "2026-12-01")


def test_extract_stock_items_from_real_product_page_structure():
    brand_config = make_brand_config()

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = NorcoScraper(brand_config, browser)
            try:
                scraper.page.goto(FIXTURE_PRODUCT_PAGE)
                items = scraper._extract_stock_items_from_current_page()
            finally:
                scraper.close()
        finally:
            browser.close()

    assert len(items) == 3
    by_sku = {item.sku: item for item in items}

    size2 = by_sku["0610016155"]
    assert size2.product_title == "SIGHT C1 160"
    assert size2.size == "Size 2"
    assert size2.color == "Warm Grey"
    assert size2.regular_retail_price == 10499.0
    assert size2.status == StockStatus.IN_STOCK

    size3 = by_sku["0610016156"]
    assert size3.size == "Size 3"
    assert size3.status == StockStatus.OUT_OF_STOCK

    for item in items:
        assert item.brand == "Norco"
