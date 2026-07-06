# allegro-deals

Finds mispriced offers on **allegro.cz** — most notably the "PLN price
displayed as CZK" bug.

## The bug this hunts

allegro.cz mirrors offers from Polish sellers and converts their prices from
PLN to CZK (1 PLN ≈ 5.6 CZK). Occasionally the conversion goes missing and
the CZK price equals the raw PLN number, making the item ~5–6× cheaper than
intended. Example that inspired this tool: a BRIO 36029 train set listed at
~239 CZK while every other offer for the same product sat around 1 400 CZK —
239 was clearly the Polish price (239 PLN ≈ 1 350 CZK).

Two detectors:

1. **Median check** (`scan`) — a product page on allegro.cz lists every offer
   for one catalogue product. An offer priced far below the median of its
   peers is flagged; if `median / price` lands near the PLN/CZK rate, it is
   classified as a `currency_swap` (the strong signal), otherwise
   `underpriced`.
2. **Cross-market check** (`check`) — the same offer id exists on allegro.pl
   in PLN. If `price_czk / price_pln ≈ 1` instead of ≈ 5.6, the currency was
   swapped.

The PLN/CZK rate comes from the Czech National Bank daily fixing (cached,
with an offline fallback).

## Install

With [uv](https://docs.astral.sh/uv/) (recommended — no venv juggling):

```bash
git clone https://github.com/johnnyheineken/deals.git
cd deals
uv run allegro-deals --help
```

If Google Chrome is installed, the tool drives it directly — no browser
download needed (a real Chrome fingerprint is also what gets you past
DataDome). Without Chrome, download the bundled browser once:

```bash
uv run patchright install chromium
```

Or with plain pip: `pip install -e .`

## Usage

Run this **on your own machine / home connection**. Allegro sits behind
DataDome bot protection; datacenter IPs get a captcha wall no matter what,
while residential IPs are generally fine. On the first run (or when Allegro
gets suspicious) use `--headful` and solve the captcha in the opened browser
window once — the cookie is kept in a persistent profile
(`~/.cache/allegro-deals/profile`), so later headless runs pass.

The scraper uses [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)
(a stealth Playwright fork), prefers your real installed Chrome, sends the
browser's native user agent, and visits the homepage before any listing URL —
all things DataDome checks.

```bash
# scan searches for anomalies (writes findings.jsonl)
uv run allegro-deals scan "brio vlak" "lego technic" --pages 2 --max-products 20

# first run / captcha:
uv run allegro-deals --headful scan "brio vlak"

# cross-check a specific offer you found manually
uv run allegro-deals check "https://allegro.cz/produkt/...?offerId=16810772956"
uv run allegro-deals check 16810772956
```

(Drop the `uv run` prefix if you installed with pip.)

Example finding:

```
ANOMALY:
BRIO 36029 Vlaková sada s mohutnou červenou akční lokomotivou
  price: 239 CZK  vs. typical 1450 CZK  (16% of typical, save ~1211)
  peers: 3, implied FX if PLN-as-CZK: 6.07
  https://allegro.cz/oferta/...16810772956
```

## Tuning

- `--max-ratio 0.45` — how far below the typical price an offer must be to
  get flagged (default: under 45 %).
- `--max-products` / `--pages` — how much to crawl per query. Keep it modest;
  the crawler paces itself (1.5–4 s between requests) on purpose.
- `--fx 5.65` — pin the exchange rate manually.

## If you get blocked anyway

A page saying **"Byli jste zablokováni"** is a DataDome hard block, and the
block state is stored in the profile's cookie. Recover with:

```bash
uv run allegro-deals reset     # deletes the browser profile
# wait ~15 minutes, then
uv run allegro-deals --headful scan "..."
```

Scan less aggressively afterwards (fewer `--max-products`, one query per
run). If blocks persist, browse allegro.cz normally in the headful window
for a minute (click an offer or two) before scanning — a profile with human
history survives much longer.

## Notes & fair play

- This is for personal deal-hunting. Keep request volumes low — the built-in
  pacing is there so the scan behaves like a patient human, not a crawler.
- A flagged offer is *probably* a seller-side pricing mistake. Whether to buy
  is your call; sellers sometimes cancel obviously mispriced orders.
- Allegro's markup and embedded state change regularly. The extractor walks
  all embedded JSON for offer-shaped objects rather than pinning exact paths,
  which should survive most redesigns — but if `scan` suddenly returns
  nothing, the parser likely needs a refresh.

## Development

```bash
uv run pytest          # or: pip install -e ".[dev]" && python -m pytest
```

The scraping layer is isolated in `browser.py`; everything else (extraction,
detection, FX) is pure and covered by tests with fixture HTML.
