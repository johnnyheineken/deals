"""PLN -> CZK exchange rate, from the Czech National Bank daily fixing."""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

CNB_URL = (
    "https://www.cnb.cz/cs/financni-trhy/devizovy-trh/"
    "kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/denni_kurz.txt"
)
# Fallback if the CNB endpoint is unreachable (rate as of mid-2026).
DEFAULT_PLN_CZK = 5.65
CACHE_TTL = 24 * 3600


def _cache_path() -> Path:
    return Path.home() / ".cache" / "allegro-deals" / "fx.json"


def parse_cnb_rates(text: str) -> dict[str, float]:
    """Parse the CNB pipe-delimited daily fixing into {code: CZK per unit}."""
    rates: dict[str, float] = {}
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 5:
            continue
        _, _, amount, code, rate = parts
        try:
            rates[code] = float(rate.replace(",", ".")) / float(amount)
        except ValueError:
            continue  # header line
    return rates


DEFAULT_RATES = {"PLN": DEFAULT_PLN_CZK, "EUR": 24.5, "CZK": 1.0}


def get_rates(force_refresh: bool = False) -> dict[str, float]:
    """CZK per unit for the currencies we trade in, cached for a day."""
    cache = _cache_path()
    if not force_refresh and cache.exists():
        try:
            data = json.loads(cache.read_text())
            if time.time() - data["ts"] < CACHE_TTL and "rates" in data:
                return {**DEFAULT_RATES, **data["rates"]}
        except (ValueError, KeyError):
            pass
    try:
        with urllib.request.urlopen(CNB_URL, timeout=15) as resp:
            all_rates = parse_cnb_rates(resp.read().decode("utf-8"))
        rates = {"PLN": all_rates["PLN"], "EUR": all_rates["EUR"], "CZK": 1.0}
    except Exception:
        return dict(DEFAULT_RATES)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"ts": time.time(), "rates": rates}))
    return rates


def get_pln_czk(force_refresh: bool = False) -> float:
    """CZK per 1 PLN, cached for a day, falling back to a constant."""
    return get_rates(force_refresh)["PLN"]
