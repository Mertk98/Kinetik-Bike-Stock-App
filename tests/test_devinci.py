import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.sync_api import sync_playwright

from kinetik_stock.browser import launch_chromium
from kinetik_stock.config import BrandConfig
from kinetik_stock.models import StockItem, StockStatus
from kinetik_stock.scrapers import devinci as devinci_module
from kinetik_stock.scrapers.devinci import (
    DevinciScraper,
    _load_devinci_items,
    _report_status_and_eta,
    build_availability_report,
    split_description,
)

FIXTURE_PAGE = (
    (Path(__file__).parent / "fixtures" / "devinci_product" / "achats_treeview.html")
    .resolve()
    .as_uri()
)
LOGIN_FIXTURE_PAGE = (
    (Path(__file__).parent / "fixtures" / "devinci_login" / "login.html").resolve().as_uri()
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
    # Frameset-only SKUs use " - " instead of "|", confirmed against a real
    # portal capture (e.g. "Frameset Spartan - Greige").
    assert split_description("Frameset Spartan - Greige") == (
        "Frameset Spartan",
        "Greige",
    )
    # No delimiter at all -> can't tell a color from a trailing spec token,
    # so this stays unsplit (confirmed real examples: "Bike Milano 2 AL13").
    assert split_description("No pipes here") == ("No pipes here", None)
    assert split_description("Bike Milano 2 AL13") == ("Bike Milano 2 AL13", None)


def test_login_fills_credentials_and_confirms_via_logout_link(monkeypatch):
    # Exercises login() end-to-end against the real confirmed field/button
    # selectors (txtLogin/txtPassword/cmdLogin) and the LOGGED_IN_SELECTOR
    # check (#cmdFermer), using the fixture pair described in
    # tests/fixtures/devinci_login/ - a stand-in backend, not the real
    # portal (still unreachable from this sandbox).
    monkeypatch.setenv("TEST_DEVINCI_USERNAME", "demo_user")
    monkeypatch.setenv("TEST_DEVINCI_PASSWORD", "demo_pass")
    brand_config = BrandConfig(
        key="devinci",
        name="Devinci",
        portal_url=LOGIN_FIXTURE_PAGE,
        scraper="kinetik_stock.scrapers.devinci.DevinciScraper",
        username_env="TEST_DEVINCI_USERNAME",
        password_env="TEST_DEVINCI_PASSWORD",
    )

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = DevinciScraper(brand_config, browser)
            try:
                scraper.login()
                assert scraper.page.url.endswith("landing.html")
            finally:
                scraper.close()
        finally:
            browser.close()


def test_login_raises_when_logged_in_selector_never_appears(monkeypatch):
    # No credentials filled in -> the fixture backend never redirects, so
    # the real page's LOGOUT link never shows up. Timeout shortened so this
    # test doesn't have to burn the real 15s LOGGED_IN_CHECK_TIMEOUT_MS.
    monkeypatch.setattr(devinci_module, "LOGGED_IN_CHECK_TIMEOUT_MS", 500)
    monkeypatch.setenv("TEST_DEVINCI_USERNAME", "")
    monkeypatch.setenv("TEST_DEVINCI_PASSWORD", "")
    brand_config = BrandConfig(
        key="devinci",
        name="Devinci",
        portal_url=LOGIN_FIXTURE_PAGE,
        scraper="kinetik_stock.scrapers.devinci.DevinciScraper",
        username_env="TEST_DEVINCI_USERNAME",
        password_env="TEST_DEVINCI_PASSWORD",
    )

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = DevinciScraper(brand_config, browser)
            try:
                with pytest.raises(RuntimeError):
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


def test_extract_stock_items_from_page_matches_closeout_description_markup(tmp_path):
    # Confirmed against a real Closeout grid page pasted from a live
    # session: unlike In Season's disabled "aspNetDisabled
    # DescriptionSansLien" anchor, Closeout's description is a real,
    # clickable link with class "Description" instead - only the shared
    # "_MyHyperlink" id suffix is common to both, which is what
    # _extract_stock_items_from_page() now matches on. Also confirms the
    # field-name pattern's order-type code (LIQU here, vs GAMM on In
    # Season) and the onchange's order-type token (LIQUID vs REPEAT)
    # don't affect parsing - same synthetic-minimal-page approach as
    # test_extract_stock_items_treats_coincident_periods_as_in_stock.
    page_path = tmp_path / "closeout_description_markup.html"
    page_path.write_text(
        """
        <html><head><meta charset="utf-8"></head><body><form>
        <input type="hidden" name="txtDateDébutLivraison1" value="2026-08-01">
        <input type="hidden" name="txtDateDébutLivraison2" value="2026-08-16">
        <table id="RadGrid1_ctl00"><tbody>
        <tr class="rgRow" id="RadGrid1_ctl00__0">
        <td class="rgGroupCol">&nbsp;</td>
        <td class="ItemStyle">FC22044-01</td>
        <td class="ItemStyle"><a id="RadGrid1_ctl00_ctl12_MyHyperlink" class="Description" href="http://www.devinci.com/bikes/item_FC220440" target="_blank">Frameset Spartan Carbon - Blue Secret</a></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl12$txtprice6_FC22044_01__LIQU_2022_1" value="4389"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl12$txtXS1_FC22044_01__LIQU_2022_1"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl12$txtS1_FC22044_01__LIQU_2022_1"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl12$txtM1_FC22044_01__LIQU_2022_1"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl12$txtL1_FC22044_01__LIQU_2022_1"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl12$txtXL1_FC22044_01__LIQU_2022_1" onchange="ValiderQty(this.value,1,'1',this,'LIQUID','1')"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl12$txtXS2_FC22044_01__LIQU_2022_1"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl12$txtS2_FC22044_01__LIQU_2022_1"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl12$txtM2_FC22044_01__LIQU_2022_1"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl12$txtL2_FC22044_01__LIQU_2022_1"></td>
        <td><input type="text" name="RadGrid1$ctl00$ctl12$txtXL2_FC22044_01__LIQU_2022_1" onchange="ValiderQty(this.value,1,'1',this,'LIQUID','2')"></td>
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
    assert all(i.product_title == "Frameset Spartan Carbon" for i in items)
    assert all(i.color == "Blue Secret" for i in items)
    xl = by_size["XL"]
    assert (xl.status, xl.quantity, xl.eta_date) == (StockStatus.IN_STOCK, 1, None)
    assert by_size["XS"].status == StockStatus.OUT_OF_STOCK


def test_load_devinci_items(tmp_path):
    csv_path = tmp_path / "devinci_items.csv"
    csv_path.write_text(
        "System ID,Manufact. SKU,Description\n"
        "1,FV27105-32,Bike Spartan MX GX AXS Deep Olive\n"
        "2,,No Sku On File\n"
    )
    rows = _load_devinci_items(csv_path)
    assert rows == [
        {
            "system_id": "1",
            "manufacturer_sku": "FV27105-32",
            "item_description": "Bike Spartan MX GX AXS Deep Olive",
        },
        {
            "system_id": "2",
            "manufacturer_sku": "",
            "item_description": "No Sku On File",
        },
    ]


def _make_item(sku, size, status, quantity=None, eta_date=None) -> StockItem:
    return StockItem(
        brand="Devinci",
        sku=sku,
        product_title="Test Bike",
        size=size,
        status=status,
        quantity=quantity,
        eta_date=eta_date,
    )


def test_report_status_and_eta_in_stock():
    item = _make_item("SKU", "M", StockStatus.IN_STOCK, quantity=9)
    assert _report_status_and_eta(item) == ("Available (9)", "Now")


def test_report_status_and_eta_pre_order():
    item = _make_item("SKU", "M", StockStatus.PRE_ORDER, quantity=15, eta_date="2026-08-16")
    assert _report_status_and_eta(item) == ("pre-order", "2026-08-16")


def test_report_status_and_eta_out_of_stock():
    item = _make_item("SKU", "XS", StockStatus.OUT_OF_STOCK, quantity=0)
    assert _report_status_and_eta(item) == ("Out of Stock", "N/A")


def test_build_availability_report_expands_one_row_per_size():
    # Same shape as the real FV27105-32 example the user confirmed live.
    items = [
        _make_item("FV27105-32", "XS", StockStatus.OUT_OF_STOCK, quantity=0),
        _make_item("FV27105-32", "S", StockStatus.IN_STOCK, quantity=9),
        _make_item(
            "FV27105-32", "M", StockStatus.PRE_ORDER, quantity=15, eta_date="2026-08-16"
        ),
        _make_item(
            "FV27105-32", "L", StockStatus.PRE_ORDER, quantity=12, eta_date="2026-08-16"
        ),
        _make_item("FV27105-32", "XL", StockStatus.IN_STOCK, quantity=5),
    ]
    rows = [
        {
            "system_id": "1",
            "manufacturer_sku": "FV27105-32",
            "item_description": "Bike Spartan MX GX AXS Deep Olive",
        },
        {"system_id": "2", "manufacturer_sku": "", "item_description": "No Sku On File"},
        {
            "system_id": "3",
            "manufacturer_sku": "NOT-FOUND-SKU",
            "item_description": "Missing Bike",
        },
    ]

    report_rows = build_availability_report(items, rows)

    matched = [r for r in report_rows if r["System ID"] == "1"]
    assert [r["Description"] for r in matched] == [
        "Bike Spartan MX GX AXS Deep Olive - XS",
        "Bike Spartan MX GX AXS Deep Olive - S",
        "Bike Spartan MX GX AXS Deep Olive - M",
        "Bike Spartan MX GX AXS Deep Olive - L",
        "Bike Spartan MX GX AXS Deep Olive - XL",
    ]
    assert [r["Status"] for r in matched] == [
        "Out of Stock",
        "Available (9)",
        "pre-order",
        "pre-order",
        "Available (5)",
    ]
    assert [r["ETA"] for r in matched] == ["N/A", "Now", "2026-08-16", "2026-08-16", "Now"]

    # No Manufacturer SKU on file at all is treated as discontinued (per
    # the user), and isn't expanded/appended since there's nothing to
    # look up.
    no_sku = next(r for r in report_rows if r["System ID"] == "2")
    assert (no_sku["Status"], no_sku["ETA"]) == ("Discontinued", "N/A")
    assert no_sku["Description"] == "No Sku On File"

    not_found = next(r for r in report_rows if r["System ID"] == "3")
    assert (not_found["Status"], not_found["ETA"]) == ("N/A", "N/A")


def test_generate_availability_report_uses_fetch_stock_and_expands_sizes(tmp_path, monkeypatch):
    csv_path = tmp_path / "devinci_items.csv"
    csv_path.write_text(
        "System ID,Manufact. SKU,Description\n"
        "1,FV27105-32,Bike Spartan MX GX AXS Deep Olive\n"
    )

    fake_items = [
        _make_item("FV27105-32", "S", StockStatus.IN_STOCK, quantity=9),
        _make_item("FV27105-32", "XL", StockStatus.IN_STOCK, quantity=5),
    ]

    brand_config = make_brand_config()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = DevinciScraper(brand_config, browser)
            monkeypatch.setattr(DevinciScraper, "fetch_stock", lambda self: fake_items)
            try:
                rows = scraper.generate_availability_report(csv_path)
            finally:
                scraper.close()
        finally:
            browser.close()

    assert [r["Status"] for r in rows] == ["Available (9)", "Available (5)"]
