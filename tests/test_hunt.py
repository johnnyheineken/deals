from allegro_deals.extract import bazos_offers_from_html, kaufland_offers_from_html
from allegro_deals.hunt import Finding, cross_source_gaps, triangulate_allegro, _token_index
from allegro_deals.models import Offer

RATES = {"CZK": 1.0, "PLN": 5.6, "EUR": 24.5}

BAZOS_HTML = """
<div class="inzeraty inzeratyflex">
<div class="inzeratynadpis"><a href="https://deti.bazos.cz/inzerat/221224405/brio-33873.php"><img></a>
<h2 class=nadpis><a href="https://deti.bazos.cz/inzerat/221224405/brio-33873.php">BRIO World 33873 Chytry vlak Smart Tech</a></h2><span class=velikost10> - [12.7. 2026]</span><br>
<div class=popis>popis</div></div>
<div class="inzeratycena"><b><span translate="no">  1 090 Kč</span></b></div>
</div>
"""

KAUFLAND_HTML = """
<a href="/p/12345/"><span class="product-title product-title--bold tile__title" title="BRIO 33873 Smart Tech vlak" data-v-1="">BRIO 33873 Smart Tech vlak</span></a>
<div class="product-price__final-price" data-v-2="">1&nbsp;299,00&nbsp;Kč</div>
"""


def test_bazos_parser():
    offers = bazos_offers_from_html(BAZOS_HTML)
    assert len(offers) == 1
    assert offers[0].price == 1090.0
    assert offers[0].offer_id == "bazos-221224405"
    assert "Smart Tech" in offers[0].title


def test_kaufland_parser():
    offers = kaufland_offers_from_html(KAUFLAND_HTML)
    assert len(offers) == 1
    assert offers[0].price == 1299.0
    assert offers[0].currency == "CZK"


def _offer(price, currency, oid, title="BRIO 36029 vlak"):
    return Offer(title=title, price=price, currency=currency, offer_id=oid)


def test_triangulation_catches_eur_swap():
    # 9.99 EUR shown as 9.99-ish number where PLN twin costs 239 PLN
    markets = {
        "allegro.sk": [_offer(43.0, "EUR", "111")],   # ~1054 CZK, fair
        "allegro.pl": [_offer(239.0, "PLN", "111")],  # ~1338 CZK
        "allegro.cz": [_offer(239.0, "CZK", "111")],  # raw PLN number -> swap
    }
    found = triangulate_allegro(markets, RATES)
    assert len(found) == 1
    f = found[0]
    assert f.kind == "currency_swap"
    assert f.source == "allegro.cz"
    assert f.saving > 1000


def test_triangulation_ok_prices_not_flagged():
    markets = {
        "allegro.cz": [_offer(1350.0, "CZK", "1")],
        "allegro.pl": [_offer(239.0, "PLN", "1")],
        "allegro.sk": [_offer(54.0, "EUR", "1")],
    }
    assert triangulate_allegro(markets, RATES) == []


def test_cross_source_gap_by_token():
    kaufland = [(Offer(title="BRIO 33873 vlak", price=600.0, currency="CZK"), 600.0)]
    allegro_index = _token_index(
        [(Offer(title="BRIO 33873 Smart Tech", price=1500.0, currency="CZK"), 1500.0)]
    )
    found = cross_source_gaps(kaufland, allegro_index, "kaufland.cz", "allegro", "kaufland_gap")
    assert len(found) == 1
    assert found[0].saving == 900.0
    assert found[0].kind == "kaufland_gap"
