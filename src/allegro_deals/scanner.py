"""Orchestrate a scan: search listing -> product pages -> anomaly detection."""

from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path
from typing import Callable

from .browser import AllegroBrowser
from .detect import DEFAULT_MAX_RATIO, find_anomalies
from .extract import offers_from_html, product_links_from_html
from .models import Anomaly

SEARCH_URL = "https://allegro.cz/listing?string={query}&p={page}"


def scan_query(
    browser: AllegroBrowser,
    query: str,
    fx_pln_czk: float,
    pages: int = 1,
    max_products: int = 15,
    max_ratio: float = DEFAULT_MAX_RATIO,
    log: Callable[[str], None] = print,
) -> list[Anomaly]:
    """Search allegro.cz, then inspect product pages for price anomalies.

    Product pages (``/produkt/...``) list every offer for one catalogue
    product, which gives a trustworthy median to compare against - the same
    comparison a human makes when a 239 CZK offer sits under a 1400 CZK one.
    """
    product_urls: list[str] = []
    for page_no in range(1, pages + 1):
        url = SEARCH_URL.format(query=urllib.parse.quote(query), page=page_no)
        log(f"searching: {url}")
        html = browser.get_html(url)
        for link in product_links_from_html(html):
            if link not in product_urls:
                product_urls.append(link)
        if len(product_urls) >= max_products:
            break

    product_urls = product_urls[:max_products]
    log(f"inspecting {len(product_urls)} product pages")

    anomalies: list[Anomaly] = []
    for url in product_urls:
        html = browser.get_html(url)
        offers = [o for o in offers_from_html(html) if o.currency == "CZK"]
        found = find_anomalies(offers, fx_pln_czk, max_ratio=max_ratio)
        for anomaly in found:
            anomaly.context["product_url"] = url
            anomaly.context["query"] = query
            log("ANOMALY:\n" + anomaly.describe())
        anomalies.extend(found)
    return anomalies


def append_findings(anomalies: list[Anomaly], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for a in anomalies:
            fh.write(
                json.dumps(
                    {
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "kind": a.kind,
                        "title": a.offer.title,
                        "price": a.offer.price,
                        "currency": a.offer.currency,
                        "reference_price": a.reference_price,
                        "ratio": round(a.ratio, 3),
                        "implied_fx": round(a.implied_fx, 2),
                        "peer_count": a.peer_count,
                        "offer_id": a.offer.offer_id,
                        "url": a.offer.url,
                        "context": a.context,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
