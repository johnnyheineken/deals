"""Build corolla_ts_cz.csv (Sauto.cz) and corolla_ts_carvago.csv from raw JSON.

usage: python3 build_dataset.py RAW_DIR OUT_DIR
  RAW_DIR/sauto/detail/*.json   (from fetch_sauto.py)
  RAW_DIR/carvago/cars.json     (from fetch_carvago.py, optional)
"""
import csv, glob, json, os, re, sys
from collections import Counter
from datetime import datetime, timezone

RAW, OUT = sys.argv[1], sys.argv[2]
SCRAPED_AT = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

COLUMNS = [
    "source", "listing_id", "url", "title", "price_czk", "price_ex_vat_czk",
    "vat_deductible", "first_reg", "km", "power_kw", "engine", "trim",
    "condition", "seller_type", "seller_name", "origin_cz", "first_owner",
    "service_book", "color", "region", "listed_date", "scraped_at",
    # extras, not in the original spec
    "vin", "manufactured",
]

# ---------------------------------------------------------------- text parsing

TRIMS = [  # (label, pattern) - first match wins, most specific first
    ("GR Sport", r"\bGR[\s,-]*S(port)?\b|\bGR\b(?!\s*Dyn)"),
    ("Executive", r"\bExec(utive)?(?=\b|Tech|Plus)"),
    ("Style", r"\bStyle?(?=\b|Tech|Plus)"),
    ("Comfort", r"\bComf(ort)?(?=\b|Tech|Plus)"),
    ("Active", r"\bActive\b"),
]
OTHER_TRIMS = r"\b(Trek|Selection|Business|Dynamic|Classic|Prestige|Lounge|Team\s?D(eutschland)?|Premiere|Live|Elegant|Luna|Sol|Edition|Club|Trend|Teamplayer|Limited)\b"


def parse_trim(title, desc):
    for text in (title, desc or ""):
        hits = [lab for lab, pat in TRIMS if re.search(pat, text, re.I)]
        if text is title and hits:
            return hits[0]
        if text is not title and len(set(hits)) == 1:  # description: only if unambiguous
            return hits[0]
        if text is title and re.search(OTHER_TRIMS, text, re.I):
            return "other"
    return ""


ORIGIN_CZ = re.compile(
    r"původ\w*\s*(v\s*)?(ČR|CZ|Česk)|"
    r"(koupen|zakoupen|pořízen|prodán)\w*\s+(jako\s+)?(nov\w*\s+)?(v\s+|u\s+\w+\s+)?(ČR|CZ|Česk)|"
    r"\bvůz\s+z\s+ČR\b|\bauto\s+z\s+ČR\b|\bčesk\w*\s+(původ|vůz|auto)|"
    r"\b1\.?\s*maj\w*\s*(v\s*)?ČR|\bČR\s*,?\s*1\.?\s*maj",
    re.I)
ORIGIN_FOREIGN = re.compile(r"\b(dovoz|dovezen\w*|import\w*)\b", re.I)
FIRST_OWNER = re.compile(r"\b(1\.|první|prvního|jeden|1)\s*(majitel|maj\.)|\bod\s+1\.?\s*majitele", re.I)
NOT_FIRST_OWNER = re.compile(r"\b(2\.|3\.|druh\w+|třet\w+)\s*majitel", re.I)
SERVICE_BOOK = re.compile(r"servisní\s+kn[ií]ž|servisk|kompletní\s+servisní\s+histori|pravidelně\s+servis|servis\w*\s+(pouze\s+)?(v|u)\s+(autorizovan|značkov)|servisováno", re.I)
LPG = re.compile(r"\bLPG\b|\bCNG\b|plyn", re.I)
OTHER_BODY = re.compile(r"\bHB\b|hatchback|\bsedan\b|\bliftback\b|\b5d\s*HB\b", re.I)
DAMAGED = re.compile(r"havarovan|poškozen\w*\s+(vůz|auto|vozidlo)|nepojízdn|po\s+havárii|na\s+díly", re.I)


def b(x):
    return "" if x is None else int(bool(x))


def engine_from(volume, text):
    if volume:
        if 1700 <= volume <= 1850:
            return "1.8"
        if 1900 <= volume <= 2050:
            return "2.0"
        if volume < 1700:
            return f"{volume/1000:.1f}"
    m = re.search(r"\b(1[.,]8|2[.,]0|1[.,]2|1[.,]5|1[.,]6)\b", text or "")
    return m.group(1).replace(",", ".") if m else ""


# ---------------------------------------------------------------- sauto

def sauto_rows():
    rows, dropped = [], Counter()
    for fn in sorted(glob.glob(f"{RAW}/sauto/detail/*.json")):
        d = json.load(open(fn))["result"]
        title = d.get("name") or ""
        desc = d.get("description") or ""
        text = f"{title}\n{desc}"
        cond = (d.get("condition_cb") or {}).get("name", "")
        fuel = (d.get("fuel_cb") or {}).get("name", "")
        body = (d.get("vehicle_body_cb") or {}).get("name", "")
        reg = d.get("in_operation_date") or ""
        man = d.get("manufacturing_date") or ""
        year = int((reg or man or "0")[:4])
        engine = engine_from(d.get("engine_volume"), title)

        if d.get("status") != "active":
            dropped["inactive"] += 1; continue
        if body != "Kombi" or fuel != "Hybridní" or OTHER_BODY.search(title):
            dropped["not kombi hybrid"] += 1; continue
        if year < 2019:
            dropped["first_reg < 2019 or unknown"] += 1; continue
        if engine not in ("1.8", "2.0"):
            dropped[f"engine {engine or '?'}"] += 1; continue
        if cond == "Havarované" or DAMAGED.search(title):
            dropped["damaged"] += 1; continue
        if LPG.search(title) or re.search(r"\bLPG\b", desc):
            dropped["LPG"] += 1; continue

        price = d.get("price")
        ex_vat = d.get("price_without_vat")
        if d.get("price_is_without_vat"):  # advertised price is net; gross not shown
            ex_vat, price = ex_vat or price, None

        country = (d.get("country_of_origin_cb") or {}).get("name")
        if country == "Česká republika" or ORIGIN_CZ.search(text):
            origin = 1
        elif country or ORIGIN_FOREIGN.search(text):
            origin = 0
        else:
            origin = ""

        # first_owner is a checkbox too: only True counts as a statement
        fo = 1 if d.get("first_owner") or FIRST_OWNER.search(text) else (
            0 if NOT_FIRST_OWNER.search(text) else None)
        # sauto's service_book is a checkbox: True = stated; False/None = not stated
        sb = 1 if d.get("service_book") or SERVICE_BOOK.search(desc) else ""

        premise = d.get("premise")
        loc = d.get("locality") or {}
        cmap = {"Nové": "new", "Ojeté": "used", "Předváděcí": "demo"}
        rows.append({
            "source": "sauto",
            "listing_id": f"sauto-{d['id']}",
            "url": f"https://www.sauto.cz/osobni/detail/toyota/corolla/{d['id']}",
            "title": title,
            "price_czk": price or "",
            "price_ex_vat_czk": ex_vat or "",
            "vat_deductible": int(bool(d.get("price_is_vat_deductible"))),
            "first_reg": reg[:7],
            "km": d.get("tachometer") if d.get("tachometer") is not None else "",
            "power_kw": d.get("engine_power") or "",
            "engine": engine,
            "trim": parse_trim(title, desc),
            "condition": cmap.get(cond, cond),
            "seller_type": "dealer" if premise else "private",
            "seller_name": (premise or {}).get("name", ""),
            "origin_cz": origin,
            "first_owner": b(fo),
            "service_book": sb,
            "color": (d.get("color_cb") or {}).get("name", ""),
            "region": loc.get("region", ""),
            "listed_date": (d.get("create_date") or "")[:10],
            "scraped_at": SCRAPED_AT,
            "vin": "" if d.get("hide_vin") else (d.get("vin") or ""),
            "manufactured": man[:7],
        })
    return rows, dropped


# ---------------------------------------------------------------- carvago

CV_COLORS = {"WHITE": "Bílá", "BLACK": "Černá", "GREY": "Šedá", "SILVER": "Stříbrná", "BLUE": "Modrá",
             "RED": "Červená", "GREEN": "Zelená", "BROWN": "Hnědá", "BEIGE": "Béžová", "ORANGE": "Oranžová",
             "YELLOW": "Žlutá", "GOLD": "Zlatá", "VIOLET": "Fialová", "BRONZE": "Bronzová"}


def carvago_rows():
    fn = f"{RAW}/carvago/cars.json"
    if not os.path.exists(fn):
        return [], Counter()
    rows, dropped = [], Counter()
    for c in json.load(open(fn)):
        feats = {f["const_key"] for f in c.get("catalog_features") or []}
        title = c.get("title") or ""
        reg = c.get("registration_date") or ""
        year = int((reg or c.get("manufacture_date") or "0")[:4])
        engine = engine_from(c.get("cubic_capacity"), title)
        if "CARSTYLE_ESTATE_CAR" not in feats or "FUELTYPE_HYBRID" not in feats:
            dropped["not kombi hybrid"] += 1; continue
        if year < 2019:
            dropped["first_reg < 2019 or unknown"] += 1; continue
        if engine not in ("1.8", "2.0"):
            dropped[f"engine {engine or '?'}"] += 1; continue
        if feats & {"CONDITION_DAMAGED", "CONDITION_ACCIDENT"} or DAMAGED.search(title) or LPG.search(title):
            dropped["damaged/LPG"] += 1; continue
        cond = "new" if "CONDITION_NEW" in feats else ("demo" if "CONDITION_DEMO" in feats else
                                                      ("used" if "CONDITION_USED" in feats else ""))
        color = next((CV_COLORS.get(k[6:], k[6:].title()) for k in feats if k.startswith("COLOR_")), "")
        seller = c.get("seller") or {}
        stype = (seller.get("type") or {}).get("const_key", "")
        oc = (c.get("origin_country") or {}).get("iso_code")
        pi = c.get("price_information") or {}
        rows.append({
            "source": "carvago",
            "listing_id": f"carvago-{c['id']}",
            "url": f"https://carvago.com/cs/auto/{c['id']}/{c.get('slug', '')}",
            "title": title,
            "price_eur": c.get("price") or "",
            "price_ex_vat_eur": c.get("price_without_vat") if c.get("vat_reclaimable") else "",
            "currency": (c.get("price_currency") or {}).get("name", ""),
            "vat_deductible": b(c.get("vat_reclaimable")),
            "first_reg": reg[:7],
            "km": c.get("mileage") if c.get("mileage") is not None else "",
            "power_kw": c.get("power") or "",
            "engine": engine,
            "trim": parse_trim(title, ""),
            "condition": cond,
            "seller_type": "dealer" if "DEALER" in stype else ("private" if "PRIVATE" in stype else ""),
            "origin_country": oc or "",
            "location_country": (c.get("location_country") or {}).get("iso_code", ""),
            "location_city": c.get("location_city") or "",
            "first_owner": b(c["number_of_owners"] == 1) if c.get("number_of_owners") else "",
            "color": color,
            "listed_date": (c.get("source_created_at") or "")[:10],
            "source_site": c.get("source_name") or "",
            "external_url": c.get("external_url") or "",
            "scraped_at": SCRAPED_AT,
            "vin": c.get("vin") or "",
        })
    return rows, dropped


# ---------------------------------------------------------------- dedupe + output

def dedupe(rows):
    seen, out, dup = set(), [], 0
    for r in rows:
        keys = {("id", r["listing_id"])}
        keys.add(("vin", r["vin"]) if r["vin"] else
                 ("pkf", r.get("price_czk") or r.get("price_eur"), r["km"], r["first_reg"]))
        if keys & seen:
            dup += 1; continue
        seen |= keys
        out.append(r)
    return out, dup


def summary(name, rows, dropped, dup, price_col):
    print(f"\n=== {name}: {len(rows)} rows (dropped by scope: {dict(dropped)}, duplicates removed: {dup})")
    print("rows per source:", dict(Counter(r["source"] for r in rows)))
    yrs = Counter((r["first_reg"] or "?")[:4] for r in rows)
    print("by first-registration year:", dict(sorted(yrs.items())))
    km = [r["km"] for r in rows if r["km"] != ""]
    pr = [r[price_col] for r in rows if r[price_col] != ""]
    if km: print(f"km range: {min(km):,} - {max(km):,} (median {sorted(km)[len(km)//2]:,})")
    if pr: print(f"{price_col} range: {min(pr):,} - {max(pr):,} (median {sorted(pr)[len(pr)//2]:,})")


def write(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    rows, dropped = sauto_rows()
    rows, dup = dedupe(rows)
    write(os.path.join(OUT, "corolla_ts_cz.csv"), rows, COLUMNS)
    summary("corolla_ts_cz.csv", rows, dropped, dup, "price_czk")

    cv, cdrop = carvago_rows()
    if cv:
        cv, cdup = dedupe(cv)
        write(os.path.join(OUT, "corolla_ts_carvago.csv"), cv, list(cv[0].keys()))
        summary("corolla_ts_carvago.csv", cv, cdrop, cdup, "price_eur")
        print("carvago location_country:", dict(Counter(r["location_country"] for r in cv)))
