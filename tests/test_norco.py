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
FIXTURE_TORRENT_PAGE = (
    (Path(__file__).parent / "fixtures" / "norco_product" / "torrent_dh_a1.html")
    .resolve()
    .as_uri()
)
FIXTURE_DISCONTINUED_PAGE = (
    (Path(__file__).parent / "fixtures" / "norco_product" / "sight_c1_150.html")
    .resolve()
    .as_uri()
)
FIXTURE_SOLD_OUT_PAGE = (
    (Path(__file__).parent / "fixtures" / "norco_product" / "sight_c3_150_mx.html")
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
    row = {"qty_availh": 0, "qty_availnh": 0, "eta_h": "N", "eta_nh": "N", "active": 1}
    assert _status_and_eta(row) == (StockStatus.OUT_OF_STOCK, None)


def test_status_and_eta_discontinued():
    row = {"qty_availh": 0, "qty_availnh": 0, "eta_h": "N", "eta_nh": "N", "active": 0}
    assert _status_and_eta(row) == (StockStatus.DISCONTINUED, None)


def test_status_and_eta_in_stock_even_when_inactive():
    # A last-unit sellthrough on a discontinued line is still in stock, not
    # discontinued - confirmed by Sight C1 150's Size 2 row (active=0, qty=1).
    row = {"qty_availh": 1, "qty_availnh": 0, "eta_h": "N", "eta_nh": "N", "active": 0}
    assert _status_and_eta(row) == (StockStatus.IN_STOCK, None)


def test_status_and_eta_pre_order_from_real_eta_object():
    row = {
        "qty_availh": 0,
        "qty_availnh": 0,
        "eta_h": {"eta_date": "02-15-2027", "qty_eta": 25},
        "eta_nh": {"eta_date": "02-15-2027", "qty_eta": 25},
    }
    assert _status_and_eta(row) == (StockStatus.PRE_ORDER, "2027-02-15")


def test_status_and_eta_in_stock_even_with_eta_object_on_other_warehouse():
    # An ETA object on a warehouse that isn't the one with current stock is
    # a restock-in-transit signal, not a change to the item's current status.
    row = {
        "qty_availh": 15,
        "qty_availnh": 0,
        "eta_h": "N",
        "eta_nh": {"eta_date": "02-15-2027", "qty_eta": 18},
    }
    assert _status_and_eta(row) == (StockStatus.IN_STOCK, None)


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
    # All rows on this page have active=0 - with no stock and no ETA, that
    # means discontinued rather than a plain temporary out-of-stock.
    assert size3.status == StockStatus.DISCONTINUED

    for item in items:
        assert item.brand == "Norco"


def test_extract_stock_items_handles_real_eta_object():
    brand_config = make_brand_config()

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = NorcoScraper(brand_config, browser)
            try:
                scraper.page.goto(FIXTURE_TORRENT_PAGE)
                items = scraper._extract_stock_items_from_current_page()
            finally:
                scraper.close()
        finally:
            browser.close()

    assert len(items) == 2
    by_sku = {item.sku: item for item in items}

    in_stock = by_sku["0634017714"]
    assert in_stock.status == StockStatus.IN_STOCK
    assert in_stock.eta_date is None

    pre_order = by_sku["0634017916"]
    assert pre_order.status == StockStatus.PRE_ORDER
    assert pre_order.eta_date == "2027-02-15"
    assert pre_order.product_title == "TORRENT DH A1"


def test_extract_stock_items_handles_real_discontinued_page():
    brand_config = make_brand_config()

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = NorcoScraper(brand_config, browser)
            try:
                scraper.page.goto(FIXTURE_DISCONTINUED_PAGE)
                items = scraper._extract_stock_items_from_current_page()
            finally:
                scraper.close()
        finally:
            browser.close()

    assert len(items) == 3
    by_sku = {item.sku: item for item in items}

    assert by_sku["0620116154"].status == StockStatus.DISCONTINUED
    assert by_sku["0620116156"].status == StockStatus.DISCONTINUED
    # Last unit of an otherwise-discontinued size is still sellable.
    assert by_sku["0620116155"].status == StockStatus.IN_STOCK


def test_extract_stock_items_handles_real_sold_out_page():
    brand_config = make_brand_config()

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = NorcoScraper(brand_config, browser)
            try:
                scraper.page.goto(FIXTURE_SOLD_OUT_PAGE)
                items = scraper._extract_stock_items_from_current_page()
            finally:
                scraper.close()
        finally:
            browser.close()

    assert len(items) == 2
    assert all(item.status == StockStatus.OUT_OF_STOCK for item in items)
