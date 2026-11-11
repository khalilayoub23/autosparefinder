"""Meilisearch <-> catalog synchronisation (2026-11-11): deletion queue, incremental propagation, reconciliation.
Runs the REAL meili_sync.run() and meili_reconcile against the sandbox Postgres + sandbox Meilisearch with a throwaway
index per test (isolated_db_guard: RUN_DB_MUTATION_TESTS=1, ENVIRONMENT=sandbox, non-production host)."""
import json
import os
import uuid

import pytest

import isolated_db_guard as guard

_REFUSAL = guard.static_refusal()
pytestmark = pytest.mark.skipif(_REFUSAL is not None, reason=f"isolated DB only: {_REFUSAL}")

import asyncpg  # noqa: E402

import meili_reconcile as mr  # noqa: E402
import meili_sync as ms  # noqa: E402


def _dsn():
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


class Env:
    pass


@pytest.fixture
async def env(tmp_path, monkeypatch):
    e = Env()
    e.tag = uuid.uuid4().hex[:8]
    e.index = f"parts_t_{e.tag}"
    e.http = mr.MeiliHttp(index=e.index)
    e.http._req("/indexes", "POST", {"uid": e.index, "primaryKey": "id"})
    t = e.http.wait(e.http._req(f"/indexes/{e.index}/settings", "PATCH", {"filterableAttributes": ["id", "sku", "manufacturer"]})["taskUid"])
    assert t["status"] == "succeeded", t
    monkeypatch.setattr(ms, "INDEX_NAME", e.index)
    monkeypatch.setattr(ms, "CHECKPOINT_FILE", str(tmp_path / "ckpt.json"))
    monkeypatch.setattr(mr, "STATE_DIR", tmp_path / "rec")
    e.state = tmp_path / "rec"
    e.c = await asyncpg.connect(_dsn())
    brand = await e.c.fetchval("SELECT id FROM car_brands LIMIT 1")
    if brand is None:
        brand = await e.c.fetchval("INSERT INTO car_brands(id,name) VALUES(gen_random_uuid(),$1) RETURNING id", f"mr-{e.tag}")
    e.brand, e.parts = brand, []

    async def part(oem=None, ptype="oem", active=True, name="mr part"):
        pid = await e.c.fetchval(
            "INSERT INTO parts_catalog(id,sku,oem_number,name,manufacturer,manufacturer_id,part_type,is_active) "
            "VALUES(gen_random_uuid(),$1,$2,$3,'MR',$4,$5,$6) RETURNING id",
            f"MR-{e.tag}-{len(e.parts)}", oem or f"OEM{e.tag}{len(e.parts)}", name, brand, ptype, active)
        e.parts.append(pid)
        return str(pid)

    async def sync():
        await ms.run(rebuild_index=False)

    e.part, e.sync = part, sync
    e.ids_in_index = lambda: {d["id"] for p in range(0, 1) for d in e.http._req(f"/indexes/{e.index}/documents?fields=id&limit=100000")["results"]}
    yield e
    try:
        e.http._req(f"/indexes/{e.index}", "DELETE")
    except Exception:
        pass
    await e.c.execute("DELETE FROM meili_pending_deletes WHERE part_id = ANY($1::uuid[])", e.parts) \
        if await mr.tombstones_installed(e.c) else None
    await e.c.execute("DELETE FROM parts_catalog WHERE id = ANY($1::uuid[])", e.parts)
    await e.c.close()
    from BACKEND_DATABASE_MODELS import engine
    await engine.dispose()


async def test_tombstone_install_is_idempotent(env):
    await mr.ensure_tombstones(env.c)
    assert await mr.tombstones_installed(env.c)
    assert await mr.ensure_tombstones(env.c) is False            # second call: no DDL at all


async def test_insert_update_then_repeated_sync_is_stable(env):
    a, b = await env.part(), await env.part()
    await env.sync()
    assert {a, b} <= env.ids_in_index()
    await env.c.execute("UPDATE parts_catalog SET name='renamed', updated_at=now() WHERE id=$1::uuid", a)
    await env.sync()
    doc = env.http.docs([a])[0]
    assert doc["name"] == "renamed"
    n = len(env.ids_in_index())
    await env.sync(); await env.sync()                            # repeated sync: nothing changes
    assert len(env.ids_in_index()) == n
    assert await env.c.fetchval("SELECT count(*) FROM meili_pending_deletes WHERE part_id = ANY($1::uuid[])", env.parts) == 0


async def test_deactivation_propagates_in_the_next_incremental_sync(env):
    a, b = await env.part(), await env.part()
    await env.sync()
    await env.c.execute("UPDATE parts_catalog SET is_active=false WHERE id=$1::uuid", b)      # NO updated_at bump
    assert await env.c.fetchval("SELECT reason FROM meili_pending_deletes WHERE part_id=$1::uuid", uuid.UUID(b)) == "deactivated"
    await env.sync()
    ids = env.ids_in_index()
    assert a in ids and b not in ids
    assert await env.c.fetchval("SELECT count(*) FROM meili_pending_deletes WHERE part_id=$1::uuid", uuid.UUID(b)) == 0


async def test_hard_delete_and_merge_propagate_and_keep_the_survivor(env):
    keep, gone = await env.part(oem="SAMEOEM"), await env.part(oem="SAMEOEM")
    variant = await env.part(oem="SAMEOEM", ptype="aftermarket")                              # a legitimate variant stays
    await env.sync()
    assert {keep, gone, variant} <= env.ids_in_index()
    # a merge = the duplicate row is removed (the code that merges deletes or deactivates it)
    await env.c.execute("DELETE FROM parts_catalog WHERE id=$1::uuid", gone)
    assert await env.c.fetchval("SELECT reason FROM meili_pending_deletes WHERE part_id=$1::uuid", uuid.UUID(gone)) == "deleted"
    await env.sync()
    ids = env.ids_in_index()
    assert gone not in ids and keep in ids and variant in ids


async def test_failed_deletion_is_not_a_successful_sync_and_is_retried(env, monkeypatch):
    a, b = await env.part(), await env.part()
    await env.sync()
    await env.c.execute("DELETE FROM parts_catalog WHERE id=$1::uuid", b)
    real = mr.MeiliHttp.delete_batch
    monkeypatch.setattr(mr.MeiliHttp, "delete_batch", lambda self, ids: (_ for _ in ()).throw(RuntimeError("meili down")))
    with pytest.raises(RuntimeError, match="deletion queue not fully applied"):
        await env.sync()
    assert b in env.ids_in_index()                                                     # still there...
    assert await env.c.fetchval("SELECT count(*) FROM meili_pending_deletes WHERE part_id=$1::uuid", uuid.UUID(b)) == 1  # ...and still queued
    monkeypatch.setattr(mr.MeiliHttp, "delete_batch", real)
    await env.sync()                                                                  # retry succeeds
    assert b not in env.ids_in_index() and a in env.ids_in_index()


async def test_reactivated_part_is_dequeued_not_deleted(env):
    a = await env.part()
    await env.sync()
    await env.c.execute("UPDATE parts_catalog SET is_active=false WHERE id=$1::uuid", a)
    await env.c.execute("UPDATE parts_catalog SET is_active=true WHERE id=$1::uuid", a)
    out = await mr.drain_tombstones(env.c, env.http)
    assert out["reactivated"] >= 1 and a in env.ids_in_index()


async def test_reconcile_removes_stale_documents_backs_them_up_and_is_idempotent(env):
    active, variant = await env.part(oem="V1"), await env.part(oem="V1", ptype="aftermarket")
    inactive = await env.part(active=False)
    await env.sync()
    ghost = str(uuid.uuid4())                                                          # id that exists nowhere (merged away)
    upper = str(uuid.uuid4()).upper()                                                  # non-canonical id
    env.http.wait(env.http.add_documents([{"id": ghost, "sku": "GHOST", "name": "g"}, {"id": inactive, "sku": "X", "name": "i"},
                                          {"id": upper, "sku": "UP", "name": "u"}]))
    rec = await mr.reconcile(env.c, env.http, run_id="t1", state_dir=env.state)
    assert rec["status"] == "completed", rec
    assert rec["stale_found"] == 3 and rec["stale_deleted"] == 3 and rec["stale_failed"] == 0
    assert rec["stale_by_reason"] == {"absent": 1, "inactive": 1, "malformed_id": 1}
    ids = env.ids_in_index()
    assert ghost not in ids and inactive not in ids and upper not in ids
    assert active in ids and variant in ids                                            # active parts + variants untouched
    man = [json.loads(l) for l in open(rec["manifest"], encoding="utf-8")]
    assert {m["id"] for m in man} == {ghost, inactive, upper} and all(m["doc"] for m in man)   # backup has the documents
    again = await mr.reconcile(env.c, env.http, run_id="t2", state_dir=env.state)
    assert again["status"] == "completed" and again["stale_found"] == 0 and again["missing_repaired"] == 0
    assert again["manifest"] is None


async def test_reconcile_repairs_missing_documents(env):
    a, b = await env.part(), await env.part()
    await env.sync()
    env.http.wait(env.http.delete_batch([a]))
    rec = await mr.reconcile(env.c, env.http, run_id="m1", state_dir=env.state)
    assert rec["missing_found"] >= 1 and rec["missing_repaired"] >= 1 and rec["status"] == "completed"
    assert a in env.ids_in_index() and b in env.ids_in_index()


async def test_dry_run_changes_nothing(env):
    ghost = str(uuid.uuid4())
    env.http.wait(env.http.add_documents([{"id": ghost, "sku": "G", "name": "g"}]))
    rec = await mr.reconcile(env.c, env.http, apply=False, run_id="d1", state_dir=env.state)
    assert rec["stale_found"] >= 1 and rec["stale_deleted"] == 0 and ghost in env.ids_in_index() and rec["manifest"] is None


async def test_safety_threshold_aborts_without_deleting(env, monkeypatch):
    ghosts = [str(uuid.uuid4()) for _ in range(6)]
    env.http.wait(env.http.add_documents([{"id": g, "sku": "G", "name": "g"} for g in ghosts]))
    monkeypatch.setattr(mr, "MAX_STALE", 2); monkeypatch.setattr(mr, "MAX_STALE_FRACTION", 0.0)
    rec = await mr.reconcile(env.c, env.http, run_id="s1", state_dir=env.state)
    assert rec["status"] == "aborted_threshold" and rec["stale_deleted"] == 0 and set(ghosts) <= env.ids_in_index()
    rec = await mr.reconcile(env.c, env.http, allow_large=True, run_id="s2", state_dir=env.state)
    assert rec["status"] == "completed" and not (set(ghosts) & env.ids_in_index())


async def test_failed_index_deletion_is_never_reported_as_completed(env, monkeypatch):
    ghost = str(uuid.uuid4())
    env.http.wait(env.http.add_documents([{"id": ghost, "sku": "G", "name": "g"}]))
    monkeypatch.setattr(mr.MeiliHttp, "delete_batch", lambda self, ids: (_ for _ in ()).throw(RuntimeError("boom")))
    rec = await mr.reconcile(env.c, env.http, run_id="f1", state_dir=env.state)
    assert rec["status"] == "partial" and rec["stale_failed"] >= 1 and rec["failed_tasks"]
    assert ghost in env.ids_in_index()
    monkeypatch.undo()
    monkeypatch.setattr(mr, "STATE_DIR", env.state)
    rec = await mr.reconcile(env.c, env.http, run_id="f2", state_dir=env.state)               # retry after recovery
    assert rec["status"] == "completed" and ghost not in env.ids_in_index()


async def test_large_index_is_processed_in_bounded_chunks(env, monkeypatch):
    # 25,000 stale documents against PAGE=20,000 / small probe + delete batches: nothing is held whole in memory
    docs = [{"id": str(uuid.uuid4()), "sku": f"S{i}", "name": "n"} for i in range(25000)]
    for i in range(0, len(docs), 5000):
        assert env.http.wait(env.http.add_documents(docs[i:i + 5000]))["status"] == "succeeded"
    pages = []
    real = mr.MeiliHttp.page_ids
    monkeypatch.setattr(mr.MeiliHttp, "page_ids", lambda self, o, l: (pages.append(len(real(self, o, l))) or real(self, o, l)))
    monkeypatch.setattr(mr, "PROBE", 2000); monkeypatch.setattr(mr, "DELETE_BATCH", 7000)
    rec = await mr.reconcile(env.c, env.http, allow_large=True, run_id="L1", state_dir=env.state)
    assert rec["status"] == "completed" and rec["stale_deleted"] >= 25000 and rec["scanned_index"] >= 25000
    assert max(pages) <= 20000 and len([p for p in pages if p]) >= 2          # paged, never one giant read
    assert env.http.count() < 100


async def test_restore_only_reinstates_active_parts(env):
    a = await env.part()
    gone = str(uuid.uuid4())
    env.http.wait(env.http.add_documents([{"id": gone, "sku": "G", "name": "g"}]))
    await env.sync()
    rec = await mr.reconcile(env.c, env.http, run_id="r1", state_dir=env.state)
    assert gone not in env.ids_in_index()
    out = await mr.restore(env.c, env.http, rec["manifest"])
    assert out["restored"] == 0 and out["skipped_not_active"] == 1 and gone not in env.ids_in_index()


async def test_status_file_exposes_the_observability_fields(env):
    await env.part()
    await env.sync()
    rec = await mr.reconcile(env.c, env.http, run_id="o1", state_dir=env.state)
    st = json.loads((env.state / "status.json").read_text())
    for k in ("db_active", "index_before", "index_after", "stale_found", "missing_found", "duplicate_ids", "failed_tasks",
              "duration_s", "status", "last_success_at"):
        assert k in st, k
    assert st["last_success_at"] and st["status"] == "completed" and rec["duplicate_ids"] == 0
