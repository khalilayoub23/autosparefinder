"""
Script: measure_119.py  (release evidence, READ-ONLY)
Purpose: #119 - measure the work the six steps disabled in run_all_tasks would still do, with the steps' OWN rules:
         BMW / Ford / Jaguar fitment-from-name backfills, merge_catalog_fitment_from_part_vehicle_fitment,
         fix_base_prices, normalize_base_price.
Process: one READ ONLY transaction (statement_timeout 120 s, lock_timeout 1 s); streams candidate rows with a
         server-side cursor and applies the same regexes / maps the tasks import from db_update_agent. Writes nothing.
Run:     docker exec -i autospare_backend python3 - < measure_119.py
Data Imported/Modified: none.
Last Updated: 2026-10-28 (measures the PRE-#119 code: it imports the BMW/Ford/Jaguar rules the bundle deletes)
"""
import asyncio
import json
import os
import re
import sys
import time

sys.path.insert(0, "/app")
import asyncpg  # noqa: E402

import db_update_agent as d  # noqa: E402   (rules only; no task is called)

DSN = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
MERCH = re.compile(r'\b(shirt|polo|shoe|shoes|boot|jacket|wallet|keyring|key\s+ring|mug|notebook|ebook|e-book|badge|'
                   r'scarf|hat\b|bag\b|scale\s+model|umbrella|cap\b|sock|cufflink|glove|passport|money\s+clip|suede|'
                   r'driving\s+shoe|print\b|sticker|poster|pennant|artwork|miniature)\b', re.IGNORECASE)   # = task's copy
FORD = [(re.compile(p, re.IGNORECASE), m) for p, m, _yf, _yt in d._FORD_MODEL_PATTERNS]


def bmw_parsable(t):
    raw = {m.upper().replace('-', '') for m in d._BMW_CHASSIS_RE.findall(t)}
    return any(c in d._BMW_CHASSIS_MAP or c in d._BMW_MINI_PASSTHROUGH for c in raw)


def ford_parsable(t):
    return any(p.search(t) for p, _m in FORD)


def jaguar_parsable(t):
    return not MERCH.search(t) and any(p.search(t) for p, *_ in d._JAGUAR_MODEL_MAP)


BRANDS = (("BMW", bmw_parsable, ""), ("Ford", ford_parsable, ""),
          ("Jaguar", jaguar_parsable, "AND (pc.category IS NULL OR pc.category != 'accessories')"))


async def main():
    out = {"measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    c = await asyncpg.connect(DSN)
    try:
        async with c.transaction(readonly=True, isolation="repeatable_read"):
            await c.execute("SET LOCAL statement_timeout = '120s'; SET LOCAL lock_timeout = '1s'")
            for brand, parsable, extra in BRANDS:
                t0 = time.monotonic()
                total = await c.fetchval("SELECT count(*) FROM parts_catalog WHERE manufacturer = $1 AND is_active", brand)
                no_fit, would, samples = 0, 0, []
                async for r in c.cursor(f"""SELECT pc.id, coalesce(pc.name_he,'') || ' ' || coalesce(pc.name,'') AS t
                        FROM parts_catalog pc WHERE pc.manufacturer = $1 AND pc.is_active = TRUE {extra}
                        AND NOT EXISTS (SELECT 1 FROM part_vehicle_fitment pvf WHERE pvf.part_id = pc.id)""", brand,
                                       prefetch=2000):
                    no_fit += 1
                    if parsable(r["t"]):
                        would += 1
                        if len(samples) < 5:
                            samples.append(r["t"][:90])
                out[f"fitment_{brand}"] = {"active_parts": total, "active_without_fitment": no_fit,
                                           "parsable_by_task_rules_REMAINING": would, "samples": samples,
                                           "elapsed_s": round(time.monotonic() - t0, 1)}
            # merge: parts with fitment rows whose JSON copy is missing (sample-based, the full join is the OOM)
            t0 = time.monotonic()
            pvf_parts = await c.fetchval("SELECT count(DISTINCT part_id) FROM part_vehicle_fitment")
            sample = await c.fetch("""SELECT p.part_id, pc.compatible_vehicles AS cv FROM
                    (SELECT DISTINCT part_id FROM part_vehicle_fitment TABLESAMPLE SYSTEM (1)) p
                    JOIN parts_catalog pc ON pc.id = p.part_id WHERE pc.is_active LIMIT 3000""")
            def _cv(v):
                v = json.loads(v) if isinstance(v, str) else v
                return v if isinstance(v, list) else []
            missing = sum(1 for r in sample
                          if not any(isinstance(x, dict) and x.get("source") == "part_vehicle_fitment" for x in _cv(r["cv"])))
            out["merge_compatible_vehicles"] = {"parts_with_fitment_rows": pvf_parts, "sample": len(sample),
                                                "sample_without_pvf_copy": missing,
                                                "elapsed_s": round(time.monotonic() - t0, 1)}
            # fix_base_prices: base below the cheapest compatible offer x margin (the task only RAISES base_price)
            t0 = time.monotonic()
            margin = float(os.getenv("IL_MARGIN", "1.45"))
            out["fix_base_prices"] = dict(await c.fetchrow("""
                WITH m AS (SELECT sp.part_id, MIN(sp.price_ils) * $1 AS want FROM supplier_parts sp
                           WHERE sp.is_available AND sp.price_ils > 0 GROUP BY sp.part_id)
                SELECT count(*) AS parts_with_priced_offer,
                       count(*) FILTER (WHERE pc.base_price IS NULL) AS base_null,
                       count(*) FILTER (WHERE pc.base_price < round(m.want::numeric, 2)) AS base_below_cheapest_x_margin
                FROM m JOIN parts_catalog pc ON pc.id = m.part_id WHERE pc.is_active""", margin))
            out["fix_base_prices"]["elapsed_s"] = round(time.monotonic() - t0, 1)
            out["fix_base_prices"]["note"] = ("upper bound: ignores the task's offer-class rank and USD-only offers")
            # normalize_base_price: rows whose base_price differs from the 3-case formula
            t0 = time.monotonic()
            out["normalize_base_price"] = dict(await c.fetchrow("""
                SELECT count(*) FILTER (WHERE importer_price_ils > 0) AS case1_rows,
                       count(*) FILTER (WHERE importer_price_ils > 0
                                          AND base_price IS DISTINCT FROM round((importer_price_ils*$1)::numeric,2)) AS case1_off,
                       count(*) FILTER (WHERE coalesce(importer_price_ils,0) = 0 AND online_price_ils > 0
                                          AND base_price IS DISTINCT FROM round((online_price_ils*$1)::numeric,2)) AS case2_off,
                       count(*) FILTER (WHERE coalesce(importer_price_ils,0) = 0 AND coalesce(online_price_ils,0) = 0
                                          AND max_price_ils > 0
                                          AND base_price IS DISTINCT FROM round((max_price_ils/1.18*$1)::numeric,2)) AS case3_off
                FROM parts_catalog WHERE is_active""", margin))
            out["normalize_base_price"]["elapsed_s"] = round(time.monotonic() - t0, 1)
            out["normalize_base_price"]["note"] = ("upper bound: ignores the task's offer-class guard")
    finally:
        await c.close()
    print(json.dumps(out, indent=1, default=str))


asyncio.run(main())
