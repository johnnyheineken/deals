"""Cross-country price comparison on the Kaufland marketplace.

Kaufland runs one marketplace across .cz/.sk/.de (and more) with
seller-set per-country prices and *shared numeric product ids* - the same
bug surface as Allegro's cross-market mirroring. We harvest candidate ids
from a kaufland.cz search, fetch each id's product page in every country,
and rank the converted price gaps.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Callable

from .extract import kaufland_candidate_ids, kaufland_offers_from_html, kaufland_product_from_html
from .scanner import fetch_many

SEARCH_URL = "https://www.kaufland.cz/s/?search_value={query}"
PRODUCT_URL = "https://www.kaufland.{cc}/product/{pid}/"
COUNTRY_CURRENCY = {"cz": "CZK", "sk": "EUR", "de": "EUR"}


@dataclass
class KauflandGap:
    pid: str
    title: str
    prices_czk: dict[str, float]  # country -> converted price
    raw: dict[str, str]  # country -> "34.81 EUR"

    @property
    def cheapest(self) -> tuple[str, float]:
        return min(self.prices_czk.items(), key=lambda kv: kv[1])

    @property
    def dearest(self) -> tuple[str, float]:
        return max(self.prices_czk.items(), key=lambda kv: kv[1])

    @property
    def ratio(self) -> float:
        return self.cheapest[1] / self.dearest[1] if self.dearest[1] else 1.0

    @property
    def saving(self) -> float:
        return self.dearest[1] - self.cheapest[1]

    def describe(self) -> str:
        cc, czk = self.cheapest
        rc, rczk = self.dearest
        raws = ", ".join(f"{c}: {r}" for c, r in sorted(self.raw.items()))
        return (
            f"{self.title[:70]}\n"
            f"  cheapest kaufland.{cc} ~{czk:.0f} CZK vs kaufland.{rc} ~{rczk:.0f} CZK "
            f"({self.ratio:.0%}, save ~{self.saving:.0f} CZK)\n"
            f"  raw: {raws}\n"
            f"  https://www.kaufland.{cc}/product/{self.pid}/"
        )


def compare_kaufland(
    fetcher,
    query: str,
    rates: dict[str, float],
    countries: list[str] | None = None,
    limit: int = 15,
    max_ratio: float = 0.8,
    min_saving: float = 200.0,
    log: Callable[[str], None] = print,
) -> list[KauflandGap]:
    countries = countries or ["cz", "de"]
    search = SEARCH_URL.format(query=urllib.parse.quote(query))
    log(f"kaufland search: {search}")
    html = fetch_many(fetcher, [search], log).get(search, "")
    tiles = kaufland_offers_from_html(html)
    ids = kaufland_candidate_ids(html, limit=limit)
    log(f"parsed {len(tiles)} tiles, {len(ids)} candidate product ids")
    if not ids:
        return []

    urls = [
        PRODUCT_URL.format(cc=cc, pid=pid) for pid in ids for cc in countries
    ]
    htmls = fetch_many(fetcher, urls, log)

    gaps: list[KauflandGap] = []
    for pid in ids:
        prices_czk: dict[str, float] = {}
        raw: dict[str, str] = {}
        title = ""
        for cc in countries:
            page = htmls.get(PRODUCT_URL.format(cc=cc, pid=pid), "")
            offer = kaufland_product_from_html(page)
            if offer is None:
                continue
            prices_czk[cc] = offer.price * rates[offer.currency]
            raw[cc] = f"{offer.price:.2f} {offer.currency}"
            if cc == "cz" or not title:
                title = offer.title
        if len(prices_czk) < 2:
            continue
        gap = KauflandGap(pid=pid, title=title, prices_czk=prices_czk, raw=raw)
        verdict = "GAP" if gap.ratio <= max_ratio and gap.saving >= min_saving else "ok"
        log(
            f"  {pid}: " + ", ".join(f"{c}~{v:.0f}CZK" for c, v in sorted(gap.prices_czk.items()))
            + f"  ratio {gap.ratio:.2f}  {verdict}  {title[:40]}"
        )
        if verdict == "GAP":
            gaps.append(gap)
    gaps.sort(key=lambda g: -g.saving)
    for g in gaps:
        log("KAUFLAND GAP:\n" + g.describe())
    if not gaps:
        log("no cross-country kaufland gaps")
    return gaps
