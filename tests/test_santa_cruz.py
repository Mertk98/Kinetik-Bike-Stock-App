import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.sync_api import sync_playwright

from kinetik_stock.browser import launch_chromium
from kinetik_stock.config import BrandConfig
from kinetik_stock.scrapers import santa_cruz as santa_cruz_module
from kinetik_stock.scrapers.santa_cruz import SantaCruzScraper

LOGIN_FIXTURE_PAGE = (
    (Path(__file__).parent / "fixtures" / "santa_cruz_login" / "login.html").resolve().as_uri()
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
