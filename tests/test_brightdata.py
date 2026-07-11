import pytest

from allegro_deals.brightdata import (
    BrightDataError,
    BrightDataFetcher,
    build_request_payload,
    country_for_url,
)


def test_country_per_marketplace():
    assert country_for_url("https://allegro.cz/vyhledavani?string=brio") == "cz"
    assert country_for_url("https://allegro.pl/listing?string=brio") == "pl"


def test_build_request_payload():
    payload = build_request_payload("https://allegro.pl/oferta/x-123", "myzone")
    assert payload == {
        "zone": "myzone",
        "url": "https://allegro.pl/oferta/x-123",
        "format": "raw",
        "country": "pl",
    }


def test_fetcher_requires_token(monkeypatch):
    monkeypatch.delenv("BRIGHTDATA_API_TOKEN", raising=False)
    monkeypatch.delenv("BRIGHTDATA_TOKEN", raising=False)
    with pytest.raises(BrightDataError):
        BrightDataFetcher()


def test_get_many_collects_and_reports_failures(monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_API_TOKEN", "test-token")
    fetcher = BrightDataFetcher()

    def fake_get_html(url):
        if "bad" in url:
            raise BrightDataError("boom")
        return f"<html>{url}</html>"

    monkeypatch.setattr(fetcher, "get_html", fake_get_html)
    logs = []
    out = fetcher.get_many(
        ["https://allegro.cz/a", "https://allegro.cz/bad", "https://allegro.pl/b"],
        log=logs.append,
    )
    assert out == {
        "https://allegro.cz/a": "<html>https://allegro.cz/a</html>",
        "https://allegro.pl/b": "<html>https://allegro.pl/b</html>",
    }
    assert any("1 of 3 pages failed" in line for line in logs)
