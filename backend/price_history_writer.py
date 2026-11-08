"""
Script: price_history_writer.py
Purpose: ONE shared way for the SKU-upsert importers to leave a `price_history` row when an EXISTING offer's price
         changes (OFFER_DEDUPLICATION_POLICY 1.0.0 class C: a price update is recorded, never silent).
Process:
  tracker = PriceHistoryTracker(conn, source)         # asyncpg connection (PriceHistoryTrackerSync for psycopg2)
  (record_row_change(conn, old_offer, new_price, source) is the per-row variant for the big harvest importers)
  await tracker.snapshot(supplier_id)                 # BEFORE the first upsert: id -> (price_ils, price_usd) of the supplier
  ... importer upserts supplier_parts exactly as before ...
  await tracker.flush()                               # AFTER the last upsert: one history row per offer whose price_ils changed
  - rows that did not exist at snapshot time (first inserts) write NO history; unchanged prices write none; running the
    importer again with the same data writes none (idempotent). change_pct is clamped to NUMERIC(7,4); usd columns use the
    row's own price_usd (NOT NULL column -> 0 when the row has none). No schema change, no trigger, no pricing logic.
  - a failure here is logged and swallowed: history is an audit trail and must never abort an import.
Data Imported/Modified: price_history (INSERT only).
Data Sources: supplier_parts (read).
Missing Data Delegation: n/a.
Last Updated: 2026-11-06
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("price_history_writer")

_BATCH = 5000
_Row = Tuple[Optional[float], Optional[float]]  # (price_ils, price_usd)

_INSERT = """
INSERT INTO price_history (id, supplier_part_id, old_price_ils, new_price_ils, old_price_usd, new_price_usd,
                           change_pct, source, created_at)
SELECT gen_random_uuid(), t.id, t.o_ils, t.n_ils, t.o_usd, COALESCE(t.n_usd, 0), t.pct, {src}, NOW()
FROM unnest({ids}::uuid[], {o_ils}::numeric[], {n_ils}::numeric[], {o_usd}::numeric[], {n_usd}::numeric[], {pct}::numeric[])
     AS t(id, o_ils, n_ils, o_usd, n_usd, pct)
"""


def _f(v) -> Optional[float]:
    return None if v is None else float(v)


def _changes(before: Dict[str, _Row], after: Dict[str, _Row]) -> List[tuple]:
    out = []
    for spid, (o_ils, o_usd) in before.items():
        cur = after.get(spid)
        if cur is None:
            continue
        n_ils, n_usd = cur
        if n_ils is None or (o_ils is not None and round(o_ils, 2) == round(n_ils, 2)):
            continue
        pct = None
        if o_ils is not None and o_ils > 0:
            pct = max(-999.9999, min(999.9999, round((n_ils - o_ils) / o_ils * 100, 4)))
        out.append((spid, o_ils, n_ils, o_usd, n_usd, pct))
    return out


async def record_row_change(conn, old_offer, new_price_ils, source: str) -> int:
    """Per-row variant for high-volume harvest importers (no whole-supplier snapshot): `old_offer` is the row fetched
    BEFORE the upsert (id, price_ils, price_usd) or None for a first insert. Writes one history row when price_ils changed."""
    try:
        if old_offer is None or new_price_ils is None:
            return 0
        ch = _changes({str(old_offer["id"]): (_f(old_offer["price_ils"]), _f(old_offer["price_usd"]))},
                      {str(old_offer["id"]): (_f(new_price_ils), _f(old_offer["price_usd"]))})
        if not ch:
            return 0
        c = ch[0]
        async with conn.transaction():  # savepoint when already in a transaction: a failed audit row must not poison it
            await conn.execute(_INSERT.format(src="$1", ids="$2", o_ils="$3", n_ils="$4", o_usd="$5", n_usd="$6", pct="$7"),
                               source[:50], [c[0]], [c[1]], [c[2]], [c[3]], [c[4]], [c[5]])
        return 1
    except Exception as exc:  # audit trail only - never abort the import
        log.warning("price_history row write failed (%s): %s", source, exc)
        return 0


class PriceHistoryTracker:
    """asyncpg flavour."""

    def __init__(self, conn, source: str):
        self.conn, self.source = conn, source[:50]
        self._before: Dict[str, _Row] = {}
        self._suppliers: set = set()

    async def snapshot(self, supplier_id) -> int:
        if not supplier_id or str(supplier_id) in self._suppliers:
            return 0
        sid = str(supplier_id)
        try:
            rows = await self.conn.fetch("SELECT id::text, price_ils, price_usd FROM supplier_parts WHERE supplier_id = $1::uuid", sid)
        except Exception as exc:  # audit trail only - never abort the import
            log.warning("price_history snapshot failed (%s): %s", self.source, exc)
            return 0
        self._suppliers.add(sid)
        for r in rows:
            self._before[r[0]] = (_f(r[1]), _f(r[2]))
        return len(rows)

    async def flush(self) -> int:
        """Write the history rows; returns how many. Safe to call once at the end of a run."""
        if not self._before:
            return 0
        try:
            after: Dict[str, _Row] = {}
            for sid in self._suppliers:
                for r in await self.conn.fetch("SELECT id::text, price_ils, price_usd FROM supplier_parts WHERE supplier_id = $1::uuid", sid):
                    after[r[0]] = (_f(r[1]), _f(r[2]))
            ch = _changes(self._before, after)
            for i in range(0, len(ch), _BATCH):
                part = ch[i:i + _BATCH]
                async with self.conn.transaction():  # savepoint if the importer holds a transaction
                    await self.conn.execute(
                        _INSERT.format(src="$1", ids="$2", o_ils="$3", n_ils="$4", o_usd="$5", n_usd="$6", pct="$7"),
                        self.source, [c[0] for c in part], [c[1] for c in part], [c[2] for c in part],
                        [c[3] for c in part], [c[4] for c in part], [c[5] for c in part])
            self._before = {}
            self._suppliers = set()
            return len(ch)
        except Exception as exc:  # audit trail only - never abort the import
            log.warning("price_history flush failed (%s): %s", self.source, exc)
            return 0


class PriceHistoryTrackerSync:
    """psycopg2 flavour (cursor-based)."""

    def __init__(self, cur, source: str):
        self.cur, self.source = cur, source[:50]
        self._before: Dict[str, _Row] = {}
        self._suppliers: set = set()

    def snapshot(self, supplier_id) -> int:
        if not supplier_id or str(supplier_id) in self._suppliers:
            return 0
        sid = str(supplier_id)
        self.cur.execute("SAVEPOINT price_history_snap")
        try:
            self.cur.execute("SELECT id::text, price_ils, price_usd FROM supplier_parts WHERE supplier_id = %s::uuid", (sid,))
            rows = self.cur.fetchall()
            self.cur.execute("RELEASE SAVEPOINT price_history_snap")
        except Exception as exc:
            self.cur.execute("ROLLBACK TO SAVEPOINT price_history_snap")
            log.warning("price_history snapshot failed (%s): %s", self.source, exc)
            return 0
        self._suppliers.add(sid)
        for r in rows:
            self._before[r[0]] = (_f(r[1]), _f(r[2]))
        return len(rows)

    def flush(self) -> int:
        if not self._before:
            return 0
        self.cur.execute("SAVEPOINT price_history_flush")
        try:
            after: Dict[str, _Row] = {}
            for sid in self._suppliers:
                self.cur.execute("SELECT id::text, price_ils, price_usd FROM supplier_parts WHERE supplier_id = %s::uuid", (sid,))
                for r in self.cur.fetchall():
                    after[r[0]] = (_f(r[1]), _f(r[2]))
            ch = _changes(self._before, after)
            for i in range(0, len(ch), _BATCH):
                part = ch[i:i + _BATCH]
                self.cur.execute(
                    _INSERT.format(src="%s", ids="%s", o_ils="%s", n_ils="%s", o_usd="%s", n_usd="%s", pct="%s"),
                    (self.source, [c[0] for c in part], [c[1] for c in part], [c[2] for c in part],
                     [c[3] for c in part], [c[4] for c in part], [c[5] for c in part]))
            self.cur.execute("RELEASE SAVEPOINT price_history_flush")
            self._before = {}
            self._suppliers = set()
            return len(ch)
        except Exception as exc:
            self.cur.execute("ROLLBACK TO SAVEPOINT price_history_flush")
            log.warning("price_history flush failed (%s): %s", self.source, exc)
            return 0
