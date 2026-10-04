import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.sync_api import sync_playwright

from kinetik_stock.browser import launch_chromium
from kinetik_stock.config import BrandConfig
from kinetik_stock.scrapers import santa_cruz as santa_cruz_module
from kinetik_stock.scrapers.santa_cruz import (
    QUICK_ORDER_ADDED_ROW_SELECTOR,
    QUICK_ORDER_ITEM_SKU_RE,
    QUICK_ORDER_ITEM_TITLE_SELECTOR,
    SantaCruzScraper,
)

LOGIN_FIXTURE_PAGE = (
    (Path(__file__).parent / "fixtures" / "santa_cruz_login" / "login.html").resolve().as_uri()
)
ADDED_ROW_FIXTURE_PAGE = (
    (Path(__file__).parent / "fixtures" / "santa_cruz_quick_order" / "added_row.html")
    .resolve()
    .as_uri()
)
SKIPPED_ITEM_FIXTURE_PAGE = (
    (Path(__file__).parent / "fixtures" / "santa_cruz_quick_order" / "skipped_item.html")
    .resolve()
    .as_uri()
)


def make_brand_config() -> BrandConfig:
    return BrandConfig(
        key="santa_cruz",
        name="Santa Cruz Bicycles",
        portal_url=LOGIN_FIXTURE_PAGE,
        scraper="kinetik_stock.scrapers.santa_cruz.SantaCruzScraper",
        username_env="TEST_SANTA_CRUZ_USERNAME",
        password_env="TEST_SANTA_CRUZ_PASSWORD",
    )


def test_login_fills_credentials_and_confirms_via_account_greeting(monkeypatch):
    # Exercises login() end-to-end against the real confirmed field
    # selectors (username/password/submit) and the LOGGED_IN_SELECTOR check
    # (#navHeading), using the fixture pair described in
    # tests/fixtures/santa_cruz_login/ - a stand-in backend, not the real
    # portal (this sandbox can't reach vip.santacruzbicycles.com).
    monkeypatch.setenv("TEST_SANTA_CRUZ_USERNAME", "demo_user")
    monkeypatch.setenv("TEST_SANTA_CRUZ_PASSWORD", "demo_pass")
    brand_config = make_brand_config()

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = SantaCruzScraper(brand_config, browser)
            try:
                scraper.login()
                assert scraper.page.url.endswith("landing.html")
            finally:
                scraper.close()
        finally:
            browser.close()


def test_login_raises_when_logged_in_selector_never_appears(monkeypatch):
    # The fixture's "login" always "succeeds" (it's a static stand-in, not a
    # real backend that can reject credentials) - this instead confirms
    # login() surfaces a clear RuntimeError if the expected post-login
    # selector never shows up at all, e.g. a changed/broken page.
    monkeypatch.setattr(santa_cruz_module, "LOGGED_IN_SELECTOR", "#this-never-appears")
    monkeypatch.setattr(santa_cruz_module, "LOGGED_IN_CHECK_TIMEOUT_MS", 500)
    monkeypatch.setenv("TEST_SANTA_CRUZ_USERNAME", "demo_user")
    monkeypatch.setenv("TEST_SANTA_CRUZ_PASSWORD", "demo_pass")
    brand_config = make_brand_config()

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = SantaCruzScraper(brand_config, browser)
            try:
                with pytest.raises(RuntimeError):
                    scraper.login()
            finally:
                scraper.close()
        finally:
            browser.close()


def test_quick_order_item_sku_re_matches_sku_not_build_kit_line():
    # The real row has two ".cx-code" divs - a build-kit abbreviation, then
    # "SKU: <code>" - confirm the regex only matches the second shape.
    assert QUICK_ORDER_ITEM_SKU_RE.search(" Brsn 5 C MX 27 MD CBN Deore ") is None
    match = QUICK_ORDER_ITEM_SKU_RE.search(" SKU: 58-27313-448-3-933-132801 ")
    assert match.group(1) == "58-27313-448-3-933-132801"


def test_added_row_selectors_match_real_quick_order_markup():
    # Confirms QUICK_ORDER_ADDED_ROW_SELECTOR/QUICK_ORDER_ITEM_TITLE_SELECTOR
    # locate the right elements in a real captured added-row page, and that
    # the SKU is read from the correct one of its two ".cx-code" divs.
    brand_config = make_brand_config()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = SantaCruzScraper(brand_config, browser)
            try:
                scraper.page.goto(ADDED_ROW_FIXTURE_PAGE)
                row = scraper.page.query_selector(QUICK_ORDER_ADDED_ROW_SELECTOR)
                assert row is not None

                title = row.query_selector(QUICK_ORDER_ITEM_TITLE_SELECTOR).inner_text().strip()
                assert title == "Bronson 5 C"

                sku = None
                for code_div in row.query_selector_all(".cx-code"):
                    match = QUICK_ORDER_ITEM_SKU_RE.search(code_div.inner_text())
                    if match:
                        sku = match.group(1)
                assert sku == "58-27313-448-3-933-132801"
            finally:
                scraper.close()
        finally:
            browser.close()


def test_skipped_item_leaves_quick_order_table_with_no_matching_row():
    # Confirmed against a real captured Quick Order page for a known-bad
    # part number (discontinued or OOS-with-no-ETA): no row gets added for
    # it at all, so detecting a skip means finding no matching row, not
    # looking for a per-item warning - the page's only message at this
    # point is the generic (not per-item) "Error proceeding to Cart."
    # banner, which per the user doesn't even show until "Add to cart" is
    # clicked.
    brand_config = make_brand_config()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = SantaCruzScraper(brand_config, browser)
            try:
                scraper.page.goto(SKIPPED_ITEM_FIXTURE_PAGE)
                rows = scraper.page.query_selector_all(QUICK_ORDER_ADDED_ROW_SELECTOR)
                assert rows == []
            finally:
                scraper.close()
        finally:
            browser.close()


def test_fetch_stock_not_implemented():
    brand_config = make_brand_config()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = SantaCruzScraper(brand_config, browser)
            try:
                with pytest.raises(NotImplementedError):
                    scraper.fetch_stock()
            finally:
                scraper.close()
        finally:
            browser.close()
