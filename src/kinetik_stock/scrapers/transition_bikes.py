from __future__ import annotations

from kinetik_stock.models import StockItem
from kinetik_stock.scrapers.base import BaseScraper

# TODO: fill in once we have the real login page HTML from
# https://b2b.transitionbikes.com/Account. The `/Account` path is a common
# convention for ASP.NET Identity-based sites, so these are placeholder
# guesses - replace them with the real field selectors before enabling this
# brand in config/brands.yaml.
USERNAME_SELECTOR = "input[name='Email']"
PASSWORD_SELECTOR = "input[name='Password']"
LOGIN_BUTTON_SELECTOR = "button[type='submit']"

# TODO: once logged in, find the actual stock/inventory page URL and update
# this (it may not be the same page you land on after login).
STOCK_PAGE_URL = "https://b2b.transitionbikes.com/Account"

# TODO: replace with the real row/column selectors from the stock page.
STOCK_ROW_SELECTOR = "table tbody tr"


class TransitionBikesScraper(BaseScraper):
    """Scraper for Transition Bikes' B2B dealer portal.

    Not wired up yet - selectors below are placeholders. See the TODOs.
    Disabled in config/brands.yaml until confirmed against the real site.
    """

    def login(self) -> None:
        self.page.goto(self.brand_config.portal_url)
        self.page.fill(USERNAME_SELECTOR, self.brand_config.username or "")
        self.page.fill(PASSWORD_SELECTOR, self.brand_config.password or "")
        self.page.click(LOGIN_BUTTON_SELECTOR)
        self.page.wait_for_load_state("networkidle")

        # TODO: replace with a real check that login succeeded, e.g.
        # checking for a "Log out" link or a known logged-in-only element.
        raise NotImplementedError(
            "TransitionBikesScraper.login() has placeholder selectors only - "
            "confirm the real login form fields before enabling this brand."
        )

    def fetch_stock(self) -> list[StockItem]:
        self.page.goto(STOCK_PAGE_URL)
        rows = self.page.query_selector_all(STOCK_ROW_SELECTOR)

        items: list[StockItem] = []
        for row in rows:
            # TODO: replace with real column selectors/parsing once we've
            # seen the actual stock page markup.
            raise NotImplementedError(
                "TransitionBikesScraper.fetch_stock() has placeholder "
                "selectors only - confirm the real stock table markup."
            )

        return items
