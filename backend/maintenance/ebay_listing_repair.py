#!/usr/bin/env python3
"""
Script:  maintenance/ebay_listing_repair.py
Purpose: ONE-OFF, bounded repair after the pre-guard eBay refresh (2026-11-18). Brings the offers that run touched, and the
         offers the first scheduled run could not match (799 "other listing" + 210 "no listing"), through the Listing
         Identity Guard WITHOUT a second write path: it only (a) corrects the stored listing URL of offers a pre-guard
         run swapped to another listing when the ORIGINAL listing is still live and verified, and (b) makes the offers due
         so the normal guarded refresh (`services.ebay_price_sync.sync_ebay_prices`) judges and prices them.
Process: 1. --plan   (default, read-only): classify targets from the call log + backup rows, print counts.
         2. --apply  : back the affected rows up to --backup-dir, fix the 13 swapped URLs, queue the rest as due, then run ONE
                       guarded refresh limited to the queued offers (EBAY_REFRESH_LIMIT) with no discovery pass.
Data Imported/Modified: supplier_parts.supplier_url (swapped offers only, only to the ORIGINAL listing after verification),
         supplier_offer_checks (queue + verdicts), and whatever the guarded refresh writes (price, shipping, price_history).
Data Sources: backup autospare-20261118.sql.gz extract (old rows TSV), scraper_api_calls, live eBay Browse API.
Missing Data Delegation: an offer whose identity cannot be verified is marked non-purchasable with its exact verdict; nothing is guessed.
Last Updated: 2026-11-19
"""
import argparse
import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone

from sqlalchemy import text

from BACKEND_DATABASE_MODELS import async_session_factory
from services import ebay_listing_identity as guard
from services.ebay_listing_identity import Stored, legacy_item_id
from services.ebay_price_sync import _mark_offer_unavailable, _record_check
import services.ebay_price_sync as sync

WINDOW = ("2026-11-18 23:14", "2026-11-19 00:46")     # the first scheduled run on the refresh code, UTC


def read_old_rows(path: str) -> dict:
    """supplier_parts rows as they were in the 2026-11-18 04:03 backup (before the pre-guard run)."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) > 16 and re.match(r"[0-9a-f-]{36}$", p[0]):
                out[p[0]] = {"url": p[16], "price_ils": p[5], "ship_ils": p[7]}
    return out


async def classify_window_offers(db) -> dict:
    """The 799 (a listing came back, but not the stored one) and the 210 (no listing at all) of the 2026-11-19 01:14 IL run."""
    rows = (await db.execute(text("""
        WITH calls AS (SELECT part_id, max(results_count) rc FROM scraper_api_calls
                       WHERE created_at BETWEEN :a AND :b AND source ILIKE '%ebay%' GROUP BY part_id),
        off AS (SELECT sp.id, sp.part_id FROM supplier_parts sp JOIN suppliers s ON s.id = sp.supplier_id AND s.name = 'eBay Motors'
                WHERE sp.part_type IS NULL AND sp.price_ils > 0 AND sp.last_checked_at BETWEEN :a AND :b),
        upd AS (SELECT DISTINCT ph.supplier_part_id FROM price_history ph WHERE ph.source = 'ebay_sync' AND ph.created_at BETWEEN :a AND :b)
        SELECT o.id::text AS id, CASE WHEN c.rc = 0 THEN 'no_listing' ELSE 'other_listing' END AS cls
        FROM off o JOIN calls c ON c.part_id = o.part_id LEFT JOIN upd u ON u.supplier_part_id = o.id
        WHERE u.supplier_part_id IS NULL"""), {"a": datetime.fromisoformat(WINDOW[0]), "b": datetime.fromisoformat(WINDOW[1])})).fetchall()
    out = {"no_listing": [], "other_listing": []}
    for r in rows:
        out[r.cls].append(r.id)
    return out


async def decide_swapped(db, ebay, offer_id: str, old: dict) -> dict:
    """The pre-guard run replaced the offer's listing. Restore the ORIGINAL one only if it is live and passes the candidate
    checks; otherwise the current (swapped-in) listing must pass them; otherwise the offer stops being purchasable."""
    row = (await db.execute(text("SELECT sp.supplier_url, pc.oem_number, pc.manufacturer FROM supplier_parts sp JOIN parts_catalog pc ON pc.id = sp.part_id "
                                 "WHERE sp.id = CAST(:i AS uuid)"), {"i": offer_id})).one()
    old_id, cur_id = legacy_item_id(old["url"]), legacy_item_id(row.supplier_url)
    if old_id == cur_id:
        return {"offer": offer_id, "action": "same_listing_queue"}
    lo = await ebay.get_listing(old_id)
    if lo.status == "error":
        return {"offer": offer_id, "action": "unverified_api_error"}                      # nothing changes; retried by the normal flow
    if lo.status == "ok":
        v = guard.check_candidate(row.oem_number, row.manufacturer, lo, existing_item_id=cur_id, existing_verified_missing=True)
        if v.ok:
            return {"offer": offer_id, "action": "restore_original", "url": str((lo.raw or {}).get("itemWebUrl") or old["url"])[:1000],
                    "old_item": old_id, "verdict": v.code}
    lc = await ebay.get_listing(cur_id)
    if lc.status == "ok":
        v = guard.check_candidate(row.oem_number, row.manufacturer, lc, existing_item_id=cur_id, existing_verified_missing=True)
        if v.ok:
            return {"offer": offer_id, "action": "keep_current_verified", "verdict": v.code}
        return {"offer": offer_id, "action": "mark_unavailable", "verdict": v.code, "reason": v.reason}
    return {"offer": offer_id, "action": "mark_unavailable", "verdict": guard.MISSING_LISTING if lc.status == "ended" else guard.AMBIGUOUS_IDENTITY,
            "reason": "neither the original nor the swapped-in listing is live"}


async def main(args) -> None:
    old = read_old_rows(args.old_rows)
    async with async_session_factory() as db:
        window = await classify_window_offers(db)
        plan = {"swapped_or_changed_pre_guard": len(old), "window_no_listing": len(window["no_listing"]),
                "window_other_listing": len(window["other_listing"])}
        print(json.dumps({"plan": plan}))
        if not args.apply:
            return
        ids = sorted(set(old) | set(window["no_listing"]) | set(window["other_listing"]))
        os.makedirs(args.backup_dir, exist_ok=True)
        rows = (await db.execute(text("SELECT * FROM supplier_parts WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids})).mappings().all()
        bpath = os.path.join(args.backup_dir, f"supplier_parts_before_repair_{int(time.time())}.json")
        json.dump([dict(r) for r in rows], open(bpath, "w"), default=str)
        print("backup", bpath, len(rows))

        from services.suppliers.ebay_supplier import EbaySupplier
        ebay = EbaySupplier()
        decisions = []
        for oid, o in old.items():
            d = await decide_swapped(db, ebay, oid, o)
            decisions.append(d)
            if d["action"] == "restore_original":
                await db.execute(text("UPDATE supplier_parts SET supplier_url = :u, updated_at = NOW() WHERE id = CAST(:i AS uuid)"),
                                 {"u": d["url"], "i": oid})
            elif d["action"] == "mark_unavailable":
                await _mark_offer_unavailable(db, oid)
                await _record_check(db, oid, guard.Verdict(d["verdict"], d["reason"], 7), None, source="ebay_listing_repair")
            await db.commit()
            await asyncio.sleep(0.2)
        # queue everything else as DUE for the guarded refresh (the normal refresh path judges + prices them)
        queue = [i for i in ids if not any(d["offer"] == i and d["action"] in ("mark_unavailable", "unverified_api_error") for d in decisions)]
        for oid in queue:
            await db.execute(text("""INSERT INTO supplier_offer_checks (supplier_part_id, verdict, reason, source, checked_at, retry_after, detail)
                VALUES (CAST(:i AS uuid), 'ambiguous_identity', 'queued for the guarded re-check (pre-guard result unverified)', 'ebay_listing_repair',
                        NOW(), '-infinity'::timestamptz, '{}'::jsonb)
                ON CONFLICT (supplier_part_id) DO UPDATE SET retry_after = '-infinity'::timestamptz, source = 'ebay_listing_repair'"""), {"i": oid})
        await db.commit()
        print(json.dumps({"decisions": {a: sum(1 for d in decisions if d["action"] == a) for a in {d["action"] for d in decisions}}, "queued": len(queue)}))
        os.environ["EBAY_REFRESH_LIMIT"] = str(len(queue) + 50)
        os.environ["EBAY_RUN_CALL_BUDGET"] = str(args.call_budget)
        rep = await sync.sync_ebay_prices(db, limit_per_run=0)       # discovery pass limited to 0 parts: refresh only
        keep = {k: rep[k] for k in ("refresh_checked", "refresh_updated", "refresh_unchanged", "refresh_no_listing", "refresh_listing_changed",
                                    "calls_used", "call_budget_hit", "price_history_rows", "parts_indexed", "index_failures", "refresh_verdicts")}
        print(json.dumps({"guarded_refresh": keep, "errors": rep["errors"][:5]}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-rows", required=True, help="TSV of supplier_parts rows from the 2026-11-18 04:03 backup")
    ap.add_argument("--backup-dir", default="/app/state/ebay_repair_backup")
    ap.add_argument("--call-budget", type=int, default=1500)
    ap.add_argument("--apply", action="store_true")
    asyncio.run(main(ap.parse_args()))
