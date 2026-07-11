from allegro_deals.extract import (
    offer_id_from_url,
    offers_from_html,
    product_links_from_html,
)

LISTING_HTML = """
<html><head><title>brio</title></head><body>
<script>
window.__listing_StoreState = {"items":{"elements":[
 {"id":"16810772956",
  "name":"BRIO 36029 Vlakova sada s mohutnou cervenou akcni lokomotivou",
  "url":"https://allegro.cz/oferta/brio-36029-16810772956",
  "seller":{"login":"toyshop_pl"},
  "sellingMode":{"format":"BUY_NOW","price":{"amount":"239.00","currency":"CZK"}}},
 {"id":"99900011122",
  "name":"BRIO 36029 Vlakova sada cervena lokomotiva NOVA",
  "url":"https://allegro.cz/oferta/brio-36029-jina-99900011122",
  "sellingMode":{"format":"BUY_NOW","price":{"amount":"1399.00","currency":"CZK"}}}
]}};
</script>
<script type="application/json" data-serialize-box-id="x">
{"offers":[{"title":"BRIO 36029 dalsi nabidka",
            "url":"https://allegro.cz/oferta/brio-36029-treti-88800011122",
            "price":{"mainPrice":{"amount":"1450,00","currency":"CZK"}}}]}
</script>
<a href="https://allegro.cz/produkt/brio-36029-vlakova-sada-6a43af52"></a>
<a href="/produkt/lego-duplo-10874-abcdef12"></a>
</body></html>
"""


def test_offers_from_listing_state():
    offers = offers_from_html(LISTING_HTML)
    by_id = {o.offer_id: o for o in offers if o.offer_id}
    assert by_id["16810772956"].price == 239.0
    assert by_id["16810772956"].currency == "CZK"
    assert by_id["16810772956"].seller == "toyshop_pl"
    assert by_id["99900011122"].price == 1399.0


def test_offers_from_application_json_and_comma_decimal():
    offers = offers_from_html(LISTING_HTML)
    third = [o for o in offers if o.offer_id == "88800011122"]
    assert third and third[0].price == 1450.0


def test_product_links():
    links = product_links_from_html(LISTING_HTML)
    assert "https://allegro.cz/produkt/brio-36029-vlakova-sada-6a43af52" in links
    assert "https://allegro.cz/produkt/lego-duplo-10874-abcdef12" in links


def test_offer_id_from_url():
    assert (
        offer_id_from_url(
            "https://allegro.cz/produkt/brio-36029-6a43af52?offerId=16810772956"
        )
        == "16810772956"
    )
    assert offer_id_from_url("https://allegro.pl/oferta/cos-tam-12345678901") == "12345678901"
    assert offer_id_from_url("https://allegro.cz/produkt/bez-id-abc") is None


def test_ignores_broken_json():
    html = "<script>window.x = {broken json;</script>"
    assert offers_from_html(html) == []


OFFER_PAGE_HTML = """
<script>window.dataLayer=[{"price":252,"currency":"CZK","pageType":"detail",
"offerName":"Brio 30411 Parni vlacek Steam & Go","offerId":"15370652022"}];</script>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product",
 "name":"Brio 30411 Parni vlacek Steam & Go",
 "offers":{"@type":"Offer","price":"252.00","priceCurrency":"CZK",
           "url":"https://allegro.cz/produkt/parni-lokomotiva-brio-30411-01c5b2ee"}}
</script>
"""


def test_offer_detail_page_shapes():
    # flat dataLayer dict and schema.org ld+json, as on /nabidka/ pages
    offers = offers_from_html(OFFER_PAGE_HTML)
    prices = {(o.price, o.currency) for o in offers}
    assert (252.0, "CZK") in prices
    assert any(o.offer_id == "15370652022" for o in offers)
