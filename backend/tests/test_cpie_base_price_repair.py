"""cpie_base_price_repair: manifest = ONLY parts that diverge from the ownership rule; apply is compare-and-swap + rule re-check +
journalled; rollback restores only unchanged rows. Isolated DB only."""
import argparse, gzip, json, os, sys, uuid
from pathlib import Path
import asyncpg, pytest
import isolated_db_guard

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "maintenance"))


def _dsn():
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def test_build_apply_cas_rollback(tmp_path):
    ref = isolated_db_guard.static_refusal()
    if ref is not None:
        pytest.skip(f"isolated DB only: {ref}")
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool
    from BACKEND_DATABASE_MODELS import engine
    _e = create_async_engine(engine.url, poolclass=NullPool)
    async with AsyncSession(_e) as _s:
        live = await isolated_db_guard.live_refusal(_s)
    await _e.dispose()
    if live is not None:
        pytest.fail(f"REFUSED before any write: {live}")
    import cpie_base_price_repair as T
    T.DSN = _dsn(); T.STATE = tmp_path / "st"; T.STOP_FILE = T.STATE / "STOP"
    c = await asyncpg.connect(_dsn())
    tag = uuid.uuid4().hex[:8]
    sid = str(uuid.uuid4()); T.CPIE = sid; src = f"BPSRC-{tag}"
    pids = {}
    try:
        await c.execute("INSERT INTO suppliers(id,name,country,website,reliability_score,is_active,priority,supports_express,rate_limit_per_minute,is_manufacturer,created_at,updated_at)"
                        " VALUES($1::uuid,$2,'IE','https://x.invalid',0.8,TRUE,5,FALSE,30,FALSE,NOW(),NOW())", sid, src)
        bid = str(uuid.uuid4())
        await c.execute("INSERT INTO car_brands(id,name) VALUES($1::uuid,$2)", bid, f"BPB-{tag}")
        async def part(name, base, importer=None):
            pid = str(uuid.uuid4())
            await c.execute("INSERT INTO parts_catalog(id,sku,oem_number,name,name_he,manufacturer,manufacturer_id,part_type,part_condition,importer_price_ils,base_price,specifications,is_active,created_at,updated_at)"
                            " VALUES($1::uuid,$2,$2,$3,$3,'X',$7::uuid,'aftermarket','new',$4,$5,$6::jsonb,TRUE,NOW(),NOW())",
                            pid, f"BPT-{tag}-{name}", name, importer, base, json.dumps({"source": src}), bid)
            return pid
        async def offer(pid, ils):
            await c.execute("INSERT INTO supplier_parts(id,supplier_id,part_id,supplier_sku,price_usd,price_ils,availability,is_available,estimated_delivery_days,supplier_url,created_at,updated_at)"
                            " VALUES(gen_random_uuid(),$1::uuid,$2::uuid,$3,0,$4,'in_stock',TRUE,5,'https://x.invalid/p',NOW(),NOW())", sid, pid, f"O-{pid}", ils)
        pids["legacy"] = await part("legacy", 123.28, 85.02); await offer(pids["legacy"], 100.0)    # legacy: importer=offer/1.18 -> 123.28, rule says 145.00
        pids["ok"] = await part("ok", 145.0, 100.0); await offer(pids["ok"], 100.0)                   # already correct
        pids["nobasis"] = await part("nobasis", 5.0)                                                  # no offer, no estimate input -> never written
        out = tmp_path / "m.jsonl.gz"
        await T.build(argparse.Namespace(out=str(out), batch=100, limit=0))
        rows = [json.loads(l) for l in gzip.open(out, "rt")]
        assert [r["part_id"] for r in rows] == [pids["legacy"]] and rows[0]["target"] == "145.00" and rows[0]["current"] == "123.28"
        sha = open(str(out) + ".sha256").read().strip()
        ns = lambda **k: argparse.Namespace(**{**dict(manifest=str(out), expect_sha256=sha, expect_count=1, approval_ref="test", journal=str(tmp_path / "j.jsonl"),
                                                       batch=100, max_batches=0, sleep=0), **k})
        # a newer legitimate write after the manifest -> compare-and-swap must skip, not overwrite
        await c.execute("UPDATE parts_catalog SET base_price=130.00 WHERE id=$1::uuid", pids["legacy"])
        await T.apply(ns())
        assert float(await c.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1::uuid", pids["legacy"])) == 130.0
        await c.execute("UPDATE parts_catalog SET base_price=123.28 WHERE id=$1::uuid", pids["legacy"])
        # an offer moves after the manifest -> the rule re-check must skip
        await c.execute("UPDATE supplier_parts SET price_ils=90.0 WHERE part_id=$1::uuid", pids["legacy"])
        await T.apply(ns(journal=str(tmp_path / "j2.jsonl")))
        assert float(await c.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1::uuid", pids["legacy"])) == 123.28
        await c.execute("UPDATE supplier_parts SET price_ils=100.0 WHERE part_id=$1::uuid", pids["legacy"])
        # clean apply, idempotent re-run, wrong sha refused, rollback
        await T.apply(ns(journal=str(tmp_path / "j3.jsonl")))
        assert float(await c.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1::uuid", pids["legacy"])) == 145.0
        await T.apply(ns(journal=str(tmp_path / "j3.jsonl")))                       # resumable: nothing left to do
        assert float(await c.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1::uuid", pids["ok"])) == 145.0
        assert float(await c.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1::uuid", pids["nobasis"])) == 5.0
        with pytest.raises(SystemExit):
            await T.apply(ns(expect_sha256="0" * 64))
        await T.rollback(argparse.Namespace(journal=str(tmp_path / "j3.jsonl"), approval_ref="test", batch=100))
        assert float(await c.fetchval("SELECT base_price FROM parts_catalog WHERE id=$1::uuid", pids["legacy"])) == 123.28
    finally:
        await c.execute("DELETE FROM supplier_parts WHERE supplier_id=$1::uuid", sid)
        await c.execute("DELETE FROM parts_catalog WHERE sku LIKE $1", f"BPT-{tag}-%")
        await c.execute("DELETE FROM suppliers WHERE id=$1::uuid", sid)
        await c.execute("DELETE FROM car_brands WHERE name=$1", f"BPB-{tag}")
        await c.close()
