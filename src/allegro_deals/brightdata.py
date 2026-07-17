"""Fetch Allegro pages through Bright Data's Web Unlocker API.

Web Unlocker takes a URL and returns the unlocked, rendered HTML in a
single synchronous request - Bright Data handles DataDome (proxy
selection, fingerprints, challenges) on their side. Billing is per
successful request.

Setup: create a Web Unlocker zone at https://brightdata.com/cp/zones,
then set ``BRIGHTDATA_API_TOKEN`` (account settings -> API tokens) and
``BRIGHTDATA_ZONE`` (the zone name, defaults to ``web_unlocker1``).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

API_URL = "https://api.brightdata.com/request"
MAX_PARALLEL = 5


class BrightDataError(RuntimeError):
    pass


def country_for_url(url: str) -> str:
    """Proxy exit country per marketplace - Allegro 403s foreign IPs."""
    host = url.split("/")[2] if "://" in url else ""
    if host.endswith(".pl"):
        return "pl"
    if host.endswith(".sk"):
        return "sk"
    return "cz"


def build_request_payload(url: str, zone: str) -> dict:
    return {
        "zone": zone,
        "url": url,
        "format": "raw",
        "country": country_for_url(url),
    }


class BrightDataFetcher:
    """Same duck-type as the other engines: get_html / get_many."""

    def __init__(self, token: str | None = None, zone: str | None = None) -> None:
        self.token = (
            token
            or os.environ.get("BRIGHTDATA_API_TOKEN")
            or os.environ.get("BRIGHTDATA_TOKEN")
            or ""
        )
        if not self.token:
            raise BrightDataError(
                "no Bright Data token: set BRIGHTDATA_API_TOKEN (create one "
                "at https://brightdata.com, account settings -> API tokens)"
            )
        self.zone = zone or os.environ.get("BRIGHTDATA_ZONE", "web_unlocker1")

    def __enter__(self) -> "BrightDataFetcher":
        return self

    def __exit__(self, *exc) -> None:
        pass

    def get_html(self, url: str, attempts: int = 3) -> str:
        payload = json.dumps(build_request_payload(url, self.zone)).encode()
        last_error = ""
        for attempt in range(attempts):
            req = urllib.request.Request(
                API_URL,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.token}",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    return resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:300]
                last_error = f"Bright Data {exc.code} for {url}: {detail}"
                if exc.code < 500:  # 4xx won't get better on retry
                    raise BrightDataError(last_error) from exc
            except (TimeoutError, OSError) as exc:
                last_error = f"Bright Data request for {url} failed: {exc}"
            if attempt + 1 < attempts:
                time.sleep(3 * (attempt + 1))
        raise BrightDataError(last_error)

    def get_many(self, urls: list[str], log=print) -> dict[str, str]:
        if not urls:
            return {}
        log(f"brightdata: fetching {len(urls)} pages ({MAX_PARALLEL} in parallel)...")
        out: dict[str, str] = {}
        failures: list[str] = []
        lock = Lock()
        done = 0

        def fetch(url: str) -> None:
            nonlocal done
            started = time.monotonic()
            try:
                html = self.get_html(url)
            except BrightDataError as exc:
                with lock:
                    done += 1
                    failures.append(f"{url}: {exc}")
                    log(f"  [{done}/{len(urls)}] FAILED {url[:80]}")
                return
            elapsed = time.monotonic() - started
            with lock:
                done += 1
                out[url] = html
                log(
                    f"  [{done}/{len(urls)}] {elapsed:5.1f}s {len(html) // 1024:>5} KB  "
                    f"{url[:80]}"
                )

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            list(pool.map(fetch, urls))
        if failures:
            log(f"brightdata: {len(failures)} of {len(urls)} pages failed")
        return out
