from __future__ import annotations

import logging

from kinetik_stock.models import StockItem
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

# UNCONFIRMED - still needed to finish fetch_stock():
# - The quick-order-table row's HTML once a part number is successfully
#   added (to read back SKU/name/price and confirm it matches what was
#   typed).
# - The warning message's exact HTML/text for a skipped (discontinued or
#   OOS-with-no-ETA) part number, to tell that case apart from a real match.
# - The cart page's HTML, specifically the "EST. Shipment QTY" field's
#   markup per line (plain text? a date? a table?) and the "Clear Cart"
#   button's selector.


class SantaCruzScraper(BaseScraper):
    """Scraper for Santa Cruz Bicycles' B2B dealer portal
    (https://vip.santacruzbicycles.com/), an SAP Commerce Cloud / Spartacus
    storefront.

    login() is confirmed against real captured login and post-login pages.
    fetch_stock() is fully unimplemented - the Quick Order page's search
    input, 20-item batch limit, and Add to cart button are confirmed (see
    QUICK_ORDER_PAGE_URL above), but the row/warning markup and the cart
    page's "EST. Shipment QTY" field aren't captured yet, so there's nothing
    to parse a real ETA from.
    """

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
            "Santa Cruz fetch_stock() not implemented yet - the Quick Order "
            "page's search/add-to-cart flow is confirmed, but the added-row "
            "markup, the skipped-item warning, and the cart page's EST. "
            "Shipment QTY field all still need a real capture."
        )
