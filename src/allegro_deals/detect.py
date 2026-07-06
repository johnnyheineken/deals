"""Anomaly detection: offers priced far below their product's typical price.

The signature of the PLN-as-CZK bug: the cheap offer's price multiplied by
the PLN/CZK rate lands close to what the other offers charge. A plain
"too cheap" offer (used item, clearance, scam listing) usually doesn't hit
that ratio.
"""

from __future__ import annotations

from statistics import median

from .models import Anomaly, Offer

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
