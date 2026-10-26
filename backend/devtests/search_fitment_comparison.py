"""
devtests/search_fitment_comparison.py — before/after vehicle-search comparison (FIXES_TRACKER #69 §15-16).
READ-ONLY against the database. search_parts' own response cache is PATCHED OUT for every run:
_store_cached_search_response writes through to PRODUCTION Redis (setex, 120 s) and
_get_cached_search_response reads it. Before 2026-10-20 01:50 UTC this harness did not patch them,
so its results were written to the live cache (FIXES_TRACKER #69 §16). redis=None only disables the
Meili-id and external-supplier caches.

Both sides run in-process through the SAME search_parts code path, without the Redis search cache:
  baseline  = old model matching (word-prefix superset) + old correlated EXISTS (pre-resolution off)
              + the deployed model input (ingest normalizer, erases numeric models)
  candidate = vehicle_model_match rule + fitting-id pre-resolution + customer_vehicle_model input
Each case runs twice per side (run 1 = cold-ish, run 2 = warm PostgreSQL buffers); both are reported.
Every case result is written to the JSONL file immediately. Each search runs under the search's own
statement limit (SEARCH_STATEMENT_TIMEOUT_MS) plus an overall per-case deadline.

Run: docker exec -w /app autospare_backend python3 devtests/search_fitment_comparison.py --out /tmp/cmp.jsonl --revision <git-rev>
"""
import argparse
import asyncio
import json
import sys
import time

sys.path.insert(0, "/app")

from sqlalchemy import text  # noqa: E402

CASES = [("Toyota", "Corolla", 2021, "brakes"), ("Toyota", "Corolla", 2021, None),
         ("Land Rover", "Range Rover Evoque", 2018, None), ("Land Rover", "Range Rover", 2015, None),
         ("Toyota", "Land Cruiser Prado", 2015, None), ("Toyota", "Corolla E11", 2001, None),
         ("Kia", "Sportage", 2019, "brakes"), ("Mazda", "3", 2018, None), ("Honda", "Civic", 2017, None),
         # fitting-id set sizes 40,700 / 23,052 / 6,060 / 3,561 / 393 (cap equivalence, #69 §16)
         ("Toyota", "Hilux", 2016, None), ("Hyundai", "i30", 2015, None), ("Peugeot", "3008", 2019, None),
         ("Ford", "Focus", 2012, None), ("Volkswagen", "Golf", 2015, None)]


REFERENCE_TIMEOUT_MS = 120000
ISOLATION = {"read_only_transactions": 0, "redis_blocked_calls": 0}


def isolate_process():
    """Make this measurement process unable to change production state (FIXES_TRACKER #69 §16):
    every database transaction starts READ ONLY (a write fails with an error, never lands), and
    Redis is unreachable (get_redis raises), so no cache can be read or written. Call once, first."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    import BACKEND_AUTH_SECURITY as auth

    @event.listens_for(Session, "after_begin")
    def _read_only(session, transaction, connection):      # noqa: ARG001
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        ISOLATION["read_only_transactions"] += 1

    async def _no_redis(*_a, **_k):
        ISOLATION["redis_blocked_calls"] += 1
        raise RuntimeError("redis blocked in the measurement harness")
    auth.get_redis = _no_redis
    import routes.parts as parts
    if hasattr(parts, "get_redis"):
        parts.get_redis = _no_redis


def _ids(d):
    return [str((r.get("part") or {}).get("id")) for k in ("original_options", "oem_options", "aftermarket_options")
            for r in (d.get(k) or []) if (r.get("part") or {}).get("id")]


def _chain(e):
    out, x = [], e
    while x is not None and len(out) < 4:
        out.append(f"{type(x).__name__}: {str(x)[:160]}")
        x = x.__cause__ or x.__context__
    return " <- ".join(out)


async def _qualifying_models(session_factory, part_ids, make, year):
    if not part_ids:
        return {}
    async with session_factory() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        await db.execute(text("SET LOCAL statement_timeout = '30s'"))
        rows = (await db.execute(text(
            "SELECT part_id::text, array_agg(DISTINCT lower(trim(model))) FROM part_vehicle_fitment "
            "WHERE part_id = ANY(CAST(:p AS uuid[])) AND lower(trim(manufacturer)) LIKE '%' || lower(:mk) || '%' "
            "AND year_from <= :y AND COALESCE(year_to, year_from) >= :y GROUP BY 1"),
            {"p": part_ids, "mk": make, "y": year})).all()
    return {r[0]: r[1] for r in rows}


async def run_side(side, case, deadline_s, attempts=2, fit_id_cap=None):
    import routes.parts as parts
    import vehicle_model_match as vm
    from BACKEND_DATABASE_MODELS import async_session_factory
    mk, md, yr, cat = case
    orig_allowed, orig_cap = vm.allowed_fitment_models, parts.FIT_ID_RESOLVE_CAP
    orig_stmt_ms = parts.SEARCH_STATEMENT_TIMEOUT_MS
    orig_get, orig_store = parts._get_cached_search_response, parts._store_cached_search_response

    async def _no_cache_get(_key):
        return None

    async def _no_cache_store(_key, _payload):
        return None
    parts._get_cached_search_response, parts._store_cached_search_response = _no_cache_get, _no_cache_store
    if side == "baseline":
        async def _old_superset(db, makes, models):         # what the word-prefix rule matched
            terms, fwd = vm.candidate_terms([m for m in models if str(m or "").strip()])
            mk_sql = " OR ".join(f"LOWER(TRIM(manufacturer)) LIKE '%' || :m{i} || '%'" for i in range(len(makes)))
            if not fwd:                                     # deployed: no model terms -> `_model_sql = TRUE`
                return sorted(set((await db.execute(text(
                    f"SELECT DISTINCT LOWER(TRIM(model)) FROM part_vehicle_fitment WHERE ({mk_sql}) AND model IS NOT NULL"),
                    {f"m{i}": m.lower() for i, m in enumerate(makes)})).scalars().all()))
            rows = (await db.execute(text(
                "SELECT DISTINCT LOWER(TRIM(model)) FROM part_vehicle_fitment WHERE ("
                + " OR ".join(f"LOWER(TRIM(manufacturer)) LIKE '%' || :m{i} || '%'" for i in range(len(makes)))
                + ") AND (LOWER(TRIM(model)) = ANY(:t) OR " + " OR ".join(f"LOWER(TRIM(model)) LIKE :f{i} || ' %'" for i in range(len(fwd))) + ")"),
                {**{f"m{i}": m.lower() for i, m in enumerate(makes)}, "t": terms,
                 **{f"f{i}": f for i, f in enumerate(fwd)}})).scalars().all()
            return sorted(set(rows))
        vm.allowed_fitment_models = _old_superset
        orig_cvm = parts.customer_vehicle_model           # deployed input: the ingest normalizer

        def _old_model(mfr, model):
            from manufacturer_normalization import canonicalize_vehicle_model_for_manufacturer as _c
            from manufacturer_normalization import normalize_vehicle_model_name as _n
            return _c(mfr, model) or _n(model)
        parts.customer_vehicle_model = _old_model
        parts.FIT_ID_RESOLVE_CAP = 0                      # always keep the old correlated EXISTS
    elif side == "reference_in":
        # Second independent form: the same predicate as an UNCORRELATED IN-subquery, so the fitting
        # set is built inside PostgreSQL (checks the Python id transfer and the cap logic).
        parts.FIT_ID_RESOLVE_CAP = 0
        parts.SEARCH_STATEMENT_TIMEOUT_MS = REFERENCE_TIMEOUT_MS
        orig_strict = parts._build_strict_vehicle_match_clause

        async def _as_in(*a, **k):
            clause = await orig_strict(*a, **k)
            head = "EXISTS ( SELECT 1 FROM part_vehicle_fitment pvf WHERE pvf.part_id = pc.id   AND "
            if clause and clause.startswith(head):
                return "pc.id IN (SELECT pvf.part_id FROM part_vehicle_fitment pvf WHERE " + clause[len(head):]
            raise RuntimeError("reference_in: unexpected strict clause shape: " + str(clause)[:120])
        parts._build_strict_vehicle_match_clause = _as_in
    elif side == "reference":
        # Same rule as the candidate, but the fitting-id pre-resolution OFF (correlated EXISTS)
        # and a longer statement limit: an independent check that the id form returns the same.
        parts.FIT_ID_RESOLVE_CAP = 0
        parts.SEARCH_STATEMENT_TIMEOUT_MS = REFERENCE_TIMEOUT_MS
    elif fit_id_cap is not None:
        parts.FIT_ID_RESOLVE_CAP = fit_id_cap               # candidate variant under test
    vm._cache.clear()
    runs = []
    from sqlalchemy.ext.asyncio import AsyncSession
    orig_exec = AsyncSession.execute
    db_time = {"s": 0.0, "n": 0, "q": []}

    async def _timed(self, *a, **k):                     # database time = time spent awaiting queries
        t = time.monotonic()
        err = None
        try:
            return await orig_exec(self, *a, **k)
        except BaseException as e:
            err = type(e).__name__
            raise
        finally:
            dt = time.monotonic() - t
            db_time["s"] += dt
            db_time["n"] += 1
            sql = " ".join(str(a[0]).split())[:220] if a else "?"
            db_time["q"].append((round(dt, 2), sql, err))
    AsyncSession.execute = _timed
    try:
        for attempt in range(1, attempts + 1):
            t0 = time.monotonic()
            db_time.update(s=0.0, n=0, q=[])
            try:
                async with async_session_factory() as db:
                    res = await asyncio.wait_for(parts.search_parts(
                        query="", vehicle_id=None, category=cat, per_type=24, sort_by="price_ils",
                        vehicle_manufacturer=mk, vehicle_model=md, vehicle_submodel=None,
                        vehicle_year=yr, db=db, request=None, redis=None), timeout=deadline_s)
                wall = time.monotonic() - t0
                runs.append({"attempt": attempt, "seconds": round(wall, 2), "db_query_seconds_sum": round(db_time["s"], 2),
                             "db_queries": db_time["n"], "ids": _ids(res),
                             "slowest_queries": sorted(db_time["q"], key=lambda x: -x[0])[:6]})
            except Exception as e:                               # timeout or error, never masked
                runs.append({"attempt": attempt, "seconds": round(time.monotonic() - t0, 2),
                             "db_query_seconds_sum": round(db_time["s"], 2), "db_queries": db_time["n"],
                             "slowest_queries": sorted(db_time["q"], key=lambda x: -x[0])[:6],
                             "error": _chain(e), "complete": False})
    finally:
        AsyncSession.execute = orig_exec
        vm.allowed_fitment_models, parts.FIT_ID_RESOLVE_CAP = orig_allowed, orig_cap
        parts.SEARCH_STATEMENT_TIMEOUT_MS = orig_stmt_ms
        if side == "reference_in":
            parts._build_strict_vehicle_match_clause = orig_strict
        if side == "baseline":
            parts.customer_vehicle_model = orig_cvm
        parts._get_cached_search_response, parts._store_cached_search_response = orig_get, orig_store
    return runs


async def main(out, revision, deadline_s, sides=("baseline", "candidate"), attempts=2, only=None, fit_id_cap=None):
    isolate_process()
    import vehicle_model_match as vm
    from BACKEND_DATABASE_MODELS import async_session_factory
    with open(out, "w", encoding="utf-8") as fh:
        for case in CASES:
            mk, md, yr, cat = case
            if only and f"{mk}|{md}|{cat or ''}".lower() not in only:
                continue
            rec = {"revision": revision, "candidate_fit_id_cap": fit_id_cap, "case": {"make": mk, "model": md, "year": yr, "category": cat},
                   "cache": "search response cache (L1 memory + L2 Redis) patched out on both sides, no reads or writes; run 1 cold-ish, run 2 warm DB buffers",
                   "timing_note": "seconds = wall; db_query_seconds_sum = summed query time (buckets run in "
                                  "parallel, so it can exceed wall); API latency not measured (see report)"}
            for side in ("baseline", "candidate", "reference", "reference_in"):
                if side in sides:
                    rec[side] = await run_side(side, case, (REFERENCE_TIMEOUT_MS / 1000 + 30) if side.startswith("reference") else deadline_s,
                                               1 if side.startswith("reference") else attempts, fit_id_cap)
                else:
                    rec[side] = []
            async with async_session_factory() as db:
                await db.execute(text("SET TRANSACTION READ ONLY"))
                allowed = set(await vm.allowed_fitment_models(db, [mk], [md]))
            last_ok = lambda runs: next((r["ids"] for r in reversed(runs) if "ids" in r), None)  # noqa: E731
            b, c = last_ok(rec["baseline"]), last_ok(rec["candidate"])
            check = {}
            for side, ids in (("baseline", b), ("candidate", c)):
                if ids is None:
                    continue
                q = await _qualifying_models(async_session_factory, ids, mk, yr)
                wrong = [i for i in ids if q.get(i) and not (set(q[i]) & allowed)]
                check[side] = {"parts": len(ids), "via_fitment_table": sum(1 for i in ids if q.get(i)),
                               "parts_only_via_excluded_models": len(wrong),
                               "example_excluded_models": sorted({m for i in wrong[:20] for m in q[i]})[:6]}
            if b is not None and c is not None:
                check["removed_parts"] = len(set(b) - set(c))
                check["added_parts"] = len(set(c) - set(b))
            ref_in = last_ok(rec.get("reference_in") or [])
            if "reference_in" in sides:
                check["candidate_equals_reference_in"] = (None if ref_in is None or c is None else
                                                          {"same_ids": set(ref_in) == set(c), "same_order": ref_in == c,
                                                           "ref_parts": len(ref_in), "cand_parts": len(c)})
            ref = last_ok(rec.get("reference") or [])
            if "reference" in sides:
                check["candidate_equals_reference"] = (None if ref is None or c is None else
                                                       {"same_ids": set(ref) == set(c), "same_order": ref == c,
                                                        "ref_parts": len(ref), "cand_parts": len(c),
                                                        "only_ref": len(set(ref) - set(c)), "only_cand": len(set(c) - set(ref))})
            rec["correctness"] = check
            ran = rec["baseline"] + rec["candidate"] + rec["reference"] + rec["reference_in"]
            rec["complete"] = bool(ran) and all("ids" in r for r in ran) and all(rec[s] for s in sides)
            rec["sides_run"] = list(sides)
            rec["isolation"] = dict(ISOLATION)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            print(json.dumps({"case": rec["case"], "complete": rec["complete"],
                              "baseline_s": [r["seconds"] for r in rec["baseline"]],
                              "candidate_s": [r["seconds"] for r in rec["candidate"]], "check": check},
                             ensure_ascii=False), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--revision", default="unknown")
    ap.add_argument("--deadline", type=float, default=60.0)
    ap.add_argument("--sides", default="baseline,candidate")
    ap.add_argument("--attempts", type=int, default=2)
    ap.add_argument("--case", action="append", default=[], help="make|model|category (repeatable; empty category = none)")
    ap.add_argument("--fit-id-cap", type=int, default=None, help="candidate side only")
    a = ap.parse_args()
    asyncio.run(main(a.out, a.revision, a.deadline, tuple(a.sides.split(",")), a.attempts,
                     {c.lower() for c in a.case} or None, a.fit_id_cap))
