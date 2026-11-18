"""
Script:  base_price_rule.py
Purpose: THE base_price ownership rule, in ONE light module (no SQLAlchemy, no models) so every writer shares it: the maintenance tasks
         (db_update_agent.apply_base_price_rule, SQLAlchemy session) AND asyncpg writers such as importers/car_parts_ie_import_generic.py
         (apply_to_parts). base_price is an internal reference = the price policy's own net (cost x 1.45, conditional VAT basis) of the
         part's price-basis offer of its own class, else the catalog estimate, else untouched (docs/PRICE_POLICY.md). A second formula in
         any writer is the defect this module exists to prevent (2026-11-16 RCA: car-parts.ie importer wrote price_ils x 1.45 on its own
         and ~41% of a 6,054-part sample diverged from this rule).
Process:  inputs_sql(only_ids) -> one row per part with its price-basis offer + estimate inputs; target(row, usd_to_ils) -> (net, source).
Data Imported/Modified: apply_to_parts() UPDATEs parts_catalog.base_price (+ updated_at, the Meilisearch incremental key) for the given
         parts only, FOR UPDATE SKIP LOCKED, only rows whose value changes.
Data Sources: parts_catalog, supplier_parts, suppliers, system_settings (USD/ILS rate).
Missing Data Delegation: no basis (no visible offer of the part's class, no estimate input) -> the row is skipped and counted, never defaulted.
Last Updated: 2026-11-17 (ensure_vat_registry: subprocess writers load the supplier VAT-basis registry)
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

FALLBACK_USD_TO_ILS = 3.72   # same fallback as db_update_agent.ILS_PER_USD / BACKEND_DATABASE_MODELS.USD_TO_ILS


def inputs_sql(only_ids: bool) -> str:
    import price_policy as pp
    from offer_classification import class_rank_sql
    rank = class_rank_sql("sp.part_type", "pc.part_type")
    cost = pp.offer_cost_sql(":rate")
    where = "pc.id = ANY(CAST(:ids AS uuid[]))" if only_ids else "pc.id > CAST(:after AS uuid)"
    return f"""
        SELECT pc.id::text AS id, pc.base_price, pc.importer_price_ils, pc.max_price_ils, pc.online_price_ils,
               bo.price_ils AS offer_ils, bo.price_usd AS offer_usd,
               bo.name AS offer_supplier, bo.country AS offer_country,
               bo.offer_id AS offer_id, bo.offer_updated_at AS offer_updated_at,
               EXISTS (SELECT 1 FROM supplier_parts sp JOIN suppliers s ON s.id = sp.supplier_id
                       WHERE sp.part_id = pc.id AND {pp.VISIBLE_OFFER_SQL} AND {cost} > 0) AS has_visible,
               (EXISTS (SELECT 1 FROM supplier_parts sp WHERE sp.part_id = pc.id AND sp.price_ils > 0 AND {rank} <> 0)
                AND NOT EXISTS (SELECT 1 FROM supplier_parts sp
                                WHERE sp.part_id = pc.id AND sp.price_ils > 0 AND {rank} = 0)) AS other_class_only
        FROM parts_catalog pc
        LEFT JOIN LATERAL (
            SELECT sp.price_ils, sp.price_usd, s.name, s.country, sp.id::text AS offer_id, sp.updated_at AS offer_updated_at
            FROM supplier_parts sp JOIN suppliers s ON s.id = sp.supplier_id
            WHERE sp.part_id = pc.id AND {pp.VISIBLE_OFFER_SQL} AND {cost} > 0 AND {rank} = 0
            ORDER BY {pp.price_basis_order_sql("pc.part_type", ":rate")}
            LIMIT 1) bo ON TRUE
        WHERE pc.is_active AND {where}
        ORDER BY pc.id
        {"" if only_ids else "LIMIT :batch"}"""


def target(row: Any, usd_to_ils: float) -> Tuple[Optional[float], str]:
    """(new base_price, source) for one part under the ownership rule; source is offer | estimate |
    other_class_only | no_basis. Pure: the same function the tests and the tasks use."""
    import price_policy as pp
    if row.offer_ils is not None or row.offer_usd is not None:
        u = pp.unit_price(pp.offer_cost_ils(row.offer_ils, row.offer_usd, usd_to_ils),
                          getattr(row, "offer_supplier", None), getattr(row, "offer_country", None))  # supplier VAT basis (#129)
        if u is not None:
            return u.net, "offer"
    if row.has_visible or row.other_class_only:
        return None, "other_class_only"
    # max_price_ils is the IL CONSUMER REFERENCE (a retail figure incl. VAT): the customer-facing estimate (price_policy.catalog_estimate)
    # may read it as a display fallback, but it is NEVER cost evidence for base_price (owner rule 2026-11-18): only the importer's
    # ex-VAT cost or the foreign buy price count. A part with neither stays unpriced (reason 'no_basis'), visibly.
    est = pp.catalog_estimate_price(row.importer_price_ils, None, row.online_price_ils)
    if est is not None:
        return est.net, "estimate"
    return None, "no_basis"


VAT_REGISTRY_SQL = "SELECT name, vat_price_basis FROM suppliers"


def vat_registry_loaded() -> bool:
    import price_policy as pp
    return pp._BASIS_REGISTRY is not None


async def ensure_vat_registry(conn=None, db=None) -> bool:
    """Make sure THIS process prices from the supplier VAT-basis registry (suppliers.vat_price_basis), exactly like the
    uvicorn process (BACKEND_API_ROUTES._vat_basis_refresh_once). A subprocess writer (importer, maintenance script) starts
    with the registry UNLOADED, where a local supplier silently reads as 'exclusive' and an 'inclusive' supplier's base_price
    comes out 18% high (2026-11-17 RCA). Pass an asyncpg `conn` or a SQLAlchemy `db`. Returns True when loaded; on failure it
    is logged loudly and False is returned - the caller must not pretend the basis was applied."""
    import logging
    import price_policy as pp
    if pp._BASIS_REGISTRY is not None:
        return True
    try:
        if conn is not None:
            rows = [(r["name"], r["vat_price_basis"]) for r in await conn.fetch(VAT_REGISTRY_SQL)]
        elif db is not None:
            from sqlalchemy import text
            async with db.begin_nested():     # a failed load must not abort the caller's transaction
                rows = [(r[0], r[1]) for r in (await db.execute(text(VAT_REGISTRY_SQL))).all()]
        else:
            return False
        pp.load_vat_bases(dict(rows))
        return True
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).error("[vat_basis] registry load FAILED in a subprocess writer (legacy exclusive reading "
                                          "applies to local suppliers): %s", exc)
        return False


def _asyncpg_sql(sql: str) -> str:
    return sql.replace(":ids", "$1").replace(":rate", "$2")


async def get_rate(conn) -> float:
    """USD->ILS from system_settings (canonical key first, then legacy; 2..10 only), mirroring currency_rate.get_usd_to_ils_rate."""
    try:
        rows = await conn.fetch("SELECT key, value FROM system_settings WHERE key IN ('ils_per_usd','currency_exchange_rate_usd_to_ils') "
                                "ORDER BY CASE WHEN key='ils_per_usd' THEN 0 ELSE 1 END")
        for r in rows:
            try:
                v = float(r["value"])
                if 2.0 <= v <= 10.0:
                    return v
            except (TypeError, ValueError):
                continue
    except Exception:
        pass
    return FALLBACK_USD_TO_ILS


async def apply_to_parts(conn, part_ids: Iterable[str], usd_to_ils: Optional[float] = None, chunk: int = 2000) -> Dict[str, Any]:
    """Apply the rule to exactly these parts on an asyncpg connection (own statements, caller owns the transaction).
    Returns counters; reasons explain every skip. Never raises for an individual part."""
    ids = list(dict.fromkeys(str(i) for i in part_ids))
    registry_ok = await ensure_vat_registry(conn=conn)
    rate = usd_to_ils if usd_to_ils is not None else await get_rate(conn)
    sql = _asyncpg_sql(inputs_sql(True))
    c = {"scanned": 0, "updated": 0, "unchanged": 0, "skipped": 0, "reasons": {}, "vat_registry_loaded": registry_ok}
    for i in range(0, len(ids), chunk):
        writes: Dict[str, Decimal] = {}
        for r in await conn.fetch(sql, ids[i:i + chunk], rate):
            c["scanned"] += 1
            row = SimpleNamespace(**dict(r))
            tgt, src = target(row, rate)
            if tgt is None:
                c["skipped"] += 1
                c["reasons"][src] = c["reasons"].get(src, 0) + 1
            elif row.base_price is not None and round(float(row.base_price), 2) == tgt:
                c["unchanged"] += 1
            else:
                writes[row.id] = Decimal(f"{tgt:.2f}")
        if writes:
            done = await conn.fetchval(
                """WITH locked AS (SELECT id FROM parts_catalog WHERE id = ANY($1::uuid[]) FOR UPDATE SKIP LOCKED),
                        u AS (UPDATE parts_catalog pc SET base_price = t.v, updated_at = NOW()
                              FROM unnest($1::uuid[], $2::numeric[]) AS t(id, v)
                              WHERE pc.id = t.id AND pc.id IN (SELECT id FROM locked) AND pc.base_price IS DISTINCT FROM t.v
                              RETURNING 1)
                   SELECT count(*) FROM u""", list(writes), list(writes.values()))
            c["updated"] += done
            if done < len(writes):
                c["skipped"] += len(writes) - done
                c["reasons"]["locked_or_changed"] = c["reasons"].get("locked_or_changed", 0) + len(writes) - done
    return c


# ── importer-facing wrapper: one call per batch, loud on failure, retryable ─────────────────────────────────────────────────
# An importer's offer ingestion must survive a rule failure, but the failure must be VISIBLE (log + returned counters) and the
# parts must be retried, never left silently at 0.0: failed ids are appended to RETRY_FILE (the persistent worker_state volume) and
# drained by the next apply_and_report call of ANY importer (idempotent: the rule only writes rows whose value changes).
import json as _json
import logging as _logging
import os as _os
import sys as _sys

RETRY_FILE = _os.environ.get("BASE_PRICE_RETRY_FILE", "/app/state/base_price_retry.jsonl")
_log = _logging.getLogger("base_price_rule")


def _valid_ids(ids) -> List[str]:
    import uuid as _uuid
    out = []
    for i in ids:
        try:
            out.append(str(_uuid.UUID(str(i))))
        except (ValueError, AttributeError, TypeError):
            _log.error("[base_price] dropping a non-UUID part id from the price queue: %r", i)
    return list(dict.fromkeys(out))


def _retry_append(importer: str, ids: List[str], error: str) -> None:
    import fcntl
    try:
        _os.makedirs(_os.path.dirname(RETRY_FILE), exist_ok=True)
        with open(RETRY_FILE, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(_json.dumps({"importer": importer, "ids": _valid_ids(ids), "error": error[:200]}) + "\n")
    except OSError as exc:   # no state volume (host run): still loud via the ERROR line, never raised
        _log.error("[base_price] could not persist retry list for %s: %s", importer, exc)


def _retry_take() -> List[str]:
    """Atomically read-and-empty the retry file under an exclusive lock (an append that races the drain is never lost). A malformed
    line is skipped and logged on its own - it can neither stick the queue nor be silently dropped with the good ones."""
    import fcntl
    ids: List[str] = []
    try:
        with open(RETRY_FILE, "r+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            raw = fh.read()
            fh.seek(0)
            fh.truncate(0)
    except OSError:
        return []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            ids.extend(_valid_ids(_json.loads(line).get("ids") or []))
        except (ValueError, AttributeError):
            _log.error("[base_price] skipping a malformed line in %s: %.120r", RETRY_FILE, line)
    return list(dict.fromkeys(ids))


async def apply_and_report(conn, part_ids: Iterable[str], importer: str, usd_to_ils: Optional[float] = None,
                           verbose: bool = True) -> Dict[str, Any]:
    """The ONE call every importer makes after its offers exist. Applies the ownership rule to the touched parts (plus any ids a
    previous failed run left in RETRY_FILE), prints one parseable line, and NEVER raises: a failure returns {"error": ...}, is logged
    at ERROR with the importer and the part count, and the ids are queued for retry. Parts the rule could not price (no cost evidence)
    are counted under `reasons` (no_basis / other_class_only) - visible, not defaulted."""
    ids = _valid_ids(i for i in part_ids if i)
    # FAIL CLOSED: with the supplier VAT-basis registry unloaded a local supplier reads as 'exclusive' and an 'inclusive' supplier's
    # base_price would come out 18% high. Nothing is written; the ids wait in the retry queue for a call that can load the registry.
    if ids and not await ensure_vat_registry(conn=conn):
        msg = "vat_registry_unavailable"
        _retry_append(importer, ids, msg)
        print(f"ERROR [base_price] {importer}: {msg} - {len(ids)} parts NOT priced, queued for retry in {RETRY_FILE}", file=_sys.stderr, flush=True)
        return {"error": msg, "parts": len(ids), "scanned": 0, "updated": 0, "unchanged": 0, "skipped": len(ids), "reasons": {"vat_registry_unavailable": len(ids)}}
    retry = _retry_take()
    todo = list(dict.fromkeys(ids + retry))
    if not todo:
        res: Dict[str, Any] = {"scanned": 0, "updated": 0, "unchanged": 0, "skipped": 0, "reasons": {}}
        if verbose:
            print(f"[base_price] {importer}: no parts to price", flush=True)
        return res
    try:
        res = await apply_to_parts(conn, todo, usd_to_ils)
        res["retried"] = len(retry)
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {str(exc)[:160]}"
        _retry_append(importer, todo, msg)
        print(f"ERROR [base_price] {importer}: rule FAILED for {len(todo)} parts ({msg}); queued for retry in {RETRY_FILE}",
              file=_sys.stderr, flush=True)
        return {"error": msg, "parts": len(todo), "scanned": 0, "updated": 0, "unchanged": 0, "skipped": len(todo), "reasons": {"rule_failed": len(todo)}}
    unpriced = sum(v for k, v in res["reasons"].items() if k in ("no_basis", "other_class_only"))   # other_class_only also = a local supplier whose VAT basis is unknown
    if verbose or unpriced or res.get("updated"):
        print(f"[base_price] {importer}: scanned={res['scanned']} updated={res['updated']} unchanged={res['unchanged']} "
              f"skipped={res['skipped']} unpriced_no_cost_evidence={unpriced} retried={res['retried']} reasons={res['reasons']}", flush=True)
    return res
