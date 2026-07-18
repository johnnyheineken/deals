"""End-to-end watch test: a stub fetcher yields one clean allegro currency
swap, and we assert it survives the run_watch mapping into the digest.

This locks the CrossDiscrepancy field names (cz_offer/expected_czk) that a
silent try/except once swallowed.
"""

import allegro_deals.watch as watch

# A cz offer at 239 CZK whose pl twin sells for 239 PLN -> currency swap,
# ~1100 CZK saving. Same offer id on both markets.
CZ_SEARCH = """
<script>window.__s={"items":[
 {"id":"18810772956","name":"BRIO 36029 Vlakova sada akcni lokomotiva",
  "url":"https://allegro.cz/oferta/brio-18810772956",
  "sellingMode":{"price":{"amount":"239.00","currency":"CZK"}}}
]};</script>
"""
PL_OFFER = """
<script>window.__s={"offer":
 {"id":"18810772956","name":"BRIO 36029 Pociag",
  "sellingMode":{"price":{"amount":"239.00","currency":"PLN"}}}};</script>
"""


class StubFetcher:
    def get_many(self, urls, log=print):
        out = {}
        for u in urls:
            if "allegro.cz/vyhledavani" in u:
                out[u] = CZ_SEARCH
            elif "allegro.pl/oferta" in u:
                out[u] = PL_OFFER
            else:
                out[u] = "<html></html>"  # empty pl search, kaufland, etc.
        return out


def test_run_watch_surfaces_allegro_swap(tmp_path, monkeypatch):
    # keep the sweep tiny and offline
    monkeypatch.setattr(watch, "ALLEGRO_WATCH", [("brio", None, 150)])
    monkeypatch.setattr(watch, "KAUFLAND_WATCH", [])
    monkeypatch.setattr(watch, "HUNT_WATCH", [])
    rates = {"CZK": 1.0, "PLN": 5.6, "EUR": 24.5}

    path = watch.run_watch(StubFetcher(), rates, out_dir=tmp_path, log=lambda _m: None)
    digest = path.read_text()

    # the swap must reach the digest via the cz_offer/expected_czk mapping
    assert "currency swap" in digest.lower() or "🚨" in digest
    assert "BRIO 36029" in digest
    assert "18810772956" in digest
