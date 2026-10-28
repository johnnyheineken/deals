"""Fetch TipCars.com listings via the Apify actor rainminer/tipcars-com-scraper.

Needs APIFY_TOKEN in the environment. Cost is ~$0.008 per returned listing.
usage: APIFY_TOKEN=... python3 fetch_tipcars_apify.py RAW_DIR/tipcars
"""
import json, os, sys
import requests

ACTOR = "rainminer~tipcars-com-scraper"
START_URLS = ["https://www.tipcars.com/toyota-corolla/kombi/"]

out = sys.argv[1] if len(sys.argv) > 1 else "raw/tipcars"
os.makedirs(out, exist_ok=True)
token = os.environ["APIFY_TOKEN"]
r = requests.post(
    f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items",
    params={"token": token, "timeout": 290},
    json={"startUrls": [{"url": u} for u in START_URLS], "maxItems": 1000,
          "proxyConfiguration": {"useApifyProxy": True}},
    timeout=300,
)
r.raise_for_status()
items = r.json()
json.dump(items, open(f"{out}/items.json", "w"), ensure_ascii=False)
print("done", len(items))
