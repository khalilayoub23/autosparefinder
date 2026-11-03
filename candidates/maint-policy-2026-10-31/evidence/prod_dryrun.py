"""#119 production DRY RUN — READ-ONLY (server-enforced default_transaction_read_only=on). Writes nothing.

Runs the NEW code's own rule functions (mounted at /app from the candidate tree) over the live catalogue and reports
what each task WOULD change, with per-batch timing for the batch plan.
usage: python3 prod_dryrun.py <out.json> [base|merge|names|all]
"""
import asyncio
import collections
import json
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, "/app")
import asyncpg  # noqa: E402
import db_update_agent as dua  # noqa: E402
from currency_rate import FX_CANONICAL_KEY, FX_LEGACY_KEY, _normalize_rate, DEFAULT_USD_TO_ILS  # noqa: E402

DB = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
OUT, WHAT = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "all")
MAX_S = float(os.getenv("DRYRUN_MAX_S", "2400"))


async def conn():
    c = await asyncpg.connect(DB, server_settings={"default_transaction_read_only": "on",
                                                   "statement_timeout": "120000", "application_name": "m119_dryrun"})
    assert await c.fetchval("SHOW default_transaction_read_only") == "on"
    return c


def _pg(sql):
    """SQLAlchemy :name params -> asyncpg $n (only the names these queries use)."""
    for i, n in enumerate((":rate", ":after", ":batch", ":ids"), 1):
        sql = sql.replace(n, f"${i}")
    return sql


async def base(c, out):
    fx = 0.0
    for k in (FX_CANONICAL_KEY, FX_LEGACY_KEY):
        v = await c.fetchval("SELECT value FROM system_settings WHERE key=$1", k)
        if v is not None and _normalize_rate(v, fallback=0) > 0:
            fx = _normalize_rate(v, fallback=0); break
    fx = fx or DEFAULT_USD_TO_ILS
    sql = _pg(dua._base_price_inputs_sql(False)).replace("CAST($4 AS uuid[])", "$4")
    batch = int(os.getenv("DRYRUN_BATCH", "2000"))
    after, t0 = dua._ZERO_UUID, time.monotonic()
    src = collections.Counter(); change = collections.Counter(); mag = collections.Counter(); times = []
    samples = collections.defaultdict(list); scanned = 0; complete = False
    while time.monotonic() - t0 < MAX_S:
        tb = time.monotonic()
        rows = await c.fetch(sql, fx, after, batch)
        times.append(round(time.monotonic() - tb, 3))
        for r in rows:
            scanned += 1
            ns = SimpleNamespace(**dict(r))
            target, source = dua.base_price_target(ns, fx)
            owner = {"offer": "fix_base_prices", "estimate": "normalize_base_price"}.get(source, "none")
            src[f"{owner}:{source}"] += 1
            if target is None:
                continue
            cur = None if r["base_price"] is None else round(float(r["base_price"]), 2)
            if cur == target:
                change[f"{owner}:unchanged"] += 1
                continue
            kind = "fill" if not cur else ("raise" if target > cur else "lower")
            change[f"{owner}:{kind}"] += 1
            if cur:
                pct = abs(target - cur) / cur * 100
                b = "<=1%" if pct <= 1 else "<=5%" if pct <= 5 else "<=20%" if pct <= 20 else "<=50%" if pct <= 50 else ">50%"
                mag[f"{owner}:{b}"] += 1
                if pct > 50 and len(samples[owner]) < 8:
                    samples[owner].append({"id": r["id"], "base": cur, "target": target, "importer": r["importer_price_ils"],
                                           "offer_ils": r["offer_ils"], "offer_usd": r["offer_usd"]})
        if len(rows) < batch:
            complete = True
            break
        after = rows[-1]["id"]
        await asyncio.sleep(0.05)
    out["base_price"] = {"usd_to_ils": fx, "batch": batch, "scanned": scanned, "complete": complete,
                         "by_source": dict(src), "would_change": dict(change), "change_magnitude": dict(mag),
                         "large_change_samples": samples,
                         "batch_s": {"n": len(times), "avg": round(sum(times) / max(len(times), 1), 3),
                                     "p95": sorted(times)[int(len(times) * .95)] if times else None, "max": max(times or [0])},
                         "elapsed_s": round(time.monotonic() - t0, 1)}


async def merge(c, out):
    batch = int(os.getenv("DRYRUN_MERGE_BATCH", "1000"))
    after, t0, times = dua._ZERO_UUID, time.monotonic(), []
    cnt = collections.Counter(); complete = False; multi_lost = 0
    while time.monotonic() - t0 < MAX_S:
        tb = time.monotonic()
        ids = [r[0] for r in await c.fetch("SELECT DISTINCT part_id FROM part_vehicle_fitment WHERE part_id > $1::uuid ORDER BY 1 LIMIT $2", after, batch)]
        if not ids:
            complete = True; break
        pvf = collections.defaultdict(list)
        for r in await c.fetch("""SELECT part_id, manufacturer, model, year_from, year_to, engine_type FROM part_vehicle_fitment
                WHERE part_id = ANY($1::uuid[]) AND manufacturer IS NOT NULL AND TRIM(manufacturer) <> ''
                  AND model IS NOT NULL AND TRIM(model) <> '' AND year_from IS NOT NULL AND year_to IS NOT NULL""", ids):
            pvf[r[0]].append(tuple(r[1:]))
        cur = {r[0]: (json.loads(r[1]) if isinstance(r[1], str) else r[1])
               for r in await c.fetch("SELECT id, compatible_vehicles FROM parts_catalog WHERE id = ANY($1::uuid[])", ids)}
        times.append(round(time.monotonic() - tb, 3))
        for pid in ids:
            if pid not in cur:
                cnt["part_missing"] += 1; continue
            merged = dua.merged_compatible_vehicles(cur[pid], pvf.get(pid, []))
            had = isinstance(cur[pid], list)
            if not had and not merged:
                cnt["nothing_to_copy"] += 1; continue
            if had and merged == dua._canonical_fitment_list(cur[pid]):
                cnt["unchanged"] += 1; continue
            old_pvf = {(e.get("manufacturer"),) for e in (cur[pid] if had else []) if isinstance(e, dict) and e.get("source") == "part_vehicle_fitment"}
            new_mk = {e["manufacturer"] for e in merged if e.get("source") == "part_vehicle_fitment"}
            cnt["would_update"] += 1
            cnt["would_update:first_copy" if not old_pvf else "would_update:refresh"] += 1
            if len(new_mk) > 1 and len(old_pvf) <= 1:
                multi_lost += 1
        if len(ids) < batch:
            complete = True; break
        after = str(ids[-1])
        await asyncio.sleep(0.05)
    out["merge"] = {"parts_scanned": sum(cnt.values()) - cnt["would_update:first_copy"] - cnt["would_update:refresh"],
                    "counts": dict(cnt), "multi_make_parts_restored": multi_lost, "complete": complete,
                    "batch_s": {"n": len(times), "avg": round(sum(times) / max(len(times), 1), 3), "max": max(times or [0])},
                    "elapsed_s": round(time.monotonic() - t0, 1)}


async def names(c, out):
    res = {}
    for task, mfr, fn, extra in (("bmw", "BMW", dua._bmw_fitment_from_name, ""),
                                 ("ford", "Ford", dua._ford_fitment_from_name, ""),
                                 ("jaguar", "Jaguar", dua._jaguar_fitment_from_name,
                                  "AND (pc.category IS NULL OR pc.category != 'accessories')")):
        t0 = time.monotonic()
        rows = await c.fetch(f"""SELECT pc.id, pc.name, pc.name_he FROM parts_catalog pc WHERE pc.manufacturer = $1 AND pc.is_active {extra}
              AND NOT EXISTS (SELECT 1 FROM part_vehicle_fitment pvf WHERE pvf.part_id = pc.id)""", mfr)
        reasons, models, matched = collections.Counter(), collections.Counter(), []
        for r in rows:
            found, why = fn(r["name"] or "", r["name_he"] or "")
            if not found:
                reasons[why] += 1; continue
            models.update(f"{f[0]} {f[1]}" for f in found)
            matched.append([r["name"], r["name_he"], [f[1] for f in found]])
        res[task] = {"candidates": len(rows), "would_match_parts": len(matched), "would_insert_rows": sum(len(m[2]) for m in matched),
                     "skip_reasons": dict(reasons), "models": models.most_common(20), "matches": matched,
                     "elapsed_s": round(time.monotonic() - t0, 1)}
    r = await c.fetchrow("""SELECT count(*) n, count(DISTINCT part_id) parts,
        count(*) FILTER (WHERE EXISTS (SELECT 1 FROM part_vehicle_fitment q WHERE q.part_id = p.part_id AND q.manufacturer = 'Ford'
                                       AND q.model = substr(p.model, 6) AND q.year_from = p.year_from)) AS would_delete_as_duplicate
        FROM part_vehicle_fitment p WHERE manufacturer = 'Ford' AND notes = 'extracted:name_he' AND model LIKE 'Ford %'""")
    res["ford_repair"] = dict(r)
    res["ford_repair"]["by_model"] = [dict(x) for x in await c.fetch("""SELECT model, count(*) n FROM part_vehicle_fitment
        WHERE manufacturer = 'Ford' AND notes = 'extracted:name_he' AND model LIKE 'Ford %' GROUP BY 1 ORDER BY 2 DESC""")]
    res["jaguar_existing"] = [dict(x) for x in await c.fetch("""SELECT pc.name, pvf.model FROM part_vehicle_fitment pvf
        JOIN parts_catalog pc ON pc.id = pvf.part_id WHERE pvf.notes = 'name_parse:jaguar' ORDER BY 1""")]
    res["bmw_existing_sample"] = [dict(x) for x in await c.fetch("""SELECT pc.name, pc.name_he, pvf.manufacturer, pvf.model, pvf.notes
        FROM part_vehicle_fitment pvf JOIN parts_catalog pc ON pc.id = pvf.part_id WHERE pvf.notes LIKE 'chassis:%'
        ORDER BY md5(pvf.id::text) LIMIT 40""")]
    out["names"] = res


async def main():
    out = {"started": time.strftime("%Y-%m-%d %H:%M:%S")}
    c = await conn()
    try:
        if WHAT in ("names", "all"):
            await names(c, out)
        if WHAT in ("merge", "all"):
            await merge(c, out)
        if WHAT in ("base", "all"):
            await base(c, out)
    finally:
        await c.close()
    out["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print("wrote", OUT)


asyncio.run(main())
