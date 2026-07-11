from allegro_deals.detect import find_cross_discrepancies, model_tokens
from allegro_deals.models import Offer
from allegro_deals.scanner import compare_markets

FX = 5.65


def _cz(price, oid, title="BRIO 36029 Vlakova sada s lokomotivou"):
    return Offer(title=title, price=price, currency="CZK", offer_id=oid,
                 url=f"https://allegro.cz/oferta/x-{oid}")


def _pl(price, oid, title="BRIO 36029 Pociag towarowy z lokomotywa"):
    return Offer(title=title, price=price, currency="PLN", offer_id=oid)


def test_model_tokens():
    assert model_tokens("BRIO 36029 Vlakova sada (2024) 3 ks") == {"36029"}
    assert model_tokens("LEGO 10874 vlacek") == {"10874"}


def test_match_by_shared_offer_id():
    # same offer id on both markets, same number -> classic currency swap
    found = find_cross_discrepancies([_cz(239.0, "16810772956")], [_pl(239.0, "16810772956")], FX)
    assert len(found) == 1
    assert found[0].matched_by == "offer_id"
    assert found[0].kind == "currency_swap"
    assert abs(found[0].expected_czk - 239.0 * FX) < 0.01


def test_match_by_model_number_median():
    cz = [_cz(250.0, "111111111")]
    pl = [_pl(230.0, "222222222"), _pl(249.0, "333333333"), _pl(260.0, "444444444")]
    found = find_cross_discrepancies(cz, pl, FX)
    assert len(found) == 1
    assert found[0].matched_by == "model:36029"
    assert found[0].pln_reference == 249.0
    assert found[0].kind == "currency_swap"


def test_correctly_converted_price_not_flagged():
    # 1400 CZK vs 249 PLN -> ratio 5.6, that's a correct conversion
    assert find_cross_discrepancies([_cz(1400.0, "1")], [_pl(249.0, "2")], FX) == []


def test_no_match_no_flag():
    cz = [_cz(239.0, "1", title="BRIO 36029 vlak")]
    pl = [_pl(249.0, "2", title="BRIO 33884 stanice")]
    assert find_cross_discrepancies(cz, pl, FX) == []


CZ_SEARCH_HTML = """
<script>window.__s = {"items":[
 {"id":"16810772956","name":"BRIO 36029 Vlakova sada s lokomotivou",
  "url":"https://allegro.cz/oferta/brio-16810772956",
  "sellingMode":{"price":{"amount":"239.00","currency":"CZK"}}},
 {"id":"55500000005","name":"BRIO 33884 Nadrazi",
  "url":"https://allegro.cz/oferta/brio-55500000005",
  "sellingMode":{"price":{"amount":"899.00","currency":"CZK"}}}
]};</script>
"""

PL_SEARCH_HTML = """
<script>window.__s = {"items":[
 {"id":"16810772956","name":"BRIO 36029 Pociag towarowy",
  "url":"https://allegro.pl/oferta/brio-16810772956",
  "sellingMode":{"price":{"amount":"239.00","currency":"PLN"}}},
 {"id":"66600000006","name":"BRIO 33884 Stacja kolejowa",
  "url":"https://allegro.pl/oferta/brio-66600000006",
  "sellingMode":{"price":{"amount":"159.00","currency":"PLN"}}}
]};</script>
"""


class StubFetcher:
    def get_many(self, urls, log=print):
        return {
            u: (CZ_SEARCH_HTML if "allegro.cz" in u else PL_SEARCH_HTML) for u in urls
        }


def test_compare_markets_end_to_end():
    found, n_cz, n_pl = compare_markets(
        StubFetcher(), "brio vlak", "brio pociag", fx_pln_czk=FX, pages=1,
        log=lambda _: None,
    )
    assert (n_cz, n_pl) == (2, 2)
    # 36029: 239 CZK vs 239 PLN -> swap; 33884: 899 CZK vs 159 PLN -> 5.65, ok
    assert len(found) == 1
    assert found[0].cz_offer.offer_id == "16810772956"
    assert found[0].kind == "currency_swap"
    assert found[0].matched_by == "offer_id"
