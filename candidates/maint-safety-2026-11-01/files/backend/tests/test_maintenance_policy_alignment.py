"""#119 — the six maintenance tasks against the price policy and the fitment data flow (2026-10-31).

Isolated database only (tests/isolated_db_guard.py). Every test builds its own rows inside a rolled-back
transaction, except the lock test, which needs a second connection and so commits and deletes its own rows.

Defects these tests guard (each fails on the pre-#119 code unless marked "contract"):
  base_price
    * fix_base_prices only RAISED base_price (a stale higher value stayed)                  -> test_offer_part_*
    * fix_base_prices / normalize_base_price wrote different values for the same part        -> test_no_ping_pong
    * a URL-less (not customer-visible) offer set base_price                                -> test_url_less_*
    * normalize_base_price used online before max_price_ils (policy: importer, max, online)  -> test_estimate_order
    * the cleanup recovery wrote the raw USD cost (no margin, HIGHEST offer) as base_price   -> test_recover_*
    * counters: no scanned/eligible/unchanged/skipped/failed/reasons                         -> test_counters_*
    * no resume point (one unbounded UPDATE)                                                 -> test_resume_*
  compatible_vehicles
    * a part fitting two makes kept only the last make processed                             -> test_merge_two_makes
  name backfills
    * Ford rows were written as "Ford Focus", which vehicle search never matches            -> test_ford_model_*
    * "\\bCONNECT" matched CONNECTION; the Hebrew Ranger name was not recognised           -> test_ford_patterns
    * Jaguar re-read the same first 2000 parts forever                                       -> test_jaguar_walks_*
    * a failed insert aborted the batch silently and inserts were counted on conflict       -> test_backfill_*
"""
import os
import uuid
from contextlib import asynccontextmanager

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import db_cleanup_agent as cleanup
import db_update_agent as dua
import price_policy as pp
from BACKEND_DATABASE_MODELS import engine
import isolated_db_guard  # noqa: E402

RATE = 3.0


@pytest.fixture(autouse=True)
def _guard_env(monkeypatch):
    """Every guarded task call needs Redis (lock) and probes: use the sandbox ones, neutral load/CPU."""
    import maint_guard_support as _mgs
    _mgs.neutral_probes(monkeypatch)


@pytest.fixture
async def db():
    _refusal = isolated_db_guard.static_refusal()
    if _refusal is not None:
        pytest.skip(f"isolated DB only: {_refusal}")
    eng = create_async_engine(engine.url, poolclass=NullPool)
    try:
        conn = await eng.connect()
    except Exception as exc:                                                            # pragma: no cover
        await eng.dispose()
        pytest.skip(f"database unreachable: {type(exc).__name__}")
    trans = await conn.begin()
    sess = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        _live = await isolated_db_guard.live_refusal(sess)
        if _live is not None:
            pytest.fail(f"REFUSED before any write: {_live}")
        await sess.execute(sa.text("""INSERT INTO system_settings (id, key, value, value_type, is_public, updated_at)
            VALUES (gen_random_uuid(), 'ils_per_usd', '3.0', 'string', false, NOW())
            ON CONFLICT (key) DO UPDATE SET value = '3.0'"""))
        await _brands(sess)
        yield sess
    finally:
        await sess.close(); await trans.rollback(); await conn.close(); await eng.dispose()


async def _brands(s):
    # the fitment backfills write fixed car_brands ids (FK); parts need a manufacturer_id
    for name, bid in dua._NAME_FITMENT_MFR_IDS.items():
        await s.execute(sa.text("INSERT INTO car_brands (id, name) VALUES (CAST(:i AS uuid), :n) ON CONFLICT DO NOTHING"),
                        {"i": bid, "n": name})
    s.info["brand"] = (await s.execute(sa.text(
        "INSERT INTO car_brands (id, name) VALUES (gen_random_uuid(), :n) RETURNING id::text"),
        {"n": f"zz-mpa-brand-{uuid.uuid4().hex[:8]}"})).scalar_one()


# ── builders ──────────────────────────────────────────────────────────────────────────────────────────────────
async def part(db, part_type="oem", *, base=None, importer=None, mx=None, online=None, manufacturer="TestMake",
               name="Test part", name_he=None, active=True, category=None):
    pid = str(uuid.uuid4())
    await db.execute(sa.text("""INSERT INTO parts_catalog (id, sku, name, name_he, manufacturer, manufacturer_id, oem_number,
            part_type, category, is_active, base_price, importer_price_ils, max_price_ils, online_price_ils)
        VALUES (CAST(:i AS uuid), :sku, :n, :nh, :m, CAST(:mid AS uuid), :o, :t, :c, :a, :b, :imp, :mx, :onl)"""),
        {"i": pid, "mid": db.info["brand"], "sku": f"MPA-{pid[:12]}", "n": name, "nh": name_he, "m": manufacturer, "o": f"MPA{pid[:8]}",
         "t": part_type, "c": category, "a": active, "b": base, "imp": importer, "mx": mx, "onl": online})
    return pid


async def supplier(db, country="IL", *, active=True, name=None):
    return (await db.execute(sa.text("""INSERT INTO suppliers (id, name, country, is_active)
        VALUES (gen_random_uuid(), :n, :c, :a) RETURNING id::text"""),
        {"n": name or f"MPA supplier {uuid.uuid4().hex[:10]}", "c": country, "a": active})).scalar_one()


async def offer(db, pid, *, ils=None, usd=None, country="IL", klass=None, url=True, available=True, sup=None):
    sup = sup or await supplier(db, country)
    await db.execute(sa.text("""INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_ils, price_usd,
            is_available, availability, part_type, supplier_url, updated_at, created_at)
        VALUES (gen_random_uuid(), CAST(:s AS uuid), CAST(:p AS uuid), :k, :i, :u, :av, 'in_stock', :t, :url, NOW(), NOW())"""),
        {"s": sup, "p": pid, "k": f"MPA-{uuid.uuid4().hex[:12]}", "i": ils,
         "u": usd if usd is not None else round(ils / RATE, 2),        # price_usd is NOT NULL in the schema
         "av": available, "t": klass,
         "url": "https://fixture.invalid/mpa" if url else None})
    return sup


async def base_of(db, pid):
    v = (await db.execute(sa.text("SELECT base_price FROM parts_catalog WHERE id = CAST(:p AS uuid)"), {"p": pid})).scalar()
    return None if v is None else float(v)


async def fix(db, ids):
    r = await dua.fix_base_prices(db, only_part_ids=ids)
    assert r["status"] == "ok", r
    return r


async def normalize(db, ids):
    r = await dua.normalize_base_price(db, only_part_ids=ids)
    assert r["status"] == "ok", r
    return r


# ── base_price ────────────────────────────────────────────────────────────────────────────────────────────────
async def test_offer_part_base_price_is_the_policy_net_in_both_directions(db):
    low = await part(db, base=50)          # stale, below
    high = await part(db, base=999)        # stale, above (the old task only raised)
    for pid in (low, high):
        await offer(db, pid, ils=100)
    await fix(db, [low, high])
    assert await base_of(db, low) == pp.unit_price(100).net == 145.0
    assert await base_of(db, high) == 145.0


@pytest.mark.parametrize("country,usd", [("IL", None), ("IE", None), ("", None), ("IL", 40)])
async def test_local_foreign_unknown_and_usd_offers_match_the_customer_price(db, country, usd):
    from routes.parts import _customer_price_fields
    pid = await part(db)
    sup = await offer(db, pid, ils=None if usd else 80, usd=usd, country=country)
    await fix(db, [pid])
    cost = pp.offer_cost_ils(None if usd else 80, usd, RATE)
    s_row = (await db.execute(sa.text("SELECT name, country FROM suppliers WHERE id = CAST(:s AS uuid)"), {"s": sup})).first()
    shown = _customer_price_fields(cost, 0, s_row.name, s_row.country)
    assert await base_of(db, pid) == shown["customer_price_ils"] == pp.unit_price(cost).net    # base = shown net
    assert shown["customer_vat_ils"] == pp.unit_price(cost, s_row.name, s_row.country).vat     # VAT untouched


async def test_competing_offers_use_the_policy_price_basis(db):
    pid = await part(db, "oem")
    await offer(db, pid, ils=90)                                   # cheapest compatible visible -> basis
    await offer(db, pid, ils=120, klass="oem")
    await offer(db, pid, ils=10, klass="aftermarket")              # other class
    await offer(db, pid, ils=20, available=False)                  # stale / unavailable
    await offer(db, pid, ils=30, url=False)                        # not customer-visible (price list)
    await offer(db, pid, ils=40, sup=await supplier(db, active=False))   # inactive supplier
    await fix(db, [pid])
    assert await base_of(db, pid) == 130.5                         # 90 x 1.45


async def test_url_less_offer_does_not_set_base_price_the_estimate_does(db):
    pid = await part(db, importer=100)
    await offer(db, pid, ils=50, url=False)        # the customer is shown the estimate, not this offer
    r = await fix(db, [pid])
    assert await base_of(db, pid) is None and r["reasons"].get("other_owner") == 1
    await normalize(db, [pid])
    assert await base_of(db, pid) == pp.catalog_estimate_price(100, None, None).net == 145.0


async def test_estimate_order_follows_the_policy(db):
    a = await part(db, importer=0, mx=118, online=50)      # max before online
    b = await part(db, importer=0, mx=0, online=50)
    c = await part(db, importer=80, mx=118, online=50)
    await normalize(db, [a, b, c])
    assert await base_of(db, a) == pp.catalog_estimate_price(0, 118, 50).net == 145.0
    assert await base_of(db, b) == 72.5
    assert await base_of(db, c) == 116.0


async def test_stale_offer_hands_the_part_to_the_estimate(db):
    pid = await part(db, importer=100)
    await offer(db, pid, ils=60)
    await fix(db, [pid])
    assert await base_of(db, pid) == 87.0
    await db.execute(sa.text("UPDATE supplier_parts SET is_available = FALSE WHERE part_id = CAST(:p AS uuid)"), {"p": pid})
    await fix(db, [pid])
    assert await base_of(db, pid) == 87.0                  # fix no longer owns it ...
    await normalize(db, [pid])
    assert await base_of(db, pid) == 145.0                 # ... the estimate does


async def test_other_class_only_and_no_basis_are_left_alone(db):
    a = await part(db, "oem", base=77, importer=100)
    await offer(db, a, ils=20, klass="aftermarket")
    b = await part(db, base=None)
    r1, r2 = await fix(db, [a, b]), await normalize(db, [a, b])
    assert await base_of(db, a) == 77.0 and await base_of(db, b) is None
    assert r2["reasons"] == {"other_class_only": 1, "no_basis": 1} and r1["updated"] == r2["updated"] == 0


async def test_no_ping_pong_between_the_two_owners(db):
    pid = await part(db, importer=80)
    await offer(db, pid, ils=100)
    for _ in range(2):
        await normalize(db, [pid])
        await fix(db, [pid])
    assert await base_of(db, pid) == 145.0
    assert (await normalize(db, [pid]))["updated"] == 0 and (await fix(db, [pid]))["updated"] == 0


async def test_counters_equal_the_rows_actually_changed(db):
    ids = [await part(db, base=b) for b in (None, 145, 10, 145)]
    for pid in ids:
        await offer(db, pid, ils=100)
    ids.append(await part(db))                             # no basis
    before = {p: await base_of(db, p) for p in ids}
    r = await fix(db, ids)
    after = {p: await base_of(db, p) for p in ids}
    changed = sum(1 for p in ids if before[p] != after[p])
    assert (r["scanned"], r["eligible"], r["updated"], r["unchanged"], r["skipped"], r["failed"]) == (5, 4, changed, 2, 1, 0)
    assert changed == 2 and r["reasons"] == {"no_basis": 1}
    again = await fix(db, ids)
    assert again["updated"] == 0 and again["unchanged"] == 4


async def test_resume_after_interruption_and_wrap(db, monkeypatch):
    for _ in range(10):
        await offer(db, await part(db), ils=100)
    monkeypatch.setenv("BASE_PRICE_TASK_BATCH", "3")
    await dua._save_task_cursor(db, "fix_base_prices", dua._ZERO_UUID)
    first = await dua.fix_base_prices(db, max_batches=1)
    assert first["status"] == "ok" and first["batches"] == 1 and first["resume_after"] != dua._ZERO_UUID
    assert await dua._get_task_cursor(db, "fix_base_prices") == first["resume_after"]
    second = await dua.fix_base_prices(db, max_batches=1)
    assert second["started_after"] == first["resume_after"]             # continues, does not restart

    calls = {"n": 0}
    real = dua.apply_base_price_rule

    async def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated failure")
        return await real(*a, **k)

    monkeypatch.setattr(dua, "apply_base_price_rule", flaky)
    broken = await dua.fix_base_prices(db, max_batches=5)
    assert broken["status"] == "error" and broken["failed"] == 1
    kept = await dua._get_task_cursor(db, "fix_base_prices")
    assert kept == broken["resume_after"]                               # the failed batch is retried next run
    monkeypatch.setattr(dua, "apply_base_price_rule", real)
    resumed = await dua.fix_base_prices(db, max_batches=1)
    assert resumed["started_after"] == kept and resumed["status"] == "ok"
    monkeypatch.setenv("BASE_PRICE_TASK_BATCH", "100000")
    done = await dua.fix_base_prices(db)
    assert done["pass_completed"] and await dua._get_task_cursor(db, "fix_base_prices") == dua._ZERO_UUID


async def test_cleanup_normalizer_uses_the_rule_and_fills_only_local_cost_columns(db):
    loc = await part(db)
    await offer(db, loc, ils=100, url=False)               # local price-list offer: not visible
    frn = await part(db)
    await offer(db, frn, ils=100, country="IE")            # foreign visible offer
    kept = await part(db, importer=70)
    await offer(db, kept, ils=100, url=False)              # existing importer cost is never overwritten
    await cleanup.normalize_base_price_for_parts(db, 10, only_ids=[loc, frn, kept])
    rows = {r.id: r for r in (await db.execute(sa.text(
        "SELECT id::text AS id, base_price, importer_price_ils, max_price_ils FROM parts_catalog WHERE id = ANY(CAST(:i AS uuid[]))"),
        {"i": [loc, frn, kept]})).fetchall()}
    assert (float(rows[loc].importer_price_ils), float(rows[loc].max_price_ils), float(rows[loc].base_price)) == (100, 118, 145)
    assert rows[frn].importer_price_ils is None and rows[frn].max_price_ils is None and float(rows[frn].base_price) == 145
    assert float(rows[kept].importer_price_ils) == 70 and float(rows[kept].base_price) == 101.5   # estimate 70 x 1.45


async def test_recover_priced_inactive_writes_the_policy_price_not_the_raw_cost(db, monkeypatch):
    pid = await part(db, active=False)
    await offer(db, pid, usd=40)                            # visible USD offer
    await offer(db, pid, usd=90, url=False)                 # the old code took the HIGHEST, without margin

    @asynccontextmanager
    async def same_session():
        yield db
    monkeypatch.setattr(cleanup, "scraper_session_factory", same_session)
    monkeypatch.setattr(cleanup, "_RECOVER_INACTIVE_SKIP_UNTIL", 0.0)
    assert await cleanup.task_recover_priced_inactive() >= 1
    assert await base_of(db, pid) == pp.unit_price(40 * RATE).net == 174.0      # not 90 x 3.0 = 270


# ── compatible_vehicles ───────────────────────────────────────────────────────────────────────────────────────
async def pvf(db, pid, mfr, model, yf=2015, yt=2018, engine=None):
    await db.execute(sa.text("""INSERT INTO part_vehicle_fitment (id, part_id, manufacturer, manufacturer_id, model,
            year_from, year_to, engine_type, updated_at)
        VALUES (gen_random_uuid(), CAST(:p AS uuid), :m, CAST(:mid AS uuid), :mo, :yf, :yt, :e, NOW())"""),
        {"p": pid, "m": mfr, "mid": db.info["brand"], "mo": model, "yf": yf, "yt": yt, "e": engine})


async def cv(db, pid):
    return (await db.execute(sa.text("SELECT compatible_vehicles FROM parts_catalog WHERE id = CAST(:p AS uuid)"),
                             {"p": pid})).scalar()


async def test_merge_two_makes_keeps_both_and_is_idempotent(db):
    pid = await part(db)
    await db.execute(sa.text("""UPDATE parts_catalog SET compatible_vehicles = CAST(:j AS jsonb) WHERE id = CAST(:p AS uuid)"""),
                     {"p": pid, "j": '[{"manufacturer": "Toyota", "model": "Corolla", "source": "xls"},'
                                     ' {"manufacturer": "Toyota", "model": "Corolla", "source": "xls"},'
                                     ' {"manufacturer": "Lexus", "model": "OLD", "source": "part_vehicle_fitment"}]'})
    await pvf(db, pid, "Toyota", "Corolla")
    await pvf(db, pid, "Lexus", "CT")
    r = await dua.merge_catalog_fitment_from_part_vehicle_fitment(db, only_part_ids=[pid])
    got = await cv(db, pid)
    makes = sorted({e["manufacturer"] for e in got if e.get("source") == "part_vehicle_fitment"})
    assert r["status"] == "ok" and r["updated_parts"] == 1 and len(makes) == 2          # both makes
    assert not any(e.get("model") == "OLD" for e in got)                                 # stale copy replaced
    assert sum(1 for e in got if e.get("source") == "xls") == 1                          # other source kept, deduped
    again = await dua.merge_catalog_fitment_from_part_vehicle_fitment(db, only_part_ids=[pid])
    assert again["updated_parts"] == 0 and again["unchanged"] == 1 and await cv(db, pid) == got


async def test_merge_cursor_resumes(db, monkeypatch):
    monkeypatch.setenv("FITMENT_MERGE_BATCH", "2")
    for _ in range(3):
        await pvf(db, await part(db), "Mazda", "3")
    await dua._save_task_cursor(db, "merge_catalog_fitment_from_part_vehicle_fitment", dua._ZERO_UUID)
    a = await dua.merge_catalog_fitment_from_part_vehicle_fitment(db, max_batches=1)
    b = await dua.merge_catalog_fitment_from_part_vehicle_fitment(db, max_batches=1)
    assert a["status"] == b["status"] == "ok" and b["started_after"] == a["resume_after"] != dua._ZERO_UUID


# ── name backfills ────────────────────────────────────────────────────────────────────────────────────────────
async def fit_rows(db, pid):
    return sorted((r[0], r[1]) for r in (await db.execute(sa.text(
        "SELECT manufacturer, model FROM part_vehicle_fitment WHERE part_id = CAST(:p AS uuid)"), {"p": pid})).fetchall())


async def test_ford_model_is_stored_without_the_make_and_is_found_by_vehicle_search(db):
    import vehicle_model_match as vmm
    pid = await part(db, manufacturer="Ford", name="PUMP-FUEL INJ.", name_he="#  משאבת הזרקה פוקוס דיזל")
    r = await dua.backfill_ford_fitment_from_name_he(db, only_part_ids=[pid])
    assert r["updated"] == 1 and await fit_rows(db, pid) == [("Ford", "Focus")]
    vmm._cache.clear()
    assert "focus" in await vmm.allowed_fitment_models(db, ["ford"], ["focus"])


async def test_ford_patterns(db):
    a = await part(db, manufacturer="Ford", name="CONNECTION", name_he="# מחבר ליציאת מים ריינג'ר")
    b = await part(db, manufacturer="Ford", name="CONNECTOR ASY")
    await dua.backfill_ford_fitment_from_name_he(db, only_part_ids=[a, b])
    assert await fit_rows(db, a) == [("Ford", "Ranger")] and await fit_rows(db, b) == []


async def test_jaguar_walks_past_the_first_2000_and_skips_merchandise(db):
    await db.execute(sa.text("""INSERT INTO parts_catalog (id, sku, name, manufacturer, manufacturer_id, part_type, is_active)
        SELECT CAST('00000000-0000-4000-8000-' || lpad(to_hex(g), 12, '0') AS uuid), 'MPA-JAG-' || g, 'Bolt', 'Jaguar',
               CAST(:mid AS uuid), 'oem', TRUE
        FROM generate_series(1, 2001) g"""), {"mid": db.info["brand"]})
    tail = await part(db, manufacturer="Jaguar", name="XF 2.7D Rear Sports Exhaust Sections")
    merch = await part(db, manufacturer="Jaguar", name="F-type T-shi")
    await db.execute(sa.text("UPDATE parts_catalog SET id = CAST('ffffffff-ffff-4fff-8fff-fffffffffff0' AS uuid) WHERE id = CAST(:p AS uuid)"),
                     {"p": tail})
    await dua._save_task_cursor(db, "backfill_jaguar_fitment_from_name", dua._ZERO_UUID)
    r = await dua.backfill_jaguar_fitment_from_name(db)
    assert r["pass_completed"] and await fit_rows(db, "ffffffff-ffff-4fff-8fff-fffffffffff0") == [("Jaguar", "XF")]
    assert await fit_rows(db, merch) == [] and r["reasons"].get("merchandise", 0) >= 1


async def test_backfill_counts_real_inserts_and_survives_one_failed_insert(db, monkeypatch):
    pid = await part(db, manufacturer="BMW", name="WIPER BLADE", name_he="F-54 G20 'מגב אח")    # MINI F54 + BMW G20
    monkeypatch.setitem(dua._NAME_FITMENT_MFR_IDS, "BMW", str(uuid.uuid4()))                 # BMW insert -> FK error
    r = await dua.backfill_bmw_fitment_from_name_he(db, only_part_ids=[pid])
    assert r["failed"] == 1 and r["updated"] == 1 and r["status"] == "error"
    assert await fit_rows(db, pid) == [("MINI", "MINI Clubman")]                               # the MINI row survived
    monkeypatch.undo()
    await db.execute(sa.text("DELETE FROM part_vehicle_fitment WHERE part_id = CAST(:p AS uuid)"), {"p": pid})
    first = await dua.backfill_bmw_fitment_from_name_he(db, only_part_ids=[pid])
    assert (first["updated"], first["failed"]) == (2, 0)
    await db.execute(sa.text("DELETE FROM part_vehicle_fitment WHERE part_id = CAST(:p AS uuid) AND manufacturer = 'MINI'"),
                     {"p": pid})
    assert (await dua.backfill_bmw_fitment_from_name_he(db, only_part_ids=[pid]))["scanned"] == 0   # has fitment now


# ── concurrency (committed rows: a second connection must hold the lock) ─────────────────────────────────────
async def test_a_row_another_writer_holds_is_skipped_not_waited_for():
    _refusal = isolated_db_guard.static_refusal()
    if _refusal is not None:
        pytest.skip(f"isolated DB only: {_refusal}")
    eng = create_async_engine(engine.url, poolclass=NullPool)
    pid = str(uuid.uuid4())
    try:
        async with AsyncSession(eng) as s:
            assert await isolated_db_guard.live_refusal(s) is None
            sup = await supplier(s)
            mid = (await s.execute(sa.text("SELECT id::text FROM car_brands LIMIT 1"))).scalar()
            if mid is None:
                mid = (await s.execute(sa.text("INSERT INTO car_brands (id, name) VALUES (gen_random_uuid(), 'zz-mpa-lock') RETURNING id::text"))).scalar_one()
            await s.execute(sa.text("INSERT INTO parts_catalog (id, sku, name, manufacturer_id, part_type, is_active) VALUES (CAST(:i AS uuid), :k, 'lock', CAST(:m AS uuid), 'oem', TRUE)"),
                            {"i": pid, "k": f"MPA-LOCK-{pid[:8]}", "m": mid})
            await offer(s, pid, ils=100, sup=sup)
            await s.commit()
        async with eng.connect() as holder:
            await holder.begin()
            await holder.execute(sa.text("SELECT 1 FROM parts_catalog WHERE id = CAST(:p AS uuid) FOR UPDATE"), {"p": pid})
            async with AsyncSession(eng) as s:
                await s.execute(sa.text("SET LOCAL lock_timeout = '3s'"))
                r = await dua.apply_base_price_rule(s, part_ids=[pid], usd_to_ils=RATE)
                await s.commit()
            await holder.rollback()
        assert r["updated"] == 0 and r["reasons"] == {"locked_or_changed": 1}
        async with AsyncSession(eng) as s:
            r2 = await dua.apply_base_price_rule(s, part_ids=[pid], usd_to_ils=RATE)
            await s.commit()
            assert r2["updated"] == 1 and await base_of(s, pid) == 145.0
    finally:
        async with AsyncSession(eng) as s:
            await s.execute(sa.text("DELETE FROM supplier_parts WHERE part_id = CAST(:p AS uuid)"), {"p": pid})
            await s.execute(sa.text("DELETE FROM parts_catalog WHERE id = CAST(:p AS uuid)"), {"p": pid})
            await s.execute(sa.text("DELETE FROM suppliers WHERE name LIKE 'MPA supplier %' AND NOT EXISTS "
                                    "(SELECT 1 FROM supplier_parts sp WHERE sp.supplier_id = suppliers.id)"))
            await s.commit()
        await eng.dispose()


# ── contract: the six tasks stay blocked until the owner approves the scheduler change ──────────────────────────
def test_contract_tasks_still_registered_and_blocked():
    six = {"fix_base_prices", "normalize_base_price", "backfill_bmw_fitment_from_name_he",
           "backfill_ford_fitment_from_name_he", "backfill_jaguar_fitment_from_name",
           "merge_catalog_fitment_from_part_vehicle_fitment"}
    assert six <= set(dua.TASK_REGISTRY) and six <= dua.OOM_BLOCKED_TASKS


# ── name extractors (pure; every case is a live catalogue name, 2026-10-31) ──────────────────────────────────────
@pytest.mark.parametrize("name,name_he,expected", [
    ("235 55 R18 104v Ventus", "", []),                                   # tyre size, not BMW R 18 (644 live rows)
    ("225/45ZR17 tyre", "", []),
    ("Fixed side wor", "U25 'שמשה לדלת אח'ימ", [("MINI", "MINI Countryman")]),   # U25 = MINI Countryman, not X2
    ("Grill front or", "U10 סורג חזית", [("BMW", "X2")]),
    ("Left illuminor", "G20 מגן שמן נהג", [("BMW", "3 Series")]),
])
def test_bmw_extractor(name, name_he, expected):
    assert [(f[0], f[1]) for f in dua._bmw_fitment_from_name(name, name_he)[0]] == expected


@pytest.mark.parametrize("name,reason", [
    ("F-type T-shi", "merchandise"), ("Jr M Xe Tee,", "merchandise"), ("F-Type 24\" suitcase - Black", "merchandise"),
    ("F-Type 'F' cufflinks - Silver", "merchandise"), ("Leather F-Type card holder", "merchandise"),
    ("1/43 F-pace", "merchandise"), ("XK D.I.Y. Restoration & Maintenance, by Bob Exelby", "merchandise"),
    ("X-Type Parts & Technical USB", "merchandise"),
    ("Borg & Beck Clutch Kit for Jaguar E-Type; MK II and XK", "classic_era"),
    ("Borg & Beck Clutch Kit for Jaguar XJ (1968-1993) 6 4.2", "classic_era"),
    ("Jaguar S Type Revotec Fan Kit - Positive Earth", "classic_era"),
    ("Jaguar Xj", "no_descriptive_word"),
    ("MEYLE 300 135 1005/XK Gearbox service kit with accessories", "no_model_name"),     # /XK = MEYLE kit suffix
])
def test_jaguar_extractor_rejects(name, reason):
    assert dua._jaguar_fitment_from_name(name, "") == ([], reason)


def test_jaguar_extractor_keeps_real_parts():
    assert [f[1] for f in dua._jaguar_fitment_from_name("XF 2.7D Rear Sports Exhaust Sections", "")[0]] == ["XF"]
    assert [f[1] for f in dua._jaguar_fitment_from_name("S-Type Body Rubber Seal Kit", "")[0]] == ["S-Type"]
    assert [f[1] for f in dua._jaguar_fitment_from_name("Tee piece hose XF", "")[0]] == ["XF"]


def test_ford_extractor_has_no_make_prefix_and_no_connection_false_positive():
    assert dua._ford_fitment_from_name("CONNECTION", "# מחבר ליציאת מים ריינג'ר")[0] == [("Ford", "Ranger", 1983, 2026, "extracted:name_he")]
    assert dua._ford_fitment_from_name("CONNECTOR ASY", "")[0] == []
    assert all(not f[1].startswith("Ford ") for p, *_ in dua._FORD_MODEL_PATTERNS for f in dua._ford_fitment_from_name(p, "")[0])
