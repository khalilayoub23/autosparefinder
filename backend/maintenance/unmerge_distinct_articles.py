"""
Script:  maintenance/unmerge_distinct_articles.py
Purpose: Undo merges of DIFFERENT aftermarket articles that merge_master_parts made before 2026-11-14 (it matched part
         numbers after stripping dots/dashes/spaces, so REMSA 1187.02 brake pads were merged into a TOPRAN 118 702 gear
         cable). Also restores the fitment of a part that policy kept active but whose fitment was moved to the master.
Process:
  1. `--manifest`: READ-ONLY. Finds every (loser -> master) pair recorded by the merge (`dedup_merged_into` on the
     loser, or an ACTIVE part named in a master's `merged_from`) that `part_identity.distinct_articles` proves to be
     two different articles, and lists exactly what would be moved back:
       - offers on the master whose supplier_sku is the LOSER's number (and not the master's);
       - fitment rows on the master created with the loser (same instant as the loser row), or created before the
         master existed when this loser is the master's only merged part;
       - images by the same time rule;
       - master fields that equal the loser's value (description / name_he / barcode / compatible_vehicles): copied
         by the merge's enrichment.
     Anything it cannot attribute is listed under `unattributed` and is NOT touched.
  2. `--apply --manifest FILE --expect-sha256 H --approval-ref REF`: applies exactly that manifest, one transaction
     per pair, re-checking every precondition (pair still in the recorded state, each row still where the manifest
     saw it). A pair whose state changed, or whose rows are locked, is skipped and reported. Writes a result file
     with the previous values of everything it changed (rollback source).
Data Imported/Modified: supplier_parts.part_id (moved back, never deleted, price untouched), part_vehicle_fitment
         (moved back; a row whose key the loser already has is deleted from the master and recorded),
         parts_images.part_id, parts_catalog (loser reactivated + provenance; master: merged_from entry removed and
         copied fields cleared).
Data Sources: none (catalogue DB only).
Missing Data Delegation: master PRICE fields raised by the merge (base/min/max) are not recomputed here - the nightly
         price jobs own them; fitment added to the loser after its creation and before the merge is not attributable
         and is reported, not moved.
Last Updated: 2026-11-14
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import offer_policy  # noqa: E402
import part_identity  # noqa: E402

ACTOR = "maintenance/unmerge_distinct_articles.py"
DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
OUT_DIR = Path(os.getenv("STATE_DIR", "/app/state")) / "unmerge_distinct"
SAME_INSTANT = timedelta(seconds=5)
COPIED_FIELDS = ("description", "name_he", "barcode", "compatible_vehicles")

PAIRS_SQL = """
    SELECT l.id AS loser, w.id AS master, FALSE AS loser_active
    FROM parts_catalog l JOIN parts_catalog w ON w.id = (l.specifications->>'dedup_merged_into')::uuid
    WHERE NOT l.is_active AND l.specifications ? 'dedup_merged_into'
      AND upper(btrim(l.oem_number)) <> upper(btrim(w.oem_number))
    UNION
    SELECT l.id, m.master, TRUE
    FROM (SELECT id AS master, jsonb_array_elements_text(specifications->'merged_from')::uuid AS loser
          FROM parts_catalog WHERE is_active AND specifications ? 'merged_from') m
    JOIN parts_catalog l ON l.id = m.loser AND l.is_active
"""


def _naive(ts):
    """UTC without tzinfo: the three tables do not all store created_at with a time zone."""
    if ts is None:
        return None
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts


def _raw(v: Any) -> str:
    return part_identity.raw_number(v)


async def _part(conn, pid) -> Optional[Dict[str, Any]]:
    r = await conn.fetchrow(
        "SELECT id, sku, name, oem_number, is_active, created_at, specifications, description, name_he, barcode, "
        "compatible_vehicles, part_type, manufacturer_id, manufacturer FROM parts_catalog WHERE id = $1", pid)
    if not r:
        return None
    d = dict(r)
    for k in ("specifications", "compatible_vehicles"):
        if isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


async def build_pair_plan(conn, loser_id, master_id) -> Optional[Dict[str, Any]]:
    """The read-only plan for one pair, or None when the pair is not a provable wrong merge."""
    l, w = await _part(conn, loser_id), await _part(conn, master_id)
    if not l or not w or not w["is_active"]:
        return None
    if not part_identity.distinct_articles(l, w):
        return None
    lspec = l["specifications"] if isinstance(l["specifications"], dict) else {}
    wspec = w["specifications"] if isinstance(w["specifications"], dict) else {}
    merged_into = lspec.get("dedup_merged_into") == str(w["id"])
    in_merged_from = str(l["id"]) in [str(x) for x in (wspec.get("merged_from") or [])]
    if not (merged_into or (l["is_active"] and in_merged_from)):
        return None
    lraw, wraw = _raw(l["oem_number"]), _raw(w["oem_number"])
    only_loser = [str(x) for x in (wspec.get("merged_from") or [])] in ([str(l["id"])], [])

    offers, offers_conflict = [], []
    loser_keys = {(r["supplier_id"], r["part_type"]) for r in await conn.fetch(
        "SELECT supplier_id, part_type FROM supplier_parts WHERE part_id = $1", l["id"])}
    for o in await conn.fetch("SELECT id, supplier_id, supplier_sku, part_type FROM supplier_parts WHERE part_id = $1", w["id"]):
        sraw = _raw(o["supplier_sku"])
        if sraw and sraw == lraw and sraw != wraw:
            (offers_conflict if (o["supplier_id"], o["part_type"]) in loser_keys else offers).append(str(o["id"]))

    lc, wc = _naive(l["created_at"]), _naive(w["created_at"])

    def _is_losers(created) -> bool:
        created = _naive(created)
        if created is None or lc is None:
            return False
        near_l = abs(created - lc) <= SAME_INSTANT
        near_w = wc is not None and abs(created - wc) <= SAME_INSTANT
        if near_l and not near_w:
            return True
        return bool(only_loser and wc is not None and created < wc - SAME_INSTANT)

    loser_fit = {(r["manufacturer"], r["model"], r["year_from"]) for r in await conn.fetch(
        "SELECT manufacturer, model, year_from FROM part_vehicle_fitment WHERE part_id = $1", l["id"])}
    fit_move, fit_dup, fit_total = [], [], 0
    for f in await conn.fetch("SELECT id, manufacturer, model, year_from, created_at FROM part_vehicle_fitment WHERE part_id = $1", w["id"]):
        fit_total += 1
        if _is_losers(f["created_at"]):
            (fit_dup if (f["manufacturer"], f["model"], f["year_from"]) in loser_fit else fit_move).append(str(f["id"]))

    loser_imgs = {r["url"] for r in await conn.fetch("SELECT url FROM parts_images WHERE part_id = $1", l["id"])}
    img_move = [str(i["id"]) for i in await conn.fetch("SELECT id, url, created_at FROM parts_images WHERE part_id = $1", w["id"])
                if _is_losers(i["created_at"]) and i["url"] not in loser_imgs]

    clear = [f for f in COPIED_FIELDS if l.get(f) not in (None, "", [], {}) and w.get(f) == l.get(f)]
    return {
        "loser": str(l["id"]), "master": str(w["id"]), "loser_sku": l["sku"], "master_sku": w["sku"],
        "loser_name": (l["name"] or "")[:90], "master_name": (w["name"] or "")[:90],
        "loser_active": bool(l["is_active"]), "reactivate": not l["is_active"],
        "offers_move": offers, "offers_conflict": offers_conflict,
        "fitment_move": fit_move, "fitment_delete_duplicate": fit_dup, "images_move": img_move,
        "master_fields_clear": clear,
        "unattributed": {"master_fitment_rows_left": fit_total - len(fit_move) - len(fit_dup),
                         "same_raw_number": lraw == wraw,       # offers cannot be told apart by supplier SKU: none is moved
                         "loser_has_no_offer_after": not offers and not loser_keys},
    }


async def apply_pair(conn, plan: Dict[str, Any]) -> Dict[str, Any]:
    """Apply one pair inside the CALLER's transaction. Returns the previous values (rollback source). Raises
    RuntimeError('precondition: ...') when the recorded state changed - the caller rolls the pair back."""
    lid, wid = plan["loser"], plan["master"]
    rows = await conn.fetch("SELECT id, is_active, specifications FROM parts_catalog WHERE id = ANY($1::uuid[]) FOR UPDATE NOWAIT", [lid, wid])
    by = {str(r["id"]): r for r in rows}
    if len(by) != 2:
        raise RuntimeError("precondition: part missing")
    fresh = await build_pair_plan(conn, lid, wid)
    if fresh is None:
        raise RuntimeError("precondition: pair is no longer a recorded wrong merge")
    for k in ("offers_move", "fitment_move", "fitment_delete_duplicate", "images_move", "master_fields_clear", "reactivate"):
        if fresh[k] != plan[k]:
            raise RuntimeError(f"precondition: {k} changed since the manifest")
    prev: Dict[str, Any] = {"loser": lid, "master": wid}

    if plan["offers_move"]:
        n = await conn.execute("UPDATE supplier_parts SET part_id = $1, updated_at = NOW() WHERE id = ANY($2::uuid[]) AND part_id = $3",
                               lid, plan["offers_move"], wid)
        prev["offers_moved"] = int(n.split()[-1])
    if plan["fitment_delete_duplicate"]:
        gone = await conn.fetch("DELETE FROM part_vehicle_fitment WHERE id = ANY($1::uuid[]) AND part_id = $2 RETURNING *",
                                plan["fitment_delete_duplicate"], wid)
        prev["fitment_deleted_rows"] = [json.loads(json.dumps(dict(r), default=str)) for r in gone]
    if plan["fitment_move"]:
        n = await conn.execute("UPDATE part_vehicle_fitment SET part_id = $1 WHERE id = ANY($2::uuid[]) AND part_id = $3",
                               lid, plan["fitment_move"], wid)
        prev["fitment_moved"] = int(n.split()[-1])
    if plan["images_move"]:
        n = await conn.execute("UPDATE parts_images SET part_id = $1 WHERE id = ANY($2::uuid[]) AND part_id = $3",
                               lid, plan["images_move"], wid)
        prev["images_moved"] = int(n.split()[-1])

    if plan["master_fields_clear"]:
        old = await conn.fetchrow(f"SELECT {', '.join(plan['master_fields_clear'])} FROM parts_catalog WHERE id = $1", wid)
        prev["master_fields_before"] = json.loads(json.dumps(dict(old), default=str))
        await conn.execute(f"UPDATE parts_catalog SET {', '.join(f + ' = NULL' for f in plan['master_fields_clear'])} WHERE id = $1", wid)
    await conn.execute(
        """UPDATE parts_catalog
              SET specifications = jsonb_set(COALESCE(specifications, '{}'::jsonb), '{merged_from}',
                      COALESCE((SELECT jsonb_agg(x) FROM jsonb_array_elements(COALESCE(specifications->'merged_from', '[]'::jsonb)) x
                                WHERE x <> to_jsonb($2::text)), '[]'::jsonb)),
                  updated_at = NOW()
            WHERE id = $1""", wid, lid)
    await conn.execute(
        """UPDATE parts_catalog
              SET is_active = TRUE,
                  specifications = (COALESCE(specifications, '{}'::jsonb) - 'dedup_merged_into')
                                   || jsonb_build_object('unmerged_from', $2::text, 'unmerged_at', NOW()::text),
                  updated_at = NOW()
            WHERE id = $1""", lid, wid)
    return prev


async def build_manifest(pairs_file: Optional[str] = None) -> Dict[str, Any]:
    """`pairs_file` (JSON list of {"loser", "master"}): plan only these recorded merges - used for wrong merges whose RAW
    numbers are identical (ERA 550348 sensor merged into NRF 550348 radiator), which PAIRS_SQL deliberately does not
    scan (that would be every merge ever made). Every pair still has to pass build_pair_plan's proof."""
    conn = await asyncpg.connect(DB, statement_cache_size=0)
    try:
        await conn.execute("SET statement_timeout = '600s'")
        async with conn.transaction(readonly=True):
            if pairs_file:
                pairs = [{"loser": x["loser"], "master": x["master"]} for x in json.loads(Path(pairs_file).read_text())]
            else:
                pairs = await conn.fetch(PAIRS_SQL)
            plans = []
            for p in pairs:
                plan = await build_pair_plan(conn, p["loser"], p["master"])
                if plan:
                    plans.append(plan)
    finally:
        await conn.close()
    plans.sort(key=lambda x: (x["master"], x["loser"]))
    return {"tool": ACTOR, "candidate_pairs": len(pairs), "pairs": plans}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _totals(plans: List[Dict[str, Any]]) -> Dict[str, int]:
    return {"pairs": len(plans), "reactivate": sum(p["reactivate"] for p in plans),
            "offers_move": sum(len(p["offers_move"]) for p in plans),
            "offers_conflict": sum(len(p["offers_conflict"]) for p in plans),
            "fitment_move": sum(len(p["fitment_move"]) for p in plans),
            "fitment_delete_duplicate": sum(len(p["fitment_delete_duplicate"]) for p in plans),
            "images_move": sum(len(p["images_move"]) for p in plans),
            "master_fields_clear": sum(len(p["master_fields_clear"]) for p in plans),
            "pairs_with_unattributed_master_fitment": sum(1 for p in plans if p["unattributed"]["master_fitment_rows_left"] > 0),
            "losers_without_any_offer": sum(1 for p in plans if p["unattributed"]["loser_has_no_offer_after"])}


async def apply_manifest(manifest: Dict[str, Any], limit: Optional[int]) -> Dict[str, Any]:
    conn = await asyncpg.connect(DB, statement_cache_size=0)
    done, skipped = [], []
    try:
        await conn.execute("SET lock_timeout = '5s'")
        for plan in manifest["pairs"][: limit or None]:
            try:
                async with conn.transaction():
                    await conn.execute("SET LOCAL statement_timeout = '60s'")
                    done.append(await apply_pair(conn, plan))
            except Exception as exc:
                skipped.append({"loser": plan["loser"], "master": plan["master"], "why": f"{type(exc).__name__}: {str(exc)[:160]}"})
    finally:
        await conn.close()
    return {"applied": done, "skipped": skipped}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", action="store_true", help="read-only: write the manifest and print totals")
    ap.add_argument("--pairs-file", help="with --manifest: JSON list of {loser, master} to plan instead of the full scan")
    ap.add_argument("--apply", metavar="MANIFEST_FILE")
    ap.add_argument("--expect-sha256")
    ap.add_argument("--approval-ref")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if a.manifest:
        offer_policy.gate_or_exit(ACTOR, True)
        m = asyncio.run(build_manifest(a.pairs_file))
        m["totals"] = _totals(m["pairs"])
        out = OUT_DIR / f"manifest_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
        out.write_text(json.dumps(m, ensure_ascii=False, indent=1))
        print(json.dumps({"manifest": str(out), "sha256": _sha(out), "candidate_pairs": m["candidate_pairs"], **m["totals"]}, indent=1))
        return 0
    if a.apply:
        path = Path(a.apply)
        if not a.expect_sha256 or _sha(path) != a.expect_sha256:
            print("refusing: --expect-sha256 missing or does not match the manifest file", file=sys.stderr)
            return 2
        if not (a.approval_ref or "").strip():
            print("refusing: --approval-ref (the owner's approval) is required", file=sys.stderr)
            return 2
        offer_policy.gate_or_exit(ACTOR, False)
        res = asyncio.run(apply_manifest(json.loads(path.read_text()), a.limit))
        res.update({"manifest": str(path), "manifest_sha256": a.expect_sha256, "approval_ref": a.approval_ref})
        out = OUT_DIR / f"result_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
        print(json.dumps({"result": str(out), "applied": len(res["applied"]), "skipped": len(res["skipped"]),
                          "skip_reasons": sorted({s["why"][:60] for s in res["skipped"]})[:8]}, indent=1))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
