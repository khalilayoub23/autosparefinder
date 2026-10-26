"""
devtests/numeric_model_e2e.py — end-to-end check of the customer vehicle-model input (FIXES_TRACKER #69 §16).
READ-ONLY and isolated: isolate_process() makes every DB transaction READ ONLY and Redis unreachable;
the search response cache and the chat search-miss logger are patched out.

For each (make, model, year) case it records:
  deployed_input  what the deployed code passes on (canonicalize(...) or normalize(...))
  repo_input      customer_vehicle_model(...)
  allowed         the fitting fitment models (vehicle_model_match)
  website         search_parts() result ids, then an INDEPENDENT check of every id: its part_vehicle_fitment
                  rows for the make/year, and its compatible_vehicles JSON models
  chat            search_parts_in_db() with a plate-style vehicle_profile (text query), same check
Verdict per case: no returned part may be outside the allowed models (wildcard check); cases marked
expect_empty must return nothing.

Run: docker exec -w /app autospare_backend python3 devtests/numeric_model_e2e.py --out /tmp/numeric_e2e.jsonl
"""
import argparse
import asyncio
import json
import sys
import time

sys.path.insert(0, "/app")

from sqlalchemy import text  # noqa: E402

CASES = [
    # (kind, make, model, year, expect_empty)
    ("valid", "Peugeot", "3008", 2019, False),
    ("valid", "Peugeot", "2008", 2019, False),
    ("valid", "Peugeot", "208", 2019, False),
    ("valid", "Peugeot", "308", 2016, False),
    ("valid", "Mazda", "3", 2018, False),
    ("valid_registry_form", "Mazda", "MAZDA 3", 2018, False),
    ("valid_registry_form", "Mazda", "MAZDA3", 2018, False),
    ("registry_form_raw_only", "MG", "MG4", 2023, False),
    ("registry_form_raw_only", "Jaecoo", "JAECOO7", 2025, False),
    ("registry_form_raw_only", "Fiat", "FIAT 500 1.2", 2015, False),
    ("registry_form_raw_only", "Zeekr", "ZEEKR 001", 2024, False),
    ("malformed", "Peugeot", "3008  2019", 2019, False),
    ("malformed", "Peugeot", " 3008 ", 2019, False),
    ("malformed", "Peugeot", "PEUGEOT-3008", 2019, False),
    ("malformed", "Peugeot", "3008@@", 2019, True),
    ("missing", "Peugeot", "   ", 2019, True),
    ("missing_junk", "Peugeot", "sku", 2019, True),
    ("ambiguous", "Peugeot", "3008-5008", 2019, False),
    ("ambiguous", "Mazda", "CX-3", 2018, False),
]


async def _independent_check(session_factory, ids, make, year, allowed, customer):
    """Which models does each returned part fit, by the DATABASE (not by the search's own SQL)?"""
    if not ids:
        return {"parts": 0, "outside_allowed": 0, "examples": []}
    ok_models = set(allowed) | {c for c in customer if c}
    async with session_factory() as db:
        await db.execute(text("SET LOCAL statement_timeout = '30s'"))
        pvf = dict((await db.execute(text(
            "SELECT part_id::text, array_agg(DISTINCT lower(trim(model))) FROM part_vehicle_fitment "
            "WHERE part_id = ANY(CAST(:p AS uuid[])) AND lower(trim(manufacturer)) LIKE '%' || lower(:mk) || '%' "
            "AND year_from <= :y AND COALESCE(year_to, year_from) >= :y GROUP BY 1"),
            {"p": ids, "mk": make, "y": year})).all())
        js = dict((await db.execute(text(
            "SELECT pc.id::text, array_agg(DISTINCT lower(trim(cv->>'model'))) FROM parts_catalog pc, "
            "jsonb_array_elements(CASE WHEN jsonb_typeof(pc.compatible_vehicles)='array' THEN pc.compatible_vehicles "
            "ELSE '[]'::jsonb END) cv WHERE pc.id = ANY(CAST(:p AS uuid[])) GROUP BY 1"), {"p": ids})).all())
    outside = []
    for i in ids:
        models = set(pvf.get(i) or []) | set(js.get(i) or [])
        if not (models & ok_models):
            outside.append({"id": i, "pvf_models": sorted(pvf.get(i) or [])[:5], "json_models": sorted(js.get(i) or [])[:5]})
    return {"parts": len(ids), "via_fitment_table": sum(1 for i in ids if pvf.get(i)),
            "outside_allowed": len(outside), "examples": outside[:3]}


def _ids(res):
    return [str((r.get("part") or {}).get("id")) for k in ("original_options", "oem_options", "aftermarket_options")
            for r in (res.get(k) or []) if (r.get("part") or {}).get("id")]


async def main(out, chat_query):
    from search_fitment_comparison import isolate_process, ISOLATION
    isolate_process()
    import routes.parts as parts
    import BACKEND_AI_AGENTS as agents
    import vehicle_model_match as vm
    from BACKEND_DATABASE_MODELS import async_session_factory
    from manufacturer_normalization import (canonicalize_vehicle_model_for_manufacturer as _canon,
                                            normalize_vehicle_model_name as _norm, customer_vehicle_model,
                                            customer_vehicle_model_forms)

    async def _none(*_a, **_k):
        return None
    parts._get_cached_search_response = _none
    parts._store_cached_search_response = _none
    agents._log_search_miss = _none
    finder = agents.PartsFinderAgent()

    with open(out, "w", encoding="utf-8") as fh:
        for kind, mk, md, yr, expect_empty in CASES:
            rec = {"kind": kind, "make": mk, "model": md, "year": yr, "expect_empty": expect_empty,
                   "deployed_input": _canon(mk, md) or _norm(md), "repo_input": customer_vehicle_model(mk, md),
                   "repo_forms": customer_vehicle_model_forms(mk, md)}
            async with async_session_factory() as db:
                rec["allowed"] = await vm.allowed_fitment_models(db, [mk], rec["repo_forms"])
            t = time.monotonic()
            try:
                async with async_session_factory() as db:
                    res = await asyncio.wait_for(parts.search_parts(
                        query="", vehicle_id=None, category=None, per_type=24, sort_by="price_ils",
                        vehicle_manufacturer=mk, vehicle_model=md, vehicle_submodel=None, vehicle_year=yr,
                        db=db, request=None, redis=None), timeout=45)
                ids = _ids(res)
                rec["website"] = {"seconds": round(time.monotonic() - t, 2), **await _independent_check(
                    async_session_factory, ids, mk, yr, rec["allowed"], [f.lower() for f in rec["repo_forms"]])}
            except Exception as e:
                rec["website"] = {"seconds": round(time.monotonic() - t, 2), "error": f"{type(e).__name__}: {str(e)[:160]}"}
            if finder is not None:
                t = time.monotonic()
                try:
                    async with async_session_factory() as db:
                        rows = await asyncio.wait_for(finder.search_parts_in_db(
                            query=chat_query, vehicle_id=None, category=None, db=db, limit=20,
                            vehicle_profile={"manufacturer": mk, "model": md, "year": yr}), timeout=45)
                    ids = [str(r.get("id")) for r in rows if r.get("id")]
                    rec["chat"] = {"seconds": round(time.monotonic() - t, 2), **await _independent_check(
                        async_session_factory, ids, mk, yr, rec["allowed"], [f.lower() for f in rec["repo_forms"]])}
                except Exception as e:
                    rec["chat"] = {"seconds": round(time.monotonic() - t, 2), "error": f"{type(e).__name__}: {str(e)[:160]}"}
            verdict = []
            for lane in ("website", "chat"):
                r = rec.get(lane) or {}
                if "error" in r:
                    verdict.append(f"{lane}:ERROR")
                elif r.get("outside_allowed"):
                    verdict.append(f"{lane}:FAIL_wildcard({r['outside_allowed']})")
                elif expect_empty and r.get("parts"):
                    verdict.append(f"{lane}:FAIL_expected_empty({r['parts']})")
                else:
                    verdict.append(f"{lane}:PASS")
            rec["verdict"] = verdict
            rec["isolation"] = dict(ISOLATION)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            print(json.dumps({k: rec[k] for k in ("kind", "make", "model", "deployed_input", "repo_forms", "verdict")},
                             ensure_ascii=False), "| allowed", len(rec["allowed"]),
                  "| web", {k: rec.get("website", {}).get(k) for k in ("seconds", "parts", "outside_allowed", "error")},
                  "| chat", {k: (rec.get("chat") or {}).get(k) for k in ("seconds", "parts", "outside_allowed", "error")},
                  flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--chat-query", default="brake pads")
    a = ap.parse_args()
    sys.path.insert(0, "/app/devtests")
    asyncio.run(main(a.out, a.chat_query))
