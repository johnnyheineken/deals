"""Fetch rendered Allegro pages through Apify instead of a local browser.

Runs the generic ``apify/web-scraper`` actor: it loads each URL in a real
headless browser on Apify's infrastructure (behind Apify proxies, which is
what gets past DataDome) and returns the rendered HTML. The rest of the
pipeline (extraction, detection) is identical to the local-browser path.

Needs an Apify account and its API token (https://console.apify.com,
Settings -> API & Integrations), passed via ``APIFY_TOKEN`` or
``--apify-token``. Residential proxies give by far the best pass rate on
Allegro; set ``APIFY_PROXY_GROUPS=DATACENTER`` to trade reliability for
lower cost.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

API_BASE = "https://api.apify.com/v2"
DEFAULT_ACTOR = "apify~web-scraper"

_PAGE_FUNCTION = """
async function pageFunction(context) {
    return {
        url: context.request.url,
        status: context.response ? context.response.status : null,
        html: document.documentElement.outerHTML,
    };
}
""".strip()


class ApifyError(RuntimeError):
    pass


def build_actor_input(urls: list[str], proxy_groups: list[str], country: str = "CZ") -> dict:
    return {
        "startUrls": [{"url": u} for u in urls],
        "pageFunction": _PAGE_FUNCTION,
        "proxyConfiguration": {
            "useApifyProxy": True,
            "apifyProxyGroups": proxy_groups,
            "apifyProxyCountry": country,
        },
        "maxPagesPerCrawl": len(urls),
        "maxRequestRetries": 2,
        "pageLoadTimeoutSecs": 90,
        "injectJQuery": False,
        "browserLog": False,
    }


def htmls_from_items(items: list[dict]) -> dict[str, str]:
    """Map url -> html from a web-scraper dataset, skipping failed loads."""
    out: dict[str, str] = {}
    for item in items:
        url = item.get("url")
        html = item.get("html")
        if isinstance(url, str) and isinstance(html, str) and html:
            out[url] = html
    return out


class ApifyFetcher:
    """Same duck-type as AllegroBrowser: get_html / get_many, context manager."""

    def __init__(
        self,
        token: str | None = None,
        actor: str = DEFAULT_ACTOR,
        poll_interval: float = 10.0,
        run_timeout: float = 900.0,
    ) -> None:
        self.token = token or os.environ.get("APIFY_TOKEN") or ""
        if not self.token:
            raise ApifyError(
                "no Apify token: set APIFY_TOKEN or pass --apify-token "
                "(get one at https://console.apify.com)"
            )
        self.actor = actor
        self.poll_interval = poll_interval
        self.run_timeout = run_timeout
        groups = os.environ.get("APIFY_PROXY_GROUPS", "RESIDENTIAL")
        self.proxy_groups = [g.strip() for g in groups.split(",") if g.strip()]
        self.proxy_country = os.environ.get("APIFY_PROXY_COUNTRY", "CZ")

    def __enter__(self) -> "ApifyFetcher":
        return self

    def __exit__(self, *exc) -> None:
        pass

    # -- HTTP plumbing ------------------------------------------------------
    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{API_BASE}{path}{'&' if '?' in path else '?'}token={self.token}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            url, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise ApifyError(f"Apify API {exc.code} on {path}: {detail}") from exc

    # -- public interface ---------------------------------------------------
    def get_many(self, urls: list[str], log=print) -> dict[str, str]:
        """Fetch URLs, one actor run per marketplace country.

        allegro.pl serves DataDome 403s to Czech residential IPs and vice
        versa, so each domain gets proxies from its own country.
        """
        by_country: dict[str, list[str]] = {}
        for url in urls:
            host = url.split("/")[2] if "://" in url else ""
            if host.endswith(".pl"):
                country = "PL"
            elif host.endswith(".sk"):
                country = "SK"
            elif host.endswith(".de"):
                country = "DE"
            else:
                country = self.proxy_country
            by_country.setdefault(country, []).append(url)
        out: dict[str, str] = {}
        for country, batch in by_country.items():
            out.update(self._run_batch(batch, country, log))
        return out

    def _run_batch(self, urls: list[str], country: str, log=print) -> dict[str, str]:
        if not urls:
            return {}
        actor_input = build_actor_input(urls, self.proxy_groups, country)
        run = self._request("POST", f"/acts/{self.actor}/runs", actor_input)["data"]
        run_id, dataset_id = run["id"], run["defaultDatasetId"]
        log(f"apify: run {run_id} fetching {len(urls)} pages via {country} proxies...")
        deadline = time.monotonic() + self.run_timeout
        status = run["status"]
        while status in ("READY", "RUNNING") and time.monotonic() < deadline:
            time.sleep(self.poll_interval)
            status = self._request("GET", f"/actor-runs/{run_id}")["data"]["status"]
        if status != "SUCCEEDED":
            raise ApifyError(
                f"Apify run {run_id} ended with status {status} - check "
                f"https://console.apify.com/actors/runs/{run_id}"
            )
        items = self._request("GET", f"/datasets/{dataset_id}/items?format=json&clean=true")
        htmls = htmls_from_items(items if isinstance(items, list) else [])
        missing = [u for u in urls if u not in htmls]
        if missing:
            log(f"apify: {len(missing)} of {len(urls)} pages failed to load")
        return htmls

    def get_html(self, url: str) -> str:
        htmls = self.get_many([url], log=lambda _msg: None)
        if url not in htmls:
            raise ApifyError(f"Apify could not fetch {url}")
        return htmls[url]
