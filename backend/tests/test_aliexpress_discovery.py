"""Discovery-seeded AliExpress targeting (2026-09-21).

Root cause it fixes: nothing recorded which catalog parts AliExpress carries, so the sync probed blindly (0/130
unpriced, 2/144 priced). Discovery harvests the OE numbers sellers CITE in real listing titles (same token-bounded
guard as matching), persists catalog matches in `aliexpress_candidates`, and the sync forward-matches only those.
Real Postgres schema, rolled-back transaction, AliExpress network mocked. Matching rules are NOT exercised/changed here.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import services.aliexpress_price_sync as svc
from BACKEND_DATABASE_MODELS import engine
from services.suppliers.base_supplier import PartResult


@pytest.fixture
async def db():
    eng = create_async_engine(engine.url, poolclass=NullPool)
    try:
        conn = await eng.connect()
    except Exception as exc:                                                            # pragma: no cover
        await eng.dispose()
        pytest.skip(f"database unreachable: {type(exc).__name__}")
    trans = await conn.begin()
    sess = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    await svc.ensure_candidates_table(sess)
    try:
        yield sess
    finally:
        await sess.close(); await trans.rollback(); await conn.close(); await eng.dispose()


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("ALIEXPRESS_TARGETING", "discovery")
    monkeypatch.setenv("ALIEXPRESS_BLIND_SHARE", "0")
    monkeypatch.setenv("ALIEXPRESS_PRICED_SHARE", "0.5")
    monkeypatch.setattr(svc.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(svc, "get_usd_to_ils_rate", AsyncMock(return_value=3.7))
    monkeypatch.setattr(svc, "_meili_sync_part", AsyncMock(return_value=True))
    monkeypatch.setattr(svc.aliexpress, "_ensure_token", AsyncMock(return_value=True))
    monkeypatch.setattr(svc.aliexpress, "get_part_details", AsyncMock(return_value=None))


async def _ali(db):
    return (await db.execute(sa.text("SELECT id::text FROM suppliers WHERE name='AliExpress'"))).scalar_one()


async def _fresh_parts(db, n, priced=False, skip=0):
    """n real catalog parts with an OEM of 8+ alnum chars and no supplier rows; optionally priced + one VISIBLE other offer."""
    rows = (await db.execute(sa.text("""SELECT pc.id::text, pc.oem_number FROM parts_catalog pc WHERE pc.is_active
        AND length(regexp_replace(upper(pc.oem_number),'[^A-Z0-9]','','g')) BETWEEN 9 AND 14 AND pc.oem_number ~ '[0-9]'
        AND NOT EXISTS (SELECT 1 FROM supplier_parts sp WHERE sp.part_id = pc.id) ORDER BY pc.id LIMIT :n OFFSET :skip"""), {"n": n, "skip": skip})).fetchall()
    out = []
    for pid, oem in rows:
        if priced:
            other = (await db.execute(sa.text("SELECT id::text FROM suppliers WHERE is_active AND name NOT IN ('AliExpress','Official Manufacturer Sites','Sandbox Supplier QA') ORDER BY name LIMIT 1"))).scalar_one()
            await db.execute(sa.text("UPDATE parts_catalog SET base_price = 145 WHERE id = CAST(:p AS uuid)"), {"p": pid})
            await db.execute(sa.text("""INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_ils, price_usd, is_available, availability, supplier_url)
                VALUES (gen_random_uuid(), CAST(:s AS uuid), CAST(:p AS uuid), :sku, 100, 33, TRUE, 'in_stock', 'https://x/y')"""), {"s": other, "p": pid, "sku": "DSC" + pid[:8]})
        else:
            await db.execute(sa.text("UPDATE parts_catalog SET base_price = NULL WHERE id = CAST(:p AS uuid)"), {"p": pid})
        out.append((pid, oem))
    return out


async def _cand(db, pid, oem, status="cited"):
    await db.execute(sa.text("INSERT INTO aliexpress_candidates (part_id, oem_norm, status) VALUES (CAST(:p AS uuid), :n, :s) ON CONFLICT (part_id) DO UPDATE SET status = :s"),
                     {"p": pid, "n": svc.AliExpressSupplier._norm_oem(oem), "s": status})


def _hit(oem, price=10.0, item="1005000000000009"):
    return PartResult(supplier="aliexpress", item_id=item, title=f"{oem} Oil Filter", price=price, currency="USD", shipping_cost=0.0, total_cost=price,
                      condition="New", seller="", seller_rating=None, item_url=f"https://www.aliexpress.com/item/{item}.html", image_url="https://ae/x.jpg",
                      location="CN", estimated_delivery_days=20, ships_to_israel=True, image_urls=["https://ae/x.jpg"])


# ---------------------------------------------------------------- query rotation
def test_discovery_query_rotation_is_deterministic_and_pages_after_a_full_cycle():
    brands = ["Kia", "Toyota", "Mazda"]
    combos = len(svc.DISCOVERY_TERMS) * len(brands)
    q0, p0 = svc._discovery_query(0, brands)
    assert q0 == "Kia oil filter" and p0 == 1
    assert svc._discovery_query(1, brands) == ("Toyota oil filter", 1)
    assert svc._discovery_query(combos, brands) == (q0, 2)                   # same query, next result page
    assert len({svc._discovery_query(i, brands) for i in range(combos)}) == combos


# ---------------------------------------------------------------- discovery -> candidate store
async def test_discovery_persists_only_catalog_parts_cited_in_real_titles(db, monkeypatch):
    parts = await _fresh_parts(db, 2)
    (p1, oem1), (p2, oem2) = parts
    listings = [{"itemId": "111", "title": f"Oil Filter {oem1} for Honda"},
                {"itemId": "222", "title": f"Brake pad {oem2} kit"},
                {"itemId": "333", "title": "Universal pad ZZ99887766 fits many"},        # cited number NOT in our catalog
                {"itemId": "444", "title": "Motorcycle brake pads 2007-2008 CBF"}]       # junk year range: rejected by the guard
    monkeypatch.setattr(svc.aliexpress, "text_search", AsyncMock(return_value=listings))
    report = {"errors": [], "candidates_added": 0, "discovery_queries": 0}
    await svc.discover_candidates(db, await _ali(db), {"unpriced": 10**6, "priced": 10**6}, 1, report)
    got = {r[0]: r[1] for r in (await db.execute(sa.text("SELECT part_id::text, status FROM aliexpress_candidates WHERE part_id = ANY(CAST(:ids AS uuid[]))"), {"ids": [p1, p2]})).fetchall()}
    assert got == {p1: "cited", p2: "cited"} and report["discovery_queries"] == 1 and report["candidates_added"] >= 2
    assert not (await db.execute(sa.text("SELECT 1 FROM aliexpress_candidates WHERE oem_norm IN ('ZZ99887766','20072008')"))).first()
    # idempotent: a second discovery pass adds nothing and does not duplicate
    report2 = {"errors": [], "candidates_added": 0, "discovery_queries": 0}
    await svc.discover_candidates(db, await _ali(db), {"unpriced": 10**6, "priced": 10**6}, 1, report2)
    assert report2["candidates_added"] == 0
    assert (await db.execute(sa.text("SELECT count(*) FROM aliexpress_candidates WHERE part_id = ANY(CAST(:ids AS uuid[]))"), {"ids": [p1, p2]})).scalar_one() == 2


async def test_discovery_stops_when_enough_pending_and_respects_query_budget(db, monkeypatch):
    ts = AsyncMock(return_value=[])
    monkeypatch.setattr(svc.aliexpress, "text_search", ts)
    report = {"errors": [], "candidates_added": 0, "discovery_queries": 0}
    await svc.discover_candidates(db, await _ali(db), {"unpriced": 10**6, "priced": 10**6}, 3, report)     # never satisfied: budget stops it
    assert ts.await_count == 3 and report["discovery_queries"] == 3
    ts2 = AsyncMock(return_value=[])
    monkeypatch.setattr(svc.aliexpress, "text_search", ts2)
    await svc.discover_candidates(db, await _ali(db), {"unpriced": 0, "priced": 0}, 50, {"errors": [], "candidates_added": 0, "discovery_queries": 0})
    assert ts2.await_count == 0                                                                            # nothing wanted => no calls


# ---------------------------------------------------------------- candidate-driven selection
async def test_selection_partitions_populations_and_applies_eligibility(db):
    ali = await _ali(db)
    unp = await _fresh_parts(db, 2)
    pri = await _fresh_parts(db, 2, priced=True, skip=2)
    for pid, oem in unp + pri: await _cand(db, pid, oem)
    # priced part whose ONLY offer has no URL (invisible in search) is NOT eligible
    hidden = (await _fresh_parts(db, 1, skip=4))[0]
    await db.execute(sa.text("UPDATE parts_catalog SET base_price = 145 WHERE id = CAST(:p AS uuid)"), {"p": hidden[0]})
    other = (await db.execute(sa.text("SELECT id::text FROM suppliers WHERE is_active AND name NOT IN ('AliExpress') ORDER BY name LIMIT 1"))).scalar_one()
    await db.execute(sa.text("""INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_ils, price_usd, is_available, availability, supplier_url)
        VALUES (gen_random_uuid(), CAST(:s AS uuid), CAST(:p AS uuid), 'HID1', 100, 33, TRUE, 'in_stock', NULL)"""), {"s": other, "p": hidden[0]})
    await _cand(db, *hidden)
    mine = {p for p, _ in unp + pri + [hidden]}
    got = [t for t in await svc._select_candidate_targets(db, ali, 10**6, 10**6) if str(t.id) in mine]
    by = {str(t.id): t.population for t in got}
    assert {by[p] for p, _ in unp} == {"unpriced"} and {by[p] for p, _ in pri} == {"priced"}
    assert hidden[0] not in by                                                     # priced but no VISIBLE offer: never additional-only


async def test_selection_excludes_matched_recent_no_match_and_fresh_aliexpress_checks(db):
    ali = await _ali(db)
    parts = await _fresh_parts(db, 4)
    (a, oa), (b, ob), (c, oc), (d, od) = parts
    await _cand(db, a, oa, "matched")
    await _cand(db, b, ob, "no_match"); await db.execute(sa.text("UPDATE aliexpress_candidates SET last_checked_at = NOW() WHERE part_id = CAST(:p AS uuid)"), {"p": b})
    await _cand(db, c, oc, "no_match"); await db.execute(sa.text("UPDATE aliexpress_candidates SET last_checked_at = NOW() - INTERVAL '31 days' WHERE part_id = CAST(:p AS uuid)"), {"p": c})
    await _cand(db, d, od, "cited")
    await db.execute(sa.text("""INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_ils, price_usd, is_available, availability, last_checked_at)
        VALUES (gen_random_uuid(), CAST(:a AS uuid), CAST(:p AS uuid), 'FRESH', 10, 3, TRUE, 'in_stock', NOW())"""), {"a": ali, "p": d})
    got = {str(t.id) for t in await svc._select_candidate_targets(db, ali, 10**6, 0)} & {a, b, c, d}
    assert got == {c}                     # matched, recently-no_match and fresh-AliExpress-check parts are skipped; an OLD no_match is retried


# ---------------------------------------------------------------- dynamic target allocation (2026-09-22)
# Root cause: `_select_targets` requested round(n*share) from EACH population unconditionally; when a
# population's REAL eligible pool was smaller than its request (live: unpriced held 1-9 while priced held
# 78-247), the unfulfilled request was silently dropped instead of handed to the other population — a run
# asked for 200 targets and got 185. `_allocate_candidate_targets` fixes this at the one point both callers
# (`_select_targets`) share.
def test_allocate_plentiful_pools_matches_the_configured_ideal_split():
    got = svc._allocate_candidate_targets({"unpriced": 1000, "priced": 1000}, 100, 0.5)
    assert got == {"unpriced": 50, "priced": 50}


def test_allocate_reallocates_unpriced_shortfall_to_priced_without_wasting_capacity():
    # exactly the shape measured live: unpriced pool nearly empty, priced pool deep
    got = svc._allocate_candidate_targets({"unpriced": 9, "priced": 247}, 200, 0.5)
    assert got["unpriced"] == 9                          # takes everything the small pool has to offer
    assert got["unpriced"] + got["priced"] == 200          # the other 191 slots go to priced — nothing wasted


def test_allocate_reallocates_priced_shortfall_to_unpriced_symmetrically():
    got = svc._allocate_candidate_targets({"unpriced": 300, "priced": 4}, 100, 0.5)
    assert got["priced"] == 4
    assert got["unpriced"] + got["priced"] == 100


def test_allocate_never_requests_more_than_the_real_pool_holds():
    got = svc._allocate_candidate_targets({"unpriced": 3, "priced": 5}, 200, 0.5)
    assert got == {"unpriced": 3, "priced": 5}             # both pools fit inside the budget: take everything, no more


def test_allocate_zero_budget_or_empty_pool_is_a_noop():
    assert svc._allocate_candidate_targets({"unpriced": 50, "priced": 50}, 0, 0.5) == {"unpriced": 0, "priced": 0}
    assert svc._allocate_candidate_targets({"unpriced": 0, "priced": 0}, 50, 0.5) == {"unpriced": 0, "priced": 0}


def test_allocate_honors_a_non_default_share_as_the_ceiling_not_a_guess():
    got = svc._allocate_candidate_targets({"unpriced": 1000, "priced": 1000}, 100, 0.8)
    assert got == {"unpriced": 20, "priced": 80}           # share is still the caller's business rule, just clamped+reallocated


async def test_select_targets_wires_real_pending_counts_into_the_allocator(db, monkeypatch):
    """`_select_targets` must consult the REAL pool (`_pending_candidates`) and hand its result straight to
    `_allocate_candidate_targets` — not recompute a blind split itself. Pending/selection mocked so the
    assertion is exactly on that wiring, independent of whatever real candidates already exist in this DB
    (production data would otherwise pollute a live-row integration test — see the skewed-pool unit tests
    above for the allocation arithmetic itself)."""
    ali = await _ali(db)
    monkeypatch.setattr(svc, "_pending_candidates", AsyncMock(return_value={"unpriced": 1, "priced": 999}))
    captured = {}
    async def fake_select(db_, ali_, n_unpriced, n_priced):
        captured["unpriced"], captured["priced"] = n_unpriced, n_priced
        return []
    monkeypatch.setattr(svc, "_select_candidate_targets", fake_select)
    await svc._select_targets(db, ali, 5)
    assert captured == {"unpriced": 1, "priced": 4}        # the one real unpriced candidate + the reallocated remainder


async def test_default_targeting_never_probes_blindly(db, monkeypatch):
    ali = await _ali(db)
    monkeypatch.setattr(svc, "_select_blind_targets", AsyncMock(side_effect=AssertionError("blind path must not run")))
    await svc._select_targets(db, ali, 20)                                         # BLIND_SHARE=0 => candidates only
    monkeypatch.setenv("ALIEXPRESS_BLIND_SHARE", "0.5")
    monkeypatch.setattr(svc, "_select_blind_targets", AsyncMock(return_value=[SimpleNamespace(id=uuid.uuid4(), oem_number="X", name="", manufacturer="", population="unpriced")]))
    got = await svc._select_targets(db, ali, 20)
    assert any(t.oem_number == "X" for t in got)                                   # only when explicitly enabled


# ---------------------------------------------------------------- end to end (mocked network)
async def test_sync_checks_only_candidates_and_records_outcomes(db, monkeypatch):
    ali = await _ali(db)
    hit_p, miss_p, err_p = (await _fresh_parts(db, 3))
    for pid, oem in (hit_p, miss_p, err_p): await _cand(db, pid, oem)
    mine = {hit_p[1]: "matched", miss_p[1]: "no_match", err_p[1]: "cited"}

    async def search(oem, limit=3, brand="", name="", **kw):
        if oem == hit_p[1]: return [_hit(oem)]
        if oem == err_p[1]: raise RuntimeError("transient")
        return []
    monkeypatch.setattr(svc.aliexpress, "search_by_oem", search)
    monkeypatch.setattr(svc, "discover_candidates", AsyncMock())                   # discovery is covered above
    monkeypatch.setattr(svc, "_select_targets", AsyncMock(return_value=[SimpleNamespace(id=uuid.UUID(p), oem_number=o, name="", manufacturer="", population="unpriced") for p, o in (hit_p, miss_p, err_p)]))
    rep = await svc.sync_aliexpress_prices(db, limit_per_run=3)
    st = {r[0]: r[1] for r in (await db.execute(sa.text("SELECT part_id::text, status FROM aliexpress_candidates WHERE part_id = ANY(CAST(:ids AS uuid[]))"), {"ids": [hit_p[0], miss_p[0], err_p[0]]})).fetchall()}
    assert st == {hit_p[0]: "matched", miss_p[0]: "no_match", err_p[0]: "cited"}   # a transient error is NEVER recorded as no_match
    # parts_not_found counts CONFIRMED negatives only (the old expectation of 2 folded the unanswered search into it —
    # the very conflation the 2026-09-26 remediation removes); the unanswered one is reported as searches_unconfirmed.
    assert rep["parts_checked"] == 3 and rep["parts_updated"] == 1 and rep["parts_not_found"] == 1 and rep["searches_unconfirmed"] == 1
    assert rep["targeting"] == "discovery" and rep["search_failure_kinds"] == {"error": 1}
    assert (await db.execute(sa.text("SELECT count(*) FROM supplier_parts WHERE part_id = CAST(:p AS uuid) AND supplier_id = CAST(:a AS uuid)"), {"p": hit_p[0], "a": ali})).scalar_one() == 1


# ---- transient failure vs confirmed negative, through the REAL sync (2026-09-26 remediation) --------------
# Failure chain being fixed: ds.text.search times out -> text_search swallows it into [] -> search_by_oem returns []
# -> the sync could not tell "no answer" from "answered: nothing" -> candidate marked no_match (30-day cache).
import httpx
from services.suppliers.aliexpress_supplier import AliExpressSearchUnconfirmed


def _only(monkeypatch, mine):
    """Real eligibility SQL (_select_candidate_targets), restricted to this test's own rows so production candidates
    that happen to exist in this database never leak into the run."""
    real = svc._select_candidate_targets
    async def sel(db_, ali_, limit):
        rows = await real(db_, ali_, 10**6, 10**6)
        return [t for t in rows if str(t.id) in mine]
    monkeypatch.setattr(svc, "_select_targets", sel)
    monkeypatch.setattr(svc, "discover_candidates", AsyncMock())


async def _cand_state(db, pid):
    r = (await db.execute(sa.text("SELECT status, attempts, last_checked_at IS NOT NULL FROM aliexpress_candidates WHERE part_id = CAST(:p AS uuid)"), {"p": pid})).fetchone()
    return tuple(r)


async def _offers(db, pid, ali):
    n = (await db.execute(sa.text("SELECT count(*) FROM supplier_parts WHERE part_id = CAST(:p AS uuid) AND supplier_id = CAST(:a AS uuid)"), {"p": pid, "a": ali})).scalar_one()
    h = (await db.execute(sa.text("SELECT count(*) FROM price_history ph JOIN supplier_parts s ON s.id = ph.supplier_part_id WHERE s.part_id = CAST(:p AS uuid) AND s.supplier_id = CAST(:a AS uuid)"), {"p": pid, "a": ali})).scalar_one()
    return n, h


async def test_confirmed_empty_answer_is_recorded_as_no_match_with_the_negative_cache(db, monkeypatch):
    ali = await _ali(db)
    (pid, oem), = await _fresh_parts(db, 1); await _cand(db, pid, oem)
    _only(monkeypatch, {pid})
    monkeypatch.setattr(svc.aliexpress, "search_by_oem", AsyncMock(return_value=[]))          # AliExpress ANSWERED: nothing
    rep = await svc.sync_aliexpress_prices(db, limit_per_run=5)
    assert await _cand_state(db, pid) == ("no_match", 1, True)                                 # cached (last_checked_at set)
    assert rep["parts_not_found"] == 1 and rep["searches_unconfirmed"] == 0 and rep["errors"] == []
    assert pid not in {str(t.id) for t in await svc._select_candidate_targets(db, ali, 10**6, 10**6)}   # 30-day cache still holds


async def test_transport_failure_is_not_a_no_match_and_leaves_the_target_eligible(db, monkeypatch):
    ali = await _ali(db)
    (pid, oem), = await _fresh_parts(db, 1); await _cand(db, pid, oem)
    _only(monkeypatch, {pid})
    monkeypatch.setattr(svc.aliexpress, "search_by_oem", AsyncMock(side_effect=AliExpressSearchUnconfirmed("transport", "ReadTimeout")))
    rep = await svc.sync_aliexpress_prices(db, limit_per_run=5)
    assert await _cand_state(db, pid) == ("cited", 0, False)                                   # untouched: no status, no attempt, no cache stamp
    assert rep["parts_not_found"] == 0 and rep["searches_unconfirmed"] == 1 and rep["search_failure_kinds"] == {"transport": 1}
    assert any("transport:ReadTimeout" in e for e in rep["errors"]) and not rep["search_aborted"]
    assert await _offers(db, pid, ali) == (0, 0)
    assert pid in {str(t.id) for t in await svc._select_candidate_targets(db, ali, 10**6, 10**6)}    # eligible for a subsequent retry


async def test_failed_search_is_retried_next_run_and_the_offer_is_written_exactly_once(db, monkeypatch):
    ali = await _ali(db)
    (pid, oem), = await _fresh_parts(db, 1); await _cand(db, pid, oem)
    _only(monkeypatch, {pid})
    outcomes = [AliExpressSearchUnconfirmed("transport", "ReadTimeout"), [_hit(oem)]]
    async def search(o, limit=3, brand="", name="", **kw):
        r = outcomes.pop(0)
        if isinstance(r, Exception): raise r
        return r
    monkeypatch.setattr(svc.aliexpress, "search_by_oem", search)
    rep1 = await svc.sync_aliexpress_prices(db, limit_per_run=5)                                # run 1: unanswered
    assert rep1["searches_unconfirmed"] == 1 and await _cand_state(db, pid) == ("cited", 0, False) and await _offers(db, pid, ali) == (0, 0)
    rep2 = await svc.sync_aliexpress_prices(db, limit_per_run=5)                                # run 2: the retry succeeds
    assert rep2["parts_updated"] == 1 and rep2["searches_unconfirmed"] == 0 and rep2["errors"] == []
    assert (await _cand_state(db, pid))[0] == "matched" and await _offers(db, pid, ali) == (1, 1)
    rep3 = await svc.sync_aliexpress_prices(db, limit_per_run=5)                                # run 3: nothing left to do
    assert rep3["parts_checked"] == 0 and await _offers(db, pid, ali) == (1, 1)                # no duplicate offer / history


async def test_persistent_unconfirmed_failures_abort_safely_with_a_bounded_number_of_calls(db, monkeypatch):
    ali = await _ali(db)
    parts = await _fresh_parts(db, svc._MAX_CONSECUTIVE_UNCONFIRMED + 3)
    for pid, oem in parts: await _cand(db, pid, oem)
    _only(monkeypatch, {p for p, _ in parts})
    spy = AsyncMock(side_effect=AliExpressSearchUnconfirmed("transport", "ReadTimeout"))
    monkeypatch.setattr(svc.aliexpress, "search_by_oem", spy)
    rep = await svc.sync_aliexpress_prices(db, limit_per_run=100)
    assert spy.await_count == svc._MAX_CONSECUTIVE_UNCONFIRMED                                   # stops — no further calls
    assert rep["search_aborted"] and rep["parts_checked"] == svc._MAX_CONSECUTIVE_UNCONFIRMED
    assert any("search aborted" in e and "consecutive unconfirmed" in e and "left untouched" in e for e in rep["errors"])
    for pid, _ in parts:
        assert await _cand_state(db, pid) == ("cited", 0, False)                                 # nothing recorded, all still eligible
        assert await _offers(db, pid, ali) == (0, 0)


async def test_auth_failure_aborts_at_once_and_is_reported_accurately_not_as_no_match(db, monkeypatch):
    parts = await _fresh_parts(db, 4)
    for pid, oem in parts: await _cand(db, pid, oem)
    _only(monkeypatch, {p for p, _ in parts})
    spy = AsyncMock(side_effect=AliExpressSearchUnconfirmed("auth", "IllegalAccessToken: The specified access token is invalid or expired"))
    monkeypatch.setattr(svc.aliexpress, "search_by_oem", spy)
    rep = await svc.sync_aliexpress_prices(db, limit_per_run=10)
    assert spy.await_count == 1 and rep["search_aborted"] and rep["search_failure_kinds"] == {"auth": 1}
    assert rep["parts_not_found"] == 0 and any(":auth:IllegalAccessToken" in e for e in rep["errors"]) and any("auth failure" in e for e in rep["errors"])
    for pid, _ in parts: assert (await _cand_state(db, pid))[0] == "cited"


async def test_rate_limit_is_never_a_negative_and_a_real_answer_resets_the_breaker(db, monkeypatch):
    n = svc._MAX_CONSECUTIVE_UNCONFIRMED - 1
    parts = await _fresh_parts(db, 2 * n + 1)
    for pid, oem in parts: await _cand(db, pid, oem)
    _only(monkeypatch, {p for p, _ in parts})
    script = [AliExpressSearchUnconfirmed("rate_limit", "ApiCallLimit: ban 1s")] * n + [[]] + [AliExpressSearchUnconfirmed("rate_limit", "ApiCallLimit: ban 1s")] * n
    async def search(o, limit=3, brand="", name="", **kw):
        r = script.pop(0)
        if isinstance(r, Exception): raise r
        return r
    monkeypatch.setattr(svc.aliexpress, "search_by_oem", search)
    rep = await svc.sync_aliexpress_prices(db, limit_per_run=100)
    assert not rep["search_aborted"] and rep["parts_checked"] == 2 * n + 1                     # n-1 in a row, answer, n-1 in a row: never trips
    assert rep["searches_unconfirmed"] == 2 * n and rep["search_failure_kinds"] == {"rate_limit": 2 * n} and rep["parts_not_found"] == 1
    states = [(await _cand_state(db, pid))[0] for pid, _ in parts]
    assert sorted(states) == sorted(["no_match"] + ["cited"] * (2 * n))                          # only the ANSWERED one is a negative


async def test_sync_asks_the_supplier_for_unconfirmed_signalling(db, monkeypatch):
    (pid, oem), = await _fresh_parts(db, 1); await _cand(db, pid, oem)
    _only(monkeypatch, {pid})
    seen = {}
    async def search(o, limit=3, brand="", name="", **kw):
        seen.update(kw); return []
    monkeypatch.setattr(svc.aliexpress, "search_by_oem", search)
    await svc.sync_aliexpress_prices(db, limit_per_run=5)
    assert seen == {"raise_on_unconfirmed": True}          # without this flag the whole fix is silently bypassed


# End to end with the REAL supplier class: only the HTTP transport is scripted.
async def test_e2e_timeouts_never_become_a_no_match_and_are_bounded(db, monkeypatch):
    ali = await _ali(db)
    (pid, oem), = await _fresh_parts(db, 1); await _cand(db, pid, oem)
    _only(monkeypatch, {pid})
    posts = []
    async def post(self, url, data=None, **kw):
        posts.append(1); raise httpx.ReadTimeout("")
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    before = svc.aliexpress.api_calls
    rep = await svc.sync_aliexpress_prices(db, limit_per_run=5)
    assert len(posts) == 2 and svc.aliexpress.api_calls - before == 2                            # 1 attempt + 1 bounded retry
    assert await _cand_state(db, pid) == ("cited", 0, False) and rep["parts_not_found"] == 0
    assert rep["search_failure_kinds"] == {"transport": 1} and await _offers(db, pid, ali) == (0, 0)


async def test_e2e_timeout_then_success_within_one_run_writes_the_offer(db, monkeypatch):
    ali = await _ali(db)
    (pid, oem), = await _fresh_parts(db, 1); await _cand(db, pid, oem)
    _only(monkeypatch, {pid})
    listing = {"itemId": "1005000000000009", "title": f"{oem} Oil Filter", "targetSalePrice": "10.00", "salePrice": "10.00",
               "originalPrice": "14.00", "itemMainPic": "//ae01.alicdn.com/kf/abc.jpg"}
    ok = {"aliexpress_ds_text_search_response": {"data": {"products": {"selection_search_product": [listing]}}}}
    seq = [httpx.ReadTimeout(""), ok]
    class _Resp:
        status_code = 200
        def __init__(self, d): self._d = d
        def json(self): return self._d
        def raise_for_status(self): pass
    async def post(self, url, data=None, **kw):
        r = seq.pop(0)
        if isinstance(r, Exception): raise r
        return _Resp(r)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    rep = await svc.sync_aliexpress_prices(db, limit_per_run=5)
    assert rep["parts_updated"] == 1 and rep["searches_unconfirmed"] == 0 and rep["errors"] == []      # the retry recovered it
    assert (await _cand_state(db, pid))[0] == "matched" and await _offers(db, pid, ali) == (1, 1)


async def test_e2e_flow_control_response_is_unconfirmed_counted_and_not_retried(db, monkeypatch):
    (pid, oem), = await _fresh_parts(db, 1); await _cand(db, pid, oem)
    _only(monkeypatch, {pid})
    posts = []
    class _Resp:
        status_code = 200
        def json(self): return {"error_response": {"code": "ApiCallLimit", "msg": "Api access frequency exceeds the limit. this ban will last 1 seconds"}}
        def raise_for_status(self): pass
    async def post(self, url, data=None, **kw):
        posts.append(1); return _Resp()
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    rep = await svc.sync_aliexpress_prices(db, limit_per_run=5)
    assert len(posts) == 1 and rep["api_call_limit_hits"] == 1                                   # not hammered, still observable
    assert rep["search_failure_kinds"] == {"rate_limit": 1} and rep["parts_not_found"] == 0
    assert await _cand_state(db, pid) == ("cited", 0, False)
