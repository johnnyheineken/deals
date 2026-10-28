"""Download raw Sauto.cz search + detail JSON for Toyota Corolla kombi hybrid."""
import json, os, random, sys, time
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
API = "https://www.sauto.cz/api/v1/items"
SEARCH = {
    "category_id": 838,                       # osobní
    "manufacturer_model_seo": "toyota:corolla",
    "vehicle_body_seo": "kombi",
    "fuel_seo": "hybridni",
    "condition_seo": "nove,ojete,predvadeci",  # excludes havarovane
    "operating_lease": "false",
}

out = sys.argv[1] if len(sys.argv) > 1 else "raw/sauto"
os.makedirs(f"{out}/detail", exist_ok=True)
s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept": "application/json", "Accept-Language": "cs"})

def pause():
    time.sleep(random.uniform(1.0, 2.0))

items, offset = [], 0
while True:
    r = s.get(f"{API}/search", params={**SEARCH, "limit": 100, "offset": offset}, timeout=30)
    r.raise_for_status()
    d = r.json()
    items += d["results"]
    total = d["pagination"]["total"]
    print(f"search offset={offset} got={len(d['results'])} total={total}", flush=True)
    offset += 100
    pause()
    if offset >= total or not d["results"]:
        break
json.dump(items, open(f"{out}/search.json", "w"), ensure_ascii=False)

for i, it in enumerate(items):
    fn = f"{out}/detail/{it['id']}.json"
    if os.path.exists(fn):
        continue
    r = s.get(f"{API}/{it['id']}", timeout=30)
    if r.status_code == 200:
        open(fn, "w").write(r.text)
    else:
        print("detail", it["id"], r.status_code, flush=True)
    if i % 25 == 0:
        print(f"detail {i}/{len(items)}", flush=True)
    pause()
print("done", len(items))
