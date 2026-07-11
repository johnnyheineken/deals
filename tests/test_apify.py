import pytest

from allegro_deals.apify import ApifyError, ApifyFetcher, build_actor_input, htmls_from_items
from allegro_deals.scanner import scan_query
from test_scanner import PRODUCT_HTML, SEARCH_HTML


def test_build_actor_input():
    urls = ["https://allegro.cz/vyhledavani?string=brio", "https://allegro.cz/produkt/x-1"]
    inp = build_actor_input(urls, ["RESIDENTIAL"], "CZ")
    assert inp["startUrls"] == [{"url": u} for u in urls]
    assert inp["maxPagesPerCrawl"] == 2
    assert inp["proxyConfiguration"]["apifyProxyGroups"] == ["RESIDENTIAL"]
    assert inp["proxyConfiguration"]["apifyProxyCountry"] == "CZ"
    assert "pageFunction" in inp and "outerHTML" in inp["pageFunction"]


def test_htmls_from_items_skips_failures():
    items = [
        {"url": "https://a", "html": "<html>ok</html>", "status": 200},
        {"url": "https://b", "html": None},
        {"html": "<html>orphan</html>"},
    ]
    assert htmls_from_items(items) == {"https://a": "<html>ok</html>"}


def test_fetcher_requires_token(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    with pytest.raises(ApifyError):
        ApifyFetcher()


class StubBatchFetcher:
    """Duck-types ApifyFetcher: batch interface via get_many."""

    def __init__(self):
        self.batches = []

    def get_many(self, urls, log=print):
        self.batches.append(list(urls))
        return {u: (SEARCH_HTML if "/vyhledavani" in u else PRODUCT_HTML) for u in urls}


def test_scan_query_uses_batch_interface():
    fetcher = StubBatchFetcher()
    anomalies = scan_query(fetcher, "brio vlak", fx_pln_czk=5.65, log=lambda _: None)
    assert len(anomalies) == 1
    assert anomalies[0].kind == "currency_swap"
    # one batch for search pages, one for product pages
    assert len(fetcher.batches) == 2
    assert "/vyhledavani" in fetcher.batches[0][0]
    assert "/produkt/" in fetcher.batches[1][0]
