from __future__ import annotations

import logging
import re
from typing import Optional

from kinetik_stock.models import StockItem, StockStatus
from kinetik_stock.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Confirmed against the real login page HTML (a ColdFusion-based site, not
# ASP.NET as first guessed). The page has two <form>s with duplicate
# id="frmLogin" (login form + footer newsletter signup), so selectors target
# unique attributes rather than the id.
USERNAME_SELECTOR = "input[name='Username']"
PASSWORD_SELECTOR = "input[name='Password']"
# The only <button> on the page - the footer form uses <input type="submit">.
LOGIN_BUTTON_SELECTOR = "button[type='submit']"

# Confirmed against the real post-login account page (Account_Home.cfm):
# a successful login lands somewhere with an /Account/Logout link in the nav.
LOGGED_IN_SELECTOR = "a[href='/Account/Logout']"
LOGGED_IN_CHECK_TIMEOUT_MS = 10000

# Confirmed: the account page has a "STOCK LIST" section
# (refLocation="StockList" refURL="/Account_StockList.cfm") that AJAX-loads
# this URL's HTML into the page. Navigating there directly re-uses the same
# authenticated session/cookies.
STOCK_PAGE_URL = "https://b2b.transitionbikes.com/Account_StockList.cfm"

# The stock list also includes Accessories, Components, Parts, Frames, etc.
# Only bikes matter for Timesact/storefront availability, so filter to this
# category by default. Add more (e.g. "framesets") if that changes.
INCLUDE_CATEGORIES = {"complete bikes"}

# Each row's <td> cells get joined with " | ". Column count varies row to
# row because blank price cells (no sale price, no customer price, etc.) are
# dropped rather than left as empty segments - so we anchor on the first 4
# fields (Category, Vendor, Product, Part Number) and the last field
# (Availability), which are always present in that position, rather than a
# fixed total column count.
MIN_FIELDS = 5  # category, vendor, product, part number, availability

VARIANT_RE = re.compile(r"^(?P<before>.*?)\s*\((?P<variant>[^)]*)\)\s*(?P<after>.*)$")
PRICE_RE = re.compile(r"^\$([\d,]+\.\d{2})$")


def parse_regular_retail_price(fields: list[str]) -> Optional[float]:
    """Regular Retail is always the first dollar-amount field after Part
    Number in the row's field order (UPC12/EAN13 are plain numbers with no
    '$', and any of Sale Retail / Regular or Sale Customer Price can be
    missing) - so take the first '$...' field rather than a fixed index.
    """
    for text in fields:
        match = PRICE_RE.match(text.strip())
        if match:
            return float(match.group(1).replace(",", ""))
    return None


def split_product_and_variant(product_field: str) -> tuple[str, Optional[str]]:
    """'Complete: Bandit Hardtail (One Size, Black and Green)' ->
    ('Complete: Bandit Hardtail', 'One Size, Black and Green'). Handles a
    trailing suffix after the parens too, e.g. '... (X-Large, White) - USA'.
    """
    match = VARIANT_RE.match(product_field.strip())
    if not match:
        return product_field.strip(), None

    before = match.group("before").strip()
    after = match.group("after").strip()
    variant = match.group("variant").strip()
    title = f"{before} {after}".strip() if after else before
    return title, variant


def normalize_status(raw_text: str) -> StockStatus:
    # Exact stock counts aren't available for every SKU (only "Low Stock (N)"
    # gives one, plain "In Stock" doesn't) so counts aren't used - just the
    # four buckets the portal's availability text actually distinguishes.
    lowered = raw_text.strip().lower()

    if "low stock" in lowered:
        return StockStatus.LOW_STOCK
    if lowered == "in stock":
        return StockStatus.IN_STOCK
    if lowered == "out of stock":
        return StockStatus.OUT_OF_STOCK
    if lowered == "pre-order":
        # No specific date given by this portal - just a future-availability
        # flag, which is what ETA means for our purposes.
        return StockStatus.ETA
    # No "Discontinued" example seen yet - if one turns up with different
    # wording, add it here.
    return StockStatus.UNKNOWN


def split_size_color(variant: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """'Large, Moonstone' -> ('Large', 'Moonstone'). Every variant seen from
    this portal is exactly "SIZE, COLOR" (maxsplit=1 in case a color name
    ever contains a comma).
    """
    if not variant:
        return None, None
    parts = variant.split(",", 1)
    if len(parts) != 2:
        return variant.strip(), None
    size, color = parts
    return size.strip(), color.strip()


# Model names that are two words - everything else is assumed to be the
# single first word of the (Complete:-stripped) product title. Confirmed
# against the storefront nav: transitionbikes.com/Bikes/<name with the
# space removed>, e.g. "Repeater PT" -> /Bikes/RepeaterPT.
TWO_WORD_BIKE_NAMES = ["PBJ 24", "Regulator CX", "Regulator SX", "Repeater PT"]

PRODUCT_PAGE_BASE_URL = "https://www.transitionbikes.com/Bikes"


def parse_bike_name_and_build_kit(product_title: str) -> tuple[str, Optional[str]]:
    """'Complete: Repeater PT Carbon AXS' -> ('Repeater PT', 'Carbon AXS').
    'Complete: Regulator CX Deore - USA' -> ('Regulator CX', 'Deore') - a
    region suffix after ' - ' is dropped since it's not part of the build kit.
    """
    text = product_title.strip()
    if text.lower().startswith("complete:"):
        text = text[len("complete:") :].strip()
    text = text.split(" - ", 1)[0].strip()

    for name in TWO_WORD_BIKE_NAMES:
        if text == name or text.startswith(name + " "):
            build_kit = text[len(name) :].strip()
            return name, build_kit or None

    parts = text.split(" ", 1)
    bike_name = parts[0]
    build_kit = parts[1].strip() if len(parts) > 1 else None
    return bike_name, build_kit


def product_page_url(bike_name: str) -> str:
    return f"{PRODUCT_PAGE_BASE_URL}/{bike_name.replace(' ', '')}"


def fetch_eta_date(page, product_url: str, build_kit: str, color: str, size: str) -> Optional[str]:
    """Visit the bike's public product page and read the ETA shown after
    selecting the given build kit, color, and size.

    NOT YET IMPLEMENTED - we don't have the real product page HTML yet, so
    we don't know: what the build kit / color / size selectors look like
    (dropdowns, swatches, buttons), whether picking one triggers a page
    reload or just a JS-driven DOM update, or the exact markup the ETA text
    appears in ("under the price"). Returns None (-> "N/A" in the CSV) for
    now rather than guessing selectors against the live site.
    """
    logger.debug(
        "ETA lookup not yet implemented for %s (%s / %s / %s) - %s",
        product_url,
        build_kit,
        color,
        size,
        "leaving eta_date as N/A",
    )
    return None


class TransitionBikesScraper(BaseScraper):
    """Scraper for Transition Bikes' B2B dealer portal
    (https://b2b.transitionbikes.com/Account).

    Both login() and fetch_stock() are wired up against real portal HTML/data.
    Still not enabled by default in config/brands.yaml pending a live
    re-run to confirm this parsing against the real page (it was written
    from a sample CSV dump, not by directly inspecting the live table).
    """

    def login(self) -> None:
        self.page.goto(self.brand_config.portal_url)
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
                f"check {self.brand_config.username_env}/{self.brand_config.password_env}."
            )

    def fetch_stock(self) -> list[StockItem]:
        self.page.goto(STOCK_PAGE_URL)
        source_url = self.page.url

        items: list[StockItem] = []
        for table in self.page.query_selector_all("table"):
            for row in table.query_selector_all("tr"):
                cell_texts = [
                    cell.inner_text().strip() for cell in row.query_selector_all("td")
                ]
                cell_texts = [text for text in cell_texts if text]

                if len(cell_texts) < MIN_FIELDS:
                    continue
                if cell_texts[1].strip().upper() == "VENDOR":
                    continue  # header row

                category, _vendor, product_field, part_number = cell_texts[:4]
                raw_status = cell_texts[-1]

                if category.strip().lower() not in INCLUDE_CATEGORIES:
                    continue

                product_title, variant = split_product_and_variant(product_field)
                size, color = split_size_color(variant)
                status = normalize_status(raw_status)
                regular_retail_price = parse_regular_retail_price(cell_texts[4:-1])

                item = StockItem(
                    brand=self.brand_config.name,
                    sku=part_number,
                    product_title=product_title,
                    variant=variant,
                    size=size,
                    color=color,
                    status=status,
                    regular_retail_price=regular_retail_price,
                    raw_status_text=raw_status,
                    source_url=source_url,
                )

                # Only pre-order/ETA bikes have a date to find, and it's an
                # extra page visit per SKU, so skip everything else.
                if status == StockStatus.ETA and size and color:
                    bike_name, build_kit = parse_bike_name_and_build_kit(product_title)
                    item.eta_date = fetch_eta_date(
                        self.page,
                        product_page_url(bike_name),
                        build_kit or "",
                        color,
                        size,
                    )

                items.append(item)

        return items
