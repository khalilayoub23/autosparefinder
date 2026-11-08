"""
Script: restore_merged_offers_133.py
Purpose: restore the supplier offers that the old merge_master_parts conflict branch deleted when it soft-merged a duplicate catalog
         part into its canonical part (docs/OFFER_HISTORICAL_INTEGRITY_AUDIT_2026-11-06.md, section 5/8). DRY RUN BY DEFAULT.
         Nothing is ever overwritten: a restored row is a NEW insert, unavailable, with its original id.
Process:
  1. gate_or_exit(): the offer-deduplication policy must be loadable (fails closed, exit 3, before any DB connection on --apply).
  2. Preflight (--apply only is blocked, a dry run just reports it): REFUSE (exit 4) while the OLD unique index
     uq_supplier_parts_part_supplier(part_id, supplier_id) exists (it forbids a 2nd same-supplier offer on the canonical part) and
     REFUSE unless the replacement partial unique indexes uq_sp_part_supplier_variant / uq_sp_part_supplier_untyped exist and are valid.
  3. Input = a TSV of the ORIGINAL rows taken from the 2026-06-30 catalog dump (header line "#cols:..."); per row the DB is
     re-checked: the original part is soft-merged (specifications.dedup_merged_into, followed to the active canonical part), the
     canonical part holds an offer of the SAME supplier, the original id and (supplier_id, supplier_sku) are absent, and the pair is
     classified by offer_policy.classify_pair against every same-supplier offer on the canonical part: only DISTINCT_VARIANTS or
     UNRESOLVED (class B/F) is restorable; a TRUE_DUPLICATE/PRICE_UPDATE/competing result is never written.
     Variants are compared ONLY through the central canonical-variant function (policy 1a) before any uniqueness check; a label it
     does not recognise (or an existing offer with such a label) is not restorable. --apply additionally requires
     --approval-ref, --expect-input-sha256 (must equal the input file's hash) and --source-backup, and passes them to the write
     gate (policy 5a R1/R2); exit 5 otherwise.
  4. --apply: bounded batches, one transaction per batch, a SAVEPOINT per row, INSERT ... ON CONFLICT DO NOTHING (SKU constraint and
     both part+supplier indexes are respected, never overwritten). The row is inserted on the CANONICAL part as is_available=FALSE,
     availability='out_of_stock', last_checked_at=NULL (price is the 2026-06-30 value: STALE until the supplier's own sync refreshes
     it), part_type normalised. The inserted ids are written to a rollback manifest BEFORE each batch commits.
  5. --rollback MANIFEST (dry run unless --apply): deletes exactly the manifest ids, and only while the row is still the restored
     one (same part, supplier, SKU, still unavailable, no price_history row). Anything changed since is reported and left alone.
     This reverses this script's own insert; it is not an offer-policy "delete" of someone else's offer.
Data Imported/Modified: supplier_parts (INSERT of restored offers; DELETE of exactly the manifest ids on --rollback). NO price_history
         writes, no catalog row changes, no DDL, no audit table (provenance = the manifest + availability='out_of_stock' + NULL last_checked_at).
Data Sources: input TSV (candidates/offer-policy-2026-11-06/restore_merged_offers_133_input.tsv, from backups/catalog/monthly/autospare-202606.sql.gz).
Missing Data Delegation: a row that cannot be proven restorable stays unrestored with a named reason.
Last Updated: 2026-11-07
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import decimal
import hashlib
import json
import os
import sys
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import offer_policy as op  # noqa: E402

# Canonical variant identity (policy 1a): ONE shared function, applied BEFORE every uniqueness/duplicate check.
# `variant_identity` is the central module; until it is deployed the apply path refuses (a dry run reports the fallback).
try:
    from variant_identity import KNOWN_VARIANTS, canonical_variant  # noqa: E402
    VARIANT_API = "variant_identity"
except ImportError:  # pragma: no cover - exercised only before the central module exists
    from offer_classification import normalize_type as canonical_variant  # noqa: E402
    KNOWN_VARIANTS = frozenset({"oem", "oe_equivalent", "aftermarket", "remanufactured", "used"})
    VARIANT_API = "offer_classification.normalize_type (FALLBACK - apply refused)"

ACTOR = "maintenance/restore_merged_offers_133.py"
OLD_INDEX = "uq_supplier_parts_part_supplier"
NEW_INDEXES = ("uq_sp_part_supplier_variant", "uq_sp_part_supplier_untyped")
RESTORABLE = (op.Classification.DISTINCT_VARIANTS, op.Classification.UNRESOLVED)
UUID_COLS = {"id", "supplier_id", "part_id"}
NUM_COLS = {"price_usd", "price_ils", "shipping_cost_usd", "shipping_cost_ils", "express_price_ils"}
INT_COLS = {"warranty_months", "estimated_delivery_days", "stock_quantity", "min_order_qty", "express_delivery_days"}
BOOL_COLS = {"is_available", "express_available"}
TS_COLS = {"last_checked_at", "created_at", "last_in_stock_at", "express_last_checked", "updated_at"}


# ------------------------------------------------------------------ input parsing
def _unescape(s: str):
    """COPY text format: \\N is NULL; backslash escapes."""
    if s == "\\N":
        return None
    if "\\" not in s:
        return s
    out, i = [], 0
    m = {"t": "\t", "n": "\n", "r": "\r", "\\": "\\", "b": "\b", "f": "\f", "v": "\v"}
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            out.append(m.get(s[i + 1], s[i + 1]))
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _value(col: str, raw):
    if raw is None:
        return None
    if col in NUM_COLS:
        return decimal.Decimal(raw)
    if col in INT_COLS:
        return int(raw)
    if col in BOOL_COLS:
        return raw == "t"
    if col in TS_COLS:
        return dt.datetime.fromisoformat(raw)
    return raw


def load_input(path: Path) -> tuple[list[str], list[dict], str]:
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    lines = data.decode("utf-8").split("\n")
    if not lines or not lines[0].startswith("#cols:"):
        raise ValueError("input must start with a '#cols:' header line")
    cols = lines[0][len("#cols:"):].split("\t")
    rows = []
    for ln in lines[1:]:
        if not ln:
            continue
        parts = ln.split("\t")
        if len(parts) != len(cols):
            raise ValueError(f"row has {len(parts)} fields, header has {len(cols)}: {parts[0]}")
        rows.append({c: _value(c, _unescape(v)) for c, v in zip(cols, parts)})
    return cols, rows, sha


# ------------------------------------------------------------------ database checks
async def index_state(conn) -> dict:
    rows = await conn.fetch(
        "SELECT c.relname AS name, i.indisvalid AS valid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE i.indrelid = 'supplier_parts'::regclass")
    st = {r["name"]: bool(r["valid"]) for r in rows}
    return {"old_index_present": OLD_INDEX in st,
            "new_indexes_valid": all(st.get(n) is True for n in NEW_INDEXES),
            "indexes": {n: st.get(n) for n in (OLD_INDEX,) + NEW_INDEXES}}


def refusal(state: dict):
    if state["old_index_present"]:
        return (f"REFUSING --apply: the old unique index {OLD_INDEX}(part_id, supplier_id) still exists, so a second offer of the same "
                f"supplier on the canonical part cannot be stored. It is dropped only by the separate stage-3 approval.")
    if not state["new_indexes_valid"]:
        return (f"REFUSING --apply: the replacement indexes {', '.join(NEW_INDEXES)} must exist and be valid before restoring "
                f"same-supplier offers (they keep one offer per supplier+part+variant).")
    return None


def _variant(part_type):
    """Canonical variant of a label via the shared function; None = blank/unknown-as-untyped."""
    t = canonical_variant(part_type)
    return None if t in (None, "", "unknown", "none", "null") else t


def _label_known(part_type) -> bool:
    """A non-blank label that the shared function does not map into the canonical vocabulary is NOT restorable (policy 1a)."""
    if part_type is None or str(part_type).strip() == "":
        return True
    return _variant(part_type) in KNOWN_VARIANTS


async def resolve_canonicals(conn, part_ids: list[str]) -> dict:
    """original part id -> (canonical id, canonical part_type) following dedup_merged_into to an ACTIVE part; None if not provable."""
    out: dict = {}
    cur = {p: p for p in part_ids}
    for _ in range(6):
        ids = list({v for v in cur.values()})
        info = {str(r["id"]): r for r in await conn.fetch(
            "SELECT id, is_active, specifications->>'dedup_merged_into' AS mi, part_type FROM parts_catalog WHERE id = ANY($1::uuid[])", ids)}
        nxt = {}
        for orig, c in cur.items():
            r = info.get(c)
            if r is None:
                out[orig] = ("canonical_missing", None)
            elif orig == c:  # the original part itself: must be a soft-merged loser
                if r["is_active"] or not r["mi"]:
                    out[orig] = ("part_not_soft_merged", None)
                else:
                    nxt[orig] = str(r["mi"])
            elif r["is_active"]:
                out[orig] = (str(r["id"]), r["part_type"])
            elif r["mi"]:
                nxt[orig] = str(r["mi"])
            else:
                out[orig] = ("canonical_inactive_unmerged", None)
        cur = nxt
        if not cur:
            break
    for orig in cur:
        out[orig] = ("merge_chain_too_long", None)
    return out


async def evaluate(conn, rows: list[dict]) -> list[dict]:
    """Attach a verdict to every input row: status in {eligible, <named skip reason>}. Read-only."""
    canon = await resolve_canonicals(conn, list({r["part_id"] for r in rows}))
    exist_ids = {str(r["id"]) for r in await conn.fetch("SELECT id FROM supplier_parts WHERE id = ANY($1::uuid[])", [r["id"] for r in rows])}
    sku_taken = {(str(r["supplier_id"]), r["supplier_sku"]) for r in await conn.fetch(
        "SELECT sp.supplier_id, sp.supplier_sku FROM supplier_parts sp JOIN unnest($1::uuid[], $2::text[]) u(s, k) "
        "ON sp.supplier_id = u.s AND sp.supplier_sku = u.k", [r["supplier_id"] for r in rows], [r["supplier_sku"] or "" for r in rows])}
    pairs = [(c[0], r["supplier_id"]) for r in rows if (c := canon.get(r["part_id"])) and len(c[0]) == 36]
    existing: dict = {}
    if pairs:
        for r in await conn.fetch(
                "SELECT sp.id, sp.supplier_id, sp.part_id, sp.supplier_sku, sp.part_type, sp.price_ils, sp.is_available, sp.supplier_url "
                "FROM supplier_parts sp JOIN unnest($1::uuid[], $2::uuid[]) u(p, s) ON sp.part_id = u.p AND sp.supplier_id = u.s",
                [p for p, _ in pairs], [s for _, s in pairs]):
            existing.setdefault((str(r["part_id"]), str(r["supplier_id"])), []).append(dict(r))
    out = []
    for r in rows:
        v = {"row": r, "canonical": None, "status": None, "class": None}
        c = canon.get(r["part_id"])
        if c is None or len(c[0]) != 36:
            v["status"] = c[0] if c else "part_unknown"
        elif r["id"] in exist_ids:
            v["status"] = "original_id_already_present"
        elif (r["supplier_id"], r["supplier_sku"]) in sku_taken:
            v["status"] = "supplier_sku_already_present"
        else:
            v["canonical"] = c[0]
            ex = existing.get((c[0], r["supplier_id"]), [])
            if not ex:
                v["status"] = "no_same_supplier_offer_on_canonical"
            elif not _label_known(r["part_type"]):
                v["status"] = "variant_label_unrecognized"
            elif any(not _label_known(e["part_type"]) for e in ex):
                v["status"] = "existing_variant_label_unrecognized"
            else:
                me = {"supplier_id": r["supplier_id"], "part_id": c[0], "supplier_sku": r["supplier_sku"], "part_type": r["part_type"],
                      "catalog_part_type": c[1], "price_ils": r["price_ils"], "is_available": False, "supplier_url": r["supplier_url"],
                      "name": None, "row_id": None}
                classes = [op.classify_pair(me, {**{k: e[k] for k in ("supplier_id", "supplier_sku", "part_type", "price_ils", "is_available", "supplier_url")},
                                                 "part_id": c[0], "catalog_part_type": c[1], "name": None, "row_id": str(e["id"])}) for e in ex]
                bad = [k for k in classes if k not in RESTORABLE]
                if bad:
                    v["status"] = f"class_not_restorable:{bad[0].name}"
                else:
                    v["class"] = classes[0]
                    myvar = _variant(r["part_type"])
                    clash = any(_variant(e["part_type"]) == myvar for e in ex)  # both NULL, or equal normalised variant
                    v["status"] = "blocked_by_variant_unique_index" if clash else "eligible"
        out.append(v)
    return out


# ------------------------------------------------------------------ apply / rollback
INSERT_COLS = ["id", "supplier_id", "part_id", "supplier_sku", "price_usd", "price_ils", "shipping_cost_usd", "shipping_cost_ils", "availability",
               "warranty_months", "estimated_delivery_days", "last_checked_at", "is_available", "created_at", "stock_quantity", "min_order_qty",
               "supplier_url", "last_in_stock_at", "express_available", "express_price_ils", "express_delivery_days", "express_cutoff_time",
               "express_last_checked", "updated_at", "part_type"]


def _insert_sql(cols):
    ph = ", ".join(f"${i + 1}::{('uuid' if c in UUID_COLS else 'numeric' if c in NUM_COLS else 'int' if c in INT_COLS else 'boolean' if c in BOOL_COLS else 'timestamp' if c in TS_COLS else 'text')}"
                   for i, c in enumerate(cols))
    return f"INSERT INTO supplier_parts ({', '.join(cols)}) VALUES ({ph}) ON CONFLICT DO NOTHING RETURNING id::text"


def restored_row(v: dict, cols: list[str]) -> list:
    r = v["row"]
    o = {c: r.get(c) for c in cols}
    o["part_id"] = v["canonical"]
    o["is_available"] = False
    o["availability"] = "out_of_stock"
    o["last_checked_at"] = None
    o["updated_at"] = dt.datetime.utcnow()
    o["part_type"] = _variant(r.get("part_type"))
    return [o[c] for c in cols]


def write_manifest(path: Path, manifest: dict) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(manifest, f, indent=1, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


async def apply_restore(conn, verdicts: list[dict], cols: list[str], batch: int, manifest: dict, mpath: Path, info,
                        evidence: dict) -> Counter:
    sql = _insert_sql([c for c in INSERT_COLS if c in cols])
    use = [c for c in INSERT_COLS if c in cols]
    res: Counter = Counter()
    todo = [v for v in verdicts if v["status"] == "eligible"]
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        tr = conn.transaction()
        await tr.start()
        try:
            added = []
            for v in chunk:
                op.assert_write_allowed("restore", v["class"], policy=info, dry_run_done=True, evidence=evidence)
                try:
                    async with conn.transaction():  # per-row SAVEPOINT
                        got = await conn.fetchval(sql, *restored_row(v, use))
                except Exception as exc:  # noqa: BLE001 - one bad row must not abort the batch
                    res["error:" + type(exc).__name__] += 1
                    continue
                if got is None:
                    res["blocked_by_unique_index_on_insert"] += 1
                else:
                    res["inserted"] += 1
                    added.append({"id": got, "part_id": v["canonical"], "supplier_id": v["row"]["supplier_id"],
                                  "supplier_sku": v["row"]["supplier_sku"], "price_ils": v["row"]["price_ils"],
                                  "original_part_id": v["row"]["part_id"], "class": v["class"].name})
            manifest["inserted"].extend(added)
            write_manifest(mpath, manifest)  # manifest first: a crash after this leaves a superset of what committed, never a subset
            await tr.commit()
        except BaseException:
            await tr.rollback()
            raise
    return res


async def rollback_manifest(conn, manifest: dict, apply: bool) -> Counter:
    res: Counter = Counter()
    for m in manifest["inserted"]:
        row = await conn.fetchrow(
            "SELECT sp.id FROM supplier_parts sp WHERE sp.id = $1::uuid AND sp.part_id = $2::uuid AND sp.supplier_id = $3::uuid "
            "AND sp.supplier_sku IS NOT DISTINCT FROM $4 AND sp.is_available IS NOT TRUE "
            "AND NOT EXISTS (SELECT 1 FROM price_history ph WHERE ph.supplier_part_id = sp.id)",
            m["id"], m["part_id"], m["supplier_id"], m["supplier_sku"])
        if row is None:
            res["not_deleted_missing_or_changed_since"] += 1
        elif apply:
            await conn.execute("DELETE FROM supplier_parts WHERE id = $1::uuid", m["id"])
            res["deleted"] += 1
        else:
            res["would_delete"] += 1
    return res


# ------------------------------------------------------------------ main
async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--input", help="TSV of original rows (from the 2026-06-30 dump)")
    ap.add_argument("--apply", action="store_true", help="really write (default: dry run, read-only)")
    ap.add_argument("--rollback", metavar="MANIFEST", help="manifest JSON of a previous --apply; dry run unless --apply is also given")
    ap.add_argument("--approval-ref", help="owner approval reference for THIS scope (required with --apply; recorded in the manifest)")
    ap.add_argument("--expect-input-sha256", help="sha256 of the approved input file (required with --apply; must match)")
    ap.add_argument("--source-backup", help="dated backup the rows come from, e.g. backups/catalog/monthly/autospare-202606.sql.gz "
                                              "(required with --apply; recorded)")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="default: DATABASE_URL")
    ap.add_argument("--manifest-dir", default="/app/state")
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--limit", type=int, default=0, help="process only the first N input rows (bounded runs)")
    a = ap.parse_args(argv)
    if not a.rollback and not a.input:
        ap.error("--input is required")
    dry = not a.apply
    info = op.gate_or_exit(ACTOR, dry_run=dry)  # exit 3 BEFORE any connection when the policy is unavailable and --apply
    import asyncpg
    dsn = a.dsn.replace("postgresql+asyncpg://", "postgresql://")
    if not dsn:
        print("no database DSN (DATABASE_URL / --dsn)", file=sys.stderr)
        return 2
    conn = await asyncpg.connect(dsn)
    try:
        if a.rollback:
            manifest = json.loads(Path(a.rollback).read_text())
            if not dry:
                tr = conn.transaction()
                await tr.start()
            else:
                await conn.execute("SET default_transaction_read_only = on")
            res = await rollback_manifest(conn, manifest, apply=not dry)
            if not dry:
                await tr.commit()
            print(json.dumps({"mode": "rollback" if not dry else "rollback dry run", "run_id": manifest["run_id"],
                              "manifest_ids": len(manifest["inserted"]), **res}, indent=1))
            return 0
        cols, rows, sha = load_input(Path(a.input))
        if a.limit:
            rows = rows[:a.limit]
        st = await index_state(conn)
        why = refusal(st)
        if not dry and why:
            print(why, file=sys.stderr)
            return 4
        if not dry:
            missing = [n for n, v in (("--approval-ref", a.approval_ref), ("--expect-input-sha256", a.expect_input_sha256),
                                      ("--source-backup", a.source_backup)) if not (v or "").strip()]
            if missing:
                print(f"REFUSING --apply: authorization/provenance missing: {', '.join(missing)} (policy 5a R1/R2)", file=sys.stderr)
                return 5
            if a.expect_input_sha256.strip().lower() != sha:
                print(f"REFUSING --apply: input sha256 {sha[:16]}... is not the approved scope {a.expect_input_sha256[:16]}...", file=sys.stderr)
                return 5
            if not VARIANT_API.startswith("variant_identity"):
                print("REFUSING --apply: the central variant_identity module is not available (policy 1a)", file=sys.stderr)
                return 5
        if dry:
            await conn.execute("SET default_transaction_read_only = on")
        await conn.execute("SET statement_timeout = '280s'")
        verdicts: list = []
        for i in range(0, len(rows), 1000):
            verdicts += await evaluate(conn, rows[i:i + 1000])
        counts = Counter(v["status"] for v in verdicts)
        report = {"mode": "APPLY" if not dry else "dry run (read-only)", "input_rows": len(rows), "input_sha256": sha,
                  "variant_api": VARIANT_API,
                  "policy": op.audit_stamp(info) if info else "policy not loadable (dry run only)", "index_state": st,
                  "apply_would_refuse": why, "verdicts": dict(counts)}
        if dry:
            report["by_supplier_eligible"] = dict(Counter(v["row"]["supplier_id"] for v in verdicts if v["status"] == "eligible"))
            print(json.dumps(report, indent=1, default=str))
            return 0
        run_id = uuid.uuid4().hex[:12]
        mpath = Path(a.manifest_dir) / f"restore_merged_offers_133_{run_id}.json"
        mpath.parent.mkdir(parents=True, exist_ok=True)
        evidence = {"approval_ref": a.approval_ref, "input_sha256": sha, "source_backup": a.source_backup,
                    "canonical_variant_checked": True, "variant_api": VARIANT_API}
        manifest = {"run_id": run_id, "created": dt.datetime.utcnow().isoformat() + "Z", "input_sha256": sha,
                    "approval_ref": a.approval_ref, "source_backup": a.source_backup, "variant_api": VARIANT_API,
                    "policy": op.audit_stamp(info), "inserted": []}
        write_manifest(mpath, manifest)
        res = await apply_restore(conn, verdicts, cols, a.batch, manifest, mpath, info, evidence)
        report.update({"run_id": run_id, "manifest": str(mpath), "result": dict(res), "inserted_ids": len(manifest["inserted"])})
        print(json.dumps(report, indent=1, default=str))
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
