from allegro_deals.fx import parse_cnb_rates

CNB_SAMPLE = """03.07.2026 #127
zeme|mena|mnozstvi|kod|kurz
Austrálie|dolar|1|AUD|14,662
Polsko|zlotý|1|PLN|5,647
Maďarsko|forint|100|HUF|6,201
"""


def test_parse_cnb_rates():
    rates = parse_cnb_rates(CNB_SAMPLE)
    assert rates["PLN"] == 5.647
    assert rates["AUD"] == 14.662
    assert abs(rates["HUF"] - 0.06201) < 1e-9  # per-unit, amount=100
