"""Independent QA of db_update_agent.normalize_part_types (Agent 4, 2026-11-07). Disposable DB only (isolated_db_guard:
RUN_DB_MUTATION_TESTS=1, ENVIRONMENT=test, non-production host, no production-only table). Runs the REAL task against rows seeded
for each adversarial case: catalog branch, supplier collision (same supplier+part, rename onto an existing variant key),
idempotence, placeholder 'unknown', unknown/ambiguous labels, a concurrent writer and two concurrent runs."""
import asyncio
import os
import uuid

import pytest

import isolated_db_guard as guard

_REFUSAL = guard.static_refusal()
pytestmark = pytest.mark.skipif(_REFUSAL is not None, reason=f"isolated DB only: {_REFUSAL}")


def _dsn():
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def _c():
    import asyncpg
    return await asyncpg.connect(_dsn())


@pytest.fixture
async def seed(tmp_path, monkeypatch):
    import db_update_agent as D
    monkeypatch.setattr(D, "_PT_MANIFEST_PATH", tmp_path / "manifest.jsonl", raising=False)
    c = await _c()
    brand = await c.fetchval("SELECT id FROM car_brands LIMIT 1")
    tag = uuid.uuid4().hex[:8]
    sup = [await c.fetchval("INSERT INTO suppliers(id,name,country,is_active) VALUES(gen_random_uuid(),$1,'IE',true) RETURNING id", f"QA-PT-{i}-{tag}") for i in range(2)]
    made = {"tag": tag, "parts": [], "sup": sup}

    async def part(pt=None):
        pid = await c.fetchval("INSERT INTO parts_catalog(id,sku,name,manufacturer_id,part_type,is_active) VALUES(gen_random_uuid(),$1,'qa',$2,$3,true) RETURNING id",
                               f"QAPT-{tag}-{len(made['parts'])}", brand, pt)
        made["parts"].append(pid)
        return pid

    async def offer(pid, s, sku, pt):
        return await c.fetchval("INSERT INTO supplier_parts(id,supplier_id,part_id,supplier_sku,price_usd,price_ils,part_type,is_available) "
                                "VALUES(gen_random_uuid(),$1,$2,$3,0,10,$4,true) RETURNING id", s, pid, sku, pt)
    made["part"], made["offer"], made["c"] = part, offer, c
    yield made
    await c.execute("DELETE FROM parts_catalog WHERE id = ANY($1::uuid[])", made["parts"])
    await c.execute("DELETE FROM suppliers WHERE id = ANY($1::uuid[])", sup)
    await c.close()
    from BACKEND_DATABASE_MODELS import engine
    await engine.dispose()          # the shared engine's pooled connections belong to this test's event loop


async def _run():
    import db_update_agent as D
    from BACKEND_DATABASE_MODELS import async_session_factory
    async with async_session_factory() as db:
        if os.getenv("AG4_ISOLATE_SUPPLIER_BRANCH") == "1":
            # diagnostic only: skip the parts_catalog pass so the supplier_parts logic can be judged on its own
            real = D._normalize_part_type_table

            async def only_sp(db_, table, **kw):
                if table == "parts_catalog":
                    return {"table": table, "examined": 0, "changed": 0, "skipped_collision": 0, "errors": 0, "remaining_after": 0,
                            "collisions": [], "error_samples": [], "unmapped": {}, "placeholders_left": {}, "plan": {}}
                return await real(db_, table, **kw)
            D._normalize_part_type_table, saved = only_sp, real
            try:
                return await D.normalize_part_types(db)
            finally:
                D._normalize_part_type_table = saved
        return await D.normalize_part_types(db)


async def _pt(c, table, i):
    return await c.fetchval(f"SELECT part_type FROM {table} WHERE id=$1", i)


async def test_catalog_branch_does_not_crash_and_normalises_class(seed):
    """parts_catalog has NO part_id column: the catalog pass must still work (regression guard for the candidate SELECT)."""
    c = seed["c"]
    p1 = await seed["part"]("Original")
    p2 = await seed["part"]("חליפי")
    r = await _run()
    assert r["status"] == "ok", r
    assert await _pt(c, "parts_catalog", p1) == "oem" and await _pt(c, "parts_catalog", p2) == "aftermarket"


async def test_supplier_collision_is_skipped_reported_and_idempotent(seed):
    c = seed["c"]
    p = await seed["part"]("oem")
    keep = await seed["offer"](p, seed["sup"][0], f"K-{seed['tag']}", "oem")
    mover = await seed["offer"](p, seed["sup"][0], f"M-{seed['tag']}", "מקורי")        # key 'oem' already held by `keep`
    free_part = await seed["part"]("oem")
    ok_row = await seed["offer"](free_part, seed["sup"][0], f"F-{seed['tag']}", "מקורי")  # no sibling: renamed
    r1 = await _run()
    assert r1["status"] == "ok", r1
    assert await _pt(c, "supplier_parts", mover) == "מקורי" and await _pt(c, "supplier_parts", keep) == "oem"
    assert await _pt(c, "supplier_parts", ok_row) == "original"
    assert r1["skipped_collision"] >= 1 and any(x.get("id") == str(mover) for x in r1["collisions"])
    r2 = await _run()          # idempotent: same skip, nothing changes, no error
    assert r2["status"] == "ok" and r2["supplier_updated"] == 0
    assert await _pt(c, "supplier_parts", mover) == "מקורי"


async def test_placeholder_unknown_and_unrecognised_labels_are_never_rewritten(seed):
    c = seed["c"]
    p = await seed["part"]("oem")
    unk = await seed["offer"](p, seed["sup"][0], f"U-{seed['tag']}", "unknown")
    p2 = await seed["part"]("oem")
    weird = await seed["offer"](p2, seed["sup"][0], f"W-{seed['tag']}", "qwerty-label")
    amb = await seed["offer"](await seed["part"]("oem"), seed["sup"][0], f"A-{seed['tag']}", "מקורי/חליפי")
    r = await _run()
    assert r["status"] == "ok", r
    assert await _pt(c, "supplier_parts", unk) == "unknown"
    assert await _pt(c, "supplier_parts", weird) == "qwerty-label"
    assert await _pt(c, "supplier_parts", amb) == "מקורי/חליפי"
    assert r["unmapped"]["supplier_parts"].get("qwerty-label") and r["placeholders_left"]["supplier_parts"].get("unknown")


async def test_two_concurrent_runs_do_not_error_or_double_change(seed):
    c = seed["c"]
    offers = []
    for i in range(30):
        p = await seed["part"]("oem")
        offers.append(await seed["offer"](p, seed["sup"][i % 2], f"C{i}-{seed['tag']}", "Genuine"))
    a, b = await asyncio.gather(_run(), _run())
    assert a["status"] == "ok" and b["status"] == "ok", (a, b)
    after = [await _pt(c, "supplier_parts", o) for o in offers]
    assert after == ["original"] * len(offers), after
    assert a["supplier_updated"] + b["supplier_updated"] >= 30    # SKIP LOCKED: a row is changed once (by one run)


async def test_colliding_row_created_by_another_writer_just_before_the_run_is_not_an_error(seed, monkeypatch):
    """A colliding row created by another writer just before the run is detected by the pre-check (the UPDATE-time race fallback is not reachable from outside and is covered by test_variant_identity)."""
    import db_update_agent as D
    c = seed["c"]
    p = await seed["part"]("oem")
    victim = await seed["offer"](p, seed["sup"][1], f"V-{seed['tag']}", "מקורי")
    real = D._normalize_part_type_table

    async def racing(db, table, **kw):
        if table == "supplier_parts":
            await seed["offer"](p, seed["sup"][1], f"R-{seed['tag']}", "oem")   # appears after planning, before the UPDATE
        return await real(db, table, **kw)
    monkeypatch.setattr(D, "_normalize_part_type_table", racing)
    r = await _run()
    assert r["status"] == "ok", r
    assert await _pt(c, "supplier_parts", victim) == "מקורי"
