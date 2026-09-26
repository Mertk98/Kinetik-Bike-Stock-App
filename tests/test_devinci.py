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


def test_extract_stock_items_treats_coincident_periods_as_in_stock(tmp_path):
    # Per the user: period 2's start date isn't always later than period
    # 1's - when they coincide, period 2 isn't really "future production"
    # at all, so a quantity there is IN_STOCK, not PRE_ORDER, with no ETA.
    # This is a synthetic minimal page (not a full real fixture) built from
    # the same confirmed field-name/attribute patterns as achats_treeview.html,
    # just with both periods' start dates set equal.
    page_path = tmp_path / "coincident_periods.html"
    page_path.write_text(
        """
        <html><head><meta charset="utf-8"></head><body><form>
        <input type="hidden" name="txtDateDébutLivraison1" value="2026-08-01">
        <input type="hidden" name="txtDateDébutLivraison2" value="2026-08-01">
        <table id="RadGrid1_ctl00"><tbody>
        <tr class="rgRow" id="RadGrid1_ctl00__0">
        <td class="rgGroupCol">&nbsp;</td>
        <td class="ItemStyle">FV00000-00</td>
        <td class="ItemStyle"><a class="DescriptionSansLien">Bike Test | Spec | Black</a></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl00$txtprice6_FV00000_00__GAMM_2027_1" value="1999"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl00$txtXS1_FV00000_00__GAMM_2027_1"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl00$txtS1_FV00000_00__GAMM_2027_1"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl00$txtM1_FV00000_00__GAMM_2027_1"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl00$txtL1_FV00000_00__GAMM_2027_1"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl00$txtXL1_FV00000_00__GAMM_2027_1"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl00$txtXS2_FV00000_00__GAMM_2027_1"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl00$txtS2_FV00000_00__GAMM_2027_1"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl00$txtM2_FV00000_00__GAMM_2027_1" onchange="ValiderQty(this.value,7,'1',this,'REPEAT','2')"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl00$txtL2_FV00000_00__GAMM_2027_1"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl00$txtXL2_FV00000_00__GAMM_2027_1"></td>
        </tr>
        </tbody></table>
        </form></body></html>
        """,
        encoding="utf-8",
    )

    brand_config = make_brand_config()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = DevinciScraper(brand_config, browser)
            try:
                scraper.page.goto(page_path.resolve().as_uri())
                items = scraper._extract_stock_items_from_page()
            finally:
                scraper.close()
        finally:
            browser.close()

    by_size = {i.size: i for i in items}
    m = by_size["M"]
    assert (m.status, m.quantity, m.eta_date) == (StockStatus.IN_STOCK, 7, None)
    assert by_size["XS"].status == StockStatus.OUT_OF_STOCK
