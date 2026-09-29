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
    _closest_schedule_date,
    _decode_response_body,
    _load_devinci_items,
    _parse_size_from_description,
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


def test_parse_size_from_description():
    # Confirmed against the user's real 128-row inventory CSV: the size is
    # the leading letters of the description's last token, not a separate
    # column - covers every real shape seen (single wheel size, dual wheel
    # size, and a decimal second wheel size).
    assert _parse_size_from_description("Devinci Troy GX 12S Alien Blue S29") == "S"
    assert _parse_size_from_description("Devinci E-Troy Bosch Deore 12S Black XL29") == "XL"
    assert (
        _parse_size_from_description("Devinci E-Troy Bosch GX 12S Green Gold M29/27") == "M"
    )
    assert _parse_size_from_description("Devinci Troy ST Deore Gloss Black Dust XS27") == "XS"
    assert (
        _parse_size_from_description("Devinci Spartan GX AXS Gloss Deep Olive S29/27.5")
        == "S"
    )
    # "11S" mid-description must not be mistaken for a trailing size token.
    assert _parse_size_from_description("Devinci Kobain Deore 11S Navy M29") == "M"
    # No parseable trailing size token at all.
    assert _parse_size_from_description("No size token here") is None
    assert _parse_size_from_description("") is None


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


def test_fetch_stock_scrapes_both_in_season_and_closeout_grids():
    # Exercises fetch_stock() end-to-end against the real confirmed
    # Menu.aspx markup (each order type link's href Type= GUID) and both
    # real grid fixtures it lands on in turn - see tests/fixtures/
    # devinci_login/landing.html for how the clicks are simulated (a
    # stand-in WebForm_DoPostBackWithOptions routing by GUID, not the real
    # portal) and tests/fixtures/devinci_product/achats_treeview_closeout.html
    # for the real Closeout SKU (FC22044-01) this confirms gets combined
    # with the In Season grid's items - per the user, their real inventory
    # has SKUs that only exist in Closeout, which is exactly what got
    # missed before fetch_stock() scraped both.
    brand_config = make_brand_config()
    menu_url = (
        (Path(__file__).parent / "fixtures" / "devinci_login" / "landing.html")
        .resolve()
        .as_uri()
    )
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = DevinciScraper(brand_config, browser)
            try:
                scraper._menu_url = menu_url
                items = scraper.fetch_stock()
            finally:
                scraper.close()
        finally:
            browser.close()

    skus = {i.sku for i in items}
    assert "FV27122-21" in skus  # from the In Season grid fixture
    assert "FE26100-11" in skus  # from the In Season grid fixture
    assert "FC22044-01" in skus  # from the Closeout grid fixture


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


def test_closest_schedule_date_picks_earliest_of_multiple_dates():
    # Confirmed against a real captured Achats_Cedule.aspx response body (a
    # "PRODUCTION SCHEDULE" QUANTITY/AVAILABLE ON table) that listed two
    # future batches for one size: 3 units arriving 2027-01-05, and 5 more
    # arriving 2027-05-06 - per the user, the closest (earliest) date is the
    # one to report as the ETA.
    html = """
    <table>
      <tr><th>QUANTITY</th><th>AVAILABLE ON</th></tr>
      <tr><td>5</td><td>2027-05-06</td></tr>
      <tr><td>3</td><td>2027-01-05</td></tr>
    </table>
    """
    assert _closest_schedule_date(html) == "2027-01-05"


def test_closest_schedule_date_returns_none_when_no_dates():
    assert _closest_schedule_date("<table><tr><td>No batches scheduled</td></tr></table>") is None


def test_decode_response_body_falls_back_to_windows_1252():
    # Confirmed live: Achats_Cedule.aspx's response body has raw
    # Windows-1252 bytes for accented characters (e.g. b"\xe9" for "é" in
    # "Livraison prévue") with no charset in its Content-Type header -
    # decoding it as UTF-8 (as Playwright's own response.text() does)
    # raises UnicodeDecodeError, which is exactly what broke the live run.
    body = "Livraison prévue".encode("cp1252")
    assert _decode_response_body(body, "text/html") == "Livraison prévue"


def test_decode_response_body_prefers_declared_charset():
    body = "Livraison prévue".encode("cp1252")
    assert (
        _decode_response_body(body, "text/html; charset=windows-1252")
        == "Livraison prévue"
    )


def test_decode_response_body_handles_real_utf8():
    body = "Livraison prévue".encode("utf-8")
    assert _decode_response_body(body, "text/html; charset=utf-8") == "Livraison prévue"


def test_fetch_closest_schedule_date_decodes_windows_1252_response(monkeypatch):
    # Regression test for the live UnicodeDecodeError: the real response
    # isn't UTF-8, so the fetch must not crash (or silently mis-parse) on
    # non-UTF-8 bytes in its body.
    onmouseover = "OuvrirPopUp_Cedule(event,'FE26100322','MTL','CAN','desc','REPEAT')"

    class FakeResponse:
        ok = True
        headers = {"content-type": "text/html"}

        def body(self):
            return "<td>Livraison prévue: 2027-01-05</td>".encode("cp1252")

    brand_config = make_brand_config()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = DevinciScraper(brand_config, browser)
            try:
                scraper.page.goto(FIXTURE_PAGE)
                monkeypatch.setattr(scraper.page.request, "get", lambda url: FakeResponse())
                result = scraper._fetch_closest_schedule_date(onmouseover)
            finally:
                scraper.close()
        finally:
            browser.close()

    assert result == "2027-01-05"


def test_fetch_closest_schedule_date_builds_url_and_parses_response(monkeypatch):
    # Confirmed against a real captured request/response pair: hovering a
    # cell calls OuvrirPopUp_Cedule(event, MyItem, whse, eut, desc, type),
    # and _fetch_closest_schedule_date() must turn those same 5 args into a
    # GET to Achats_Cedule.aspx and return the earliest date in the reply.
    onmouseover = (
        "OuvrirPopUp_Cedule(event,'FE26100322','MTL','CAN',"
        "'Bike E-Spartan Lite Bosch SX Smart MX | GX AXS | Deep Olive','REPEAT')"
    )

    class FakeResponse:
        ok = True
        headers = {"content-type": "text/html; charset=utf-8"}

        def body(self):
            return b"<table><tr><td>2027-05-06</td></tr><tr><td>2027-01-05</td></tr></table>"

    captured_urls = []

    def fake_get(url):
        captured_urls.append(url)
        return FakeResponse()

    brand_config = make_brand_config()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = DevinciScraper(brand_config, browser)
            try:
                scraper.page.goto(FIXTURE_PAGE)
                monkeypatch.setattr(scraper.page.request, "get", fake_get)
                result = scraper._fetch_closest_schedule_date(onmouseover)
            finally:
                scraper.close()
        finally:
            browser.close()

    assert result == "2027-01-05"
    assert len(captured_urls) == 1
    url = captured_urls[0]
    assert "Achats_Cedule.aspx" in url
    assert "MyItem=FE26100322" in url
    assert "whse=MTL" in url
    assert "eut=CAN" in url
    assert "type=REPEAT" in url


def test_fetch_closest_schedule_date_returns_none_on_non_ok_response(monkeypatch, caplog):
    # A non-2xx response (e.g. a session/auth problem) shouldn't be parsed
    # as if it were a schedule table - it should be treated as a failure
    # and logged with enough detail (status + body) to diagnose live.
    onmouseover = "OuvrirPopUp_Cedule(event,'FE26100322','MTL','CAN','desc','REPEAT')"

    class FakeResponse:
        ok = False
        status = 500
        headers = {}

        def body(self):
            return b"Internal Server Error"

    brand_config = make_brand_config()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = DevinciScraper(brand_config, browser)
            try:
                scraper.page.goto(FIXTURE_PAGE)
                monkeypatch.setattr(scraper.page.request, "get", lambda url: FakeResponse())
                with caplog.at_level("WARNING"):
                    result = scraper._fetch_closest_schedule_date(onmouseover)
            finally:
                scraper.close()
        finally:
            browser.close()

    assert result is None
    assert "HTTP 500" in caplog.text


def test_fetch_closest_schedule_date_returns_none_for_unrecognized_onmouseover():
    brand_config = make_brand_config()
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = DevinciScraper(brand_config, browser)
            try:
                scraper.page.goto(FIXTURE_PAGE)
                assert scraper._fetch_closest_schedule_date("not a real onmouseover") is None
            finally:
                scraper.close()
        finally:
            browser.close()


def test_extract_stock_items_from_page_uses_per_size_production_schedule(monkeypatch):
    # This fixture's page-wide period 2 start date is "2026-08-16" (the
    # older, less accurate fallback) - confirm a pre-order size's real
    # onmouseover args get routed into _fetch_closest_schedule_date() and
    # that its result (the real per-size ETA) wins over the fallback.
    brand_config = make_brand_config()
    seen_onmouseover = []

    def fake_fetch(self, onmouseover):
        seen_onmouseover.append(onmouseover)
        return "2027-01-05"

    monkeypatch.setattr(DevinciScraper, "_fetch_closest_schedule_date", fake_fetch)

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
    m = by_sku_size[("FV27105-32", "M")]
    assert (m.status, m.quantity, m.eta_date) == (StockStatus.PRE_ORDER, 15, "2027-01-05")
    l = by_sku_size[("FV27105-32", "L")]
    assert (l.status, l.quantity, l.eta_date) == (StockStatus.PRE_ORDER, 12, "2027-01-05")
    assert len(seen_onmouseover) == 2
    assert all("OuvrirPopUp_Cedule" in s for s in seen_onmouseover)


def test_extract_stock_items_from_page_falls_back_when_schedule_fetch_fails(monkeypatch):
    # If the schedule fetch can't be resolved (network error, unrecognized
    # markup, etc.) the page-wide period 2 start date is still better than
    # no ETA at all, so it stays as the fallback.
    brand_config = make_brand_config()
    monkeypatch.setattr(
        DevinciScraper, "_fetch_closest_schedule_date", lambda self, onmouseover: None
    )

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
    m = by_sku_size[("FV27105-32", "M")]
    assert (m.status, m.quantity, m.eta_date) == (StockStatus.PRE_ORDER, 15, "2026-08-16")


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
    # Per the user: an available item doesn't need its stock count shown.
    item = _make_item("SKU", "M", StockStatus.IN_STOCK, quantity=9)
    assert _report_status_and_eta(item) == ("Available", "Now")


def test_report_status_and_eta_pre_order():
    item = _make_item("SKU", "M", StockStatus.PRE_ORDER, quantity=15, eta_date="2026-08-16")
    assert _report_status_and_eta(item) == ("pre-order", "2026-08-16")


def test_report_status_and_eta_out_of_stock():
    item = _make_item("SKU", "XS", StockStatus.OUT_OF_STOCK, quantity=0)
    assert _report_status_and_eta(item) == ("Out of Stock", "N/A")


def test_build_availability_report_matches_by_sku_and_size():
    # Real CSV shape (per the user's live run): one input row per SKU+size
    # already, not one row per SKU to expand out - FV27105-32 here has 5
    # separate rows, one per size, same as their real config/devinci_items.csv.
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
            "item_description": "Bike Spartan MX GX AXS Deep Olive XS29",
        },
        {
            "system_id": "2",
            "manufacturer_sku": "FV27105-32",
            "item_description": "Bike Spartan MX GX AXS Deep Olive S29",
        },
        {
            "system_id": "3",
            "manufacturer_sku": "FV27105-32",
            "item_description": "Bike Spartan MX GX AXS Deep Olive M29",
        },
        {
            "system_id": "4",
            "manufacturer_sku": "FV27105-32",
            "item_description": "Bike Spartan MX GX AXS Deep Olive L29",
        },
        {
            "system_id": "5",
            "manufacturer_sku": "FV27105-32",
            "item_description": "Bike Spartan MX GX AXS Deep Olive XL29",
        },
        {"system_id": "6", "manufacturer_sku": "", "item_description": "No Sku On File M29"},
        {
            "system_id": "7",
            "manufacturer_sku": "NOT-FOUND-SKU",
            "item_description": "Missing Bike S29",
        },
        {
            "system_id": "8",
            "manufacturer_sku": "FV27105-32",
            "item_description": "No parseable size here",
        },
    ]

    report_rows = build_availability_report(items, rows)

    # Exactly one output row per input row - not one per scraped size.
    assert len(report_rows) == len(rows)

    by_id = {r["System ID"]: r for r in report_rows}
    assert (by_id["1"]["Size"], by_id["1"]["Status"], by_id["1"]["ETA"]) == (
        "XS",
        "Out of Stock",
        "N/A",
    )
    assert (by_id["2"]["Size"], by_id["2"]["Status"], by_id["2"]["ETA"]) == (
        "S",
        "Available",
        "Now",
    )
    assert (by_id["3"]["Size"], by_id["3"]["Status"], by_id["3"]["ETA"]) == (
        "M",
        "pre-order",
        "2026-08-16",
    )
    assert (by_id["4"]["Size"], by_id["4"]["Status"], by_id["4"]["ETA"]) == (
        "L",
        "pre-order",
        "2026-08-16",
    )
    assert (by_id["5"]["Size"], by_id["5"]["Status"], by_id["5"]["ETA"]) == (
        "XL",
        "Available",
        "Now",
    )
    # Descriptions are passed through as-is now (they already carry their
    # own size), not rewritten with a "- SIZE" suffix.
    assert by_id["1"]["Description"] == "Bike Spartan MX GX AXS Deep Olive XS29"

    # No Manufacturer SKU on file at all is treated as discontinued (per
    # the user).
    assert (by_id["6"]["Status"], by_id["6"]["ETA"]) == ("Discontinued", "N/A")
    assert by_id["6"]["Size"] == "M"

    # SKU present but not in the scraped catalog at all.
    assert (by_id["7"]["Status"], by_id["7"]["ETA"]) == ("N/A", "N/A")

    # SKU present and scraped, but this row's own description has no
    # parseable size suffix - can't match a specific size, so N/A rather
    # than guessing.
    assert (by_id["8"]["Status"], by_id["8"]["ETA"], by_id["8"]["Size"]) == ("N/A", "N/A", "")


def test_generate_availability_report_uses_fetch_stock_and_matches_size(tmp_path, monkeypatch):
    csv_path = tmp_path / "devinci_items.csv"
    csv_path.write_text(
        "System ID,Manufact. SKU,Description\n"
        "1,FV27105-32,Bike Spartan MX GX AXS Deep Olive S29\n"
        "2,FV27105-32,Bike Spartan MX GX AXS Deep Olive XL29\n"
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

    assert [r["Status"] for r in rows] == ["Available", "Available"]
