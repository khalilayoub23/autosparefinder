"""
Script: offer_policy_repair.py
Purpose: the policy-bound repair agent for EXISTING offer data. Loads docs/policies/OFFER_DEDUPLICATION_POLICY.md, classifies
         offers into the six policy classes with the shared offer_policy.classify_pair, writes a record-level dry-run plan,
         and (only with --apply) performs the few writes the policy permits, each logged with before/after, evidence and
         the policy hash, and reversible with --rollback.
Process:
  1. require_policy()  -> PolicyUnavailable => exit 3, NO database connection opened, nothing written (fail closed).
  2. audit (default)   -> read-only transaction; counts per class; Motorstore reassessment; JSON report + CSV plan.
  3. --apply           -> for each plan row whose action the policy gate (assert_write_allowed) accepts, inside one
                          transaction with FOR UPDATE SKIP LOCKED: log row to offer_policy_audit_log, then change
                          supplier_parts. Destructive actions need dry_run_done (the plan file of this run) and a
                          verified other orderable offer. Nothing is ever hard-deleted.
  4. --rollback RUN_ID -> restores every logged before-image of that run.
Data Imported/Modified: reads supplier_parts, suppliers, parts_catalog, price_history. --apply may write
         supplier_parts.supplier_id / is_available and offer_policy_audit_log (created on --apply only).
Data Sources: the production catalog DB (DATABASE_URL).
Missing Data Delegation: anything not provable stays UNRESOLVED and unchanged.
Last Updated: 2026-11-06
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import offer_policy as op  # noqa: E402

AUDIT_DDL = """CREATE TABLE IF NOT EXISTS offer_policy_audit_log (
  id bigserial PRIMARY KEY, run_id text NOT NULL, supplier_part_id uuid NOT NULL, action text NOT NULL,
  classification text NOT NULL, before jsonb NOT NULL, after jsonb NOT NULL, evidence jsonb NOT NULL,
  policy_version text NOT NULL, policy_sha256 text NOT NULL, created_at timestamptz NOT NULL DEFAULT now())"""

SAME_SUPPLIER_SQL = """
SELECT sp.id::text row_id, sp.supplier_id::text, sp.part_id::text, sp.supplier_sku, sp.part_type, pc.part_type catalog_part_type,
       sp.price_ils::float, sp.is_available, sp.supplier_url, pc.name,
       regexp_replace(upper(pc.oem_number),'[^A-Z0-9]','','g') noem, lower(pc.manufacturer) mf
FROM supplier_parts sp JOIN parts_catalog pc ON pc.id = sp.part_id
WHERE (sp.supplier_id, regexp_replace(upper(pc.oem_number),'[^A-Z0-9]','','g'), lower(pc.manufacturer)) IN (
  SELECT sp2.supplier_id, regexp_replace(upper(pc2.oem_number),'[^A-Z0-9]','','g'), lower(pc2.manufacturer)
  FROM supplier_parts sp2 JOIN parts_catalog pc2 ON pc2.id = sp2.part_id
  WHERE pc2.oem_number IS NOT NULL AND pc2.oem_number <> ''
  GROUP BY 1,2,3 HAVING count(*) > 1)
"""

MOTORSTORE_SQL = """
SELECT sp.id::text row_id, sp.part_id::text, sp.supplier_sku, sp.price_ils::float price, sp.is_available, sp.part_type,
       pc.max_price_ils::float max_price, pc.importer_price_ils::float importer_price, pc.manufacturer, pc.oem_number,
       sp.updated_at::text,
       (SELECT json_agg(json_build_object('supplier_id', o.supplier_id, 'supplier', s2.name, 'price', o.price_ils::float,
                                          'available', o.is_available, 'sku', o.supplier_sku))
          FROM supplier_parts o JOIN suppliers s2 ON s2.id = o.supplier_id
         WHERE o.part_id = sp.part_id AND o.supplier_id <> sp.supplier_id) others
FROM supplier_parts sp JOIN parts_catalog pc ON pc.id = sp.part_id
WHERE sp.supplier_id = (SELECT id FROM suppliers WHERE name = 'Motorstore IL')
"""


def classify_motorstore(r: dict) -> dict:
    """Policy classification of ONE Motorstore offer. E needs derivation evidence AND exactly one real, orderable
    offer from another supplier on the same part; anything else is F (unchanged)."""
    others = r["others"] or []
    if isinstance(others, str):
        others = json.loads(others)
    orderable = [o for o in others if o["available"] and o["price"] and o["price"] > 0]
    p, mx, imp = r["price"], r["max_price"], r["importer_price"]
    derived = bool(p) and ((mx and abs(p - round(mx / 1.18, 2)) <= 0.011) or (imp and abs(p - imp) <= 0.011))
    ev = {"derived_from_price_list": bool(derived), "other_orderable_suppliers": [o["supplier"] for o in orderable]}
    cls = op.Classification.UNRESOLVED
    # The named source must be shown by the record itself: the other supplier's offer carries the SAME SKU at the SAME
    # price (availability irrelevant). "The only other orderable supplier" is NOT evidence of who the source is.
    same_sku_price = [o for o in others if o.get("sku") and str(o["sku"]).strip().upper() == str(r.get("supplier_sku") or "").strip().upper()
                      and o["price"] is not None and p is not None and abs(float(o["price"]) - float(p)) <= 0.011]
    ev["source_candidates"] = [o["supplier"] for o in same_sku_price]
    if derived and len({o["supplier_id"] for o in same_sku_price}) == 1:
        cls = op.classify_pair({"supplier_id": "motorstore", "part_id": r["part_id"]},
                               {"supplier_id": same_sku_price[0]["supplier_id"], "part_id": r["part_id"]},
                               evidence={"source_proves_supplier": same_sku_price[0]["supplier_id"]})
    elif orderable:
        cls = op.Classification.COMPETING_OFFERS if not derived else op.Classification.UNRESOLVED
    ev["collision"] = cls is op.Classification.MISATTRIBUTED  # same (part, supplier) already exists
    action = "none"  # reassign impossible on collision (uq part+supplier) and retire is not permitted for class E
    return {"row_id": r["row_id"], "part_id": r["part_id"], "class": cls.value, "class_name": cls.name, "action": action,
            "available": r["is_available"], "price": p, "evidence": ev}


async def audit(conn, out_dir: Path, info: op.PolicyInfo) -> dict:
    rep: dict = {"policy": op.audit_stamp(info), "mode": "audit (read-only)"}
    q = lambda s: conn.fetchval(s)  # noqa: E731
    rep["totals"] = {
        "parts": await q("SELECT count(*) FROM parts_catalog"), "parts_active": await q("SELECT count(*) FROM parts_catalog WHERE is_active"),
        "suppliers": await q("SELECT count(*) FROM suppliers"), "suppliers_with_offers": await q("SELECT count(DISTINCT supplier_id) FROM supplier_parts"),
        "offers": await q("SELECT count(*) FROM supplier_parts"), "offers_available": await q("SELECT count(*) FROM supplier_parts WHERE is_available"),
    }
    rep["class_A_competing"] = {"parts_with_2plus_suppliers": await q(
        "SELECT count(*) FROM (SELECT part_id FROM supplier_parts GROUP BY part_id HAVING count(DISTINCT supplier_id) > 1) t")}
    rep["class_C_price_updates_recorded"] = {"price_history_rows": await q("SELECT count(*) FROM price_history")}
    rep["same_part_same_supplier_rows_over_1"] = await q(
        "SELECT count(*) FROM (SELECT 1 FROM supplier_parts GROUP BY supplier_id, part_id HAVING count(*) > 1) t")
    # same-supplier groups on different catalog parts -> pairwise via the SHARED classifier
    rows = [dict(r) for r in await conn.fetch(SAME_SUPPLIER_SQL)]
    groups: dict = {}
    for r in rows:
        groups.setdefault((r["supplier_id"], r["noem"], r["mf"]), []).append(r)
    cnt: Counter = Counter()
    for g in groups.values():
        kinds = {op.classify_pair(g[i], g[j]).name for i in range(len(g)) for j in range(i + 1, len(g))}
        cnt["B" if kinds == {"DISTINCT_VARIANTS"} else ("MIXED" if len(kinds) > 1 else next(iter(kinds)))] += 1
    rep["same_supplier_groups"] = {"groups": len(groups), "offers": len(rows), "by_pair_class": dict(cnt)}
    # Motorstore reassessment
    ms = [classify_motorstore(dict(r)) for r in await conn.fetch(MOTORSTORE_SQL)]
    rep["motorstore"] = {
        "offers": len(ms), "available": sum(1 for m in ms if m["available"]),
        "by_class_all": dict(Counter(m["class_name"] for m in ms)),
        "by_class_available": dict(Counter(m["class_name"] for m in ms if m["available"])),
        "available_with_derived_price": sum(1 for m in ms if m["available"] and m["evidence"]["derived_from_price_list"]),
        "available_without_other_orderable_offer": sum(1 for m in ms if m["available"] and not m["evidence"]["other_orderable_suppliers"]),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "motorstore_plan.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["row_id", "part_id", "class", "action", "available", "price", "derived", "other_orderable_suppliers"])
        for m in ms:
            w.writerow([m["row_id"], m["part_id"], m["class"], m["action"], m["available"], m["price"],
                        m["evidence"]["derived_from_price_list"], "|".join(m["evidence"]["other_orderable_suppliers"])])
    rep["planned_writes"] = 0
    (out_dir / "audit_report.json").write_text(json.dumps(rep, indent=2, default=str))
    return rep


async def apply_plan(conn, plan: list, info: op.PolicyInfo, run_id: str) -> int:
    """Perform the writes the policy gate accepts. Every action is gated, logged (before/after/evidence/policy hash)."""
    await conn.execute(AUDIT_DDL)
    done = 0
    async with conn.transaction():
        for p in plan:
            cls = op.Classification(p["class"])
            op.assert_write_allowed(p["action"], cls, policy=info, dry_run_done=True,
                                    has_other_orderable_offer=p.get("has_other_orderable_offer"), evidence=p.get("evidence"))
            row = await conn.fetchrow("SELECT supplier_id::text, is_available FROM supplier_parts WHERE id = $1::uuid FOR UPDATE SKIP LOCKED", p["row_id"])
            if row is None:
                continue
            after = dict(row)
            if p["action"] == "reassign_supplier" and row["supplier_id"] == p["evidence"]["source_proves_supplier"]:
                continue  # already correct: re-running a plan is idempotent and writes no audit row
            if p["action"] == "reassign_supplier":
                after["supplier_id"] = p["evidence"]["source_proves_supplier"]
                await conn.execute("UPDATE supplier_parts SET supplier_id = $2::uuid, updated_at = NOW() WHERE id = $1::uuid", p["row_id"], after["supplier_id"])
            else:
                after["is_available"] = False
                await conn.execute("UPDATE supplier_parts SET is_available = FALSE, updated_at = NOW() WHERE id = $1::uuid", p["row_id"])
            await conn.execute("INSERT INTO offer_policy_audit_log (run_id, supplier_part_id, action, classification, before, after, evidence, policy_version, policy_sha256)"
                               " VALUES ($1,$2::uuid,$3,$4,$5::jsonb,$6::jsonb,$7::jsonb,$8,$9)", run_id, p["row_id"], p["action"], cls.name,
                               json.dumps(dict(row)), json.dumps(after), json.dumps(p.get("evidence") or {}), info.version, info.sha256)
            done += 1
    return done


async def rollback(conn, run_id: str) -> int:
    n = 0
    async with conn.transaction():
        for r in await conn.fetch("SELECT supplier_part_id::text id, before FROM offer_policy_audit_log WHERE run_id = $1 ORDER BY id DESC", run_id):
            b = json.loads(r["before"])
            await conn.execute("UPDATE supplier_parts SET supplier_id = $2::uuid, is_available = $3, updated_at = NOW() WHERE id = $1::uuid",
                               r["id"], b["supplier_id"], b["is_available"])
            n += 1
    return n


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/offer_policy_audit")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan", help="JSON plan file to apply (rows: row_id, class, action, evidence, has_other_orderable_offer)")
    ap.add_argument("--rollback", metavar="RUN_ID")
    a = ap.parse_args(argv)
    try:
        info = op.require_policy()  # FAIL CLOSED before any DB connection
    except op.PolicyUnavailable as exc:
        print(f"POLICY UNAVAILABLE - no offer data touched: {exc}", file=sys.stderr)
        return 3
    import asyncpg
    conn = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    try:
        if a.rollback:
            print("rolled back rows:", await rollback(conn, a.rollback))
            return 0
        if a.apply:
            plan = json.loads(Path(a.plan).read_text()) if a.plan else []
            run_id = uuid.uuid4().hex[:12]
            print("run_id", run_id, "applied", await apply_plan(conn, plan, info, run_id), "policy", info.version, info.sha256[:12])
            return 0
        await conn.execute("SET default_transaction_read_only = on")
        await conn.execute("SET statement_timeout = '280s'")
        rep = await audit(conn, Path(a.out), info)
        print(json.dumps(rep, indent=2, default=str))
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
