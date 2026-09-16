import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.sync_api import sync_playwright

from kinetik_stock.browser import launch_chromium
from kinetik_stock.config import BrandConfig
from kinetik_stock.models import StockStatus
from kinetik_stock.scrapers import transition_bikes as tb
from kinetik_stock.scrapers.transition_bikes import (
    BIKE_MODELS,
    CLOSEOUT_MODELS,
    TransitionBikesScraper,
    closeout_page_url,
    normalize_status,
    parse_bike_name_and_build_kit,
    parse_eta_message,
    parse_regular_retail_price,
    product_page_url,
    split_product_and_variant,
    split_size_color,
)

FIXTURE_PRODUCT_PAGE = (
    (Path(__file__).parent / "fixtures" / "transition_product" / "RepeaterPT.html")
    .resolve()
    .as_uri()
)


def make_brand_config() -> BrandConfig:
    return BrandConfig(
        key="transition_bikes",
        name="Transition Bikes",
        portal_url="https://b2b.transitionbikes.com/Account",
        scraper="kinetik_stock.scrapers.transition_bikes.TransitionBikesScraper",
        username_env="TEST_TRANSITION_USERNAME",
        password_env="TEST_TRANSITION_PASSWORD",
    )


def test_split_product_and_variant():
    title, variant = split_product_and_variant(
        "Complete: Bandit Hardtail (One Size, Black and Green)"
    )
    assert title == "Complete: Bandit Hardtail"
    assert variant == "One Size, Black and Green"


def test_split_size_color():
    assert split_size_color("Large, Moonstone") == ("Large", "Moonstone")
    assert split_size_color(None) == (None, None)


def test_normalize_status():
    assert normalize_status("In Stock") == StockStatus.IN_STOCK
    assert normalize_status("Out Of Stock") == StockStatus.OUT_OF_STOCK
    assert normalize_status("Low Stock (3)") == StockStatus.LOW_STOCK
    assert normalize_status("Pre-Order") == StockStatus.ETA
    assert normalize_status("Something weird") == StockStatus.UNKNOWN


def test_parse_regular_retail_price():
    assert parse_regular_retail_price(["01.25.11.5070", "$1,899.00", "$1,291.32", "N/A"]) == 1899.00
    assert parse_regular_retail_price(["N/A"]) is None


def test_parse_bike_name_and_build_kit():
    assert parse_bike_name_and_build_kit("Complete: Repeater PT Carbon AXS") == (
        "Repeater PT",
        "Carbon AXS",
    )
    assert parse_bike_name_and_build_kit("Complete: Regulator CX Deore - USA") == (
        "Regulator CX",
        "Deore",
    )
    assert parse_bike_name_and_build_kit("Complete: TR11 Alloy GX") == ("TR11", "Alloy GX")


def test_product_page_url():
    assert product_page_url("Repeater PT") == "https://www.transitionbikes.com/Bikes/RepeaterPT"


def test_bike_models_has_all_18_current_models():
    assert len(BIKE_MODELS) == 18
    assert BIKE_MODELS["Sentinel Youth"] == "SentinelYouth"
    assert BIKE_MODELS["Sentinel"] == "Sentinel"
    assert BIKE_MODELS["PBJ 24"] == "PBJ24"
    assert BIKE_MODELS["TransAM"] == "TransAM"


def test_closeout_models_has_all_8_closeout_models():
    assert len(CLOSEOUT_MODELS) == 8
    assert CLOSEOUT_MODELS <= set(BIKE_MODELS)  # every closeout model is a known model
    assert closeout_page_url("Repeater PT") == "https://www.transitionbikes.com/Bikes/Closeout/RepeaterPT"
    assert closeout_page_url("Scout") is None  # Scout has no closeout page


def test_parse_bike_name_disambiguates_sentinel_youth():
    assert parse_bike_name_and_build_kit("Complete: Sentinel Youth Alloy XT") == (
        "Sentinel Youth",
        "Alloy XT",
    )
    assert parse_bike_name_and_build_kit("Complete: Sentinel Alloy Deore") == (
        "Sentinel",
        "Alloy Deore",
    )


def test_parse_eta_message():
    assert parse_eta_message(" SHIPS APPX: 9/25/26") == "2026-09-25"
    assert parse_eta_message(" Ships Now") is None
    assert parse_eta_message(" OUT OF STOCK") is None


def test_read_eta_from_page_matches_real_page_structure():
    brand_config = make_brand_config()

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = TransitionBikesScraper(brand_config, browser)
            try:
                # Small/Sandstone and X-Large/Sandstone are the two combos
                # the fixture marks as "SHIPS APPX" (pre-order/ETA).
                assert scraper._read_eta_from_page(
                    FIXTURE_PRODUCT_PAGE, "Carbon AXS", "Sandstone", "Small"
                ) == (True, "2026-09-25")
                assert scraper._read_eta_from_page(
                    FIXTURE_PRODUCT_PAGE, "Carbon AXS", "Sandstone", "X-Large"
                ) == (True, "2026-09-25")
                # In-stock and out-of-stock combos are found but have no ETA.
                assert scraper._read_eta_from_page(
                    FIXTURE_PRODUCT_PAGE, "Carbon AXS", "Sandstone", "Medium"
                ) == (True, None)
                assert scraper._read_eta_from_page(
                    FIXTURE_PRODUCT_PAGE, "Carbon AXS", "Stardust", "Small"
                ) == (True, None)
                # A color that doesn't exist on the page at all -> not found.
                assert scraper._read_eta_from_page(
                    FIXTURE_PRODUCT_PAGE, "Carbon AXS", "Midnight", "Small"
                ) == (False, None)
            finally:
                scraper.close()
        finally:
            browser.close()


def test_fetch_eta_date_falls_back_to_closeout_page(monkeypatch, tmp_path):
    # A "main" page that doesn't have the Midnight color at all, and a
    # separate "closeout" page that does - simulating an older color only
    # sold as clearance stock.
    main_page = tmp_path / "main.html"
    main_page.write_text(
        """
        <div id="theBikeProducts">
            <div refColor="Sandstone" refColorCode="c2a87d" class="BikeColor"></div>
            <div refColor="c2a87d" refMessage=" Ships Now" class="ProductSelectorBox c2a87d selectProduct" refNameText="Small ">Small</div>
        </div>
        """
    )
    closeout_page = tmp_path / "closeout.html"
    closeout_page.write_text(
        """
        <div id="theBikeProducts">
            <div refColor="Midnight" refColorCode="222222" class="BikeColor"></div>
            <div refColor="222222" refMessage=" SHIPS APPX: 12/1/26" class="ProductSelectorBox 222222 selectProduct" refNameText="Small ">Small</div>
        </div>
        """
    )

    monkeypatch.setattr(tb, "product_page_url", lambda name: main_page.resolve().as_uri())
    monkeypatch.setattr(tb, "closeout_page_url", lambda name: closeout_page.resolve().as_uri())

    brand_config = make_brand_config()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = TransitionBikesScraper(brand_config, browser)
            try:
                eta = scraper._fetch_eta_date("Repeater PT", "Carbon AXS", "Midnight", "Small")
                assert eta == "2026-12-01"
            finally:
                scraper.close()
        finally:
            browser.close()
