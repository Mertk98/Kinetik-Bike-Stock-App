from __future__ import annotations

from kinetik_stock.models import StockItem
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


class NorcoScraper(BaseScraper):
    """Scraper for Norco's B2B dealer portal (https://ltpdealer.com/),
    operated by distributor Live to Play Sports (LTP Dealer).

    login() is confirmed against the real login form. fetch_stock() is NOT
    implemented yet - this portal has no single "stock list" page like
    Transition Bikes does. Availability instead lives somewhere in the
    catalog browsing flow (the nav's "Norco Bikes" menu links to per-category
    ProductGroup.sa pages - Mountain, E-Bike, City, Road, Youth, etc., each
    further split into subcategories), which we haven't seen the HTML for
    yet. Once we have an example ProductGroup.sa page showing how SKU/price/
    quantity are laid out, fetch_stock() can likely crawl every "Norco
    Bikes" subcategory URL (already known from the nav) and scrape each one.
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
        raise NotImplementedError(
            "Norco's stock/availability page structure isn't known yet - "
            "see the class docstring."
        )
