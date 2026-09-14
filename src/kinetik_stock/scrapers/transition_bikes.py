from __future__ import annotations

from kinetik_stock.models import StockItem, StockStatus
from kinetik_stock.scrapers.base import BaseScraper

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


class TransitionBikesScraper(BaseScraper):
    """Scraper for Transition Bikes' B2B dealer portal
    (https://b2b.transitionbikes.com/Account).

    login() is wired up and confirmed against the real login form + a real
    post-login account page.

    fetch_stock() is a CALIBRATION pass, not final parsing: we know the stock
    data lives at Account_StockList.cfm, but not its table's column layout
    or status wording yet. It currently dumps every table row's raw cell
    text as one StockItem per row (status=UNKNOWN). Run this with
    `python run.py --brands transition_bikes --no-headless -v` and send back
    the resulting CSV so fetch_stock() can be rewritten to map real columns
    (SKU, model, size/color, status, ETA, qty) into proper StockItems with
    correct StockStatus values.

    Disabled in config/brands.yaml until that final parsing is done - test it
    explicitly with `--brands transition_bikes` in the meantime.
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
        for table_index, table in enumerate(self.page.query_selector_all("table")):
            for row_index, row in enumerate(table.query_selector_all("tr")):
                cell_texts = [
                    cell.inner_text().strip() for cell in row.query_selector_all("td")
                ]
                cell_texts = [text for text in cell_texts if text]
                if not cell_texts:
                    continue

                items.append(
                    StockItem(
                        brand=self.brand_config.name,
                        sku=f"table{table_index}-row{row_index}",
                        product_title=" | ".join(cell_texts),
                        status=StockStatus.UNKNOWN,
                        raw_status_text=" | ".join(cell_texts),
                        source_url=source_url,
                    )
                )

        return items
