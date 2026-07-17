"""Anomaly detection: offers priced far below their product's typical price.

The signature of the PLN-as-CZK bug: the cheap offer's price multiplied by
the PLN/CZK rate lands close to what the other offers charge. A plain
"too cheap" offer (used item, clearance, scam listing) usually doesn't hit
that ratio.
"""

from __future__ import annotations

import re
from statistics import median

from .models import Anomaly, CrossDiscrepancy, Offer

MIN_PEERS = 2  # need at least this many other offers to trust the median
FX_TOLERANCE = 0.30  # implied rate within +-30% of the real rate => currency swap
DEFAULT_MAX_RATIO = 0.45  # flag offers below 45% of the typical price


def find_anomalies(
    offers: list[Offer],
    fx_pln_czk: float,
    max_ratio: float = DEFAULT_MAX_RATIO,
    min_price: float = 40.0,
) -> list[Anomaly]:
    """Flag offers priced way below the median of the other offers.

    ``offers`` should belong to a single product (e.g. one product page).
    ``min_price`` skips penny items where ratios are meaningless.
    """
    priced = [o for o in offers if o.price >= min_price]
    anomalies: list[Anomaly] = []
    for offer in priced:
        peers = [o.price for o in priced if o.key() != offer.key()]
        if len(peers) < MIN_PEERS:
            continue
        ref = median(peers)
        if ref <= 0:
            continue
        ratio = offer.price / ref
        if ratio > max_ratio:
            continue
        implied_fx = ref / offer.price
        kind = (
            "currency_swap"
            if abs(implied_fx - fx_pln_czk) / fx_pln_czk <= FX_TOLERANCE
            else "underpriced"
        )
        anomalies.append(
            Anomaly(
                offer=offer,
                reference_price=ref,
                ratio=ratio,
                implied_fx=implied_fx,
                kind=kind,
                peer_count=len(peers),
            )
        )
    anomalies.sort(key=lambda a: (a.kind != "currency_swap", a.ratio))
    return anomalies


_MODEL_TOKEN_RE = re.compile(r"\b(\d{4,6})\b(?!\s*(?:mah|mAh|MAH|GB|gb|TB|W\b|Hz|hz|ml|mm|mAh))")


# Numbers that describe a standard, not a product: M.2 form factors and
# common RAM speed grades.
_STANDARD_TOKENS = {
    "2280", "2260", "2242", "2230", "22110",
    "4800", "5600", "6000", "6400", "3200", "3600",
}


def model_tokens(title: str) -> set[str]:
    """Model-number-ish tokens from a title (e.g. BRIO '36029'), minus years,
    spec values (5000 mAh, 256 GB) and standards (M.2 2280, DDR5 6000)."""
    return {
        t
        for t in _MODEL_TOKEN_RE.findall(title)
        if not (len(t) == 4 and t.startswith(("19", "20"))) and t not in _STANDARD_TOKENS
    }


def find_cross_discrepancies(
    cz_offers: list[Offer],
    pl_offers: list[Offer],
    fx_pln_czk: float,
    max_ratio: float = 0.55,
    min_price: float = 40.0,
    min_saving: float = 0.0,
) -> list[CrossDiscrepancy]:
    """Match allegro.cz offers to allegro.pl ones and flag price gaps.

    Matching is by shared offer id first (the same offer exists on both
    marketplaces), then by model-number token in the title, comparing
    against the median PLN price of the matching Polish offers.
    """
    pl_by_id = {o.offer_id: o for o in pl_offers if o.offer_id}
    pl_by_token: dict[str, list[float]] = {}
    for o in pl_offers:
        for token in model_tokens(o.title):
            pl_by_token.setdefault(token, []).append(o.price)

    # Accessories that share the main product's model number and would
    # false-positive against the full product's price when matched by token.
    partial_markers = (
        "návod", "navod", "instrukcja", "manual", "krabice", "pudełko",
        "samolepk", "naklejk", "nálepk", "minifig", "díl", "części",
    )

    results: list[CrossDiscrepancy] = []
    for cz in cz_offers:
        if cz.price < min_price:
            continue
        pln_ref: float | None = None
        matched_by = ""
        if cz.offer_id and cz.offer_id in pl_by_id:
            pln_ref = pl_by_id[cz.offer_id].price
            matched_by = "offer_id"
        else:
            lowered = cz.title.lower()
            if any(marker in lowered for marker in partial_markers):
                continue
            # Configured systems (PCs, laptops) share component model numbers
            # without being the same product - only id matches count there.
            spec_markers = sum(
                m in lowered
                for m in (" gb", " tb", "rtx", "gtx", "ryzen", "core i", "ssd", "ram", "wi-fi", "windows")
            )
            if spec_markers >= 2:
                continue
            tokens = model_tokens(cz.title)
            # pick the token with the most Polish offers behind it
            best = max(
                (t for t in tokens if t in pl_by_token),
                key=lambda t: len(pl_by_token[t]),
                default=None,
            )
            if best is not None:
                pln_ref = median(pl_by_token[best])
                matched_by = f"model:{best}"
        if pln_ref is None or pln_ref <= 0:
            continue
        expected_czk = pln_ref * fx_pln_czk
        if cz.price / expected_czk > max_ratio:
            continue
        if expected_czk - cz.price < min_saving:
            continue
        implied = cz.price / pln_ref
        kind = "currency_swap" if abs(implied - 1.0) <= 0.25 else "underpriced"
        results.append(
            CrossDiscrepancy(
                cz_offer=cz,
                pln_reference=pln_ref,
                expected_czk=expected_czk,
                implied_fx=implied,
                kind=kind,
                matched_by=matched_by,
            )
        )
    results.sort(key=lambda r: (r.kind != "currency_swap", r.cz_offer.price / r.expected_czk))
    return results


def classify_cross_market(
    czk_price: float, pln_price: float, fx_pln_czk: float, tolerance: float = 0.20
) -> str:
    """Compare the same offer on allegro.cz (CZK) and allegro.pl (PLN).

    Returns "ok" when the CZK price is roughly PLN * rate, "currency_swap"
    when the CZK price equals the raw PLN number, and "mismatch" otherwise.
    """
    if pln_price <= 0 or czk_price <= 0:
        return "mismatch"
    implied = czk_price / pln_price
    if abs(implied - fx_pln_czk) / fx_pln_czk <= tolerance:
        return "ok"
    if abs(implied - 1.0) <= tolerance:
        return "currency_swap"
    return "mismatch"
