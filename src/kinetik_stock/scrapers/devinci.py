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

# Confirmed: a sold-out cell has disabled="disabled" and a black background
# (RGB(0,0,0)); a cell for a size this model/color doesn't offer at all is
# gray (RGB(180,180,180) or RGB(200,200,200) - just alternating-row styling,
# not a distinct meaning) with neither onchange nor disabled - it's skipped
# entirely rather than emitted as an out-of-stock StockItem, since the size
# doesn't exist for this row at all.
QTY_TABLE_SELECTOR = "table#RadGrid1_ctl00"
ROW_SELECTOR = "tr.rgRow, tr.rgAltRow"

# Confirmed: period 1 is "In Stock Now" (hidden fields txtDateDébutLivraison1/
# txtDateFinLivraison1), period 2 is "Future production" (txtDateDébut
# Livraison2/txtDateFinLivraison2) - a later, separate delivery window.
# Mapped here as: period 1 -> IN_STOCK/OUT_OF_STOCK (available now), period
# 2 -> PRE_ORDER/OUT_OF_STOCK (bookable for that later window), using the
# period's own start date as the ETA. This mapping is an interpretation of
# what the two periods mean, not something the portal itself labels as
# "pre-order" - unverified against dealer-facing terminology.
PERIOD_START_DATE_FIELD = {
    "1": "txtDateDébutLivraison1",
    "2": "txtDateDébutLivraison2",
}

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
    Season" order type) pasted from a live dealer session - see the module-
    level comments above for exactly what's confirmed.

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
        period_start_date = {
            period: self._hidden_field_value(field_name)
            for period, field_name in PERIOD_START_DATE_FIELD.items()
        }

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

            for qty_input in row.query_selector_all("input[type='text']"):
                name = qty_input.get_attribute("name") or ""
                match = QTY_FIELD_RE.search(name)
                if not match:
                    continue  # a price field, not a size/period quantity cell

                size = match.group("size")
                period = match.group("period")
                disabled = qty_input.get_attribute("disabled") is not None
                onchange = qty_input.get_attribute("onchange")

                if not disabled and not onchange:
                    continue  # size not offered for this model/color at all

                if disabled:
                    status = StockStatus.OUT_OF_STOCK
                    quantity = 0
                    eta_date = None
                else:
                    qty_match = MAX_QTY_RE.search(onchange or "")
                    quantity = int(qty_match.group(1)) if qty_match else None
                    if period == "1":
                        status = StockStatus.IN_STOCK
                        eta_date = None
                    else:
                        status = StockStatus.PRE_ORDER
                        eta_date = period_start_date.get(period)

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
                        raw_status_text=f"period={period} disabled={disabled}",
                        source_url=source_url,
                    )
                )

        items.sort(key=lambda i: (i.sku, SIZE_ORDER.index(i.size) if i.size in SIZE_ORDER else -1))
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
