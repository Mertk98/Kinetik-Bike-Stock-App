import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.sync_api import sync_playwright

from kinetik_stock.browser import launch_chromium
from kinetik_stock.config import BrandConfig
from kinetik_stock.models import StockItem, StockStatus
from kinetik_stock.scrapers.norco import (
    NorcoScraper,
    _find_col_key,
    _load_norco_items,
    _report_status_and_eta,
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


def test_load_norco_items(tmp_path):
    csv_path = tmp_path / "norco_items.csv"
    csv_path.write_text(
        "System ID,Manufact. SKU,Item\n"
        "210000041889,623014114,Norco Sight A1 MX Silver/Green SZ1 (29/27.5)\n"
        "210000041890,,Norco Sight A1 MX Silver/Green SZ2 (29/27.5)\n"
    )
    rows = _load_norco_items(csv_path)
    assert rows == [
        {
            "system_id": "210000041889",
            "item_number": "623014114",
            "item_text": "Norco Sight A1 MX Silver/Green SZ1 (29/27.5)",
        },
        {
            "system_id": "210000041890",
            "item_number": "",
            "item_text": "Norco Sight A1 MX Silver/Green SZ2 (29/27.5)",
        },
    ]


def _make_item(status, quantity=None, eta_date=None) -> StockItem:
    return StockItem(
        brand="Norco",
        sku="0000000000",
        product_title="Test Bike",
        status=status,
        quantity=quantity,
        eta_date=eta_date,
    )


def test_report_status_and_eta_in_stock():
    item = _make_item(StockStatus.IN_STOCK, quantity=15)
    assert _report_status_and_eta(item) == ("Available (15)", "Now")


def test_report_status_and_eta_pre_order():
    item = _make_item(StockStatus.PRE_ORDER, quantity=0, eta_date="2027-02-15")
    assert _report_status_and_eta(item) == ("pre-order", "2027-02-15")


def test_report_status_and_eta_discontinued():
    item = _make_item(StockStatus.DISCONTINUED, quantity=0)
    assert _report_status_and_eta(item) == ("Discontinued", "N/A")


def test_report_status_and_eta_out_of_stock():
    item = _make_item(StockStatus.OUT_OF_STOCK, quantity=0)
    assert _report_status_and_eta(item) == ("Out of Stock", "N/A")


def test_generate_availability_report(tmp_path, monkeypatch):
    csv_path = tmp_path / "norco_items.csv"
    csv_path.write_text(
        "System ID,Manufact. SKU,Item\n"
        "1,IN-STOCK-ITEM,In Stock Bike SZ1\n"
        "2,,No Sku On File SZ2\n"
        "3,NOT-FOUND-ITEM,Not Found Bike SZ3\n"
        "4,PRE-ORDER-ITEM,Pre Order Bike SZ4\n"
    )

    def fake_scrape_item_page(self, item_number):
        if item_number == "IN-STOCK-ITEM":
            return [
                StockItem(
                    brand="Norco",
                    sku="IN-STOCK-ITEM",
                    product_title="In Stock Bike",
                    status=StockStatus.IN_STOCK,
                    quantity=7,
                )
            ]
        if item_number == "NOT-FOUND-ITEM":
            return []
        if item_number == "PRE-ORDER-ITEM":
            return [
                StockItem(
                    brand="Norco",
                    sku="PRE-ORDER-ITEM",
                    product_title="Pre Order Bike",
                    status=StockStatus.PRE_ORDER,
                    quantity=0,
                    eta_date="2027-03-01",
                )
            ]
        raise AssertionError(f"unexpected item_number {item_number!r}")

    brand_config = make_brand_config()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = NorcoScraper(brand_config, browser)
            monkeypatch.setattr(
                NorcoScraper, "_scrape_item_page", fake_scrape_item_page
            )
            try:
                rows = scraper.generate_availability_report(csv_path)
            finally:
                scraper.close()
        finally:
            browser.close()

    by_system_id = {row["System ID"]: row for row in rows}

    assert by_system_id["1"]["Status"] == "Available (7)"
    assert by_system_id["1"]["ETA"] == "Now"

    # No Manufact. SKU on file is treated as discontinued.
    assert by_system_id["2"]["Status"] == "Discontinued"
    assert by_system_id["2"]["ETA"] == "N/A"

    assert by_system_id["3"]["Status"] == "N/A"
    assert by_system_id["3"]["ETA"] == "N/A"

    assert by_system_id["4"]["Status"] == "pre-order"
    assert by_system_id["4"]["ETA"] == "2027-03-01"


def test_fetch_stock_treats_missing_item_number_as_discontinued(tmp_path, monkeypatch):
    csv_path = tmp_path / "norco_items.csv"
    csv_path.write_text(
        "System ID,Manufact. SKU,Item\n"
        "1,,No Sku On File SZ2\n"
        "2,IN-STOCK-ITEM,In Stock Bike SZ1\n"
    )

    def fake_scrape_item_page(self, item_number):
        assert item_number == "IN-STOCK-ITEM"
        return [
            StockItem(
                brand="Norco",
                sku="IN-STOCK-ITEM",
                product_title="In Stock Bike",
                status=StockStatus.IN_STOCK,
                quantity=7,
            )
        ]

    brand_config = make_brand_config()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = NorcoScraper(brand_config, browser)
            monkeypatch.setattr(
                "kinetik_stock.scrapers.norco.NORCO_ITEMS_CSV", csv_path
            )
            monkeypatch.setattr(
                NorcoScraper, "_scrape_item_page", fake_scrape_item_page
            )
            try:
                items = scraper.fetch_stock()
            finally:
                scraper.close()
        finally:
            browser.close()

    assert len(items) == 2
    no_sku_item = next(i for i in items if i.product_title == "No Sku On File SZ2")
    assert no_sku_item.status == StockStatus.DISCONTINUED

    in_stock_item = next(i for i in items if i.sku == "IN-STOCK-ITEM")
    assert in_stock_item.status == StockStatus.IN_STOCK
