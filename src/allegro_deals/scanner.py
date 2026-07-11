"""Orchestrate a scan: search listing -> product pages -> anomaly detection."""

from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path
from typing import Callable

from .browser import AllegroBrowser
from .detect import DEFAULT_MAX_RATIO, find_anomalies, find_cross_discrepancies
from .extract import offers_from_html, product_links_from_html
from .models import Anomaly, CrossDiscrepancy, Offer

SEARCH_URL = "https://allegro.cz/vyhledavani?string={query}"
PL_SEARCH_URL = "https://allegro.pl/listing?string={query}"


def fetch_many(fetcher, urls: list[str], log: Callable[[str], None]) -> dict[str, str]:
    """Fetch a batch of URLs via get_many when the fetcher supports it
    (one Apify actor run), else sequentially (local browser)."""
    if hasattr(fetcher, "get_many"):
        return fetcher.get_many(urls, log=log)
    out: dict[str, str] = {}
    for url in urls:
        out[url] = fetcher.get_html(url)
    return out


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
    search_urls = _search_urls(SEARCH_URL, query, pages)
    log(f"searching: {', '.join(search_urls)}")

    product_urls: list[str] = []
    for html in fetch_many(browser, search_urls, log).values():
        for link in product_links_from_html(html):
            if link not in product_urls:
                product_urls.append(link)

    product_urls = product_urls[:max_products]
    log(f"inspecting {len(product_urls)} product pages")

    anomalies: list[Anomaly] = []
    for url, html in fetch_many(browser, product_urls, log).items():
        offers = [o for o in offers_from_html(html) if o.currency == "CZK"]
        found = find_anomalies(offers, fx_pln_czk, max_ratio=max_ratio)
        for anomaly in found:
            anomaly.context["product_url"] = url
            anomaly.context["query"] = query
            log("ANOMALY:\n" + anomaly.describe())
        anomalies.extend(found)
    return anomalies


def _search_urls(
    template: str,
    query: str,
    pages: int,
    cheap_first: bool = False,
    price_from: float | None = None,
) -> list[str]:
    urls = []
    for page_no in range(1, pages + 1):
        url = template.format(query=urllib.parse.quote(query))
        if cheap_first:
            url += "&order=p"  # price ascending - mispriced offers surface first
        if price_from:
            url += f"&price_from={int(price_from)}"
        if page_no > 1:
            url += f"&p={page_no}"
        urls.append(url)
    return urls


def compare_markets(
    fetcher,
    query_cz: str,
    query_pl: str | None = None,
    fx_pln_czk: float = 5.65,
    pages: int = 2,
    max_offers: int = 100,
    cheap_first: bool = False,
    deep_limit: int = 50,
    price_from: float | None = None,
    log: Callable[[str], None] = print,
) -> tuple[list[CrossDiscrepancy], int, int]:
    """Search both marketplaces and flag cz offers far below their pl price.

    ``cheap_first`` sorts the cz side by ascending price (where mispriced
    offers live); the pl side stays relevance-sorted so reference prices
    reflect the going rate. ``price_from`` filters the cz side to skip the
    sub-price accessories that dominate cheap-sorted results.
    Returns (discrepancies, cz count, pl count).
    """
    cz_urls = _search_urls(
        SEARCH_URL, query_cz, pages, cheap_first=cheap_first, price_from=price_from
    )
    pl_urls = _search_urls(PL_SEARCH_URL, query_pl or query_cz, pages)
    log(f"searching cz: {', '.join(cz_urls)}")
    log(f"searching pl: {', '.join(pl_urls)}")
    htmls = fetch_many(fetcher, cz_urls + pl_urls, log)

    cz_offers: list[Offer] = []
    pl_offers: list[Offer] = []
    for url, html in htmls.items():
        offers = offers_from_html(html)
        if "allegro.cz" in url:
            cz_offers += [o for o in offers if o.currency == "CZK"]
        else:
            pl_offers += [o for o in offers if o.currency == "PLN"]
    cz_offers = cz_offers[:max_offers]
    pl_offers = pl_offers[:max_offers]
    log(f"parsed {len(cz_offers)} CZK offers, {len(pl_offers)} PLN offers")
    for o in sorted(cz_offers, key=lambda o: o.price)[:8]:
        log(f"  cheapest cz: {o.price:>9.0f} CZK  {o.title[:60]}")

    if cheap_first:
        # The whole point of cheap-first is catching offers whose cz rank
        # (cheap) diverges from their pl rank (normal price) - those never
        # meet in two search top-Ns, so resolve them one by one via their
        # shared offer id on allegro.pl.
        pl_ids = {o.offer_id for o in pl_offers if o.offer_id}
        no_id = sum(1 for o in cz_offers if not o.offer_id)
        unmatched = [
            o for o in cz_offers
            if o.offer_id and o.offer_id not in pl_ids and o.price >= 40.0
        ][:deep_limit]
        log(
            f"cz offers: {len(cz_offers)} total, {no_id} without id, "
            f"{len(unmatched)} to deep-check"
        )
        if unmatched:
            log(f"deep-checking {len(unmatched)} cheap cz offers by id on allegro.pl")
            cz_by_id = {o.offer_id: o for o in unmatched}
            urls = [f"https://allegro.pl/oferta/{o.offer_id}" for o in unmatched]
            resolved = 0
            for url, html in fetch_many(fetcher, urls, log).items():
                oid = url.rsplit("/", 1)[1]
                for offer in offers_from_html(html):
                    if offer.currency == "PLN" and (offer.offer_id or oid) == oid:
                        pl_offers.append(
                            Offer(title=offer.title, price=offer.price,
                                  currency="PLN", offer_id=oid, url=url)
                        )
                        resolved += 1
                        cz = cz_by_id[oid]
                        implied = cz.price / offer.price if offer.price else 0
                        if implied < 0.6 * fx_pln_czk:
                            verdict = "!! SUSPICIOUS"
                        elif implied < 0.8 * fx_pln_czk:
                            verdict = "~ cheaper than PL"
                        else:
                            verdict = "ok"
                        log(
                            f"  pair: {cz.price:>8.0f} CZK vs {offer.price:>8.0f} PLN"
                            f"  rate {implied:5.2f}  {verdict}  {cz.title[:45]}"
                        )
                        break
                else:
                    log(f"  no pl twin (cz-only offer): {cz_by_id[oid].title[:55]}")
            log(f"deep check resolved {resolved}/{len(unmatched)} pl twins")

    discrepancies = find_cross_discrepancies(cz_offers, pl_offers, fx_pln_czk)
    for d in discrepancies:
        log(f"DISCREPANCY ({d.kind}):\n" + d.describe())
    return discrepancies, len(cz_offers), len(pl_offers)


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
