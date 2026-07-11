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
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API_URL = "https://api.brightdata.com/request"
MAX_PARALLEL = 5


class BrightDataError(RuntimeError):
    pass


def country_for_url(url: str) -> str:
    """Proxy exit country per marketplace - allegro.pl 403s foreign IPs."""
    host = url.split("/")[2] if "://" in url else ""
    return "pl" if host.endswith(".pl") else "cz"


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

    def get_html(self, url: str) -> str:
        payload = build_request_payload(url, self.zone)
        req = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode(),
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
            raise BrightDataError(
                f"Bright Data {exc.code} for {url}: {detail}"
            ) from exc

    def get_many(self, urls: list[str], log=print) -> dict[str, str]:
        if not urls:
            return {}
        log(f"brightdata: fetching {len(urls)} pages...")
        out: dict[str, str] = {}
        failures: list[str] = []

        def fetch(url: str) -> None:
            try:
                out[url] = self.get_html(url)
            except BrightDataError as exc:
                failures.append(f"{url}: {exc}")

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            list(pool.map(fetch, urls))
        if failures:
            log(f"brightdata: {len(failures)} of {len(urls)} pages failed")
            for line in failures[:3]:
                log(f"  {line}")
        return out
