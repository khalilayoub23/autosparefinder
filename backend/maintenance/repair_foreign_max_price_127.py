#!/usr/bin/env python3
"""
Script: maintenance/repair_foreign_max_price_127.py
Purpose: #127 data repair. parts_catalog.max_price_ils is the ISRAELI consumer reference incl. 18% VAT (docs/PRICE_POLICY.md,
         "Catalog cost columns"). Before the #127 fix, foreign-source writers (refresh_min_max_prices, catalog_scraper,
         the eBay / RockAuto / Amayama / OEMPartsOnline / Car-Parts.ie / seed importers) put a FOREIGN cost (x1.18, x1.17,
         x1.2, x1.05 or x1.0) into it. This script finds those rows by EVIDENCE and sets max_price_ils = NULL.
Process:
  default (dry run, SELECT only): counts by signature and by supplier, and a before/after customer-estimate preview.
  --apply:    per batch, in one transaction each, re-checks the selection predicate with FOR UPDATE SKIP LOCKED and
              writes max_price_ils = NULL plus specifications.max_price_repair_127 = {prev, sig, at} (the rollback record).
  --rollback: restores max_price_ils from that record (only where the value is still NULL) and removes the record.
  A part is selected ONLY when ALL hold: max_price_ils > 0; it has NO offer from a LOCAL (Israeli) supplier; and its stored
  value equals a foreign offer's price_ils x {1.18, 1.0, 1.17, 1.2, 1.05}, or equals online_price_ils x the same factors while
  the part has no importer cost. A part with any Israeli offer, or whose value matches no foreign signature (an Israeli
  price-list reference, a Google-Shopping IL reference), is NEVER touched.
  Evidence of "foreign" counts only from a supplier with a USABLE country (a 2-letter code); a supplier whose country is NULL /
  'Unknown' / 'Global' (e.g. Motorstore IL and RockAuto until #128 corrects them, 'Official Manufacturer Sites') is not evidence,
  so those parts are left alone. Run #128's country corrections BEFORE this repair; the dry run reports what is still excluded.
  Never runs by itself: not imported, not scheduled. Production execution needs the owner's explicit authorization.
Data Imported/Modified: parts_catalog.max_price_ils (+ specifications.max_price_repair_127) - only with --apply / --rollback.
Data Sources: catalog DB (DATABASE_URL).
Missing Data Delegation: n/a.
Last Updated: 2026-11-04 (apply: a timed-out batch is split and retried, a single stuck id is skipped and reported, never aborts the run)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import price_policy as pp  # noqa: E402

FACTORS = ((1.18, "x1.18 (refresh/catalog_scraper/relay imports)"), (1.0, "x1.0 (raw foreign cost)"),
           (1.17, "x1.17 (OEMPartsOnline legacy)"), (1.2, "x1.2 (seed importers)"), (1.05, "x1.05 (BMW scraper)"))
KEY = "max_price_repair_127"
if not hasattr(pp, "country_problem_sql"):        # shipped by #128: "foreign" must be EVIDENCED by a classified country
    sys.exit("This repair needs #128 (price_policy.country_problem_sql) applied first - an unclassified supplier must never count as foreign evidence")
LOCAL = pp.local_supplier_sql("s")
CLASSIFIED = f"NOT {pp.country_problem_sql('s')}"       # a usable country (2-letter code / Israel); 'Unknown', 'Global', NULL are not


def _dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def _sig_sql(cost_expr: str) -> str:
    return " OR ".join(f"abs(pc.max_price_ils - {cost_expr} * {k}) < 0.02" for k, _ in FACTORS)


# a part is protected when ANY of its offers comes from a local supplier
NO_LOCAL = f"NOT EXISTS (SELECT 1 FROM supplier_parts l JOIN suppliers s ON s.id = l.supplier_id WHERE l.part_id = pc.id AND {LOCAL})"
ELIGIBLE = f"pc.max_price_ils > 0 AND NOT (COALESCE(pc.specifications, '{{}}'::jsonb) ? '{KEY}') AND {NO_LOCAL}"
FOREIGN_OFFER = ("EXISTS (SELECT 1 FROM supplier_parts f JOIN suppliers s ON s.id = f.supplier_id "
                 f"WHERE f.part_id = pc.id AND NOT {LOCAL} AND {CLASSIFIED} AND f.price_ils > 0 AND ({_sig_sql('f.price_ils')}))")
CATALOG_LEVEL = f"(pc.online_price_ils > 0 AND COALESCE(pc.importer_price_ils, 0) = 0 AND ({_sig_sql('pc.online_price_ils')}))"
PREDICATE = f"{ELIGIBLE} AND ({FOREIGN_OFFER} OR {CATALOG_LEVEL})"


async def foreign_supplier_ids(conn):
    rows = await conn.fetch(f"SELECT s.id, s.name FROM suppliers s WHERE NOT {LOCAL} AND {CLASSIFIED}")
    return [(r["id"], r["name"]) for r in rows]


async def select_candidates(conn, per_query_timeout="150s"):
    """-> {part_id: (old_max, signature_label, supplier_name_or_None)}. Read-only."""
    await conn.execute(f"SET statement_timeout = '{per_query_timeout}'")
    found = {}
    for sid, name in await foreign_supplier_ids(conn):
        # drive from the supplier's offers (indexed) so no query scans the whole catalogue
        rows = await conn.fetch(
            f"SELECT DISTINCT pc.id, pc.max_price_ils FROM supplier_parts f JOIN parts_catalog pc ON pc.id = f.part_id "
            f"WHERE f.supplier_id = $1 AND f.price_ils > 0 AND ({_sig_sql('f.price_ils')}) AND {ELIGIBLE}", sid)
        for r in rows:
            found.setdefault(r["id"], (float(r["max_price_ils"]), "foreign offer signature", name))
    rows = await conn.fetch(f"SELECT pc.id, pc.max_price_ils FROM parts_catalog pc WHERE {ELIGIBLE} AND {CATALOG_LEVEL}")
    for r in rows:
        found.setdefault(r["id"], (float(r["max_price_ils"]), "catalog-level online_price signature", None))
    return found


async def preview(conn, ids):
    """Customer-estimate before/after for the part ids (the estimate is the only customer reader of max_price_ils)."""
    visible = ("SELECT 1 FROM supplier_parts sp2 JOIN suppliers s2 ON s2.id = sp2.supplier_id WHERE sp2.part_id = pc.id AND "
               "sp2.is_available AND s2.is_active AND s2.name NOT IN ('Official Manufacturer Sites','Sandbox Supplier QA') "
               "AND NULLIF(BTRIM(sp2.supplier_url),'') IS NOT NULL")
    out = {"affected": len(ids), "no_visible_offer": 0, "estimate_changes": 0, "estimate_lost": 0, "samples": []}
    for i in range(0, len(ids), 5000):
        rows = await conn.fetch(
            f"SELECT pc.id, pc.sku, pc.importer_price_ils, pc.max_price_ils, pc.online_price_ils FROM parts_catalog pc "
            f"WHERE pc.id = ANY($1) AND NOT EXISTS ({visible})", ids[i:i + 5000])
        for r in rows:
            out["no_visible_offer"] += 1
            before = pp.catalog_estimate_price(r["importer_price_ils"], r["max_price_ils"], r["online_price_ils"])
            after = pp.catalog_estimate_price(r["importer_price_ils"], None, r["online_price_ils"])
            b, a = (before.total if before else None), (after.total if after else None)
            if b != a:
                out["estimate_changes"] += 1
                out["estimate_lost"] += 1 if a is None else 0
                if len(out["samples"]) < 5:
                    out["samples"].append({"sku": r["sku"], "max_before": float(r["max_price_ils"]), "estimate_before": b, "estimate_after": a})
    return out


async def _apply_chunk(conn, chunk, timeout="120s"):
    """One transaction: re-check the predicate under FOR UPDATE SKIP LOCKED, then NULL max_price_ils with provenance."""
    async with conn.transaction():
        await conn.execute(f"SET LOCAL statement_timeout = '{timeout}'")
        locked = await conn.fetch(f"SELECT pc.id FROM parts_catalog pc WHERE pc.id = ANY($1) AND ({PREDICATE}) FOR UPDATE OF pc SKIP LOCKED", chunk)
        lock_ids = [r["id"] for r in locked]
        if lock_ids:
            await conn.execute(
                "UPDATE parts_catalog SET specifications = jsonb_set(COALESCE(specifications, '{}'::jsonb), $2::text[], "
                "jsonb_build_object('prev', max_price_ils, 'at', to_jsonb(NOW()::text), 'sig', 'foreign-derived'), true), "
                "max_price_ils = NULL, updated_at = NOW() WHERE id = ANY($1)", lock_ids, [KEY])
        return len(lock_ids)


async def _apply_resilient(conn, chunk, failed):
    """A timed-out chunk is retried in halves (each its own transaction, so nothing partial); a single id that still
    times out is recorded in `failed` and skipped - the run stays resumable and idempotent."""
    try:
        return await _apply_chunk(conn, chunk)
    except asyncpg.exceptions.QueryCanceledError:
        if len(chunk) == 1:
            failed.append(chunk[0])
            print(json.dumps({"chunk_skipped": str(chunk[0])}), flush=True)
            return 0
        mid = len(chunk) // 2
        print(json.dumps({"chunk_timeout_split": len(chunk)}), flush=True)
        return await _apply_resilient(conn, chunk[:mid], failed) + await _apply_resilient(conn, chunk[mid:], failed)


async def apply(conn, ids, batch=2000, limit=None):
    done, failed = 0, []
    for i in range(0, len(ids), batch):
        if limit is not None and done >= limit:
            break
        done += await _apply_resilient(conn, ids[i:i + batch], failed)
    if failed:
        print(json.dumps({"failed_ids": [str(x) for x in failed]}), flush=True)
    return done


async def rollback(conn, batch=2000):
    ids = [r["id"] for r in await conn.fetch(f"SELECT id FROM parts_catalog WHERE specifications ? '{KEY}'")]
    restored = 0
    for i in range(0, len(ids), batch):
        async with conn.transaction():
            res = await conn.execute(
                f"UPDATE parts_catalog SET max_price_ils = (specifications->'{KEY}'->>'prev')::numeric, "
                f"specifications = specifications - '{KEY}', updated_at = NOW() "
                f"WHERE id = ANY($1) AND max_price_ils IS NULL", ids[i:i + batch])
            restored += int(res.split()[-1])
    return restored


async def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="WRITE: set max_price_ils = NULL for the selected parts")
    mode.add_argument("--rollback", action="store_true", help="WRITE: restore the values recorded by --apply")
    ap.add_argument("--batch", type=int, default=2000)
    ap.add_argument("--limit", type=int, default=None, help="max parts to change in this run (apply)")
    args = ap.parse_args()
    conn = await asyncpg.connect(_dsn())
    try:
        if args.rollback:
            print(json.dumps({"restored": await rollback(conn, args.batch)}))
            return
        found = await select_candidates(conn)
        ids = sorted(found)
        by_sig, by_sup = {}, {}
        for _id, (_mx, sig, sup) in found.items():
            by_sig[sig] = by_sig.get(sig, 0) + 1
            by_sup[sup or "(catalog-level)"] = by_sup.get(sup or "(catalog-level)", 0) + 1
        report = {"mode": "apply" if args.apply else "dry-run (no write)", "selected": len(ids), "by_signature": by_sig,
                  "by_supplier": dict(sorted(by_sup.items(), key=lambda kv: -kv[1])[:15]), "preview": await preview(conn, ids)}
        if args.apply:
            report["changed"] = await apply(conn, ids, args.batch, args.limit)
        print(json.dumps(report, indent=2, default=str))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
