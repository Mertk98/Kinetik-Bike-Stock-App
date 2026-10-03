from __future__ import annotations

import logging

from kinetik_stock.models import StockItem
from kinetik_stock.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Confirmed against the real login page HTML, pasted by the user from a live
# session at https://vip.santacruzbicycles.com/login/ - an SAP Commerce
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

# UNCONFIRMED - no real post-login capture yet. Need to know what a
# successful sign-in actually does: does it redirect to a specific URL, or
# does some element appear/disappear (e.g. the header's SiteLogin slot -
# currently an empty <scb-login> in the logged-out capture - switching to an
# account name/logout link)? Filled in once the user pastes that capture.
LOGGED_IN_SELECTOR = None
LOGGED_IN_CHECK_TIMEOUT_MS = 15000


class SantaCruzScraper(BaseScraper):
    """Scraper for Santa Cruz Bicycles' B2B dealer portal
    (https://vip.santacruzbicycles.com/), an SAP Commerce Cloud / Spartacus
    storefront.

    login()'s form-fill/submit is confirmed against the real login page, but
    it still raises NotImplementedError before returning - there's no
    confirmed way yet to tell a successful login from a failed one (see
    LOGGED_IN_SELECTOR above). fetch_stock() is fully unimplemented - no
    stock/availability page has been captured yet either.
    """

    def login(self) -> None:
        self.page.goto(self.brand_config.portal_url)
        self.page.fill(USERNAME_SELECTOR, self.brand_config.username or "")
        self.page.fill(PASSWORD_SELECTOR, self.brand_config.password or "")
        self.page.click(LOGIN_BUTTON_SELECTOR)

        raise NotImplementedError(
            "Santa Cruz login form fill/submit is wired up, but there's no "
            "confirmed way yet to verify the login actually succeeded - need "
            "a real post-login capture (redirect URL, or an element that "
            "appears/disappears in the header) to finish this."
        )

    def fetch_stock(self) -> list[StockItem]:
        raise NotImplementedError(
            "Santa Cruz scraper not implemented yet - need a real stock/"
            "availability page capture from the dealer portal."
        )
