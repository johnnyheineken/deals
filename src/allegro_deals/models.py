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
