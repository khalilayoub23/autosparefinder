"""
Script:  maintenance/cpie_overwrite_repair.py
Purpose: Repair of the cross-brand Car-Parts.ie offer overwrites classified VERIFIED_WRONG + repair.readiness=READY in the
         forensic manifest (candidates/core-identity-2026-11-14/forensics/repair_manifest.json.gz, 93 rows): puts the owner
         brand's own proven price + URL back on the bare-key offer. Never touches part_id, supplier_sku, availability,
         warranty; never touches UNDO_CANDIDATE / NOT_READY rows.
Process:  --verify                      read-only: classify every manifest row against the live row (also the pre-flight).
         --apply --phase canary|rest    one transaction per row, offer locked FOR UPDATE NOWAIT, compare-and-swap UPDATE
                                        (price, url, updated_at must still equal the manifest) -> price_ils, supplier_url,
                                        updated_at=NOW(); one price_history row (source cpie_overwrite_repair) when the
                                        price changes; parts_catalog.updated_at touched (meili incremental sync key).
                                        canary = first 20 manifest rows; rest = remaining and needs --canary-result that
                                        is re-verified against the live DB first. Journal is written + fsynced BEFORE commit.
         --fulldiff --result F          read-only: every column of the offers + parts vs the full-row pre-apply backup.
         --rebase --result F [--prior-rebase R]  base_price of the repaired parts via db_update_agent.apply_base_price_rule,
                                        one locked transaction per part, only if base_price is still the backup value (or the
                                        prior rebase `after`); --rollback-rebase --rebase-result R restores it (CAS on `after`).
         --rollback --result F | --journal J   per row, only if the live row still equals the recorded `after`; a newer legitimate
                                        write is SKIPPED, never overwritten; compensating price_history row; exit 2 if any
                                        row was skipped for another reason than already-rolled-back.
Data Imported/Modified: supplier_parts.price_ils/supplier_url/updated_at (93 READY offers only), price_history (INSERT),
         parts_catalog.updated_at (+ base_price in --rebase).
Data Sources: forensics manifest (sha256 pinned by --expect-sha256).
Missing Data Delegation: n/a (missing brand offers are created by the fixed importer / a separate gated step).
Last Updated: 2026-11-15
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import gzip
import hashlib
import json
import os
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ACTOR = "maintenance/cpie_overwrite_repair.py"
SRC_APPLY = "cpie_overwrite_repair"
SRC_ROLLBACK = "cpie_overwrite_repair_rollback"
CPIE_SUPPLIER = os.getenv("CPIE_SUPPLIER_ID", "88f6ce24-ab5f-4f99-8b41-03cf6828cf3d")
DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
OUT = Path(os.getenv("STATE_DIR", "/app/state")) / "cpie_ownership" / "overwrite_repair"
CANARY_N = 20
EXPECTED_READY = 93


def D(v):
    return None if v is None else Decimal(str(v)).quantize(Decimal("0.01"))


def ts(s):
    return dt.datetime.fromisoformat(s) if isinstance(s, str) else s


def sha256_file(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load_ready(path, expect_sha, expect_count=EXPECTED_READY):
    """Fail closed: pinned sha, exactly the READY VERIFIED_WRONG rows, each with a complete expected-current + target."""
    if sha256_file(path) != expect_sha:
        raise SystemExit("REFUSED: manifest sha256 does not match --expect-sha256")
    m = json.load(gzip.open(path))
    rows = [x for x in m["offers"] if (x.get("repair") or {}).get("readiness") == "READY"]
    if len(rows) != expect_count:
        raise SystemExit(f"REFUSED: manifest holds {len(rows)} READY rows, expected {expect_count}")
    seen = set()
    for x in rows:
        r = x["repair"]
        if x["classification"] != "VERIFIED_WRONG" or x["offer_id"] in seen or not r.get("target_url") \
                or "~" in x["article"] or not all(k in x["current"] for k in ("price", "url", "updated_at")):
            raise SystemExit(f"REFUSED: manifest row {x['offer_id']} is not a complete READY VERIFIED_WRONG row")
        seen.add(x["offer_id"])
    return rows


def scope_rows(a):
    """The pinned READY set minus the explicitly excluded offers (a row legitimately rewritten after the manifest)."""
    rows = load_ready(a.manifest, a.expect_sha256)
    ex = set(a.exclude_offer or [])
    if ex - {x["offer_id"] for x in rows}:
        raise SystemExit("REFUSED: --exclude-offer id is not in the READY manifest set")
    return [x for x in rows if x["offer_id"] not in ex]


def expected(x):
    r = x["repair"]
    return {"price": D(x["current"]["price"]), "url": x["current"]["url"], "ua": ts(x["current"]["updated_at"]),
            "t_price": D(r["target_price"]) if r.get("target_price") is not None else None, "t_url": r["target_url"]}


_SEL = ("SELECT id::text id, supplier_id::text sid, part_id::text pid, supplier_sku sku, price_ils price, price_usd, supplier_url url, "
        "updated_at ua FROM supplier_parts WHERE id=$1::uuid")


def classify(x, live):
    """Pure comparison of one live row with the manifest row (shared by --verify, apply guards and tests)."""
    e = expected(x)
    if live is None:
        return "MISSING_RECORD", e
    if live["pid"] != x["current_part_id"] or live["sid"] != CPIE_SUPPLIER or live["sku"] != x["article"]:
        return ("WRONG_TARGET" if live["pid"] != x["current_part_id"] else "UNEXPECTED_CHANGE"), e
    at_tgt_url = live["url"] == e["t_url"]
    at_tgt_price = e["t_price"] is None or D(live["price"]) == e["t_price"]
    if at_tgt_url and at_tgt_price and (live["url"] != e["url"] or D(live["price"]) != e["price"]):
        return "VERIFIED_REPAIRED", e
    if D(live["price"]) == e["price"] and live["url"] == e["url"]:
        return ("NOT_APPLIED" if live["ua"] == e["ua"] else "NEWER_WRITE_SAME_VALUE"), e
    if at_tgt_url and not at_tgt_price:
        return "WRONG_PRICE", e
    if at_tgt_price and not at_tgt_url:
        return "WRONG_URL", e
    return "UNEXPECTED_CHANGE", e


async def verify(rows, conn) -> dict:
    out = {}
    for x in rows:
        live = await conn.fetchrow(_SEL, x["offer_id"])
        out[x["offer_id"]] = classify(x, live)[0]
    return out


def _journal(path, rec):
    with open(path, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())


async def apply_rows(rows, conn, run_id, journal, only_phase) -> dict:
    done, skipped = [], []
    for x in rows:
        e = expected(x)
        try:
            async with conn.transaction():
                live = await conn.fetchrow(_SEL + " FOR UPDATE NOWAIT", x["offer_id"])
                state, _ = classify(x, live)
                if state != "NOT_APPLIED":
                    raise RuntimeError(f"guard:{state}")
                await conn.fetchval("SELECT id FROM parts_catalog WHERE id=$1::uuid FOR UPDATE NOWAIT", x["current_part_id"])
                new_price = e["t_price"] if e["t_price"] is not None else D(live["price"])
                got = await conn.fetchrow(
                    "UPDATE supplier_parts SET price_ils=$2, supplier_url=$3, updated_at=NOW() WHERE id=$1::uuid "
                    "AND price_ils=$4 AND supplier_url=$5 AND updated_at=$6 RETURNING updated_at", x["offer_id"], new_price,
                    e["t_url"], D(live["price"]), live["url"], live["ua"])
                if got is None:
                    raise RuntimeError("guard:compare_and_swap_lost")
                ph = None
                if new_price != D(live["price"]):
                    old, new = D(live["price"]), new_price
                    usd = live["price_usd"] if live["price_usd"] is not None else 0
                    ph = str(uuid.uuid4())
                    pct = None if not old else max(min((new - old) / old * 100, Decimal("999.9999")), Decimal("-999.9999"))
                    await conn.execute(
                        "INSERT INTO price_history (id, supplier_part_id, old_price_ils, new_price_ils, old_price_usd, new_price_usd,"
                        " change_pct, source, created_at) VALUES ($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8,NOW())",
                        ph, x["offer_id"], old, new, live["price_usd"], usd, pct, SRC_APPLY)
                await conn.execute("UPDATE parts_catalog SET updated_at=NOW() WHERE id=$1::uuid", x["current_part_id"])
                rec = {"offer_id": x["offer_id"], "part_id": x["current_part_id"], "supplier_sku": x["article"],
                       "before": {"price_ils": str(D(live["price"])), "supplier_url": live["url"], "updated_at": live["ua"].isoformat()},
                       "after": {"price_ils": str(new_price), "supplier_url": e["t_url"], "updated_at": got["updated_at"].isoformat()},
                       "price_history_id": ph, "run_id": run_id, "phase": only_phase}
                _journal(journal, {"status": "intent", **rec})  # write-ahead: durable BEFORE the commit
            _journal(journal, {"status": "committed", "offer_id": x["offer_id"]})
            done.append(rec)
        except Exception as exc:  # noqa: BLE001 - one row never stops the others; it is reported with its reason
            skipped.append({"offer_id": x["offer_id"], "reason": str(exc)[:200]})
            _journal(journal, {"status": "skipped", "offer_id": x["offer_id"], "reason": str(exc)[:200]})
    return {"done": done, "skipped": skipped}


async def backup_rows(rows, conn, run_id) -> tuple:
    """Full-row (to_jsonb) copy of the target offers AND their parts, taken BEFORE the first write."""
    ids = [x["offer_id"] for x in rows]
    pids = sorted({x["current_part_id"] for x in rows})
    offers = await conn.fetch("SELECT id::text id, to_jsonb(sp)::text j FROM supplier_parts sp WHERE id=ANY($1::uuid[])", ids)
    parts = await conn.fetch("SELECT id::text id, to_jsonb(pc)::text j FROM parts_catalog pc WHERE id=ANY($1::uuid[])", pids)
    p = OUT / f"pre_apply_{run_id}.json"
    p.write_text(json.dumps({"offers": {r["id"]: json.loads(r["j"]) for r in offers}, "parts": {r["id"]: json.loads(r["j"]) for r in parts}}))
    return p, sha256_file(p)


OFFER_ALLOWED = {"price_ils", "supplier_url", "updated_at"}
PART_ALLOWED = {"updated_at", "base_price"}


async def fulldiff(backup_path, rows, conn) -> dict:
    """Read-only: every column of every target offer/part vs the full-row backup; only the allowed columns may differ."""
    bk = json.load(open(backup_path))
    ids = list(bk["offers"])
    pids = list(bk["parts"])
    cur_o = {r["id"]: json.loads(r["j"]) for r in await conn.fetch("SELECT id::text id, to_jsonb(sp)::text j FROM supplier_parts sp WHERE id=ANY($1::uuid[])", ids)}
    cur_p = {r["id"]: json.loads(r["j"]) for r in await conn.fetch("SELECT id::text id, to_jsonb(pc)::text j FROM parts_catalog pc WHERE id=ANY($1::uuid[])", pids)}
    bad, changed_cols = [], {}
    for kind, cur, allowed in (("offer", cur_o, OFFER_ALLOWED), ("part", cur_p, PART_ALLOWED)):
        for i, before in bk["offers" if kind == "offer" else "parts"].items():
            now = cur.get(i)
            if now is None:
                bad.append({"kind": kind, "id": i, "diff": "ROW_MISSING"})
                continue
            diff = sorted(k for k in set(before) | set(now) if before.get(k) != now.get(k))
            for k in diff:
                changed_cols[f"{kind}.{k}"] = changed_cols.get(f"{kind}.{k}", 0) + 1
            if set(diff) - allowed:
                bad.append({"kind": kind, "id": i, "unexpected_columns": sorted(set(diff) - allowed)})
    return {"rows_checked": {"offers": len(bk["offers"]), "parts": len(bk["parts"])}, "changed_column_counts": changed_cols,
            "rows_with_unexpected_changes": bad}


async def cmd_fulldiff(a):
    res = json.load(open(a.result))
    rows = scope_rows(a)
    conn = await asyncpg.connect(a.dsn or DB)
    try:
        await conn.execute("SET default_transaction_read_only=on")
        out = await fulldiff(res["pre_apply_backup"], rows, conn)
    finally:
        await conn.close()
    print(json.dumps(out))
    return out


async def cmd_apply(a):
    import offer_policy
    offer_policy.gate_or_exit(ACTOR, False)
    if not a.approval_ref:
        raise SystemExit("REFUSED: --approval-ref required")
    rows = scope_rows(a)
    canary, rest = rows[:CANARY_N], rows[CANARY_N:]
    OUT.mkdir(parents=True, exist_ok=True)
    conn = await asyncpg.connect(a.dsn or DB)
    try:
        if a.phase == "rest":
            if not a.canary_result:
                raise SystemExit("REFUSED: --canary-result required for --phase rest")
            cr = json.load(open(a.canary_result))
            if cr.get("manifest_sha256") != a.expect_sha256 or cr.get("phase") != "canary":
                raise SystemExit("REFUSED: canary result is for another manifest/phase")
            states = await verify([x for x in canary if x["offer_id"] in {d["offer_id"] for d in cr["done"]}], conn)
            if len(cr["done"]) == 0 or any(s != "VERIFIED_REPAIRED" for s in states.values()):
                raise SystemExit(f"REFUSED: canary not verified in the DB: {states}")
            if cr["skipped"] and not a.accept_canary_skips:
                raise SystemExit(f"REFUSED: canary had {len(cr['skipped'])} skipped rows (rerun canary or --accept-canary-skips)")
            target = rest
        else:
            target = canary
        run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "_" + a.phase
        pre, pre_sha = await backup_rows(target, conn, run_id)
        journal = OUT / f"journal_{run_id}.jsonl"
        res = await apply_rows(target, conn, run_id, journal, a.phase)
    finally:
        await conn.close()
    result = {"manifest_sha256": a.expect_sha256, "approval_ref": a.approval_ref, "run_id": run_id, "phase": a.phase,
              "pre_apply_backup": str(pre), "pre_apply_backup_sha256": pre_sha, **res,
              "applied": len(res["done"]), "skipped_count": len(res["skipped"])}
    rp = OUT / f"result_{run_id}.json"
    rp.write_text(json.dumps(result, indent=1))
    print(json.dumps({"result": str(rp), "applied": result["applied"], "skipped": result["skipped_count"], "reasons": [s["reason"] for s in res["skipped"]]}))
    return result


async def rollback_rows(done, conn) -> dict:
    back, skipped = [], []
    for d in done:
        try:
            async with conn.transaction():
                live = await conn.fetchrow(_SEL + " FOR UPDATE NOWAIT", d["offer_id"])
                if live is None or live["pid"] != d["part_id"] or live["sku"] != d["supplier_sku"] or live["sid"] != CPIE_SUPPLIER:
                    raise RuntimeError("identity_changed")
                b, af = d["before"], d["after"]
                if D(live["price"]) == D(b["price_ils"]) and live["url"] == b["supplier_url"]:
                    raise RuntimeError("already_rolled_back")
                if D(live["price"]) != D(af["price_ils"]) or live["url"] != af["supplier_url"] or live["ua"] != ts(af["updated_at"]):
                    raise RuntimeError("newer_legitimate_write")
                await conn.fetchval("SELECT id FROM parts_catalog WHERE id=$1::uuid FOR UPDATE NOWAIT", d["part_id"])
                got = await conn.fetchrow(
                    "UPDATE supplier_parts SET price_ils=$2, supplier_url=$3, updated_at=NOW() WHERE id=$1::uuid AND price_ils=$4 "
                    "AND supplier_url=$5 AND updated_at=$6 RETURNING id", d["offer_id"], D(b["price_ils"]), b["supplier_url"],
                    D(live["price"]), live["url"], live["ua"])
                if got is None:
                    raise RuntimeError("newer_legitimate_write")
                if D(b["price_ils"]) != D(af["price_ils"]):
                    old, new = D(af["price_ils"]), D(b["price_ils"])
                    pct = None if not old else max(min((new - old) / old * 100, Decimal("999.9999")), Decimal("-999.9999"))
                    await conn.execute(
                        "INSERT INTO price_history (id, supplier_part_id, old_price_ils, new_price_ils, old_price_usd, new_price_usd,"
                        " change_pct, source, created_at) VALUES ($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8,NOW())",
                        str(uuid.uuid4()), d["offer_id"], old, new, live["price_usd"], live["price_usd"] if live["price_usd"] is not None else 0,
                        pct, SRC_ROLLBACK)
                await conn.execute("UPDATE parts_catalog SET updated_at=NOW() WHERE id=$1::uuid", d["part_id"])
                back.append(d["offer_id"])
        except Exception as exc:  # noqa: BLE001
            skipped.append({"offer_id": d["offer_id"], "reason": str(exc)[:200]})
    return {"restored": back, "skipped": skipped}


async def cmd_rollback(a):
    import offer_policy
    offer_policy.gate_or_exit(ACTOR, False)
    if not a.approval_ref or not (a.result or a.journal):
        raise SystemExit("REFUSED: --result (or --journal) and --approval-ref required")
    if a.result and a.expect_result_sha256 and sha256_file(a.result) != a.expect_result_sha256:
        raise SystemExit("REFUSED: result sha256 mismatch")
    if a.journal:  # crash path: the write-ahead journal is the source, not the end-of-run result file
        recs = {}
        for line in open(a.journal):
            r = json.loads(line)
            if r.get("status") == "intent":
                recs[r["offer_id"]] = r
        res = {"done": list(recs.values())}
    else:
        res = json.load(open(a.result))
    conn = await asyncpg.connect(a.dsn or DB)
    try:
        out = await rollback_rows(res["done"], conn)
    finally:
        await conn.close()
    OUT.mkdir(parents=True, exist_ok=True)
    rp = OUT / f"rollback_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    rp.write_text(json.dumps({"result": str(a.result or a.journal), "approval_ref": a.approval_ref, "restored": len(out["restored"]), **out}, indent=1))
    hard = [s for s in out["skipped"] if s["reason"] != "already_rolled_back"]
    print(json.dumps({"rollback": str(rp), "restored": len(out["restored"]), "skipped": len(out["skipped"]), "hard_skips": len(hard)}))
    if hard:
        raise SystemExit(2)
    return out


async def rebase_parts(res, backup_paths, prior_after, session_factory, rule, run_id, rate=None, only_parts=None, require_legacy=False) -> dict:
    """base_price of the repaired parts through the ONE ownership rule (`rule` = db_update_agent.apply_base_price_rule),
    one transaction per part with the part row locked FOR UPDATE NOWAIT for the whole read->write, so a concurrent writer
    can neither be overwritten nor race the rule. A part is touched only if its current base_price is the value from the
    pre-apply backup (or the `after` of a previous rebase result: idempotent re-run); anything else is a newer legitimate
    write -> skipped and reported."""
    from sqlalchemy import text
    bk = {}
    for bp in backup_paths:
        bk.update(json.load(open(bp))["parts"])
    done, skipped, unchanged = [], [], 0
    wanted = sorted({d["part_id"] for d in res["done"]} & set(only_parts)) if only_parts else sorted({d["part_id"] for d in res["done"]})
    for pid in wanted:
        try:
            async with session_factory() as db:
                async with db.begin():
                    row = (await db.execute(text("SELECT base_price, importer_price_ils FROM parts_catalog WHERE id=CAST(:i AS uuid) FOR UPDATE NOWAIT"), {"i": pid})).fetchone()
                    if row is None:
                        raise RuntimeError("part_missing")
                    cur = D(row[0])
                    ok_vals = {D(bk[pid].get("base_price")) if pid in bk else None, prior_after.get(pid)}
                    if cur not in ok_vals:
                        raise RuntimeError("base_price_changed_since_backup")
                    legacy = row[1] is not None and D(Decimal(str(row[1])) * Decimal("1.45")) == cur   # stored == importer_price_ils x 1.45 (legacy estimate)
                    if require_legacy and not legacy:
                        raise RuntimeError("not_a_legacy_estimate_value")
                    await rule(db, scope="all", part_ids=[pid], usd_to_ils=rate)
                    new = D((await db.execute(text("SELECT base_price FROM parts_catalog WHERE id=CAST(:i AS uuid)"), {"i": pid})).scalar())
                    if new == cur:
                        unchanged += 1
                    else:
                        done.append({"part_id": pid, "before": str(cur) if cur is not None else None, "after": str(new),
                                     "importer_price_ils": str(row[1]) if row[1] is not None else None, "before_equals_importer_x1_45": legacy})
        except Exception as exc:  # noqa: BLE001
            skipped.append({"part_id": pid, "reason": "locked_by_other_writer" if "could not obtain lock" in str(exc) else str(exc)[:200]})
    return {"run_id": run_id, "changed": len(done), "unchanged": unchanged, "skipped_count": len(skipped), "done": done, "skipped": skipped}


async def rollback_rebase(rr, session_factory) -> dict:
    """Restore base_price only where it still equals the recorded `after` (a newer legitimate write is skipped)."""
    from sqlalchemy import text
    back, skipped = [], []
    for d in rr["done"]:
        try:
            async with session_factory() as db:
                async with db.begin():
                    row = (await db.execute(text("SELECT base_price FROM parts_catalog WHERE id=CAST(:i AS uuid) FOR UPDATE NOWAIT"), {"i": d["part_id"]})).fetchone()
                    if row is None or D(row[0]) != D(d["after"]):
                        raise RuntimeError("newer_legitimate_write")
                    await db.execute(text("UPDATE parts_catalog SET base_price=:v, updated_at=NOW() WHERE id=CAST(:i AS uuid)"),
                                     {"v": D(d["before"]) if d["before"] is not None else None, "i": d["part_id"]})
                    back.append(d["part_id"])
        except Exception as exc:  # noqa: BLE001
            skipped.append({"part_id": d["part_id"], "reason": "locked_by_other_writer" if "could not obtain lock" in str(exc) else str(exc)[:200]})
    return {"restored": back, "skipped": skipped}


async def cmd_rebase(a):
    from BACKEND_DATABASE_MODELS import async_session_factory
    import db_update_agent as d
    results = [json.load(open(f)) for f in [a.result, *(a.also_result or [])]]
    res = {"done": [d for r in results for d in r["done"]], "run_id": results[0]["run_id"]}
    prior = {}
    if a.prior_rebase:
        prior = {x["part_id"]: D(x["after"]) for f in a.prior_rebase for x in json.load(open(f))["done"]}
    OUT.mkdir(parents=True, exist_ok=True)
    rate = a.usd_to_ils
    if rate is None:  # fetched ONCE so a rate change between passes cannot make a second pass non-zero
        async with async_session_factory() as db:
            rate = await d.get_usd_to_ils_rate(db)
    out = await rebase_parts(res, [r["pre_apply_backup"] for r in results], prior, async_session_factory, d.apply_base_price_rule, res["run_id"], rate,
                             only_parts=a.only_part, require_legacy=a.require_legacy_estimate)
    out["usd_to_ils"] = rate
    rp = OUT / f"rebase_{res['run_id']}_{time.strftime('%H%M%S', time.gmtime())}.json"
    rp.write_text(json.dumps(out, indent=1))
    print(json.dumps({"rebase": str(rp), "changed": out["changed"], "unchanged": out["unchanged"], "skipped": out["skipped_count"],
                      "reasons": [x["reason"] for x in out["skipped"]]}))
    return out


async def cmd_rollback_rebase(a):
    from BACKEND_DATABASE_MODELS import async_session_factory
    if not a.approval_ref or not a.rebase_result:
        raise SystemExit("REFUSED: --rebase-result and --approval-ref required")
    out = await rollback_rebase(json.load(open(a.rebase_result)), async_session_factory)
    print(json.dumps({"restored": len(out["restored"]), "skipped": len(out["skipped"]), "reasons": [x["reason"] for x in out["skipped"]]}))
    if out["skipped"]:
        raise SystemExit(2)
    return out


async def cmd_accept(a):
    """Read-only acceptance of one apply phase (exit 1 on any failure): applied rows are VERIFIED_REPAIRED and equal the
    journal's `after`; full-row diff clean; price_history rows == price changes; journal committed == applied; the skips of
    the phase are guard/lock skips only; rows outside the phase and not excluded were not touched by the phase."""
    res = json.load(open(a.result))
    rows = scope_rows(a)
    byid = {x["offer_id"]: x for x in rows}
    conn = await asyncpg.connect(a.dsn or DB)
    fails = []
    try:
        await conn.execute("SET default_transaction_read_only=on")
        done_ids = [d["offer_id"] for d in res["done"]]
        st = await verify([byid[i] for i in done_ids], conn)
        if any(v != "VERIFIED_REPAIRED" for v in st.values()):
            fails.append({"not_repaired": {k: v for k, v in st.items() if v != "VERIFIED_REPAIRED"}})
        live = {r["id"]: r for r in await conn.fetch(_SEL.replace("WHERE id=$1::uuid", "WHERE id=ANY($1::uuid[])"), done_ids)}
        for d in res["done"]:
            l = live[d["offer_id"]]
            if (D(l["price"]), l["url"], l["ua"]) != (D(d["after"]["price_ils"]), d["after"]["supplier_url"], ts(d["after"]["updated_at"])):
                fails.append({"later_write_or_mismatch_vs_after": d["offer_id"]})
        fd = await fulldiff(res["pre_apply_backup"], rows, conn)
        if fd["rows_with_unexpected_changes"]:
            fails.append({"unexpected_columns": fd["rows_with_unexpected_changes"][:5]})
        want_ph = sum(1 for d in res["done"] if D(d["before"]["price_ils"]) != D(d["after"]["price_ils"]))
        got_ph = await conn.fetchval("SELECT count(*) FROM price_history WHERE source=$1 AND id=ANY($2::uuid[])", SRC_APPLY,
                                     [d["price_history_id"] for d in res["done"] if d["price_history_id"]])
        if got_ph != want_ph:
            fails.append({"price_history": [got_ph, want_ph]})
        jl = [json.loads(l) for l in open(OUT / f"journal_{res['run_id']}.jsonl")]
        committed = {j["offer_id"] for j in jl if j["status"] == "committed"}
        if committed != set(done_ids) or len({j["offer_id"] for j in jl if j["status"] == "intent"} - committed) != 0:
            fails.append({"journal": [len(committed), len(done_ids)]})
        bad_skip = [s for s in res["skipped"] if not (s["reason"].startswith("guard:") or "could not obtain lock" in s["reason"])]
        if bad_skip:
            fails.append({"non_guard_skips": bad_skip})
        ph = [x["offer_id"] for x in (res["done"] + res["skipped"])]
        others = [x for x in rows if x["offer_id"] not in set(ph)]
        ost = await verify(others, conn) if (a.phase_check == "canary") else {}
        if any(v == "VERIFIED_REPAIRED" for v in ost.values()):
            fails.append({"rest_scope_touched": [k for k, v in ost.items() if v == "VERIFIED_REPAIRED"]})
        info = {"applied": len(done_ids), "skipped": [(s["offer_id"][:8], s["reason"][:40]) for s in res["skipped"]], "price_history": got_ph,
                "journal_committed": len(committed), "fulldiff_changed_columns": fd["changed_column_counts"], "rest_scope_states": dict(
                    __import__("collections").Counter(ost.values()))}
    finally:
        await conn.close()
    print(json.dumps({"ACCEPT": not fails, "fails": fails, **info}))
    if fails:
        raise SystemExit(1)


async def cmd_verify(a):
    rows = scope_rows(a)
    conn = await asyncpg.connect(a.dsn or DB)
    try:
        await conn.execute("SET default_transaction_read_only=on")
        st = await verify(rows, conn)
    finally:
        await conn.close()
    cnt = {}
    for s in st.values():
        cnt[s] = cnt.get(s, 0) + 1
    print(json.dumps({"counts": cnt, "rows": st} if a.rows else {"counts": cnt}))
    return st


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    for k in ("verify", "apply", "rollback", "rebase", "fulldiff", "rollback_rebase", "accept"):
        g.add_argument("--" + k.replace("_", "-"), dest=k, action="store_true")
    ap.add_argument("--phase", choices=["canary", "rest"])
    ap.add_argument("--manifest")
    ap.add_argument("--expect-sha256")
    ap.add_argument("--approval-ref")
    ap.add_argument("--canary-result")
    ap.add_argument("--accept-canary-skips", action="store_true")
    ap.add_argument("--result")
    ap.add_argument("--journal")
    ap.add_argument("--only-part", action="append")
    ap.add_argument("--require-legacy-estimate", action="store_true")
    ap.add_argument("--phase-check", choices=["canary", "rest"], default="rest")
    ap.add_argument("--usd-to-ils", type=float)
    ap.add_argument("--also-result", action="append")
    ap.add_argument("--exclude-offer", action="append")
    ap.add_argument("--prior-rebase", nargs="*")
    ap.add_argument("--rebase-result")
    ap.add_argument("--expect-result-sha256")
    ap.add_argument("--dsn")
    ap.add_argument("--rows", action="store_true")
    a = ap.parse_args(argv)
    if a.apply and not a.phase:
        raise SystemExit("REFUSED: --phase canary|rest required")
    if (a.apply or a.verify or a.fulldiff or a.accept) and not (a.manifest and a.expect_sha256):
        raise SystemExit("REFUSED: --manifest and --expect-sha256 required")
    fn = {"verify": cmd_verify, "apply": cmd_apply, "rollback": cmd_rollback, "rebase": cmd_rebase, "fulldiff": cmd_fulldiff,
          "rollback_rebase": cmd_rollback_rebase, "accept": cmd_accept}[[k for k in ("verify", "apply", "rollback", "rebase", "fulldiff", "rollback_rebase", "accept") if getattr(a, k)][0]]
    asyncio.run(fn(a))


if __name__ == "__main__":
    main()
