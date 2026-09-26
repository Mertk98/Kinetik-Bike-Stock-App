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

    by_sku_size = {(i.sku, i.size, i.status): i for i in items}

    # FV27122-21: fully sold out in both periods -> 10 OUT_OF_STOCK rows
    # (5 sizes x 2 periods), quantity 0, no ETA.
    sold_out = [i for i in items if i.sku == "FV27122-21"]
    assert len(sold_out) == 10
    assert all(i.status == StockStatus.OUT_OF_STOCK for i in sold_out)
    assert all(i.quantity == 0 for i in sold_out)
    assert all(i.eta_date is None for i in sold_out)
    assert all(i.color == "Sepia Green" for i in sold_out)
    assert all(i.product_title == "Bike Wilson 40 | GX DH" for i in sold_out)

    # FE26100-11: XS/S not offered at all (skipped), M/L/XL orderable in both
    # periods with the portal's "10+" display cap - real max qty (25/28/32)
    # comes from onchange, not the capped label.
    fe11 = [i for i in items if i.sku == "FE26100-11"]
    assert {(i.size) for i in fe11} == {"M", "L", "XL"}
    assert len(fe11) == 6  # 3 sizes x 2 periods

    m1 = by_sku_size[("FE26100-11", "M", StockStatus.IN_STOCK)]
    assert m1.quantity == 25
    assert m1.eta_date is None

    m2 = by_sku_size[("FE26100-11", "M", StockStatus.PRE_ORDER)]
    assert m2.quantity == 25
    assert m2.eta_date == "2026-08-16"  # txtDateDébutLivraison2

    xl1 = by_sku_size[("FE26100-11", "XL", StockStatus.IN_STOCK)]
    assert xl1.quantity == 32

    # FE26100-22: plain (non-capped) quantities.
    fe22_m1 = by_sku_size[("FE26100-22", "M", StockStatus.IN_STOCK)]
    assert fe22_m1.quantity == 7
    assert fe22_m1.color == "Deep Olive"
    assert fe22_m1.regular_retail_price == 9999.0

    fe22_l1 = by_sku_size[("FE26100-22", "L", StockStatus.IN_STOCK)]
    assert fe22_l1.quantity == 4

    fe22_xl2 = by_sku_size[("FE26100-22", "XL", StockStatus.PRE_ORDER)]
    assert fe22_xl2.quantity == 18
    assert fe22_xl2.eta_date == "2026-08-16"
