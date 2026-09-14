from __future__ import annotations

from pathlib import Path

# This environment ships a pinned Chromium at a fixed path (see
# PLAYWRIGHT_BROWSERS_PATH); pointing launch() at it directly avoids version
# mismatches between the pip-installed `playwright` package and whatever
# browser revision happens to be on disk. Falls back to Playwright's own
# managed browser (e.g. on a dev machine that ran `playwright install`).
_PINNED_CHROMIUM = Path("/opt/pw-browsers/chromium")


def launch_chromium(playwright, *, headless: bool = True):
    if _PINNED_CHROMIUM.exists():
        return playwright.chromium.launch(
            headless=headless, executable_path=str(_PINNED_CHROMIUM)
        )
    return playwright.chromium.launch(headless=headless)
