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


class SantaCruzScraper(BaseScraper):
    """Scraper for Santa Cruz Bicycles' B2B dealer portal
    (https://vip.santacruzbicycles.com/), an SAP Commerce Cloud / Spartacus
    storefront.

    login() is confirmed against real captured login and post-login pages.
    fetch_stock() is fully unimplemented - no stock/availability page has
    been captured yet (the account flyout's "Quick Order" and various
    report links - open orders, shipments, price sheets, booking program -
    are known to exist from the post-login page's own nav, but which one(s)
    hold per-SKU availability/ETA isn't confirmed yet).
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
            "Santa Cruz scraper not implemented yet - need a real stock/"
            "availability page capture from the dealer portal."
        )
