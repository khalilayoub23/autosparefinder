"""
Script: price_report_metrics.py
Purpose: The price-update / coverage section of the owner status report, each metric traceable to ONE query below and
         kept separate from the catalogue-size line (active parts). Never changes the definition of "active".
Process: collect(db) runs small, individually time-boxed queries (any failure -> that metric is None = "unavailable",
         never a made-up zero); format_lines(m) renders them (Hebrew labels, like the rest of the report).
Metrics (source):
  last_sync         catalog_versions WHERE source='supplier_manager_agent' latest row: created_at, parts_updated,
                    description (carries price_sync_report.price_sync_breakdown: per-source checked/updated/no_listing/blocked/errors)
  per_source_24h    price_history grouped by source over 7 days: rows in last 24h + last write time (ebay_sync, aliexpress_sync, ...)
  offers_estimate   pg_class.reltuples of supplier_parts (planner estimate, labelled approximate - exact COUNT times out at 4M+)
  parts_with_price  COUNT(parts_catalog WHERE is_active AND base_price > 0)   (digest only)
  sync_job          job_registry latest 'sync_prices': status + completed_at; stale when older than STALE_H
Data Imported/Modified: none (read-only).
Last Updated: 2026-11-18
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text

STALE_H = 36
SOURCES = ("ebay_sync", "aliexpress_sync")


async def _one(db, sql: str, timeout_s: int = 20):
    try:
        await db.execute(text(f"SET LOCAL statement_timeout = '{timeout_s * 1000}'"))
        return (await db.execute(text(sql))).fetchall()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        return None


async def collect(db, digest: bool = False) -> Dict[str, Any]:
    m: Dict[str, Any] = {}
    r = await _one(db, "SELECT created_at, parts_updated, description FROM catalog_versions "
                       "WHERE source='supplier_manager_agent' ORDER BY created_at DESC LIMIT 1")
    m["last_sync"] = r[0] if r else None
    r = await _one(db, "SELECT source, COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') AS n24, MAX(created_at) AS last "
                       "FROM price_history WHERE created_at > NOW() - INTERVAL '7 days' GROUP BY source")
    m["per_source"] = {x.source: (x.n24, x.last) for x in r} if r is not None else None
    r = await _one(db, "SELECT status, completed_at, started_at FROM job_registry WHERE job_name = 'sync_prices' "
                       "ORDER BY started_at DESC LIMIT 1")
    m["sync_job"] = r[0] if r else None
    if digest:
        r = await _one(db, "SELECT reltuples::bigint FROM pg_class WHERE relname='supplier_parts' AND relkind='r'")
        m["offers_estimate"] = int(r[0][0]) if r else None
        r = await _one(db, "SELECT COUNT(*) FROM parts_catalog WHERE is_active AND base_price > 0", timeout_s=90)
        m["parts_with_price"] = int(r[0][0]) if r else None
    return m


def format_lines(m: Dict[str, Any], now: Optional[datetime] = None) -> List[str]:
    now = now or datetime.utcnow()
    out: List[str] = []
    ls = m.get("last_sync")
    if ls is None:
        out.append("*עדכוני מחיר:* אין נתונים (נתוני הניטור לא זמינים)")
    else:
        age_h = int((now - ls.created_at).total_seconds() // 3600)
        stale = " ⚠️ מיושן" if age_h > STALE_H else ""
        out.append(f"*עדכוני מחיר (eBay/AliExpress), ריצה אחרונה לפני {age_h} שע':* {ls.parts_updated} עודכנו{stale}")
        why = (ls.description or "").split("synthetic drift disabled;")[-1].strip()
        if "checked=" in why:
            out.append(f"  {why}")
    ps = m.get("per_source")
    if ps is not None:
        for src in SOURCES:
            n24, last = ps.get(src, (0, None))
            out.append(f"  {src}: {n24} שינויי מחיר ב-24 שע' · כתיבה אחרונה: {last.strftime('%d/%m %H:%M') if last else 'אין ב-7 ימים'}")
    sj = m.get("sync_job")
    if sj is not None and sj.status not in ("completed", "running"):
        out.append(f"  ❌ sync_prices: {sj.status}")
    if m.get("offers_estimate") is not None:
        out.append(f"*הצעות ספקים (הערכה):* ~{m['offers_estimate']:,}")
    if m.get("parts_with_price") is not None:
        out.append(f"*חלקים פעילים עם מחיר ללקוח:* {m['parts_with_price']:,}")
    return out
