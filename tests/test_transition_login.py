import pytest
from playwright.sync_api import sync_playwright

from kinetik_stock.browser import launch_chromium
from kinetik_stock.config import BrandConfig
from kinetik_stock.scrapers import base, transition_bikes

LOGIN_HTML = """<html><head><title>Dealer Login</title></head><body>
<p class="err">Invalid username or password.</p>
<form><input name="Username"><input name="Password" type="password">
<button type="submit">Log in</button></form></body></html>"""


def test_failed_login_explains_what_the_browser_saw(monkeypatch, tmp_path):
    monkeypatch.setattr(transition_bikes, "LOGGED_IN_CHECK_TIMEOUT_MS", 500)
    monkeypatch.setattr(base, "DEBUG_DIR", tmp_path)
    monkeypatch.setenv("TB_USER", "u")
    monkeypatch.setenv("TB_PASS", "p")
    brand = BrandConfig(
        key="transition_bikes", name="Transition Bikes",
        portal_url="https://b2b.transitionbikes.com/Account",
        scraper="x", username_env="TB_USER", password_env="TB_PASS",
    )
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        try:
            scraper = transition_bikes.TransitionBikesScraper(brand, browser)
            # Serve the page without touching the network.
            scraper.page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=LOGIN_HTML))
            with pytest.raises(RuntimeError) as exc:
                scraper.login()
        finally:
            browser.close()
    msg = str(exc.value)
    assert "login form is still showing" in msg
    assert "Invalid username or password" in msg and "Dealer Login" in msg
    assert (tmp_path / "transition_bikes_login_failed.png").exists()
