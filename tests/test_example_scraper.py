import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.sync_api import sync_playwright

from kinetik_stock.browser import launch_chromium
from kinetik_stock.config import BrandConfig
from kinetik_stock.csv_export import write_csv
from kinetik_stock.models import StockStatus
from kinetik_stock.scrapers.example_brand import ExampleBrandScraper, normalize_status

FIXTURE_LOGIN = (
    (Path(__file__).parent / "fixtures" / "example_b2b" / "login.html").resolve().as_uri()
)


def make_example_brand_config(username="demo_user", password="demo_pass") -> BrandConfig:
    config = BrandConfig(
        key="example_brand",
        name="Example Brand",
        portal_url=FIXTURE_LOGIN,
        scraper="kinetik_stock.scrapers.example_brand.ExampleBrandScraper",
        username_env="TEST_EXAMPLE_BRAND_USERNAME",
        password_env="TEST_EXAMPLE_BRAND_PASSWORD",
    )
    import os

    os.environ["TEST_EXAMPLE_BRAND_USERNAME"] = username
    os.environ["TEST_EXAMPLE_BRAND_PASSWORD"] = password
    return config


def test_normalize_status():
    assert normalize_status("In Stock") == (StockStatus.IN_STOCK, None)
    assert normalize_status("Out of Stock") == (StockStatus.OUT_OF_STOCK, None)
    assert normalize_status("Discontinued") == (StockStatus.DISCONTINUED, None)
    status, eta = normalize_status("ETA 2025-11-15")
    assert status == StockStatus.PRE_ORDER
    assert eta == "2025-11-15"
    assert normalize_status("Something weird") == (StockStatus.UNKNOWN, None)


def test_scraper_login_and_fetch_stock(tmp_path):
    brand_config = make_example_brand_config()

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = ExampleBrandScraper(brand_config, browser)
            items = scraper.run()
        finally:
            browser.close()

    assert len(items) == 4
    by_sku = {item.sku: item for item in items}

    assert by_sku["EB-100-M"].status == StockStatus.IN_STOCK
    assert by_sku["EB-100-M"].quantity == 14
    assert by_sku["EB-100-L"].status == StockStatus.OUT_OF_STOCK
    assert by_sku["EB-200-M"].status == StockStatus.PRE_ORDER
    assert by_sku["EB-200-M"].eta_date == "2025-11-15"
    assert by_sku["EB-050-S"].status == StockStatus.DISCONTINUED

    for item in items:
        assert item.brand == "Example Brand"

    csv_path = tmp_path / "stock.csv"
    write_csv(items, csv_path)
    content = csv_path.read_text()
    assert "EB-100-M" in content
    assert "in_stock" in content


def test_scraper_login_failure_raises():
    brand_config = make_example_brand_config(password="wrong_password")

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = ExampleBrandScraper(brand_config, browser)
            try:
                scraper.login()
                assert False, "expected login failure to raise"
            except RuntimeError as exc:
                assert "Login to Example Brand failed" in str(exc)
        finally:
            browser.close()
