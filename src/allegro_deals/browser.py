"""Browser session against Allegro, built to survive DataDome.

Allegro uses DataDome, which fingerprints automated browsers (CDP markers,
navigator.webdriver, missing plugin quirks) far more aggressively than it
scores IP reputation. Countermeasures used here:

- patchright (a stealth fork of Playwright with the same API) instead of
  stock Playwright, which DataDome detects reliably;
- your real installed Chrome when available (``channel="chrome"``) - its
  fingerprint beats the bundled Chromium build;
- a persistent profile, so the DataDome cookie earned on a good visit is
  reused on later runs;
- a warm-up visit to the homepage before hitting search/listing URLs -
  landing cold on ``/listing?...`` is a classic bot tell;
- polite pacing between page loads.

If you still land on a hard block ("Byli jste zablokovani"), the profile's
DataDome cookie is poisoned: delete ``~/.cache/allegro-deals/profile``,
wait a while, and try again headful.
"""

from __future__ import annotations

import os
import random
import shutil
import time
from pathlib import Path

try:  # patchright is a drop-in replacement; fall back to stock playwright
    from patchright.sync_api import sync_playwright

    _ENGINE = "patchright"
except ImportError:  # pragma: no cover
    from playwright.sync_api import sync_playwright

    _ENGINE = "playwright"


class BotBlockedError(RuntimeError):
    """DataDome refused us (captcha we can't solve, or a hard block)."""


def _looks_captcha(html: str) -> bool:
    return "captcha-delivery.com" in html or "geo.captcha-delivery" in html


def _looks_hard_block(html: str) -> bool:
    return "zablokov" in html or "been blocked" in html.lower()


class AllegroBrowser:
    """Minimal wrapper: fetch rendered HTML with polite pacing."""

    def __init__(
        self,
        headful: bool = False,
        profile_dir: str | Path | None = None,
        min_delay: float = 2.5,
        max_delay: float = 6.0,
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
        self._warmed_hosts: set[str] = set()

    def _launch(self):
        launch_kwargs = dict(
            headless=not self.headful,
            locale="cs-CZ",
            viewport={"width": 1440, "height": 900},
        )
        executable = os.environ.get("ALLEGRO_DEALS_CHROME")
        proxy_server = os.environ.get("ALLEGRO_DEALS_PROXY")
        if executable:
            launch_kwargs["executable_path"] = executable
        if proxy_server:
            launch_kwargs["proxy"] = {"server": proxy_server}
        if not executable:
            # Real Chrome first: its fingerprint is much stronger against
            # DataDome than the bundled Chromium.
            try:
                return self._pw.chromium.launch_persistent_context(
                    str(self.profile_dir), channel="chrome", **launch_kwargs
                )
            except Exception:
                pass
        return self._pw.chromium.launch_persistent_context(
            str(self.profile_dir), **launch_kwargs
        )

    def __enter__(self) -> "AllegroBrowser":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._ctx = self._launch()
        return self

    def __exit__(self, *exc) -> None:
        if self._ctx:
            self._ctx.close()
        if self._pw:
            self._pw.stop()

    def reset_profile(self) -> None:
        """Drop the browser profile (poisoned DataDome cookie and all)."""
        shutil.rmtree(self.profile_dir, ignore_errors=True)

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_request
        wait = random.uniform(self.min_delay, self.max_delay) - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _warm_up(self, page, url: str) -> None:
        """Visit the marketplace homepage before any deep URL."""
        host = url.split("/")[2] if "://" in url else ""
        path = url.split(host, 1)[-1] if host else ""
        if not host.endswith(("allegro.cz", "allegro.pl")) or host in self._warmed_hosts:
            return
        self._warmed_hosts.add(host)
        if path in ("", "/"):
            return
        page.goto(f"https://{host}/", wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(random.uniform(2_000, 4_000))

    def get_html(self, url: str, captcha_timeout: float = 180.0) -> str:
        assert self._ctx is not None, "use AllegroBrowser as a context manager"
        self._pace()
        page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._warm_up(page, url)
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(random.uniform(1_500, 3_000))
        html = page.content()
        if _looks_hard_block(html):
            raise BotBlockedError(
                f"DataDome hard block at {url}. The browser profile is now "
                "flagged - run 'allegro-deals reset', wait 15+ minutes, and "
                "retry with --headful."
            )
        if _looks_captcha(html):
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
                if _looks_hard_block(html):
                    raise BotBlockedError(
                        "DataDome escalated to a hard block. Run "
                        "'allegro-deals reset', wait, and retry."
                    )
                if not _looks_captcha(html):
                    print("Captcha solved, continuing.")
                    break
            else:
                raise BotBlockedError(f"Captcha not solved within {captcha_timeout:.0f}s")
        return html
