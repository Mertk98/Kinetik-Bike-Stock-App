from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urljoin

from kinetik_stock.config import REPO_ROOT
from kinetik_stock.models import StockItem, StockStatus
from kinetik_stock.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Confirmed against the real login page (transac.devinci.com's landing page,
# pasted directly from a live session): a plain 2-field ASP.NET WebForms
# login (id="Login"), not a 3-field one like Norco's. The fields' name/id
# are the same ("txtLogin"/"txtPassword"), and the submit control is an
# <input type="submit"> (not a <button>), id="cmdLogin". There's also a
# language RadComboBox (cmdLangue, defaulting to "Français") with its own
# "OK" button (cmdLangueOk) - unrelated to login, since txtLogin/txtPassword
# are already present and fillable on this same page without touching it.
USERNAME_SELECTOR = "#txtLogin"
PASSWORD_SELECTOR = "#txtPassword"
LOGIN_BUTTON_SELECTOR = "#cmdLogin"
# Confirmed against a real captured post-login page (Menu.aspx, pasted from
# a live session): a "LOGOUT" link, id="cmdFermer" - this IS the page
# login() itself lands on right after submitting, not just an assumption
# carried over from the order-grid pages (which also have it).
LOGGED_IN_SELECTOR = "#cmdFermer"
LOGGED_IN_CHECK_TIMEOUT_MS = 15000

# Confirmed against that same real Menu.aspx capture: the landing page's own
# URL/form action already carries a per-session "no=" value (e.g.
# "Menu.aspx?no=972838468111874") - it's assigned once at login, not
# discovered via some other page. The menu then links to each order type's
# Achats_Treeview.aspx grid by reusing that exact same no= alongside a
# fixed, order-type-specific Type= GUID (In Season and Closeout confirmed
# to match the GUIDs already recorded from captured grid pages; Parts and
# Rental Orders are new - their own grid HTML hasn't been captured, so
# their layout isn't confirmed to match). This resolves the previous
# uncertainty about no= and about whether Menu.aspx was a required
# intermediate step - it is.
IN_SEASON_TYPE_GUID = "D17A9733897C4B088F16E046997B00B6"
CLOSEOUT_TYPE_GUID = "78A8E3551F714CD1A68AF58EDAA50E8C"
PARTS_TYPE_GUID = "7DB3E2BE432C441E8D46167CFDBB1FD9"
RENTAL_ORDERS_TYPE_GUID = "561820CE46BC40A0A7DE2CEB0372A8B7"

# Confirmed: each menu link's href fires WebForm_DoPostBackWithOptions(...)
# with the target Achats_Treeview.aspx URL (no=/Type=) baked into that href
# as a literal "javascript:..." string (onclick on the same link is just
# skm_LockScreen(...), an unrelated loading-overlay effect) - fetch_stock()
# below just clicks it and lets the real page handle the postback/
# navigation, rather than trying to construct and GET that URL directly
# (untested whether that would even work, since the real link submits a
# POST carrying Menu.aspx's own viewstate/eventtarget). Matching by href
# substring rather than link text also confirmed necessary: there's a
# second, differently-cased "IN SEASON(closed)" link (a distinct,
# non-navigating control, no Type= GUID in its href at all) that a
# text-based selector could collide with.
IN_SEASON_MENU_LINK_SELECTOR = f"a[href*='{IN_SEASON_TYPE_GUID}']"
CLOSEOUT_MENU_LINK_SELECTOR = f"a[href*='{CLOSEOUT_TYPE_GUID}']"
ORDER_GRID_LOAD_TIMEOUT_MS = 15000

# Confirmed live: the user's own inventory (config/devinci_items.csv) isn't
# limited to current-season bikes - several of their real SKUs (e.g.
# FV23043-A5, FE23089-31, FV25110-22, FV26110-26) only showed up in a real
# Closeout grid capture, not the In Season one, and came back "not found in
# the scraped catalog" when fetch_stock() only visited In Season. So
# fetch_stock() below scrapes both grids and combines their items - the
# user's dealer stock spans both order types, not just one.
FETCH_STOCK_MENU_LINK_SELECTORS = [IN_SEASON_MENU_LINK_SELECTOR, CLOSEOUT_MENU_LINK_SELECTOR]

# Confirmed against a real order/booking page (Achats_Treeview.aspx, "In
# Season" order type) pasted directly from a live dealer session. Devinci's
# portal isn't a simple stock-lookup page like Norco/Transition - it's a
# dealer *booking* grid (RadGrid1) where every bike model/color is one row
# and each of the 5 sizes (XS/S/M/L/XL) is its own quantity <input> cell, x2
# "periods" (delivery windows) side by side. Confirmed field-name pattern
# for each cell, e.g. "RadGrid1$ctl00$ctl10$txtM1_FE26100_11__GAMM_2027_1":
# always "txt<SIZE><PERIOD>_<SKU with '-' replaced by '_'>__<n>", where <n>
# is an order-type/model-year code that varies (confirmed "GAMM_2027" on
# In Season, "LIQU_2025"/"LIQU_2026"/etc. on Closeout - a real Closeout
# grid capture confirms this whole cell/field-name pattern is identical
# across order types, just with a different code here). The "ctlNN" segment
# is a per-row ASP.NET control index with no fixed value, so cells are
# matched by this field-name pattern, not position.
QTY_FIELD_RE = re.compile(r"\$txt(?P<size>XS|XL|S|M|L)(?P<period>[12])_")

# Confirmed: an orderable cell's onchange carries the *actual* max orderable
# quantity as its 2nd argument, e.g. onchange="ValiderQty(this.value,25,'1',
# this,'REPEAT','1')" - even when the on-page label is capped to the
# portal's own "10+" display (span class="PoliceGrandeurslbl">&nbsp;10+"),
# confirmed on the same cell (maxQty=25, label "10+"). So the exact count is
# read from onchange, not the (possibly capped) label text.
MAX_QTY_RE = re.compile(r"ValiderQty\(this\.value,\s*(\d+)")

# A cell has zero quantity for its period whenever it isn't a live,
# orderable white cell with an onchange handler - whether that's a
# disabled black "SOLD OUT" cell, or a gray cell with neither onchange nor
# disabled at all. Confirmed these are NOT the same as "this size doesn't
# exist for this model": on a real row (FV27105-32 "Bike Spartan | MX GX
# AXS | Deep Olive"), M1/L1 are gray/zero (no stock *now*) while M2/L2 are
# live orderable cells (15/12 units of *future production*) - so a gray
# period-1 cell just means nothing in that period, not that the size is
# unavailable altogether. Confirmed live: S1=9/XL1=5 (in stock now) and
# M1=L1=0 with M2=15/L2=12 (future production only, no stock now).
ROW_SELECTOR = "tr.rgRow, tr.rgAltRow"

# Confirmed: period 1 is "In Stock Now" (hidden fields txtDateDébutLivraison1/
# txtDateFinLivraison1), period 2 is "Future production" (txtDateDébut
# Livraison2/txtDateFinLivraison2) - a later, separate delivery window.
# Confirmed there are only 3 statuses (per the user, who has live access to
# the portal): a size is IN_STOCK if period 1 has any quantity; otherwise
# PRE_ORDER if period 2 has any quantity (ETA - see PRODUCTION_SCHEDULE_PATH
# below, not this page-wide field - it's only a fallback now); otherwise
# OUT_OF_STOCK (no current or future availability at all) - this covers
# both "sold out" and "this size isn't offered for this model".
#
# Per the user: the two periods' start dates can coincide (period 2 isn't
# always later than period 1) - when they do, period 2 isn't really
# "future" at all, so any quantity there counts as IN_STOCK too, not
# PRE_ORDER, and gets no ETA.
PERIOD_1_START_DATE_FIELD = "txtDateDébutLivraison1"
PERIOD_2_START_DATE_FIELD = "txtDateDébutLivraison2"

# Per the user: this page-wide period-2 start date is NOT the real ETA for
# a pre-order size - it's just a page-level summary field. The real,
# per-(SKU, size) ETA comes from a "PRODUCTION SCHEDULE" the real page
# fetches on hover (OuvrirPopUp_Cedule) from a separate page,
# Achats_Cedule.aspx?MyItem=<code>&whse=<whse>&eut=<country>&desc=<desc>
# &type=<order type token> - confirmed against a real captured
# request/response pair. Every orderable cell's onmouseover carries these
# exact args as OuvrirPopUp_Cedule(event, MyItem, whse, eut, desc, type) -
# both periods' cells for the same size carry identical args (the
# schedule doesn't depend on period), so either one works. The response is
# a small HTML page with a "QUANTITY"/"AVAILABLE ON" table listing every
# upcoming batch (confirmed: a size can have more than one, e.g. 3 units
# 2027-01-05 and 5 units 2027-05-06) - per the user, the CLOSEST
# (earliest) date is the one to report.
ONMOUSEOVER_CEDULE_RE = re.compile(
    r"OuvrirPopUp_Cedule\(event,\s*'([^']*)',\s*'([^']*)',\s*'([^']*)',\s*'([^']*)',\s*'([^']*)'\)"
)
SCHEDULE_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
PRODUCTION_SCHEDULE_PATH = "Achats_Cedule.aspx"


def _closest_schedule_date(html: str) -> Optional[str]:
    dates = SCHEDULE_DATE_RE.findall(html)
    return min(dates) if dates else None


CHARSET_RE = re.compile(r"charset=([\w-]+)", re.IGNORECASE)


def _decode_response_body(body: bytes, content_type: str) -> str:
    # Confirmed live: Achats_Cedule.aspx's response isn't UTF-8 (it has raw
    # Windows-1252 bytes for accented French characters, e.g. \xe9 for
    # "é") - unlike the main app pages, which do declare UTF-8. Playwright's
    # response.text() always assumes UTF-8 and raises UnicodeDecodeError on
    # it, so this decodes the raw bytes ourselves: try the server-declared
    # charset if any, then UTF-8, then Windows-1252 (the common default for
    # legacy ASP.NET sites), falling back to lossy UTF-8 as a last resort.
    charset_match = CHARSET_RE.search(content_type)
    candidates = [charset_match.group(1)] if charset_match else []
    candidates += ["utf-8", "cp1252"]
    for encoding in candidates:
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


SIZE_ORDER = ["XS", "S", "M", "L", "XL"]

# Confirmed against a fuller real capture (84 SKUs / 420 size-rows, not just
# the 4-SKU trimmed fixture) that "<model/build> | <component spec> |
# <color>" (e.g. "Bike Wilson 40 | GX DH | Sepia Green") is NOT universal:
#   - Frameset-only SKUs use " - " instead, e.g. "Frameset Spartan - Greige"
#     - still an unambiguous last-segment-is-color split, just a different
#     separator.
#   - Some SKUs (e.g. "Bike Milano 2 AL13", "Bike Ewoc 20" 7s Matcha") have
#     no delimiter at all, so there's no reliable way to tell a color from a
#     trailing spec/size token - these fall through to (description, None),
#     same as always.
def split_description(description: str) -> tuple[str, Optional[str]]:
    if "|" in description:
        parts = [p.strip() for p in description.split("|")]
        return " | ".join(parts[:-1]), parts[-1]
    if " - " in description:
        title, _, color = description.rpartition(" - ")
        return title.strip(), color.strip()
    return description.strip(), None


# Confirmed against the user's real 128-row inventory CSV: unlike the
# assumption this module started with, each row is already one specific
# SKU+size, not one row per SKU covering all 5 sizes - the same
# Manufact. SKU repeats across several rows, one per size, and the size
# itself isn't a separate column but the leading letters of the
# description's last whitespace-separated token, e.g. "S29" -> S,
# "M29/27" -> M, "XL29" -> XL, "XS27" -> XS, "S29/27.5" -> S (a decimal
# second wheel-size number). Verified this pattern covers all 128 real
# rows with no exceptions.
SIZE_SUFFIX_RE = re.compile(r"^(XS|XL|S|M|L)\d+(?:/\d+(?:\.\d+)?)?$")


def _parse_size_from_description(description: str) -> Optional[str]:
    tokens = description.strip().split()
    if not tokens:
        return None
    match = SIZE_SUFFIX_RE.match(tokens[-1])
    return match.group(1) if match else None


# config/devinci_items.csv is the user's own inventory export (same idea as
# Norco's config/norco_items.csv, and confirmed against the user's real
# file to use the same 3 column headers as Norco's: "System ID",
# "Manufact. SKU", "Description") - one row per SKU+size already (see
# _parse_size_from_description above), not one row per SKU to expand out,
# so build_availability_report() below matches each input row against the
# one scraped StockItem for that exact (SKU, size) pair, producing exactly
# one output row per input row - not one per scraped size, which is what
# this module originally (and wrongly) assumed and caused duplicate rows
# for every size of a SKU on every one of that SKU's own input rows.
DEVINCI_ITEMS_CSV = REPO_ROOT / "config" / "devinci_items.csv"
DEVINCI_REPORT_FIELDNAMES = [
    "System ID",
    "Manufact. SKU",
    "Description",
    "Size",
    "Status",
    "ETA",
]


def _load_devinci_items(path: Path = DEVINCI_ITEMS_CSV) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found - add a CSV with 'System ID', 'Manufact. SKU', "
            "'Description' columns (the user's own inventory export)."
        )
    with open(path, newline="", encoding="utf-8") as f:
        rows = [
            {
                "system_id": row["System ID"].strip(),
                "manufacturer_sku": (row.get("Manufact. SKU") or "").strip(),
                "item_description": row["Description"].strip(),
            }
            for row in csv.DictReader(f)
        ]
    if not rows:
        raise ValueError(f"{path} has no rows.")
    return rows


def write_devinci_report_csv(rows: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DEVINCI_REPORT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def _report_status_and_eta(item: StockItem) -> tuple[str, str]:
    """Formats a scraped StockItem into the same wording style as Norco's
    own report (_report_status_and_eta in norco.py), for consistency across
    the user's per-brand reports. Unlike Norco, Devinci's quantity is never
    capped (onchange gives the real count, not a "10+" display value), so
    the exact number is always shown.
    """
    if item.status == StockStatus.IN_STOCK:
        return f"Available ({item.quantity})", "Now"
    if item.status == StockStatus.PRE_ORDER:
        return "pre-order", item.eta_date or "N/A"
    return "Out of Stock", "N/A"


def build_availability_report(items: list[StockItem], rows: list[dict]) -> list[dict]:
    """Pure function: matches each input row's (Manufacturer SKU, size) -
    the size parsed from the row's own Description, see
    _parse_size_from_description() above - against the one already-scraped
    StockItem for that exact pair, producing exactly one report row per
    input row. Kept separate from generate_availability_report() so it's
    testable without needing a working fetch_stock() (not yet confirmed
    live - see the class docstring).
    """
    items_by_sku_and_size: dict[tuple[str, Optional[str]], StockItem] = {
        (item.sku, item.size): item for item in items
    }

    report_rows: list[dict] = []
    for row in rows:
        system_id = row["system_id"]
        sku = row["manufacturer_sku"]
        description = row["item_description"]
        size = _parse_size_from_description(description)

        if not sku:
            # Per the user: no item number on file at all means the bike is
            # considered discontinued, unlike a SKU that's present but
            # wasn't found in the scraped catalog below (reported as N/A).
            report_rows.append(
                {
                    "System ID": system_id,
                    "Manufact. SKU": sku,
                    "Description": description,
                    "Size": size or "",
                    "Status": "Discontinued",
                    "ETA": "N/A",
                }
            )
            continue

        matched = items_by_sku_and_size.get((sku, size)) if size else None
        if matched is None:
            logger.warning(
                "SKU %s size %s (%r) wasn't found in the scraped catalog.",
                sku,
                size,
                description,
            )
            report_rows.append(
                {
                    "System ID": system_id,
                    "Manufact. SKU": sku,
                    "Description": description,
                    "Size": size or "",
                    "Status": "N/A",
                    "ETA": "N/A",
                }
            )
            continue

        status_text, eta_text = _report_status_and_eta(matched)
        report_rows.append(
            {
                "System ID": system_id,
                "Manufact. SKU": sku,
                "Description": description,
                "Size": size,
                "Status": status_text,
                "ETA": eta_text,
            }
        )

    return report_rows


class DevinciScraper(BaseScraper):
    """Scraper for Devinci's B2B dealer portal ("SITE TRANSACTIONNEL").

    fetch_stock()'s row/cell parsing (_extract_stock_items_from_page) is
    confirmed against a real order grid page (Achats_Treeview.aspx, "In
    Season" order type) pasted from a live dealer session, including the
    exact in-stock/pre-order/out-of-stock split confirmed against a live
    row by the user - see the module-level comments above for exactly
    what's confirmed.

    Confirmed real portal domain: transac.devinci.com (previously a
    placeholder). This sandbox's network policy blocks outbound access to
    that host entirely (403 at the proxy, not the site), so nothing below
    could be verified live from here - it's recorded from URLs the user
    shared, not from fetching them.

    Confirmed there's more than one order type reachable this way, each
    with its own Type= GUID in the Achats_Treeview.aspx URL:
      - "In Season": Type=D17A9733897C4B088F16E046997B00B6.
      - "Closeout": Type=78A8E3551F714CD1A68AF58EDAA50E8C.
    A real Closeout grid page has now also been captured (pasted from a
    live session), confirming fetch_stock()'s row/cell parsing below works
    unchanged across both order types: same RadGrid1 layout (5 sizes x 2
    periods), same disabled/gray-cell and "10+"-cap semantics, same
    txt<SIZE><PERIOD>_<SKU>__<code> field-name pattern (just a different
    <code> - LIQU on Closeout vs GAMM on In Season) and the same onchange=
    "ValiderQty(...)" real-quantity convention (order-type token in that
    call differs too - LIQUID vs REPEAT - but parsing doesn't depend on
    it). One real difference this capture surfaced and fixed: the
    description <a>'s class differs (In Season: "aspNetDisabled
    DescriptionSansLien", not clickable; Closeout: "Description", a real
    product-page link) - both share the id suffix "_MyHyperlink", which is
    what description_el is now matched on below instead of a class name.

    login() is implemented against a real captured login page - a plain
    2-field ASP.NET WebForms form (txtLogin/txtPassword/cmdLogin), unlike
    Norco's 3-field one. Confirmed working live by the user (this sandbox
    itself still can't reach transac.devinci.com - network policy).

    fetch_stock() clicks through Menu.aspx (the confirmed post-login
    landing page) to each order type's grid in turn - In Season, then
    Closeout - and combines their items, matching each input CSV row
    against the exact (SKU, size) pair scraped. Confirmed necessary live:
    the user's own inventory spans both order types, not just In Season -
    several real SKUs only exist in the Closeout catalog. Also confirmed
    live: the real login()/fetch_stock() run wrote a report, but with two
    bugs now fixed - see build_availability_report()'s docstring for the
    (SKU, size) matching fix (was wrongly expanding every input row to
    every scraped size of its SKU) and the module comment above
    DEVINCI_ITEMS_CSV for why the input CSV needed reinterpreting as
    already one row per size, not one row per SKU.

    Keep `enabled: false` in config/brands.yaml until the user re-runs this
    live and confirms the Closeout+size-matching fixes above actually
    solved the duplicate-rows and missing-Closeout-SKUs problems seen on
    their first real run.

    generate_availability_report() reproduces the user's own report format
    (System ID, Manufact. SKU, Description, Status, ETA), like
    NorcoScraper.generate_availability_report() - see build_availability_report()
    above for the matching/expansion logic, which is independently testable
    without a working fetch_stock().
    """

    def login(self) -> None:
        self.page.goto(self.brand_config.portal_url)
        self.page.fill(USERNAME_SELECTOR, self.brand_config.username or "")
        self.page.fill(PASSWORD_SELECTOR, self.brand_config.password or "")
        self.page.click(LOGIN_BUTTON_SELECTOR)

        try:
            self.page.wait_for_selector(
                LOGGED_IN_SELECTOR, state="attached", timeout=LOGGED_IN_CHECK_TIMEOUT_MS
            )
        except Exception:
            raise RuntimeError(
                f"Login to {self.brand_config.name} failed (no logout link found) - "
                f"check {self.brand_config.username_env}/{self.brand_config.password_env}."
            )

        # Menu.aspx's own URL carries the per-session no= value (see the
        # module comment above IN_SEASON_TYPE_GUID) - fetch_stock() revisits
        # this exact URL before clicking each order type's link, since
        # clicking one navigates away from the menu entirely.
        self._menu_url = self.page.url

    def fetch_stock(self) -> list[StockItem]:
        items: list[StockItem] = []
        for menu_link_selector in FETCH_STOCK_MENU_LINK_SELECTORS:
            self.page.goto(self._menu_url)
            self.page.click(menu_link_selector)
            self.page.wait_for_selector(
                f"input[name='{PERIOD_1_START_DATE_FIELD}']",
                state="attached",
                timeout=ORDER_GRID_LOAD_TIMEOUT_MS,
            )
            items.extend(self._extract_stock_items_from_page())
        return items

    def generate_availability_report(
        self, input_csv: Path = DEVINCI_ITEMS_CSV
    ) -> list[dict]:
        """Produces the user's own report format: the input CSV's 3 columns
        (System ID, Manufact. SKU, Description) plus a parsed-out Size
        column and Status/ETA, one output row per input row (see
        build_availability_report()'s docstring for the (SKU, size)
        matching this depends on). login() has been confirmed live by the
        user; fetch_stock() is implemented but its Closeout+size-matching
        fixes haven't been re-confirmed live yet (see the class docstring)
        - build_availability_report() has the matching logic and is tested
        directly against fixture data, independent of the two.
        """
        items = self.fetch_stock()
        rows = _load_devinci_items(input_csv)
        return build_availability_report(items, rows)

    def _extract_stock_items_from_page(self) -> list[StockItem]:
        source_url = self.page.url
        period_1_start = self._hidden_field_value(PERIOD_1_START_DATE_FIELD)
        period_2_start = self._hidden_field_value(PERIOD_2_START_DATE_FIELD)
        # If the periods share a start date, period 2 isn't a future window.
        periods_coincide = period_1_start is not None and period_1_start == period_2_start

        items: list[StockItem] = []
        for row in self.page.query_selector_all(ROW_SELECTOR):
            cells = row.query_selector_all("td")
            if len(cells) < 3:
                continue  # not a product row (shouldn't happen for rgRow/rgAltRow)

            sku = cells[1].inner_text().strip()
            description_el = row.query_selector("a[id$='_MyHyperlink']")
            description = description_el.inner_text().strip() if description_el else ""
            product_title, color = split_description(description)

            retail_price = self._retail_price(row)

            # One quantity per size per period, defaulting to 0 - a size
            # always has all 10 (5 sizes x 2 periods) cells present in the
            # real grid, whether or not they're orderable.
            period_qty = {size: {"1": 0, "2": 0} for size in SIZE_ORDER}
            # An orderable cell's onmouseover carries the args
            # OuvrirPopUp_Cedule(event, MyItem, whse, eut, desc, type) needed to
            # fetch that size's real per-item production schedule - both
            # periods' cells for a size carry identical args, so either is fine.
            size_onmouseover: dict[str, str] = {}
            for qty_input in row.query_selector_all("input[type='text']"):
                name = qty_input.get_attribute("name") or ""
                match = QTY_FIELD_RE.search(name)
                if not match:
                    continue  # a price field, not a size/period quantity cell

                size = match.group("size")
                period = match.group("period")
                disabled = qty_input.get_attribute("disabled") is not None
                onchange = qty_input.get_attribute("onchange")

                if disabled or not onchange:
                    continue  # zero for this period - stays at the 0 default

                qty_match = MAX_QTY_RE.search(onchange)
                period_qty[size][period] = int(qty_match.group(1)) if qty_match else 0

                onmouseover = qty_input.get_attribute("onmouseover")
                if onmouseover:
                    size_onmouseover[size] = onmouseover

            for size in SIZE_ORDER:
                now_qty = period_qty[size]["1"]
                future_qty = period_qty[size]["2"]

                if now_qty > 0:
                    status, quantity, eta_date = StockStatus.IN_STOCK, now_qty, None
                elif future_qty > 0:
                    if periods_coincide:
                        status, quantity, eta_date = StockStatus.IN_STOCK, future_qty, None
                    else:
                        # The page-wide period 2 start date is only a fallback -
                        # the real ETA is the closest date in this size's own
                        # production schedule (see PRODUCTION_SCHEDULE_PATH above).
                        eta_date = period_2_start
                        onmouseover = size_onmouseover.get(size)
                        if onmouseover:
                            fetched = self._fetch_closest_schedule_date(onmouseover)
                            if fetched:
                                eta_date = fetched
                        status, quantity = StockStatus.PRE_ORDER, future_qty
                else:
                    status, quantity, eta_date = StockStatus.OUT_OF_STOCK, 0, None

                items.append(
                    StockItem(
                        brand=self.brand_config.name,
                        sku=sku,
                        product_title=product_title,
                        variant=size,
                        size=size,
                        color=color,
                        status=status,
                        quantity=quantity,
                        regular_retail_price=retail_price,
                        eta_date=eta_date,
                        raw_status_text=f"in_stock_now={now_qty} future_production={future_qty}",
                        source_url=source_url,
                    )
                )

        items.sort(key=lambda i: (i.sku, SIZE_ORDER.index(i.size)))
        return items

    def _retail_price(self, row) -> Optional[float]:
        for price_input in row.query_selector_all("input[type='text']"):
            name = price_input.get_attribute("name") or ""
            if "$txtprice6_" in name:
                value = price_input.get_attribute("value")
                try:
                    return float(value) if value else None
                except ValueError:
                    logger.warning("Couldn't parse retail price %r from %r", value, name)
                    return None
        return None

    def _hidden_field_value(self, field_name: str) -> Optional[str]:
        field = self.page.query_selector(f"input[name='{field_name}']")
        return field.get_attribute("value") if field else None

    def _fetch_closest_schedule_date(self, onmouseover: str) -> Optional[str]:
        """Fetch Achats_Cedule.aspx for one size and return its closest date.

        Confirmed against a real captured request/response pair: the page
        fetches this itself on hover (OuvrirPopUp_Cedule), and a size's
        schedule can list more than one future batch - the user confirmed
        the closest (earliest) one is what should be reported as the ETA.
        """
        match = ONMOUSEOVER_CEDULE_RE.search(onmouseover)
        if not match:
            return None

        item_code, whse, country, desc, order_type = match.groups()
        query = urlencode(
            {"MyItem": item_code, "whse": whse, "eut": country, "desc": desc, "type": order_type}
        )
        url = urljoin(self.page.url, PRODUCTION_SCHEDULE_PATH) + "?" + query
        try:
            response = self.page.request.get(url)
            body_text = _decode_response_body(
                response.body(), response.headers.get("content-type", "")
            )
        except Exception as exc:
            logger.warning("Couldn't fetch production schedule from %s: %r", url, exc)
            return None

        if not response.ok:
            logger.warning(
                "Production schedule fetch got HTTP %s from %s: %.200r",
                response.status,
                url,
                body_text,
            )
            return None

        return _closest_schedule_date(body_text)
