"""Owner-brand product-page recovery (2026-11-16): parser, identity verification, harvester clearance cache."""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "maintenance"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "harvesters"))
import cpie_product_page_harvest as ph

LD = ('<html><script type="application/ld+json">{"@type":"Product","name":"OSSCA 00251 Brake pedal pad","mpn":"00251","sku":"00251",'
      '"brand":{"@type":"Brand","name":"OSSCA"},"offers":{"@type":"Offer","url":"https://www.car-parts.ie/car-parts/ossca/p-13308135",'
      '"priceCurrency":"EUR","price":"1.43","availability":"http://schema.org/OutOfStock"}}</script></html>')
ROW = {"owner_brand": "OSSCA", "article": "00251"}


def test_product_page_parse_and_verify():
    p = ph.parse_product_page(LD)
    assert (p["brand"], p["sku"], p["price_eur"], p["currency"], p["availability"]) == ("OSSCA", "00251", 1.43, "EUR", "OutOfStock")
    assert ph.verify(ROW, p) == (True, "ok")
    assert ph.target_price_ils(p["price_eur"]) == round(1.43 * ph.EUR_ILS, 2)


def test_foreign_brand_page_is_never_proof():
    p = ph.parse_product_page(LD.replace('"name":"OSSCA"', '"name":"METALCAUCHO"'))
    ok, why = ph.verify(ROW, p)
    assert not ok and why.startswith("brand_mismatch")


def test_same_article_other_sku_rejected():
    p = ph.parse_product_page(LD.replace('"sku":"00251"', '"sku":"99999"').replace('"mpn":"00251"', '"mpn":"99999"'))
    assert ph.verify(ROW, p)[0] is False


def test_missing_or_zero_price_rejected():
    p = ph.parse_product_page(LD.replace('"price":"1.43"', '"price":"0"'))
    assert ph.verify(ROW, p) == (False, "no_positive_price")
    assert ph.verify(ROW, None)[0] is False


def test_listing_row_requires_brand_and_article_and_single_price():
    parts = [{"name": "FEBI BILSTEIN 103054 Oil", "sku": "103054", "price_eur": 5.0, "source_url": "u"},
             {"name": "MAPCO 103054 Oil", "sku": "103054", "price_eur": 9.0, "source_url": "u"}]
    assert ph.parse_listing_row(parts, "103054", "FEBI BILSTEIN")["price_eur"] == 5.0
    assert ph.parse_listing_row(parts, "103054", "NRF") is None
    parts.append({"name": "FEBI BILSTEIN 103054 Oil", "sku": "103054", "price_eur": 6.0, "source_url": "u"})
    assert ph.parse_listing_row(parts, "103054", "FEBI BILSTEIN") is None   # two prices for one brand+article: ambiguous


def test_clearance_cache_rejects_stale_failed_and_cookieless(tmp_path):
    import car_parts_ie_flaresolverr_harvester as h
    h._CLEARANCE_CACHE_FILE = tmp_path / "c.json"
    h._CLEARANCE.update(cookie="", ts=0.0)
    def write(**kw):
        h._CLEARANCE_CACHE_FILE.write_text(json.dumps(dict({"cookie": "cf_clearance=abc; x=1", "ua": "UA", "ts": time.time()}, **kw)))
    write(ts=time.time() - h.CLEARANCE_TTL_S - 5); assert h._load_clearance_cache() is False        # stale
    write(cookie="x=1"); assert h._load_clearance_cache() is False                                   # no cf_clearance
    write(); assert h._load_clearance_cache(reject_cookie="cf_clearance=abc; x=1") is False          # the cookie that just 403'd
    write(); assert h._load_clearance_cache() is True and h._CLEARANCE["ua"] == "UA"                  # fresh -> adopted
    h._CLEARANCE.update(cookie="", ts=0.0)
