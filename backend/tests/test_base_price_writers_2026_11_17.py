"""base_price writer closure (2026-11-17): catalog_scraper no longer writes a raw source price; subprocess writers load the
supplier VAT-basis registry (an 'inclusive' local supplier must price from its ex-VAT cost, not cost x 1.45 on a VAT-inclusive cost)."""
import asyncio, inspect, os, re, subprocess, sys, textwrap
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import base_price_rule as bpr
import price_policy as pp


class FakeConn:
    def __init__(self, rows=None, boom=False): self.rows, self.boom = rows or [], boom
    async def fetch(self, sql, *a):
        if self.boom: raise RuntimeError("db down")
        return self.rows


def _unload(): pp.load_vat_bases(None)


def test_registry_loads_in_a_bare_subprocess_and_changes_the_price():
    _unload()
    unloaded = pp.unit_price(118.0, "LocalIncl Ltd", "IL").net            # legacy exclusive reading
    assert asyncio.run(bpr.ensure_vat_registry(conn=FakeConn([{"name": "LocalIncl Ltd", "vat_price_basis": "inclusive"}]))) is True
    loaded = pp.unit_price(118.0, "LocalIncl Ltd", "IL").net               # stripped to ex-VAT first
    assert unloaded == round(118.0 * 1.45, 2) and loaded == round(100.0 * 1.45, 2) and loaded < unloaded
    _unload()


def test_ensure_is_noop_when_loaded_and_failure_is_loud_not_silent(caplog):
    pp.load_vat_bases({"a": "exclusive"})
    assert asyncio.run(bpr.ensure_vat_registry(conn=FakeConn(boom=True))) is True      # already loaded: untouched, no query
    _unload()
    with caplog.at_level("ERROR"):
        assert asyncio.run(bpr.ensure_vat_registry(conn=FakeConn(boom=True))) is False
    assert "registry load FAILED" in caplog.text and not bpr.vat_registry_loaded()


def test_apply_to_parts_loads_the_registry_first():
    src = inspect.getsource(bpr.apply_to_parts)
    assert src.index("ensure_vat_registry") < src.index("get_rate")
    import db_update_agent
    s2 = inspect.getsource(db_update_agent.apply_base_price_rule)
    assert s2.index("ensure_vat_registry") < s2.index("get_usd_to_ils_rate")


def test_catalog_scraper_has_no_base_price_writer_of_a_source_price():
    import catalog_scraper
    sig = inspect.signature(catalog_scraper.db_upsert_part)
    assert "base_price" not in sig.parameters
    src = inspect.getsource(catalog_scraper.db_upsert_part)
    assert not re.search(r"base_price\s*=\s*:base_price", src) and ":base_price" not in src
    full = open(catalog_scraper.__file__).read()
    assert "base_price=part[" not in full and "base_price=float(part.get" not in full


def test_db_upsert_part_update_leaves_base_price_alone():
    import catalog_scraper
    seen = []
    class R:
        def fetchone(self): return ("00000000-0000-0000-0000-000000000001",)
    class DB:
        async def execute(self, stmt, params=None):
            seen.append((str(stmt), params or {})); return R()
        async def commit(self): pass
    async def go():
        catalog_scraper._meili_sync_part = lambda *a, **k: asyncio.sleep(0)
        return await catalog_scraper.db_upsert_part(DB(), sku="S1", name="n", manufacturer="m", category="c", part_type="oem")
    pid, created = asyncio.run(go())
    upd = [s for s, p in seen if s.strip().upper().startswith("UPDATE PARTS_CATALOG")][0]
    assert created is False and "base_price" not in upd and all("base_price" not in p for _, p in seen)


def test_base_price_repair_evidence_classes():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "maintenance"))
    import cpie_base_price_repair as r
    assert r.evidence_class("offer", "2026-11-14 10:00:00") == "REPAIRABLE"
    assert r.evidence_class("offer", "2026-09-30 23:59:59") == "STALE_SOURCE"      # old-key offers: reported, never repaired
    assert r.evidence_class("offer", None) == "STALE_SOURCE"
    assert r.evidence_class("estimate", "2026-11-14") == "ESTIMATE_AMBIGUOUS"


def test_ai_catalog_builder_cannot_write_fabricated_prices():
    import ai_catalog_builder
    try:
        asyncio.run(ai_catalog_builder.insert_parts(None, "s", "B", [{"name_he": "x", "price_ils": 100}], set(), False, None))
        assert False, "must refuse"
    except RuntimeError as e:
        assert "fabricates prices" in str(e)
