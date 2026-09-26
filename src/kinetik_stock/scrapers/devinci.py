from __future__ import annotations

import logging
import re
from typing import Optional

from kinetik_stock.models import StockItem, StockStatus
from kinetik_stock.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Confirmed against a real order/booking page (Achats_Treeview.aspx, "In
# Season" order type) pasted directly from a live dealer session. Devinci's
# portal isn't a simple stock-lookup page like Norco/Transition - it's a
# dealer *booking* grid (RadGrid1) where every bike model/color is one row
# and each of the 5 sizes (XS/S/M/L/XL) is its own quantity <input> cell, x2
# "periods" (delivery windows) side by side. Confirmed field-name pattern
# for each cell, e.g. "RadGrid1$ctl00$ctl10$txtM1_FE26100_11__GAMM_2027_1":
# always "tx t<SIZE><PERIOD>_<SKU with '-' replaced by '_'>__GAMM_2027_<n>".
# The "ctlNN" segment is a per-row ASP.NET control index with no fixed
# value, so cells are matched by this field-name pattern, not position.
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
# PRE_ORDER if period 2 has any quantity (ETA = period 2's start date);
# otherwise OUT_OF_STOCK (no current or future availability at all) - this
# covers both "sold out" and "this size isn't offered for this model".
PERIOD_2_START_DATE_FIELD = "txtDateDébutLivraison2"

SIZE_ORDER = ["XS", "S", "M", "L", "XL"]

# Confirmed: description text is consistently "<model/build> | <component
# spec> | <color>", e.g. "Bike Wilson 40 | GX DH | Sepia Green" - the last
# '|'-segment is always the color across every row seen.
def split_description(description: str) -> tuple[str, Optional[str]]:
    parts = [p.strip() for p in description.split("|")]
    if len(parts) < 2:
        return description.strip(), None
    return " | ".join(parts[:-1]), parts[-1]


class DevinciScraper(BaseScraper):
    """Scraper for Devinci's B2B dealer portal ("SITE TRANSACTIONNEL").

    fetch_stock()'s row/cell parsing (_extract_stock_items_from_page) is
    confirmed against a real order grid page (Achats_Treeview.aspx, "In
    Season" order type) pasted from a live dealer session, including the
    exact in-stock/pre-order/out-of-stock split confirmed against a live
    row by the user - see the module-level comments above for exactly
    what's confirmed.

    UNVERIFIED and NOT implemented:
      - login(): no login page HTML has been captured yet, so there are no
        confirmed selectors for the username/password fields, the submit
        control, or a "logged in" check.
      - The navigation path from the post-login landing page to the order
        grid: the grid's own URL (Achats_Treeview.aspx?no=<order id>&Type=
        <order type guid>) has session-specific query params that appear to
        get generated when a dealer starts/opens an order in the portal UI
        (e.g. picking "In Season" from a menu) - there's no evidence it's a
        fixed URL that can just be navigated to directly after login.

    Keep `enabled: false` in config/brands.yaml until both of the above are
    confirmed against the real portal.
    """

    def login(self) -> None:
        raise NotImplementedError(
            f"{self.brand_config.name} login() is unverified - no login page "
            "HTML has been captured yet, so there are no confirmed selectors "
            "for the username/password fields or submit control. Capture the "
            "real login page HTML before implementing this."
        )

    def fetch_stock(self) -> list[StockItem]:
        raise NotImplementedError(
            f"{self.brand_config.name} fetch_stock() navigation is "
            "unverified - there's no confirmed path from the post-login "
            "landing page to the order/booking grid (Achats_Treeview.aspx), "
            "whose URL carries session-specific no=/Type= query params. "
            "_extract_stock_items_from_page() below is confirmed against the "
            "real grid HTML once a page is already on it."
        )

    def _extract_stock_items_from_page(self) -> list[StockItem]:
        source_url = self.page.url
        period_2_eta = self._hidden_field_value(PERIOD_2_START_DATE_FIELD)

        items: list[StockItem] = []
        for row in self.page.query_selector_all(ROW_SELECTOR):
            cells = row.query_selector_all("td")
            if len(cells) < 3:
                continue  # not a product row (shouldn't happen for rgRow/rgAltRow)

            sku = cells[1].inner_text().strip()
            description_el = row.query_selector("a.DescriptionSansLien")
            description = description_el.inner_text().strip() if description_el else ""
            product_title, color = split_description(description)

            retail_price = self._retail_price(row)

            # One quantity per size per period, defaulting to 0 - a size
            # always has all 10 (5 sizes x 2 periods) cells present in the
            # real grid, whether or not they're orderable.
            period_qty = {size: {"1": 0, "2": 0} for size in SIZE_ORDER}
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

            for size in SIZE_ORDER:
                now_qty = period_qty[size]["1"]
                future_qty = period_qty[size]["2"]

                if now_qty > 0:
                    status, quantity, eta_date = StockStatus.IN_STOCK, now_qty, None
                elif future_qty > 0:
                    status, quantity, eta_date = StockStatus.PRE_ORDER, future_qty, period_2_eta
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
