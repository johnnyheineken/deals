"""The daily watch: run every detector across the watchlist, emit a digest.

One invocation = one full sweep (allegro cz/pl compares with deep id
checks, kaufland cz/de/sk gaps, multi-source hunts incl. the bazos
sidenote), findings deduplicated against previous days, digest written
as markdown.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Callable

from .kaufland import compare_kaufland
from .hunt import hunt
from .scanner import compare_markets

# (query_cz, query_pl or None, cz price floor)
ALLEGRO_WATCH = [
    ("lego", None, 300),
    ("brio", None, 150),
    ("playmobil", None, 150),
    ("nintendo switch", None, 120),
    ("nintendo switch klíč", "nintendo switch klucz", 100),
    ("playstation 5", None, 500),
    ("apple watch", None, 800),
    ("airpods", None, 800),
    ("iphone", None, 1500),
    ("dyson", None, 1500),
    ("kávovar", "ekspres do kawy", 600),
    ("robotický vysavač", "robot sprzątający", 1500),
    ("mikrovlnná trouba", "kuchenka mikrofalowa", 500),
    ("lednice", "lodówka", 2000),
    ("aku vrtačka", "wiertarka akumulatorowa", 800),
]
KAUFLAND_WATCH = [
    "lego", "playmobil", "nintendo switch", "playstation 5", "apple watch",
    "dyson", "kávovar", "robotický vysavač", "mikrovlnná trouba", "lednice",
    "televize", "aku vrtačka",
]
HUNT_WATCH = ["lego", "brio", "nintendo switch", "playmobil"]

MAX_RATIO = 0.7  # >=30% off
MIN_SAVING = 500.0  # >=500 CZK


def _seen_path(out_dir: Path) -> Path:
    return out_dir / "seen.json"


def _load_seen(out_dir: Path) -> dict[str, float]:
    try:
        return json.loads(_seen_path(out_dir).read_text())
    except (OSError, ValueError):
        return {}


def run_watch(
    fetcher,
    rates: dict[str, float],
    out_dir: str | Path = "watch",
    log: Callable[[str], None] = print,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    seen = _load_seen(out_dir)
    day = time.strftime("%Y-%m-%d")
    started = time.monotonic()

    rows: list[dict] = []

    def add(kind: str, title: str, price: float, reference: float, url: str | None, source: str, detail: str = "") -> None:
        key = f"{url or title}|{price:.0f}"
        rows.append(
            {
                "kind": kind, "title": title, "price_czk": round(price),
                "reference_czk": round(reference), "saving": round(reference - price),
                "url": url, "source": source, "detail": detail,
                "new": seen.get(key) is None,
            }
        )
        seen[key] = price

    fx = rates["PLN"]
    for query, query_pl, price_from in ALLEGRO_WATCH:
        log(f"=== allegro compare: {query}")
        try:
            discrepancies, _, _ = compare_markets(
                fetcher, query, query_pl, fx_pln_czk=fx, pages=2, max_offers=150,
                cheap_first=True, price_from=price_from, deep_limit=40,
                max_ratio=MAX_RATIO, min_saving=MIN_SAVING, log=log,
            )
            for d in discrepancies:
                add(
                    d.kind, d.offer.title, d.offer.price, d.reference_price,
                    d.offer.url, "allegro.cz",
                    f"vs allegro.pl, implied FX {d.implied_fx:.2f}, matched by {d.matched_by}",
                )
        except Exception:
            log(f"allegro compare '{query}' failed:\n{traceback.format_exc(limit=1)}")

    for query in KAUFLAND_WATCH:
        log(f"=== kaufland: {query}")
        try:
            gaps = compare_kaufland(
                fetcher, query, rates=rates, countries=["cz", "de", "sk"],
                limit=10, max_ratio=MAX_RATIO, min_saving=MIN_SAVING, log=log,
            )
            for g in gaps:
                cc, czk = g.cheapest
                add(
                    "kaufland_gap", g.title, czk, g.dearest[1],
                    f"https://www.kaufland.{cc}/product/{g.pid}/",
                    f"kaufland.{cc}", ", ".join(f"{c}: {r}" for c, r in sorted(g.raw.items())),
                )
        except Exception:
            log(f"kaufland '{query}' failed:\n{traceback.format_exc(limit=1)}")

    for query in HUNT_WATCH:
        log(f"=== hunt: {query}")
        try:
            for f in hunt(
                fetcher, query, rates=rates, pages=1,
                max_ratio=MAX_RATIO, min_saving=MIN_SAVING, log=log,
            ):
                add(f.kind, f.title, f.price_czk, f.reference_czk, f.url, f.source, f.detail)
        except Exception:
            log(f"hunt '{query}' failed:\n{traceback.format_exc(limit=1)}")

    _seen_path(out_dir).write_text(json.dumps(seen, indent=0))
    digest = _render_digest(rows, day, time.monotonic() - started)
    path = out_dir / f"digest-{day}.md"
    path.write_text(digest, encoding="utf-8")
    (out_dir / f"findings-{day}.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1)
    )
    log(f"digest written to {path} ({len(rows)} findings)")
    return path


def _render_digest(rows: list[dict], day: str, elapsed: float) -> str:
    swaps = [r for r in rows if r["kind"] == "currency_swap"]
    main = [
        r for r in rows
        if r["kind"] in ("underpriced", "kaufland_gap") and r["kind"] != "bazos_used"
    ]
    bazos = [r for r in rows if r["kind"] == "bazos_used"]
    new_count = sum(1 for r in rows if r["new"])

    lines = [
        f"# Deal digest {day}",
        "",
        f"{len(rows)} findings ({new_count} new since last sweep), "
        f"sweep took {elapsed / 60:.0f} min.",
        "",
    ]

    def section(title: str, items: list[dict]) -> None:
        if not items:
            return
        lines.append(f"## {title}")
        for r in sorted(items, key=lambda r: -r["saving"]):
            flag = "🆕 " if r["new"] else ""
            lines.append(
                f"- {flag}**{r['title'][:70]}** — {r['price_czk']} CZK "
                f"(ref ~{r['reference_czk']} CZK, save ~{r['saving']} CZK) "
                f"[{r['source']}]({r['url']})" if r["url"] else
                f"- {flag}**{r['title'][:70]}** — {r['price_czk']} CZK "
                f"(ref ~{r['reference_czk']} CZK, save ~{r['saving']} CZK) [{r['source']}]"
            )
            if r["detail"]:
                lines.append(f"  - {r['detail']}")
        lines.append("")

    section("🚨 Currency swaps", swaps)
    section("💰 Deals (≥30% off, ≥500 CZK)", main)
    section("📦 Bazoš sidenotes (used)", bazos)
    if not rows:
        lines.append("_No findings today - all watched markets fairly priced._")
    return "\n".join(lines)
