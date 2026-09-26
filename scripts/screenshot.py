"""Screenshot a SparkJury page (cockpit or card) with the browser already on this machine.

    uv run --group ops python scripts/screenshot.py http://127.0.0.1:9000/?token=... docs/img/cockpit.png
    uv run --group ops python scripts/screenshot.py http://127.0.0.1:9000/runs/<id>/card.html?token=... docs/img/card.png --full

Uses Playwright with the installed Edge (Windows) or Chrome (macOS / Linux); falls back to Playwright's own
Chromium if neither is present (`uv run --group ops playwright install chromium` once). Waits until the cockpit
has loaded its card (or 40 s), which plain `--headless --screenshot` cannot do through a slow gateway.
"""

from __future__ import annotations

import platform
import sys
import time


def main(url: str, out: str, full: bool = False) -> int:
    from playwright.sync_api import sync_playwright

    channels = ["msedge", "chrome"] if platform.system() == "Windows" else ["chrome", "msedge"]
    with sync_playwright() as p:
        browser = None
        for ch in channels:
            try:
                browser = p.chromium.launch(channel=ch, headless=True)
                break
            except Exception:  # noqa: BLE001 - channel not installed
                continue
        if browser is None:
            browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1150})
        page.goto(url, wait_until="load", timeout=60000)
        for _ in range(40):
            time.sleep(1)
            if page.locator("#card .tiles").count() or page.locator(".tiles").count():
                break
        time.sleep(2)
        page.screenshot(path=out, full_page=full)
        browser.close()
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2], "--full" in sys.argv[3:]))
