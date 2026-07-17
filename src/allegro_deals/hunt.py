"""Multi-source deal hunt: Allegro cz/pl/sk triangulation + Kaufland + Bazos.

All prices are normalised to CZK via CNB rates. Three detectors:

1. Allegro triangulation - the same offer id exists on allegro.cz (CZK),
   allegro.pl (PLN) and allegro.sk (EUR). Any pair whose converted prices
   diverge hard is flagged; when the raw numbers match (239 CZK vs 239 PLN),
   it's a currency swap.
2. Kaufland vs Allegro - same catalogue product (matched by model number)
   priced differently across marketplaces, either direction.
3. Bazos - used-goods classifieds priced far under the new-goods median.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from statistics import median
from typing import Callable

from .detect import model_tokens
from .extract import bazos_offers_from_html, kaufland_offers_from_html, offers_from_html
from .models import Offer
from .scanner import fetch_many

ALLEGRO_SEARCH = {
    "allegro.cz": "https://allegro.cz/vyhledavani?string={query}",
    "allegro.pl": "https://allegro.pl/listing?string={query}",
    "allegro.sk": "https://allegro.sk/vyhladavanie?string={query}",
}
ALLEGRO_CURRENCY = {"allegro.cz": "CZK", "allegro.pl": "PLN", "allegro.sk": "EUR"}
KAUFLAND_SEARCH = "https://www.kaufland.cz/s/?search_value={query}"
BAZOS_SEARCH = "https://www.bazos.cz/search.php?hledat={query}&rubriky=www"

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"


@dataclass
class Finding:
    kind: str  # "currency_swap" | "underpriced" | "kaufland_gap" | "bazos_used"
    title: str
    price_czk: float
    reference_czk: float
    source: str
    reference_source: str
    url: str | None = None
    detail: str = ""

    @property
    def saving(self) -> float:
        return self.reference_czk - self.price_czk

    def describe(self) -> str:
        base = (
            f"[{self.kind}] {self.title[:70]}\n"
            f"  {self.price_czk:.0f} CZK on {self.source} vs "
            f"~{self.reference_czk:.0f} CZK on {self.reference_source} "
            f"(save ~{self.saving:.0f} CZK, {self.price_czk / self.reference_czk:.0%} of reference)"
        )
        if self.detail:
            base += f"\n  {self.detail}"
        if self.url:
            base += f"\n  {self.url}"
        return base


def direct_get(url: str, timeout: float = 30.0) -> str:
    """Plain fetch for sources without bot protection (bazos)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _paged(template: str, query: str, pages: int) -> list[str]:
    quoted = urllib.parse.quote(query)
    urls = [template.format(query=quoted)]
    for p in range(2, pages + 1):
        urls.append(template.format(query=quoted) + f"&p={p}")
    return urls


def triangulate_allegro(
    offers_by_market: dict[str, list[Offer]],
    rates: dict[str, float],
    max_ratio: float = 0.6,
    min_saving: float = 300.0,
) -> list[Finding]:
    """Compare the same offer id across allegro cz/pl/sk."""
    by_id: dict[str, dict[str, Offer]] = {}
    for market, offers in offers_by_market.items():
        for o in offers:
            if o.offer_id:
                by_id.setdefault(o.offer_id, {})[market] = o

    findings = []
    for oid, markets in by_id.items():
        if len(markets) < 2:
            continue
        priced = [
            (o.price * rates[o.currency], market, o) for market, o in markets.items()
        ]
        priced.sort()
        cheap_czk, cheap_market, cheap = priced[0]
        ref_czk, ref_market, ref = priced[-1]
        if ref_czk <= 0 or cheap_czk / ref_czk > max_ratio:
            continue
        if ref_czk - cheap_czk < min_saving:
            continue
        raw_ratio = cheap.price / ref.price if ref.price else 0
        kind = "currency_swap" if abs(raw_ratio - 1) <= 0.25 else "underpriced"
        findings.append(
            Finding(
                kind=kind,
                title=cheap.title,
                price_czk=cheap_czk,
                reference_czk=ref_czk,
                source=cheap_market,
                reference_source=ref_market,
                url=cheap.url,
                detail=(
                    f"raw prices: {cheap.price:.0f} {cheap.currency} vs "
                    f"{ref.price:.0f} {ref.currency} (offer id {oid})"
                ),
            )
        )
    return findings


def _token_index(offers: list[tuple[Offer, float]]) -> dict[str, list[float]]:
    index: dict[str, list[float]] = {}
    for offer, czk in offers:
        for token in model_tokens(offer.title):
            index.setdefault(token, []).append(czk)
    return index


def _best_token_median(title: str, index: dict[str, list[float]]) -> tuple[str, float] | None:
    tokens = [t for t in model_tokens(title) if t in index]
    if not tokens:
        return None
    best = max(tokens, key=lambda t: len(index[t]))
    return best, median(index[best])


def cross_source_gaps(
    left: list[tuple[Offer, float]],
    right_index: dict[str, list[float]],
    left_name: str,
    right_name: str,
    kind: str,
    max_ratio: float = 0.6,
    min_saving: float = 300.0,
) -> list[Finding]:
    """Flag offers in `left` far below the token-matched median in `right`."""
    findings = []
    for offer, czk in left:
        match = _best_token_median(offer.title, right_index)
        if match is None:
            continue
        token, ref = match
        if ref <= 0 or czk / ref > max_ratio or ref - czk < min_saving:
            continue
        findings.append(
            Finding(
                kind=kind,
                title=offer.title,
                price_czk=czk,
                reference_czk=ref,
                source=left_name,
                reference_source=right_name,
                url=offer.url,
                detail=f"matched by model number {token}",
            )
        )
    return findings


def hunt(
    fetcher,
    query: str,
    query_pl: str | None = None,
    rates: dict[str, float] | None = None,
    pages: int = 2,
    max_ratio: float = 0.6,
    min_saving: float = 300.0,
    log: Callable[[str], None] = print,
) -> list[Finding]:
    rates = rates or {"CZK": 1.0, "PLN": 5.6, "EUR": 24.5}

    urls: dict[str, list[str]] = {
        "allegro.cz": _paged(ALLEGRO_SEARCH["allegro.cz"], query, pages),
        "allegro.pl": _paged(ALLEGRO_SEARCH["allegro.pl"], query_pl or query, pages),
        "allegro.sk": _paged(ALLEGRO_SEARCH["allegro.sk"], query, 1),
        "kaufland.cz": [KAUFLAND_SEARCH.format(query=urllib.parse.quote(query))],
    }
    flat = [u for us in urls.values() for u in us]
    log(f"hunting '{query}' across {', '.join(urls)} + bazos.cz")
    htmls = fetch_many(fetcher, flat, log)

    allegro: dict[str, list[Offer]] = {}
    for market in ALLEGRO_SEARCH:
        currency = ALLEGRO_CURRENCY[market]
        allegro[market] = [
            o
            for u in urls[market]
            for o in offers_from_html(htmls.get(u, ""))
            if o.currency == currency
        ]
    kaufland = [
        o
        for u in urls["kaufland.cz"]
        for o in kaufland_offers_from_html(htmls.get(u, ""))
        if o.currency == "CZK"
    ]
    try:
        bazos = bazos_offers_from_html(direct_get(BAZOS_SEARCH.format(query=urllib.parse.quote(query))))
    except OSError as exc:
        log(f"bazos fetch failed: {exc}")
        bazos = []

    counts = {m: len(o) for m, o in allegro.items()}
    log(f"parsed: {counts}, kaufland: {len(kaufland)}, bazos: {len(bazos)}")

    to_czk = lambda o: o.price * rates[o.currency]  # noqa: E731
    allegro_priced = [(o, to_czk(o)) for offers in allegro.values() for o in offers]
    kaufland_priced = [(o, to_czk(o)) for o in kaufland]
    bazos_priced = [(o, o.price) for o in bazos]

    findings = triangulate_allegro(allegro, rates, max_ratio, min_saving)

    allegro_index = _token_index(allegro_priced)
    kaufland_index = _token_index(kaufland_priced)
    new_goods_index = _token_index(allegro_priced + kaufland_priced)

    findings += cross_source_gaps(
        kaufland_priced, allegro_index, "kaufland.cz", "allegro (median)",
        "kaufland_gap", max_ratio, min_saving,
    )
    findings += cross_source_gaps(
        [(o, czk) for o, czk in allegro_priced if o.currency == "CZK"],
        kaufland_index, "allegro.cz", "kaufland.cz (median)",
        "kaufland_gap", max_ratio, min_saving,
    )
    # Used goods: cheaper is expected; only flag *steep* gaps vs new price.
    findings += cross_source_gaps(
        bazos_priced, new_goods_index, "bazos.cz (used)", "new price (median)",
        "bazos_used", max_ratio=0.45, min_saving=max(min_saving, 500.0),
    )

    findings.sort(key=lambda f: -f.saving)
    for f in findings:
        log("FOUND:\n" + f.describe())
    if not findings:
        log("no cross-source findings")
    return findings
