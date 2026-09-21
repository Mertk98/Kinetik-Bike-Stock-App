from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
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
# The page never reaches Playwright's "networkidle" state - it loads Zendesk
# chat, Klaviyo tracking, and Google Analytics, which keep making background
# requests indefinitely - so login() waits directly for the logged-in
# selector instead of network idle first.
LOGGED_IN_CHECK_TIMEOUT_MS = 15000

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
ITEM_SEARCH_URL_TEMPLATE = "https://ltpdealer.com/ld/itemsearch?q={item_number}"
PRODUCT_PAGE_LOAD_TIMEOUT_MS = 15000

# config/norco_items.csv is the user's own inventory export, not something
# we generate: "System ID" (their internal product ID), "Manufact. SKU"
# (the LTP item/part number to search - can be blank when their system
# doesn't have one on file yet), "Item" (their own free-text model/size/
# color description, kept only for the report - not parsed for anything).
NORCO_ITEMS_CSV = REPO_ROOT / "config" / "norco_items.csv"
NORCO_REPORT_FIELDNAMES = ["System ID", "Manufact. SKU", "Item", "Status", "ETA"]

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


def _report_status_and_eta(item: StockItem) -> tuple[str, str]:
    """Formats a scraped StockItem into the specific wording the user's own
    LTP/Norco report expects, distinct from the generic StockStatus values
    used in the shared multi-brand CSV (models.py's as_csv_row()).
    """
    if item.status == StockStatus.IN_STOCK:
        return f"Available ({item.quantity})", "Now"
    if item.status == StockStatus.PRE_ORDER:
        return "pre-order", item.eta_date or "N/A"
    if item.status == StockStatus.DISCONTINUED:
        return "Discontinued", "N/A"
    return "Out of Stock", "N/A"


def _load_norco_items(path: Path = NORCO_ITEMS_CSV) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found - add a CSV with 'System ID', 'Manufact. SKU', "
            "'Item' columns (the user's own inventory export)."
        )
    with open(path, newline="", encoding="utf-8") as f:
        rows = [
            {
                "system_id": row["System ID"].strip(),
                "item_number": (row.get("Manufact. SKU") or "").strip(),
                "item_text": row["Item"].strip(),
            }
            for row in csv.DictReader(f)
        ]
    if not rows:
        raise ValueError(f"{path} has no rows.")
    return rows


def write_norco_report_csv(rows: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=NORCO_REPORT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


class NorcoScraper(BaseScraper):
    """Scraper for Norco's B2B dealer portal (https://ltpdealer.com/),
    operated by distributor Live to Play Sports (LTP Dealer).

    Unlike Transition Bikes, this portal has no single stock-list page.
    Instead, searching by an exact item/part number lands directly on that
    item's product page, which embeds a JS variable (`columnsjsondata`)
    listing every size/color variant of that model with its own item
    number, price, and per-warehouse quantity - confirmed against a real
    product page (Sight C1 160).

    config/norco_items.csv is the user's own inventory export (System ID,
    Manufact. SKU, Item) - one row per exact SKU they carry, not one per
    model. fetch_stock() looks up each row's item number and keeps only the
    matching row from that model's page, for the shared multi-brand CSV
    pipeline. generate_availability_report() instead reproduces the user's
    own report format - the original 3 columns plus Status/ETA text
    formatted their way ("Available (N)"/"Now", "pre-order"/<date>,
    "Discontinued"/"N/A", "Out of Stock"/"N/A") - including rows with no
    Manufact. SKU on file, which can't be looked up at all.

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
        for row in _load_norco_items(NORCO_ITEMS_CSV):
            item_number = row["item_number"]
            if not item_number:
                # No Manufact. SKU on file means LTP has nothing to search for
                # this SKU - the user treats that as discontinued rather than
                # leaving it unresolved.
                items.append(
                    StockItem(
                        brand=self.brand_config.name,
                        sku="",
                        product_title=row["item_text"],
                        status=StockStatus.DISCONTINUED,
                    )
                )
                continue
            page_items = self._scrape_item_page(item_number)
            matched = next((i for i in page_items if i.sku == item_number), None)
            if matched is None:
                logger.warning(
                    "Item %s (%r) wasn't found on its own product page.",
                    item_number,
                    row["item_text"],
                )
                continue
            items.append(matched)
        return items

    def generate_availability_report(
        self, input_csv: Path = NORCO_ITEMS_CSV
    ) -> list[dict]:
        """Produces the user's own report format: the input CSV's 3 columns
        plus Status/ETA, in their own wording rather than the generic
        StockStatus/eta_date used by the shared multi-brand CSV pipeline.
        """
        report_rows: list[dict] = []
        for row in _load_norco_items(input_csv):
            system_id, item_number, item_text = (
                row["system_id"],
                row["item_number"],
                row["item_text"],
            )
            status_text, eta_text = "N/A", "N/A"

            if not item_number:
                # No Manufact. SKU on file means LTP has nothing to search
                # for - the user treats that as discontinued.
                status_text, eta_text = "Discontinued", "N/A"
            else:
                try:
                    page_items = self._scrape_item_page(item_number)
                    matched = next(
                        (i for i in page_items if i.sku == item_number), None
                    )
                except Exception:
                    logger.exception(
                        "Failed to look up item %s (%r)", item_number, item_text
                    )
                    matched = None

                if matched is None:
                    logger.warning(
                        "Item %s (%r) wasn't found on its own product page.",
                        item_number,
                        item_text,
                    )
                else:
                    status_text, eta_text = _report_status_and_eta(matched)

            report_rows.append(
                {
                    "System ID": system_id,
                    "Manufact. SKU": item_number,
                    "Item": item_text,
                    "Status": status_text,
                    "ETA": eta_text,
                }
            )
        return report_rows

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
            quantity = (
                (row.get("qty_availh") or 0)
                + (row.get("qty_availnh") or 0)
                + (row.get("qty_availnh_2") or 0)
            )
            items.append(
                StockItem(
                    brand=self.brand_config.name,
                    sku=str(row.get("item_id", "")),
                    product_title=product_title,
                    variant=", ".join(v for v in (size, color) if v) or None,
                    size=size,
                    color=color,
                    status=status,
                    quantity=quantity,
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
