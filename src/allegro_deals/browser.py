"""Playwright session against Allegro, built to survive DataDome.

Run this on a residential connection (your home machine). Datacenter IPs get
a captcha wall no matter what. A persistent browser profile keeps the
DataDome cookie, so after solving one captcha in headful mode, later
headless runs typically pass.
"""

from __future__ import annotations

import random
import time
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class BotBlockedError(RuntimeError):
    """DataDome served a captcha and we cannot solve it headlessly."""


def _looks_blocked(html: str) -> bool:
    return "captcha-delivery.com" in html or "geo.captcha-delivery" in html


class AllegroBrowser:
    """Minimal wrapper: fetch rendered HTML with polite pacing."""

    def __init__(
        self,
        headful: bool = False,
        profile_dir: str | Path | None = None,
        min_delay: float = 1.5,
        max_delay: float = 4.0,
    ) -> None:
        self.headful = headful
        self.profile_dir = Path(
            profile_dir or Path.home() / ".cache" / "allegro-deals" / "profile"
        )
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._pw = None
        self._ctx = None
        self._last_request = 0.0

    def __enter__(self) -> "AllegroBrowser":
        from playwright.sync_api import sync_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(self.profile_dir),
            headless=not self.headful,
            locale="cs-CZ",
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        return self

    def __exit__(self, *exc) -> None:
        if self._ctx:
            self._ctx.close()
        if self._pw:
            self._pw.stop()

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_request
        wait = random.uniform(self.min_delay, self.max_delay) - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def get_html(self, url: str, captcha_timeout: float = 180.0) -> str:
        assert self._ctx is not None, "use AllegroBrowser as a context manager"
        self._pace()
        page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(2_000)
        html = page.content()
        if _looks_blocked(html):
            if not self.headful:
                raise BotBlockedError(
                    f"DataDome captcha at {url}. Re-run with --headful once to "
                    "solve it; the cookie persists in the browser profile."
                )
            print(f"Captcha detected at {url} - please solve it in the browser window...")
            deadline = time.monotonic() + captcha_timeout
            while time.monotonic() < deadline:
                page.wait_for_timeout(2_000)
                html = page.content()
                if not _looks_blocked(html):
                    print("Captcha solved, continuing.")
                    break
            else:
                raise BotBlockedError(f"Captcha not solved within {captcha_timeout:.0f}s")
        return html
