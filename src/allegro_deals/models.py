from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Offer:
    """A single offer as seen on a listing or product page."""

    title: str
    price: float
    currency: str
    offer_id: str | None = None
    url: str | None = None
    product_url: str | None = None
    seller: str | None = None

    def key(self) -> str:
        return self.offer_id or self.url or self.title


@dataclass
class Anomaly:
    """A suspiciously cheap offer relative to its product's other offers."""

    offer: Offer
    reference_price: float
    ratio: float
    implied_fx: float
    kind: str  # "currency_swap" | "underpriced"
    peer_count: int
    context: dict = field(default_factory=dict)

    def describe(self) -> str:
        saved = self.reference_price - self.offer.price
        base = (
            f"{self.offer.title[:70]}\n"
            f"  price: {self.offer.price:.0f} {self.offer.currency}"
            f"  vs. typical {self.reference_price:.0f} {self.offer.currency}"
            f"  ({self.ratio:.0%} of typical, save ~{saved:.0f})\n"
            f"  peers: {self.peer_count}, implied FX if PLN-as-CZK: {self.implied_fx:.2f}"
        )
        if self.offer.url:
            base += f"\n  {self.offer.url}"
        return base


@dataclass
class CrossDiscrepancy:
    """An allegro.cz offer priced far below its allegro.pl counterpart."""

    cz_offer: Offer
    pln_reference: float
    expected_czk: float
    implied_fx: float
    kind: str  # "currency_swap" | "underpriced"
    matched_by: str  # "offer_id" | "model:<token>"

    def describe(self) -> str:
        saved = self.expected_czk - self.cz_offer.price
        base = (
            f"{self.cz_offer.title[:70]}\n"
            f"  {self.cz_offer.price:.0f} CZK on allegro.cz vs. "
            f"{self.pln_reference:.0f} PLN on allegro.pl "
            f"(should be ~{self.expected_czk:.0f} CZK, save ~{saved:.0f})\n"
            f"  CZK/PLN ratio: {self.implied_fx:.2f}, matched by {self.matched_by}"
        )
        if self.cz_offer.url:
            base += f"\n  {self.cz_offer.url}"
        return base
