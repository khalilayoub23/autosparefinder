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
Last Updated: 2026-11-16
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
               EXISTS (SELECT 1 FROM supplier_parts sp JOIN suppliers s ON s.id = sp.supplier_id
                       WHERE sp.part_id = pc.id AND {pp.VISIBLE_OFFER_SQL} AND {cost} > 0) AS has_visible,
               (EXISTS (SELECT 1 FROM supplier_parts sp WHERE sp.part_id = pc.id AND sp.price_ils > 0 AND {rank} <> 0)
                AND NOT EXISTS (SELECT 1 FROM supplier_parts sp
                                WHERE sp.part_id = pc.id AND sp.price_ils > 0 AND {rank} = 0)) AS other_class_only
        FROM parts_catalog pc
        LEFT JOIN LATERAL (
            SELECT sp.price_ils, sp.price_usd, s.name, s.country
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
    est = pp.catalog_estimate_price(row.importer_price_ils, row.max_price_ils, row.online_price_ils)
    if est is not None:
        return est.net, "estimate"
    return None, "no_basis"


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
    rate = usd_to_ils if usd_to_ils is not None else await get_rate(conn)
    sql = _asyncpg_sql(inputs_sql(True))
    c = {"scanned": 0, "updated": 0, "unchanged": 0, "skipped": 0, "reasons": {}}
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
