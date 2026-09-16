from __future__ import annotations

import csv
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

from kinetik_stock.config import REPO_ROOT
from kinetik_stock.models import StockItem, StockStatus
from kinetik_stock.scrapers.base import BaseScraper

# Norco is sold through LTP Dealer (Live to Play Sports), a shared
# multi-brand distributor portal (ltpdealer.com) - not a Norco-only site.
# Confirmed against the real login page: unlike Transition Bikes, login here
# takes three fields, not two - Dealer ID, Username, and Password.
DEALER_ID_SELECTOR = "#P1_DEALER"
USERNAME_SELECTOR = "#P1_LOGIN"
PASSWORD_SELECTOR = "#P1_CPWRD"
LOGIN_BUTTON_SELECTOR = "#login"

# Confirmed against the real post-login home page: the account menu has a
# "Log Out" link at this exact href.
LOGGED_IN_SELECTOR = "a[href='/logout.sa']"
LOGGED_IN_CHECK_TIMEOUT_MS = 10000

# LTP's own search is unreliable by bike name (confirmed by the user), but
# searching by an exact item/part number always resolves to exactly one
# match. Confirmed against a real search: /ld/itemsearch?q=<item_number>
# itself only returns a search-results shell (a Solr JSON blob with that
# one item's summary, no size/color table) - the rich product page (with
# the full columnsjsondata table below) is reached by the site's own JS
# auto-navigating there for a single-match search. UNVERIFIED: that
# auto-navigation itself (inferred from the search-results HTML plus a
# separately-provided product page for the same item, not observed
# end-to-end) - _scrape_item_page() waits for columnsjsondata to appear on
# whatever page we land on, and fails loudly if it never does.
#
# Either way we only need ONE representative item number per bike model,
# not one per SKU, since the product page's table covers every size/color
# of that model. That list isn't derivable from the portal itself (no full
# model list/nav crawl gets us there reliably) - it comes from
# config/norco_items.csv, a "model,item_number" CSV the user maintains by
# hand from their own records.
ITEM_SEARCH_URL_TEMPLATE = "https://ltpdealer.com/ld/itemsearch?q={item_number}"
ITEM_LOOKUP_CSV = REPO_ROOT / "config" / "norco_items.csv"
PRODUCT_PAGE_LOAD_TIMEOUT_MS = 15000

# Column labels vary by product line (e.g. an e-bike page might add "Motor"),
# so columns are matched by their header text, not by a fixed col_N index.
# British "Colour" is what the portal actually uses; "Color" is a fallback
# in case another product category spells it differently.
COLOR_LABELS = ("colour", "color")
SIZE_LABELS = ("frame size", "size")

# Confirmed against a real product page (Torrent DH A1): when a warehouse
# has zero on hand, eta_h/eta_nh/eta_nh_2 is either the string "N" (no ETA
# at all) or an object like {"eta_date": "02-15-2027", "qty_eta": 25, ...}
# - MM-DD-YYYY. When there IS existing stock (qty > 0), an ETA object can
# still appear on a *different* warehouse field (restock in transit) - that
# doesn't change the item's current status, just its future quantity, so
# on-hand quantity always takes priority over any ETA object.
ETA_DATE_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")


def _find_col_key(label_to_col: dict[str, str], candidates: tuple[str, ...]) -> Optional[str]:
    for candidate in candidates:
        if candidate in label_to_col:
            return label_to_col[candidate]
    return None


def _format_eta_date(raw: str) -> str:
    match = ETA_DATE_RE.match(raw)
    if not match:
        logger.warning("ETA date %r didn't match the expected MM-DD-YYYY format", raw)
        return raw
    month, day, year = match.groups()
    return f"{year}-{month}-{day}"


def _extract_eta_info(row: dict) -> Optional[dict]:
    for key in ("eta_h", "eta_nh", "eta_nh_2"):
        value = row.get(key)
        if isinstance(value, dict):
            return value
    return None


def _status_and_eta(row: dict) -> tuple[StockStatus, Optional[str]]:
    # No "low stock" bucket is exposed here (unlike Transition Bikes' plain-
    # text "Low Stock (N)") - just raw quantities per warehouse, summed
    # across every warehouse this dealer can draw from.
    qty = (row.get("qty_availh") or 0) + (row.get("qty_availnh") or 0) + (
        row.get("qty_availnh_2") or 0
    )
    if qty > 0:
        return StockStatus.IN_STOCK, None

    eta_info = _extract_eta_info(row)
    if eta_info is not None:
        return StockStatus.PRE_ORDER, _format_eta_date(eta_info["eta_date"])

    # Confirmed against two real products: "active": 0 marks a discontinued
    # item (Sight C1 150 - active 0 on every row, no restock coming), while
    # "active": 1 with zero qty and no ETA is a plain temporary sellout
    # (Sight C3 150 MX). active=0 doesn't necessarily mean the whole model
    # is done, though - Sight C1 150's Size 2 row is active=0 but still had
    # 1 unit on hand, so quantity is still checked first above; this only
    # fires once we already know there's nothing left to sell.
    if row.get("active") == 0:
        return StockStatus.DISCONTINUED, None

    return StockStatus.OUT_OF_STOCK, None


def _load_item_lookup() -> list[tuple[str, str]]:
    if not ITEM_LOOKUP_CSV.exists():
        raise FileNotFoundError(
            f"{ITEM_LOOKUP_CSV} not found - add a CSV with 'model,item_number' "
            "columns (one representative item/part number per bike model; its "
            "product page lists every size/color variant of that model)."
        )
    with open(ITEM_LOOKUP_CSV, newline="", encoding="utf-8") as f:
        rows = [
            (row["model"].strip(), row["item_number"].strip()) for row in csv.DictReader(f)
        ]
    if not rows:
        raise ValueError(f"{ITEM_LOOKUP_CSV} has no rows - add at least one model/item_number.")
    return rows


class NorcoScraper(BaseScraper):
    """Scraper for Norco's B2B dealer portal (https://ltpdealer.com/),
    operated by distributor Live to Play Sports (LTP Dealer).

    Unlike Transition Bikes, this portal has no single stock-list page.
    Instead, searching by an exact item/part number lands directly on that
    item's product page, which embeds a JS variable (`columnsjsondata`)
    listing every size/color variant of that model with its own item
    number, price, and per-warehouse quantity - confirmed against a real
    product page (Sight C1 160). So fetch_stock() looks up one item number
    per model (from config/norco_items.csv, maintained by hand - LTP's own
    search/nav isn't reliable enough to discover the model list itself) and
    reads that whole table per model.

    Unverified: whether a single-match item-number search really does
    auto-navigate client-side from the search-results shell to this product
    page (inferred, not observed end-to-end - see the comment above
    ITEM_SEARCH_URL_TEMPLATE) and whether there's a "low stock" distinction
    at all - see _status_and_eta().
    """

    def login(self) -> None:
        self.page.goto(self.brand_config.portal_url)
        self.page.fill(DEALER_ID_SELECTOR, self.brand_config.extra("dealer_id") or "")
        self.page.fill(USERNAME_SELECTOR, self.brand_config.username or "")
        self.page.fill(PASSWORD_SELECTOR, self.brand_config.password or "")
        self.page.click(LOGIN_BUTTON_SELECTOR)
        self.page.wait_for_load_state("networkidle")

        try:
            self.page.wait_for_selector(
                LOGGED_IN_SELECTOR, state="visible", timeout=LOGGED_IN_CHECK_TIMEOUT_MS
            )
        except Exception:
            raise RuntimeError(
                f"Login to {self.brand_config.name} failed (no logout link found) - "
                f"check {self.brand_config.username_env}/{self.brand_config.password_env} "
                f"and its dealer ID."
            )

    def fetch_stock(self) -> list[StockItem]:
        items: list[StockItem] = []
        for _model_name, item_number in _load_item_lookup():
            items.extend(self._scrape_item_page(item_number))
        return items

    def _scrape_item_page(self, item_number: str) -> list[StockItem]:
        self.page.goto(ITEM_SEARCH_URL_TEMPLATE.format(item_number=item_number))
        try:
            self.page.wait_for_function(
                "typeof columnsjsondata !== 'undefined'",
                timeout=PRODUCT_PAGE_LOAD_TIMEOUT_MS,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Searching for item {item_number!r} never reached a product page "
                f"(stuck at {self.page.url}) - confirm it's a valid item number."
            ) from exc
        return self._extract_stock_items_from_current_page()

    def _extract_stock_items_from_current_page(self) -> list[StockItem]:
        try:
            rows = self.page.evaluate("columnsjsondata[0].Rows")
            header_map = self.page.evaluate("headerjsondata[0].Rows[0]")
        except Exception as exc:
            raise RuntimeError(
                f"Couldn't find the expected product data on {self.page.url} - "
                "portal layout may have changed."
            ) from exc

        label_to_col = {
            str(label).strip().lower(): col_key for col_key, label in header_map.items()
        }
        color_col = _find_col_key(label_to_col, COLOR_LABELS)
        size_col = _find_col_key(label_to_col, SIZE_LABELS)

        title_el = self.page.query_selector("#prodName")
        product_title = title_el.inner_text().strip() if title_el else ""
        source_url = self.page.url

        items: list[StockItem] = []
        for row in rows:
            status, eta_date = _status_and_eta(row)
            size = row.get(size_col) if size_col else None
            color = row.get(color_col) if color_col else None
            items.append(
                StockItem(
                    brand=self.brand_config.name,
                    sku=str(row.get("item_id", "")),
                    product_title=product_title,
                    variant=", ".join(v for v in (size, color) if v) or None,
                    size=size,
                    color=color,
                    status=status,
                    regular_retail_price=row.get("msrp"),
                    eta_date=eta_date,
                    raw_status_text=(
                        f"qty_availh={row.get('qty_availh')} "
                        f"qty_availnh={row.get('qty_availnh')} "
                        f"eta_h={row.get('eta_h')} eta_nh={row.get('eta_nh')}"
                    ),
                    source_url=source_url,
                )
            )
        return items
