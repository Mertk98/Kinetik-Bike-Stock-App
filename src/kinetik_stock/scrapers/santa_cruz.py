from __future__ import annotations

from kinetik_stock.models import StockItem
from kinetik_stock.scrapers.base import BaseScraper

# PLACEHOLDER - no real portal HTML/network captures have been seen yet.
# Every selector/URL below is a guess and WILL need to be replaced once the
# user pastes real captures from their own live dealer-portal session (same
# workflow used to build devinci.py and transition_bikes.py): the login
# page's HTML, the field names/ids for username and password, what a
# successful login redirects to (or what element proves it), and the stock/
# availability page's HTML structure (rows, status text, size/color/SKU
# layout, any ETA field).
LOGIN_URL_PLACEHOLDER = "https://dealer.santacruzbicycles.com/"


class SantaCruzScraper(BaseScraper):
    """Scraper for Santa Cruz Bicycles' B2B dealer portal.

    UNCONFIRMED - no real portal captures yet, see module docstring above.
    login() and fetch_stock() both raise NotImplementedError until real
    HTML/network data is available to derive real selectors from.
    """

    def login(self) -> None:
        raise NotImplementedError(
            "Santa Cruz scraper not implemented yet - need a real login page "
            "capture (HTML + field names) from the dealer portal."
        )

    def fetch_stock(self) -> list[StockItem]:
        raise NotImplementedError(
            "Santa Cruz scraper not implemented yet - need a real stock/"
            "availability page capture from the dealer portal."
        )
