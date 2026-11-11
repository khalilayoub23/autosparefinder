"""BDV pricing basis (b-d-v.co.il states its prices EXCLUDE VAT). Verified 2026-11-11, FIXES_TRACKER #151-BDV."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scrapers"))
import price_policy as pp
import bdv_playwright_scraper as bdv


def test_basis_and_evidence_are_recorded():
    assert bdv.BDV_VAT_BASIS == "exclusive"
    assert "אינם כוללים" in bdv.BDV_VAT_EVIDENCE["quote_he"] and bdv.BDV_VAT_EVIDENCE["url"]


def test_exclusive_price_is_the_cost_and_vat_is_added_once():
    f = bdv.bdv_price_fields(1000.0)
    assert f["cost_ex_vat"] == 1000.0                      # stored supplier cost: NOT grossed up
    assert f["consumer_reference"] == 1180.0               # reference = price + 18% once
    # downstream: policy adds margin once and VAT once on the stored cost
    u = pp.unit_price(f["cost_ex_vat"], "BDV - בכל דרך ושטח", "IL", vat_basis="exclusive")
    assert round(u.net, 2) == 1450.0 and round(u.total, 2) == round(1450 * 1.18, 2)


def test_no_double_vat_regression():
    # old behaviour stored 1180 as the cost -> customer paid 1000*1.18*1.45*1.18
    f = bdv.bdv_price_fields(1000.0)
    assert f["cost_ex_vat"] != round(1000 * 1.18, 2)


def test_idempotent_for_repeated_imports():
    a = bdv.bdv_price_fields(123.45); b = bdv.bdv_price_fields(a["source_price"])
    assert a == b


def test_source_format_changes_still_parse():
    assert bdv.parse_price('<span>1,234.50 ₪</span>') == 1234.5
    assert bdv.parse_price('') is None
    row = bdv.parse_row(["AB123", "name", "grp", "במלאי", "99.90"], "Volvo")
    assert row and row["price_ils"] == 99.9


def test_lookup_request_matches_the_pages_own_filter():
    import urllib.parse
    body = bdv.lookup_body("draw=1&columns%5B0%5D%5Bdata%5D=0&start=0&length=-1&search%5Bvalue%5D=&search%5Bregex%5D=false", "LR016621")
    q = urllib.parse.parse_qs(body, keep_blank_values=True)
    assert q["columns[0][search][value]"] == ["$LR016621^"] and q["columns[0][search][regex]"] == ["false"]
    assert q["length"] == ["-1"] and q["search[value]"] == [""]


def test_short_numbers_are_not_sent(monkeypatch):
    import asyncio
    sent = []

    class C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, content=None, headers=None, timeout=None):
            sent.append(content)
            class R:
                def json(self_): return {"data": [["ABC12345", "פילטר", "G", "במלאי", "100.00"]]}
            return R()
    monkeypatch.setattr(bdv.httpx, "AsyncClient", lambda: C())
    out = asyncio.run(bdv.lookup_skus("draw=1", [], ["ab", "ABC12345", ""], delay=0))
    assert len(sent) == 1 and out[0]["sku"] == "ABC12345" and out[0]["price_ils"] == 100.0


# ── real import_to_db on a disposable DB (skips unless the isolated-DB guard passes) ───────────────────────────
import os, uuid
import pytest
import isolated_db_guard as _guard

_REFUSAL = _guard.static_refusal()


@pytest.mark.skipif(_REFUSAL is not None, reason=f"isolated DB only: {_REFUSAL}")
async def test_import_stores_exclusive_cost_reference_with_vat_once_and_is_idempotent():
    import asyncpg
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    c = await asyncpg.connect(dsn)
    tag = uuid.uuid4().hex[:8]
    sku = f"BDV{tag}".upper()
    brand = await c.fetchval("SELECT id FROM car_brands LIMIT 1")
    part = await c.fetchval("INSERT INTO parts_catalog(id,sku,oem_number,name,manufacturer,manufacturer_id,part_type,is_active) "
                            "VALUES(gen_random_uuid(),$1,$1,'bdv test','Volvo',$2,'oem',true) RETURNING id", sku, brand)
    sup_before = await c.fetchval("SELECT count(*) FROM suppliers WHERE name ILIKE '%bdv%'")
    try:
        rec = [{"sku": sku, "name": "x", "group": "g", "stock": "במלאי", "price_ils": 1000.0, "manufacturer": "Volvo"}]
        for _ in range(2):                                           # repeated import = idempotent
            await bdv.import_to_db(c, rec)
        rows = await c.fetch("SELECT sp.price_ils, sp.supplier_id FROM supplier_parts sp WHERE sp.part_id=$1", part)
        assert len(rows) == 1 and float(rows[0]["price_ils"]) == 1000.0          # cost = quoted ex-VAT price
        mx = await c.fetchval("SELECT max_price_ils FROM parts_catalog WHERE id=$1", part)
        assert float(mx) == 1180.0                                                # consumer reference: VAT once
        sup = await c.fetchrow("SELECT vat_price_basis, vat_basis_evidence FROM suppliers WHERE id=$1", rows[0]["supplier_id"])
        assert sup["vat_price_basis"] == "exclusive" and sup["vat_basis_evidence"]
        # price changes are an update, not a duplicate
        rec[0]["price_ils"] = 1100.0
        await bdv.import_to_db(c, rec)
        assert float(await c.fetchval("SELECT price_ils FROM supplier_parts WHERE part_id=$1", part)) == 1100.0
    finally:
        await c.execute("DELETE FROM supplier_parts WHERE part_id=$1", part)
        await c.execute("DELETE FROM parts_catalog WHERE id=$1", part)
        if sup_before == 0:
            await c.execute("DELETE FROM suppliers WHERE name ILIKE '%bdv%' AND id NOT IN (SELECT supplier_id FROM supplier_parts)")
        await c.close()
