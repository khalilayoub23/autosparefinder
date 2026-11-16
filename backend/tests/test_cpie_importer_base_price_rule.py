"""car-parts.ie importer is no longer a second base_price writer (2026-11-16 RCA). Isolated DB only (end-to-end, subprocess-free).

Defect: the importer wrote `price_ils x 1.45` into base_price on insert AND on every re-harvest, regardless of which offer is the part's
price-basis offer; ~41% of a 6,054-part production sample diverged from the ownership rule. Now it writes NULL on insert, never updates
base_price itself, and hands the touched parts to base_price_rule.apply_to_parts (the code the maintenance tasks run)."""
import json, os, sys, uuid
from pathlib import Path
import pytest, asyncpg
import isolated_db_guard

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "importers"))


def _dsn():
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def test_importer_base_price_follows_the_price_basis_offer(tmp_path):
    ref = isolated_db_guard.static_refusal()
    if ref is not None:
        pytest.skip(f"isolated DB only: {ref}")
    import car_parts_ie_import_generic as imp
    imp.DB_DSN = _dsn()      # the importer hard-codes the production host; point it at the isolated DB the guard just verified
    c = await asyncpg.connect(_dsn())
    tag = uuid.uuid4().hex[:8].upper()
    sku = f"BPR{tag}"
    try:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.pool import NullPool
        from BACKEND_DATABASE_MODELS import engine
        _eng = create_async_engine(engine.url, poolclass=NullPool)
        async with AsyncSession(_eng) as _s:
            live = await isolated_db_guard.live_refusal(_s)
        await _eng.dispose()
        if live is not None:
            pytest.fail(f"REFUSED before any write: {live}")
        payload = tmp_path / "p.json"

        def write(eur):
            payload.write_text(json.dumps({"manufacturer": "Toyota", "maker": "toyota", "model_text": "Corolla", "vehicle_slug": "toyota/corolla",
                                           "parts": [{"name": f"RIDEX {sku} Brake pad", "sku": sku, "price_eur": eur, "brand": "RIDEX",
                                                      "category": "brake-pads", "source_url": f"https://www.car-parts.ie/car-parts/toyota/corolla/brake-pads-{tag}"}]}))
        write(10.0)
        r1 = await imp.import_file(payload, vehicle_slug="toyota/corolla")
        assert r1["parts_inserted"] == 1, r1
        pid = await c.fetchval("SELECT part_id::text FROM supplier_parts sp JOIN suppliers s ON s.id=sp.supplier_id WHERE s.name='Car-Parts.ie' AND sp.supplier_sku LIKE $1 LIMIT 1", f"%{sku}")
        assert pid, "offer not written"
        cost = round(10.0 * float(os.getenv("EUR_ILS_RATE", "3.48")), 2)
        base1 = float(await c.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1::uuid", pid))
        assert base1 == round(cost * 1.45, 2)                      # sole offer: rule == cost x 1.45 (foreign supplier, 0% VAT)

        # a CHEAPER compatible visible offer from another supplier becomes the price basis; the importer must not drag base_price back up
        sid = str(uuid.uuid4())
        await c.execute("INSERT INTO suppliers(id,name,country,website,reliability_score,is_active,priority,supports_express,rate_limit_per_minute,is_manufacturer,created_at,updated_at)"
                        " VALUES($1::uuid,$2,'IE','https://x.invalid',0.8,TRUE,5,FALSE,30,FALSE,NOW(),NOW())", sid, f"BPR-SUP-{tag}")
        await c.execute("INSERT INTO supplier_parts(id,supplier_id,part_id,supplier_sku,price_usd,price_ils,availability,is_available,estimated_delivery_days,supplier_url,created_at,updated_at)"
                        " VALUES(gen_random_uuid(),$1::uuid,$2::uuid,$3,0,20.0,'in_stock',TRUE,5,'https://x.invalid/p',NOW(),NOW())", sid, pid, f"BPR-{tag}")
        write(11.0)                                                # re-harvest with a changed price
        r2 = await imp.import_file(payload, vehicle_slug="toyota/corolla")
        assert r2["parts_updated"] == 1 and "error" not in (r2.get("base_price") or {}), r2
        base2 = float(await c.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1::uuid", pid))
        assert base2 == 29.0, base2                                # 20.0 x 1.45 - the cheapest visible offer, NOT this importer's price x 1.45
    finally:
        await c.execute("DELETE FROM supplier_parts WHERE supplier_sku LIKE $1", f"%{tag}%")
        await c.execute("DELETE FROM suppliers WHERE name=$1", f"BPR-SUP-{tag}")
        await c.execute("DELETE FROM part_vehicle_fitment WHERE part_id IN (SELECT id FROM parts_catalog WHERE sku LIKE $1)", f"%{sku}%")
        await c.execute("DELETE FROM parts_catalog WHERE sku LIKE $1", f"%{sku}%")
        await c.close()
