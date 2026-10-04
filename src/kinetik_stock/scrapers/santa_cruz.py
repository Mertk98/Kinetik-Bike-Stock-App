from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

from kinetik_stock.models import StockItem, StockStatus
from kinetik_stock.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Confirmed against the real login page HTML, pasted by the user from a live
# session at https://vip.santacruzbicycles.com/login - an SAP Commerce
# Cloud / Spartacus Angular storefront (cx-storefront, cx-page-layout, etc.).
# The same app also serves Cervelo's B2B portal off a shared brand switcher
# in the header, per that page's own markup - unrelated to this scraper, but
# explains the "Cervelo" CSS classes/assets alongside Santa Cruz's.
#
# The <form> itself declares method="POST" action=".../authorizationserver/
# login" (a separate API host, api-vip.santacruzbicycles.com) but it's an
# Angular reactive form (novalidate, ng-pristine/ng-invalid classes) that
# intercepts the real submit client-side rather than doing a plain browser
# POST - so driving it through the real page/button (fill + click) is the
# right approach, not posting to that URL directly ourselves.
USERNAME_SELECTOR = "input[name='username']"
PASSWORD_SELECTOR = "input[name='password']"
# Scoped to the login form component (scb-login-form) since a real page from
# an app this size may well have other type=submit buttons elsewhere once
# more of it is captured.
LOGIN_BUTTON_SELECTOR = "scb-login-form button[type='submit']"

# Confirmed against a real post-login Homepage capture: the logged-out login
# page has an entirely empty <scb-login></scb-login> in the header's
# SiteLogin slot, while the logged-in Homepage has it filled in with an
# account flyout menu whose always-visible heading is "Welcome, <name>"
# (id="navHeading") - the dropdown under it (which has the "/logout" Sign
# Out link) only becomes visible on hover per the page's own CSS, so
# #navHeading is used instead of that link: it's already visible without
# needing to simulate a hover interaction.
LOGGED_IN_SELECTOR = "#navHeading"
LOGGED_IN_CHECK_TIMEOUT_MS = 15000

# Confirmed against a real captured Quick Order page
# (vip.santacruzbicycles.com/my-account/quick-order, linked from the
# post-login nav's SiteLinks slot as href="/my-account/quick-order" - no
# trailing slash, unlike that page's own <link rel="canonical"> tag, which
# turned out to be wrong for the login page too). Per the user, this is how
# dealer stock/ETA is checked: type a part number into the search box and
# press Enter (no dropdown-result click needed) to add it as a row; repeat
# per SKU, up to the page's own stated limit of 20 at a time (confirmed by
# the page's own "You can add up to 20 products at a time" text); a SKU
# that's discontinued or out of stock with no ETA shows a warning instead of
# being added. Then "Add to cart" navigates to the real cart page, where
# each line's "EST. Shipment QTY" field holds the ETA - a week or less out
# means available now, more than a week out means pre-order (per the user).
QUICK_ORDER_PAGE_URL = "https://vip.santacruzbicycles.com/my-account/quick-order"
QUICK_ORDER_SEARCH_INPUT_SELECTOR = "input[formcontrolname='product']"
ADD_TO_CART_BUTTON_SELECTOR = "button[aria-label='Add to cart']"
MAX_ITEMS_PER_QUICK_ORDER_BATCH = 20

# Confirmed against a real captured Quick Order page after successfully
# adding part number 58-27313-448-3-933-132801 by typing it and pressing
# Enter: each added item is a ".cx-quick-order-table-row" containing one
# "scb-quick-order-item". Its product title is ".cx-name h4" (here,
# "Bronson 5 C"); its SKU is read back out of a ".cx-code" div whose text is
# "SKU: <code>" - there's another ".cx-code" div just above it holding the
# build-kit abbreviation ("Brsn 5 C MX 27 MD CBN Deore"), so matching only
# the one starting with "SKU:" is what tells them apart. "Empty list"
# clears this quick-order list itself (not the real cart - that's a
# separate "Clear Cart" the user described on the cart page, not captured
# yet) so it can be used between batches of 20 if Add to cart isn't
# clicked. Oddly, the added row's own quantity-counter input had max="0" in
# this capture despite the item being added successfully with no warning -
# not yet understood, so not relied upon for anything.
QUICK_ORDER_ADDED_ROW_SELECTOR = ".cx-quick-order-table-row"
QUICK_ORDER_ITEM_TITLE_SELECTOR = ".cx-name h4"
QUICK_ORDER_ITEM_SKU_RE = re.compile(r"SKU:\s*(\S+)")
QUICK_ORDER_EMPTY_LIST_BUTTON_SELECTOR = "button[aria-label='Empty list']"

# Confirmed against a real captured Quick Order page for a known-bad part
# number (58-26244-458-3-891-131501, discontinued or OOS-with-no-ETA): no
# row was added for it at all - the quick-order-table stayed completely
# empty, with no per-item warning shown at that point. Per the user, the
# generic "Error proceeding to Cart." banner (cx-message.quick-order-
# errors-message, a collapsible "Please review these errors" section whose
# detail isn't in this capture) only appears later, once "Add to cart" is
# clicked - it isn't a per-item signal and doesn't name which SKU failed.
# So a skipped item is detected the same way a real match is confirmed: by
# whether a row with the typed SKU actually showed up in the table, not by
# looking for this banner.
QUICK_ORDER_ERROR_MESSAGE_SELECTOR = "cx-message.quick-order-errors-message"

# Confirmed against a real captured Cart page (vip.santacruzbicycles.com/
# cart) after a successful "Add to cart": each line is a
# ".cx-item-list-row" (containing one "scb-cart-page-item"), using the same
# ".cx-name h4"/".cx-code" shared sub-components as the Quick Order
# table's rows above - QUICK_ORDER_ITEM_TITLE_SELECTOR and
# QUICK_ORDER_ITEM_SKU_RE apply here too. Its "Est. shipment date" field
# (rendered twice - a mobile and a desktop copy of the same data) is a
# ".schedule-line-wrapper .value" whose text is "<qty> by <start
# MM-DD-YYYY> - <end MM-DD-YYYY>" - a date *range*, not a single date. Per
# the user, the range's START date is what decides "more than a week away"
# (pre-order) vs "a week or less" (in stock now), and is what's reported
# as the ETA. A split shipment (multiple schedule lines for one item)
# isn't confirmed yet, but if it happens the closest (earliest) start date
# across all of them is used - the same "closest date" rule as Devinci's
# production schedule.
#
# SAFETY: fetch_stock_for_skus() must only ever read this page and click
# "Clear cart" - never "Proceed To Checkout", which would place a real
# order. There is no constant for that button on purpose.
CART_PAGE_URL = "https://vip.santacruzbicycles.com/cart"
CART_ITEM_ROW_SELECTOR = ".cx-item-list-row"
CART_ITEM_SCHEDULE_VALUE_SELECTOR = ".schedule-line-wrapper .value"
CLEAR_CART_BUTTON_SELECTOR = "scb-clear-cart button"
SCHEDULE_DATE_RANGE_RE = re.compile(
    r"by\s+(?P<start_month>\d{2})-(?P<start_day>\d{2})-(?P<start_year>\d{4})"
    r"\s*-\s*\d{2}-\d{2}-\d{4}"
)
# Per the user: a week or less out counts as available now; anything
# further out is a pre-order.
STOCK_NOW_THRESHOLD_DAYS = 7
# UNCONFIRMED - how long a row takes to appear in the Quick Order table
# after typing a SKU and pressing Enter hasn't been timed against the real
# portal; this is a starting guess, tunable after a live run.
QUICK_ORDER_ADD_TIMEOUT_MS = 5000


def _closest_cart_eta_date(row) -> Optional[str]:
    """Earliest schedule-line start date (ISO YYYY-MM-DD) in a cart row, or
    None if the row has none (shouldn't normally happen for a real item).
    """
    dates = []
    for value_el in row.query_selector_all(CART_ITEM_SCHEDULE_VALUE_SELECTOR):
        match = SCHEDULE_DATE_RANGE_RE.search(value_el.inner_text())
        if match:
            dates.append(
                f"{match['start_year']}-{match['start_month']}-{match['start_day']}"
            )
    return min(dates) if dates else None


def _cart_item_status(eta_date: Optional[str], today: Optional[date] = None) -> StockStatus:
    if eta_date is None:
        return StockStatus.UNKNOWN
    if today is None:
        today = date.today()
    days_out = (date.fromisoformat(eta_date) - today).days
    return StockStatus.IN_STOCK if days_out <= STOCK_NOW_THRESHOLD_DAYS else StockStatus.PRE_ORDER


class SantaCruzScraper(BaseScraper):
    """Scraper for Santa Cruz Bicycles' B2B dealer portal
    (https://vip.santacruzbicycles.com/), an SAP Commerce Cloud / Spartacus
    storefront.

    login() is confirmed against real captured login and post-login pages.
    The whole Quick Order -> Add to cart -> read ETA -> Clear cart flow the
    user described is now confirmed and implemented in
    fetch_stock_for_skus() - this portal is search-driven (no browsable
    full-catalog page), so the plain fetch_stock() BaseScraper contract
    (no arguments) doesn't fit; it raises NotImplementedError pointing
    there instead. Timing (how long to wait for a row to appear after
    pressing Enter) is an unconfirmed guess, not yet verified live.
    """

    def __init__(self, brand_config, browser, *, headless: bool = True):
        super().__init__(brand_config, browser, headless=headless)
        # Overridable so tests can point this at a local fixture instead of
        # the real portal - same pattern as devinci.py's self._menu_url.
        self._quick_order_url = QUICK_ORDER_PAGE_URL

    def login(self) -> None:
        self.page.goto(self.brand_config.portal_url)
        self.page.fill(USERNAME_SELECTOR, self.brand_config.username or "")
        self.page.fill(PASSWORD_SELECTOR, self.brand_config.password or "")
        self.page.click(LOGIN_BUTTON_SELECTOR)

        try:
            self.page.wait_for_selector(
                LOGGED_IN_SELECTOR, state="visible", timeout=LOGGED_IN_CHECK_TIMEOUT_MS
            )
        except Exception:
            raise RuntimeError(
                f"Login to {self.brand_config.name} failed (no account greeting "
                f"found) - check {self.brand_config.username_env}/"
                f"{self.brand_config.password_env}."
            )

    def fetch_stock(self) -> list[StockItem]:
        raise NotImplementedError(
            "Santa Cruz's dealer portal is search-driven (Quick Order by "
            "part number) rather than browsable, so there's no fixed "
            "catalog page to scrape - call fetch_stock_for_skus(skus) with "
            "the list of part numbers to check instead."
        )

    def fetch_stock_for_skus(self, skus: list[str]) -> list[StockItem]:
        items: list[StockItem] = []
        for start in range(0, len(skus), MAX_ITEMS_PER_QUICK_ORDER_BATCH):
            batch = skus[start : start + MAX_ITEMS_PER_QUICK_ORDER_BATCH]
            items.extend(self._fetch_stock_for_batch(batch))
        return items

    def _fetch_stock_for_batch(self, skus: list[str]) -> list[StockItem]:
        self.page.goto(self._quick_order_url)

        added_skus = []
        for sku in skus:
            self.page.fill(QUICK_ORDER_SEARCH_INPUT_SELECTOR, sku)
            self.page.press(QUICK_ORDER_SEARCH_INPUT_SELECTOR, "Enter")
            if self._wait_for_quick_order_row(sku):
                added_skus.append(sku)
            else:
                logger.info(
                    "%s wasn't added to the Quick Order list (discontinued or "
                    "out of stock with no ETA).",
                    sku,
                )

        items: list[StockItem] = [
            StockItem(
                brand=self.brand_config.name,
                sku=sku,
                product_title="",
                status=StockStatus.OUT_OF_STOCK,
                raw_status_text=(
                    "not added to Quick Order (discontinued or out of stock "
                    "with no ETA)"
                ),
                source_url=QUICK_ORDER_PAGE_URL,
            )
            for sku in skus
            if sku not in added_skus
        ]

        if not added_skus:
            return items

        self.page.click(ADD_TO_CART_BUTTON_SELECTOR)
        self.page.wait_for_load_state("networkidle")

        for row in self.page.query_selector_all(CART_ITEM_ROW_SELECTOR):
            title_el = row.query_selector(QUICK_ORDER_ITEM_TITLE_SELECTOR)
            title = title_el.inner_text().strip() if title_el else ""

            sku = None
            for code_div in row.query_selector_all(".cx-code"):
                match = QUICK_ORDER_ITEM_SKU_RE.search(code_div.inner_text())
                if match:
                    sku = match.group(1)
            if sku is None:
                logger.warning("Cart row with no matching SKU found: %r", title)
                continue

            eta_date = _closest_cart_eta_date(row)
            status = _cart_item_status(eta_date)
            items.append(
                StockItem(
                    brand=self.brand_config.name,
                    sku=sku,
                    product_title=title,
                    status=status,
                    eta_date=eta_date if status == StockStatus.PRE_ORDER else None,
                    raw_status_text=f"est_shipment_start={eta_date}",
                    source_url=self.page.url,
                )
            )

        self.page.click(CLEAR_CART_BUTTON_SELECTOR)
        return items

    def _wait_for_quick_order_row(self, sku: str) -> bool:
        selector = f"{QUICK_ORDER_ADDED_ROW_SELECTOR}:has-text('{sku}')"
        try:
            self.page.wait_for_selector(selector, timeout=QUICK_ORDER_ADD_TIMEOUT_MS)
            return True
        except Exception:
            return False
