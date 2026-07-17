"""Extract offer data from Allegro pages.

Allegro renders listing and product pages from JSON state embedded in
``<script>`` tags. Markup classes are obfuscated and change often, but the
embedded state keeps a recognisable shape: offer objects carry a name plus a
nested price ``{"amount": "...", "currency": "..."}``. Instead of pinning
exact paths into that state (which differ between listing, product and offer
pages and shift between deployments), we harvest every JSON blob we can find
and walk it for offer-shaped dicts.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Iterator

from .models import Offer

_SCRIPT_JSON_RE = re.compile(
    r"<script[^>]*type=[\"']application/(?:ld\+)?json[\"'][^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)
# catches both `window.x = {...}` and `window.dataLayer = [{...}]`
_INLINE_ASSIGN_RE = re.compile(r"=\s*\[?\s*(\{)")
_SCRIPT_ANY_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)
# Offer URLs end with the numeric id (".../oferta/nazev-nabidky-16810772956"),
# product URLs carry it as ?offerId=...
_OFFER_ID_QUERY_RE = re.compile(r"offerId=(\d{8,})")
_OFFER_ID_TRAILING_RE = re.compile(r"-(\d{8,})(?:[/?#]|$)")


def _balanced_json(text: str, start: int) -> str | None:
    """Return the balanced {...} substring starting at ``start``, or None."""
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def iter_json_blobs(html: str) -> Iterator[Any]:
    """Yield every parseable JSON object embedded in the page."""
    for m in _SCRIPT_JSON_RE.finditer(html):
        try:
            yield json.loads(m.group(1))
        except ValueError:
            continue
    for script in _SCRIPT_ANY_RE.finditer(html):
        body = script.group(1)
        if '"amount"' not in body and '"price"' not in body:
            continue
        for assign in _INLINE_ASSIGN_RE.finditer(body):
            blob = _balanced_json(body, assign.start(1))
            if blob is None or len(blob) < 50:
                continue
            try:
                yield json.loads(blob)
            except ValueError:
                continue


def _walk_dicts(obj: Any) -> Iterator[dict]:
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def _as_price(node: Any) -> tuple[float, str] | None:
    if not isinstance(node, dict):
        return None
    amount = node.get("amount")
    currency = node.get("currency")
    if amount is None or not isinstance(currency, str):
        return None
    try:
        return float(str(amount).replace(",", ".").replace("\xa0", "")), currency
    except ValueError:
        return None


def _flat_price(d: dict) -> tuple[float, str] | None:
    """Flat shapes: {"price": 252, "currency": "CZK"} (dataLayer) and
    {"price": "252.00", "priceCurrency": "CZK"} (schema.org ld+json)."""
    val = d.get("price")
    currency = d.get("currency") or d.get("priceCurrency")
    if isinstance(val, (int, float, str)) and isinstance(currency, str) and len(currency) == 3:
        try:
            return float(str(val).replace(",", ".")), currency
        except ValueError:
            return None
    return None


def _find_price(d: dict) -> tuple[float, str] | None:
    # Common shapes, most specific first:
    #   {"sellingMode": {"price": {...}}}, {"price": {...}},
    #   {"price": {"mainPrice": {...}}}, {"prices": {"main"/"minimal": {...}}}
    selling = d.get("sellingMode")
    if isinstance(selling, dict):
        price = _as_price(selling.get("price"))
        if price:
            return price
    for key in ("price", "prices", "mainPrice", "minimalPrice", "buyNowPrice"):
        node = d.get(key)
        price = _as_price(node)
        if price:
            return price
        if isinstance(node, dict):
            for sub in ("mainPrice", "main", "minimal", "amount", "sale", "regular"):
                price = _as_price(node.get(sub))
                if price:
                    return price
    price = _flat_price(d)
    if price:
        return price
    # schema.org Product: name here, price inside "offers"
    offers_node = d.get("offers")
    if isinstance(offers_node, list) and offers_node:
        offers_node = offers_node[0]
    if isinstance(offers_node, dict):
        return _flat_price(offers_node)
    return None


def _find_title(d: dict) -> str | None:
    for key in ("name", "title", "offerName", "productName"):
        val = d.get(key)
        if isinstance(val, str) and len(val) >= 5:
            return val
        if isinstance(val, dict):
            text = val.get("text") or val.get("value")
            if isinstance(text, str) and len(text) >= 5:
                return text
    return None


def _clean_url(url: str) -> str:
    """Unwrap ad-click redirect URLs (/events/clicks?...&redirect=<real>)."""
    if "/events/clicks" in url:
        query = urllib.parse.urlparse(url).query
        redirect = urllib.parse.parse_qs(query).get("redirect")
        if redirect:
            return redirect[0].split("?")[0]
    return url


def _find_url(d: dict) -> str | None:
    for key in ("url", "offerUrl", "href", "link"):
        val = d.get(key)
        if isinstance(val, str) and "allegro" in val:
            return _clean_url(val)
    return None


def offer_id_from_url(url: str) -> str | None:
    m = _OFFER_ID_QUERY_RE.search(url) or _OFFER_ID_TRAILING_RE.search(url)
    return m.group(1) if m else None


def _find_offer_id(d: dict, url: str | None) -> str | None:
    for key in ("id", "offerId"):
        val = d.get(key)
        if isinstance(val, (str, int)) and str(val).isdigit() and len(str(val)) >= 8:
            return str(val)
    if url:
        return offer_id_from_url(url)
    return None


def _find_seller(d: dict) -> str | None:
    seller = d.get("seller")
    if isinstance(seller, dict):
        login = seller.get("login") or seller.get("name")
        if isinstance(login, str):
            return login
    return None


def offers_from_html(html: str) -> list[Offer]:
    """Best-effort extraction of every offer visible in a page's JSON state."""
    seen: dict[str, Offer] = {}
    for blob in iter_json_blobs(html):
        for d in _walk_dicts(blob):
            price = _find_price(d)
            if price is None:
                continue
            title = _find_title(d)
            if title is None:
                continue
            url = _find_url(d)
            offer = Offer(
                title=title.strip(),
                price=price[0],
                currency=price[1],
                offer_id=_find_offer_id(d, url),
                url=url,
                seller=_find_seller(d),
            )
            key = offer.key()
            # Prefer entries that carry an explicit offer id.
            if key not in seen or (offer.offer_id and not seen[key].offer_id):
                seen[key] = offer
    return list(seen.values())


# --- non-Allegro sources: plain-HTML parsers ------------------------------

# Bazos serves 1998-vintage HTML with unquoted attributes:
#   <h2 class=nadpis><a href="...inzerat/ID/slug.php">TITLE</a></h2>
#   ... <div class=inzeratycena><b>1 200 Kč</b>
_BAZOS_ITEM_RE = re.compile(
    r'<h2 class="?nadpis"?[^>]*><a href="(https://[a-z]+\.bazos\.cz/inzerat/(\d+)/[^"]+)"[^>]*>'
    r"([^<]{5,150})</a></h2>"
    r"(?:(?!<h2).){0,3000}?"
    r'inzeratycena"?[^>]*><b>(?:<span[^>]*>)?((?:[0-9]|&nbsp;|\s)+)Kč',
    re.DOTALL,
)


def bazos_offers_from_html(html: str) -> list[Offer]:
    """Parse bazos.cz search results (used-goods classifieds)."""
    offers = []
    seen = set()
    for m in _BAZOS_ITEM_RE.finditer(html):
        url, ad_id, title, price_text = m.groups()
        if ad_id in seen:
            continue
        seen.add(ad_id)
        cleaned = re.sub(r"&nbsp;|\s", "", price_text)
        try:
            price = float(cleaned)
        except ValueError:
            continue
        offers.append(
            Offer(title=title.strip(), price=price, currency="CZK",
                  offer_id=f"bazos-{ad_id}", url=url)
        )
    return offers


_KAUFLAND_TITLE_RE = re.compile(
    r'class="product-title product-title--bold[^"]*"[^>]*title="([^"]{5,160})"'
)
_KAUFLAND_PRICE_RE = re.compile(
    r"product-price__final-price[^>]*>\s*((?:[0-9]|&nbsp;|[ \xa0.,])+?)\s*(Kč|€|zł)"
)
_KAUFLAND_HREF_RE = re.compile(r'href="(/product/(\d+)/[^"]*)"')
_KAUFLAND_CURRENCY = {"Kč": "CZK", "€": "EUR", "zł": "PLN"}


def kaufland_offers_from_html(html: str) -> list[Offer]:
    """Parse Kaufland marketplace search tiles (server-rendered Vue HTML)."""
    offers = []
    titles = list(_KAUFLAND_TITLE_RE.finditer(html))
    for i, tm in enumerate(titles):
        window_end = titles[i + 1].start() if i + 1 < len(titles) else len(html)
        window = html[tm.end(): window_end]
        pm = _KAUFLAND_PRICE_RE.search(window)
        if not pm:
            continue
        # "1&nbsp;299,00" / "1.299,00" / "499" -> float
        raw = re.sub(r"&nbsp;|[ \xa0]", "", pm.group(1))
        if "," in raw:
            raw = raw.replace(".", "").replace(",", ".")
        try:
            price = float(raw)
        except ValueError:
            continue
        # the tile anchor wraps image + title, so the href usually sits
        # *before* the title span; search backwards first
        hm = _KAUFLAND_HREF_RE.search(
            html[max(0, tm.start() - 5000): tm.start()]
        ) or _KAUFLAND_HREF_RE.search(window)
        offers.append(
            Offer(
                title=tm.group(1).strip(),
                price=price,
                currency=_KAUFLAND_CURRENCY[pm.group(2)],
                offer_id=hm.group(2) if hm else None,
                url=("https://www.kaufland.cz" + hm.group(1)) if hm else None,
            )
        )
    return offers


_KAUFLAND_PDP_PRICE_RE = re.compile(
    r'data-test="product-price"[^>]*>\s*((?:[0-9]|&nbsp;|[ \xa0.,])+?)\s*(Kč|€|zł)'
)
_TITLE_TAG_RE = re.compile(r"<title>([^<|]{5,160})")
_KAUFLAND_ID_TOKEN_RE = re.compile(r'[",\[](\d{6,9})[,\]"]')


def kaufland_product_from_html(html: str) -> Offer | None:
    """Title + buy-box price from a kaufland product detail page."""
    pm = _KAUFLAND_PDP_PRICE_RE.search(html)
    tm = _TITLE_TAG_RE.search(html)
    if not pm or not tm:
        return None
    raw = re.sub(r"&nbsp;|[ \xa0]", "", pm.group(1))
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        price = float(raw)
    except ValueError:
        return None
    return Offer(
        title=tm.group(1).strip(),
        price=price,
        currency=_KAUFLAND_CURRENCY[pm.group(2)],
    )


def kaufland_candidate_ids(html: str, limit: int = 30) -> list[str]:
    """Product-id candidates from a kaufland search page.

    Most tiles carry no href (hydrated client-side), but the serialized
    payload lists result ids as bare 6-9 digit numbers. Junk candidates
    just 404 on the product page and get skipped.
    """
    ids: list[str] = []
    for m in _KAUFLAND_ID_TOKEN_RE.finditer(html):
        token = m.group(1)
        if token not in ids:
            ids.append(token)
        if len(ids) >= limit:
            break
    return ids


_GTIN_RE = re.compile(r'"gtin1?3?"\s*:\s*"?(\d{12,14})')
_TITLE_EAN_RE = re.compile(r"<title>[^<]*\((\d{13})\)")


def ean_from_html(html: str) -> str | None:
    """Product EAN from schema.org data or the page title's '(...)' suffix."""
    m = _GTIN_RE.search(html) or _TITLE_EAN_RE.search(html)
    return m.group(1) if m else None


def product_links_from_html(html: str, base: str = "https://allegro.cz") -> list[str]:
    """Collect /produkt/... links (product pages aggregate all offers)."""
    links: list[str] = []
    for m in re.finditer(r"[\"'](https://allegro\.cz)?(/produkt/[a-z0-9-]+)[\"'?]", html):
        path = m.group(2)
        url = base + path
        if url not in links:
            links.append(url)
    return links
