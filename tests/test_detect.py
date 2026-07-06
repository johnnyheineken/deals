from allegro_deals.detect import classify_cross_market, find_anomalies
from allegro_deals.models import Offer

FX = 5.65


def _offer(price: float, oid: str) -> Offer:
    return Offer(title=f"BRIO 36029 vlakova sada {oid}", price=price, currency="CZK", offer_id=oid)


def test_brio_scenario_flags_currency_swap():
    offers = [_offer(239, "1"), _offer(1399, "2"), _offer(1450, "3"), _offer(1500, "4")]
    anomalies = find_anomalies(offers, FX)
    assert len(anomalies) == 1
    a = anomalies[0]
    assert a.offer.offer_id == "1"
    assert a.kind == "currency_swap"  # 1450/239 = 6.07, within 30% of 5.65
    assert a.reference_price == 1450


def test_plain_cheap_offer_is_underpriced_not_swap():
    offers = [_offer(400, "1"), _offer(1399, "2"), _offer(1450, "3"), _offer(1500, "4")]
    anomalies = find_anomalies(offers, FX)
    assert len(anomalies) == 1
    assert anomalies[0].kind == "underpriced"  # 1450/400 = 3.6, far from 5.65


def test_needs_enough_peers():
    offers = [_offer(239, "1"), _offer(1450, "2")]
    assert find_anomalies(offers, FX) == []


def test_no_flag_when_prices_agree():
    offers = [_offer(1400, "1"), _offer(1450, "2"), _offer(1500, "3")]
    assert find_anomalies(offers, FX) == []


def test_penny_items_skipped():
    offers = [_offer(5, "1"), _offer(30, "2"), _offer(35, "3")]
    assert find_anomalies(offers, FX) == []


def test_duplicate_offer_not_its_own_peer():
    # The same offer seen twice (listing + product JSON) must not create
    # a fake peer group.
    twice = [_offer(239, "1"), _offer(239, "1"), _offer(1450, "2")]
    assert find_anomalies(twice, FX) == []


def test_cross_market_classification():
    assert classify_cross_market(239.0, 239.0, FX) == "currency_swap"
    assert classify_cross_market(1350.0, 239.0, FX) == "ok"  # 5.65 implied
    assert classify_cross_market(700.0, 239.0, FX) == "mismatch"
