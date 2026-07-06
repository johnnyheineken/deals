import json

from allegro_deals.scanner import append_findings, scan_query

SEARCH_HTML = """
<html><body>
<a href="https://allegro.cz/produkt/brio-36029-vlakova-sada-6a43af52"></a>
</body></html>
"""

PRODUCT_HTML = """
<html><body><script>
window.__state = {"offers":[
 {"id":"16810772956","name":"BRIO 36029 Vlakova sada akcni lokomotiva",
  "url":"https://allegro.cz/oferta/brio-16810772956",
  "sellingMode":{"price":{"amount":"239.00","currency":"CZK"}}},
 {"id":"22200000001","name":"BRIO 36029 Vlakova sada akcni lokomotiva",
  "sellingMode":{"price":{"amount":"1399.00","currency":"CZK"}}},
 {"id":"22200000002","name":"BRIO 36029 Vlakova sada akcni lokomotiva",
  "sellingMode":{"price":{"amount":"1450.00","currency":"CZK"}}},
 {"id":"22200000003","name":"BRIO 36029 Vlakova sada (PL cena)",
  "sellingMode":{"price":{"amount":"241.00","currency":"PLN"}}}
]};
</script></body></html>
"""


class StubBrowser:
    def __init__(self):
        self.requested = []

    def get_html(self, url):
        self.requested.append(url)
        return SEARCH_HTML if "/listing" in url else PRODUCT_HTML


def test_scan_query_end_to_end(tmp_path):
    browser = StubBrowser()
    anomalies = scan_query(browser, "brio vlak", fx_pln_czk=5.65, log=lambda _: None)
    assert len(anomalies) == 1
    a = anomalies[0]
    assert a.kind == "currency_swap"
    assert a.offer.offer_id == "16810772956"
    assert a.offer.currency == "CZK"  # the PLN offer must not pollute the peer group
    assert a.context["query"] == "brio vlak"
    assert browser.requested[0].startswith("https://allegro.cz/listing?string=brio%20vlak")

    out = tmp_path / "findings.jsonl"
    append_findings(anomalies, out)
    row = json.loads(out.read_text().splitlines()[0])
    assert row["kind"] == "currency_swap"
    assert row["offer_id"] == "16810772956"
    assert row["price"] == 239.0
