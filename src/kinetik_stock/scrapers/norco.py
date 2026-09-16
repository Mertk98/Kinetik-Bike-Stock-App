from __future__ import annotations

import csv
from typing import Optional

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
# searching by an exact item/part number always lands directly on that
# item's product page - and the product page shows a table of every size/
# color variant of that same model, not just the one item number searched.
# So we only need ONE representative item number per bike model, not one
# per SKU. That list isn't derivable from the portal itself (no full model
# list/nav crawl gets us there reliably) - it comes from
# config/norco_items.csv, a "model,item_number" CSV the user maintains by
# hand from their own records.
ITEM_SEARCH_URL_TEMPLATE = "https://ltpdealer.com/ld/itemsearch?q={item_number}"
ITEM_LOOKUP_CSV = REPO_ROOT / "config" / "norco_items.csv"

# Column labels vary by product line (e.g. an e-bike page might add "Motor"),
# so columns are matched by their header text, not by a fixed col_N index.
# British "Colour" is what the portal actually uses; "Color" is a fallback
# in case another product category spells it differently.
COLOR_LABELS = ("colour", "color")
SIZE_LABELS = ("frame size", "size")


def _find_col_key(label_to_col: dict[str, str], candidates: tuple[str, ...]) -> Optional[str]:
    for candidate in candidates:
        if candidate in label_to_col:
            return label_to_col[candidate]
    return None


def _status_and_eta(row: dict) -> tuple[StockStatus, Optional[str]]:
    # UNVERIFIED: every real row we've seen so far has eta_h/eta_nh == "N"
    # (no ETA). We don't yet know what an actual ETA value looks like, so
    # this treats anything other than "N"/blank as a raw pre-order marker
    # until a real example turns up.
    eta_raw = row.get("eta_h") or row.get("eta_nh")
    if eta_raw and eta_raw != "N":
        return StockStatus.PRE_ORDER, eta_raw

    # No "low stock" bucket is exposed here (unlike Transition Bikes' plain-
    # text "Low Stock (N)") - just raw quantities per warehouse, summed
    # across every warehouse this dealer can draw from.
    qty = (row.get("qty_availh") or 0) + (row.get("qty_availnh") or 0) + (
        row.get("qty_availnh_2") or 0
    )
    if qty > 0:
        return StockStatus.IN_STOCK, None
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

    Unverified: what an actual ETA value looks like (every real row seen so
    far has eta_h/eta_nh == "N", i.e. no ETA) and whether there's a
    "low stock" distinction at all - see _status_and_eta().
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
