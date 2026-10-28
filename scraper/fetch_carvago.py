"""Download Carvago.com search results (SSR __NEXT_DATA__) for Toyota Corolla kombi."""
import json, os, random, re, sys, time
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
BASE = "https://carvago.com/cs/auta/toyota/corolla/kombi"

out = sys.argv[1] if len(sys.argv) > 1 else "raw/carvago"
os.makedirs(out, exist_ok=True)
s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept-Language": "cs"})

cars, page = {}, 1
while True:
    r = s.get(BASE, params={"page": page} if page > 1 else None, timeout=30)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    sr = json.loads(m.group(1))["props"]["pageProps"]["searchResults"]
    new = [c for c in sr["cars"] if c["id"] not in cars]
    cars.update((c["id"], c) for c in sr["cars"])
    print(f"page={page} got={len(sr['cars'])} new={len(new)} total={sr['total']} have={len(cars)}", flush=True)
    if not new or len(cars) >= sr["total"]:
        break
    page += 1
    time.sleep(random.uniform(1.0, 2.0))
json.dump(list(cars.values()), open(f"{out}/cars.json", "w"), ensure_ascii=False)
print("done", len(cars))
