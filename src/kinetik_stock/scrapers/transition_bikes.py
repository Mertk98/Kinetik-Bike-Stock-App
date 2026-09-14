from __future__ import annotations

from kinetik_stock.models import StockItem
from kinetik_stock.scrapers.base import BaseScraper

# Confirmed against the real login page HTML (a ColdFusion-based site, not
# ASP.NET as first guessed). The page has two <form>s with duplicate
# id="frmLogin" (login form + footer newsletter signup), so selectors target
# unique attributes rather than the id.
USERNAME_SELECTOR = "input[name='Username']"
PASSWORD_SELECTOR = "input[name='Password']"
# The only <button> on the page - the footer form uses <input type="submit">.
LOGIN_BUTTON_SELECTOR = "button[type='submit']"

# TODO: unconfirmed - we don't yet know what a successful login looks like
# (redirect URL, or does /Account just re-render logged in?) or what an
# invalid-credentials error looks like. This checks for the login form
# disappearing as a proxy for "logged in". Run with
# `python run.py --brands transition_bikes --no-headless -v` and watch what
# actually happens on both success and failure, then tighten this check
# (e.g. to a specific dashboard element or exact error text).
LOGGED_IN_CHECK_TIMEOUT_MS = 10000

# TODO: unknown - need the HTML of the dealer's stock/inventory/ETA page
# (wherever it lives post-login) to implement this.
STOCK_PAGE_URL = "https://b2b.transitionbikes.com/Account"
STOCK_ROW_SELECTOR = "table tbody tr"


class TransitionBikesScraper(BaseScraper):
    """Scraper for Transition Bikes' B2B dealer portal
    (https://b2b.transitionbikes.com/Account).

    login() is wired up against the real login form. fetch_stock() is still
    a placeholder - we need the post-login stock/availability page's HTML to
    finish it. Disabled in config/brands.yaml until fetch_stock() is done and
    login() is confirmed against real credentials.
    """

    def login(self) -> None:
        self.page.goto(self.brand_config.portal_url)
        self.page.fill(USERNAME_SELECTOR, self.brand_config.username or "")
        self.page.fill(PASSWORD_SELECTOR, self.brand_config.password or "")
        self.page.click(LOGIN_BUTTON_SELECTOR)
        self.page.wait_for_load_state("networkidle")

        try:
            self.page.wait_for_selector(
                USERNAME_SELECTOR, state="detached", timeout=LOGGED_IN_CHECK_TIMEOUT_MS
            )
        except Exception:
            raise RuntimeError(
                f"Login to {self.brand_config.name} failed (login form still present) - "
                f"check {self.brand_config.username_env}/{self.brand_config.password_env}, "
                "or the success/failure detection needs updating for this portal."
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
                "selectors only - need the real stock/inventory page HTML."
            )

        return items
