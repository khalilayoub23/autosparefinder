"""
Script: maintenance/fitment_impact_check.py
Purpose: READ-ONLY impact check of the vehicle-model fitment rule (vehicle_model_match) across
         every customer model, against the previous behaviour. FIXES_TRACKER #69 §9-10.
Process:
  For each distinct (manufacturer, model) in `vehicles` (or --probe make|model):
    1. open a NEW session; SET TRANSACTION READ ONLY + statement_timeout; one bounded transaction
    2. baseline   = every fitment model the old word-prefix rule matched (with row counts)
    3. previous   = the first candidate rule (frozen copy below), same inputs
    4. corrected  = vehicle_model_match.explain() with registry names + own-series row counts
    5. write one JSON line: rows per rule, removed models with rows + reason
    6. close the session (success, error or interruption), pause --sleep seconds
  WHY one session per model: the first ad-hoc version ran 6,487 models in ONE transaction for
  70 minutes; an ALTER TABLE queued behind its shared lock and every fitment read/write queued
  behind the ALTER (2026-10-19 18:19-18:33 UTC).
Data Imported/Modified: NONE (read-only transactions). Output: --out JSONL file.
Data Sources: vehicles, part_vehicle_fitment, vehicle_market_il.
Missing Data Delegation: n/a.
Last Updated: 2026-10-19
Run: docker exec autospare_backend python3 /app/maintenance/fitment_impact_check.py --out /tmp/impact.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, "/app")

from sqlalchemy import text  # noqa: E402

import vehicle_model_match as vm  # noqa: E402

STATEMENT_TIMEOUT = "60s"


# ── frozen copy of the PREVIOUS candidate rule (first version of #69 §9), for comparison ──
_PREV_VARIANT = frozenset({
    "hybrid", "hev", "phev", "mhev", "hsd", "plug", "in", "plugin", "ev", "electric", "e",
    "sdn", "sedan", "saloon", "hb", "hatch", "hatchback", "ts", "sw", "estate", "wagon",
    "combi", "kombi", "touring", "tourer", "sportswagon", "sportback", "coupe", "cabrio",
    "cabriolet", "convertible", "roadster", "van", "4x4", "awd", "4wd", "2wd", "diesel", "petrol",
    "turbo", "auto", "manual", "lwb", "swb", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
    "fl", "facelift", "hv", "hybryd", "door", "doors", "dr", "5dr", "3dr", "4dr", "2dr", "liftback",
    "fastback", "pickup", "pick", "up", "cab", "crew", "double", "single"})
_PREV_CODE = re.compile(r"^(\d\.\d|\d{1,2}d|[a-z]?\d{2,4}[a-z]?)$")


def _prev_variant_ext(name: str, base: str) -> bool:
    ext = name[len(base):].split()
    return bool(ext) and all(w in _PREV_VARIANT or _PREV_CODE.match(w) for w in ext)


def _prev_distinct(base: str, registered: Iterable[str]) -> List[str]:
    return [r for r in registered if vm._word_extends(r, base) and not _prev_variant_ext(r, base)]


def previous_candidate_fits(f: str, c: str, registered: Set[str]) -> bool:
    f, c = vm.norm(f), vm.norm(c)
    if not f or not c:
        return False
    if f == c:
        return True
    if vm._word_extends(f, c):
        return not any(vm._inside(f, r) and not vm._inside(c, r) for r in _prev_distinct(c, registered))
    if vm._word_extends(c, f):
        if registered:
            if f not in registered:
                return False
            return not any(vm._inside(c, r) for r in _prev_distinct(f, registered))
        return len(f.split()) > 1 or len(c.split()) == 1
    return False


async def evaluate_model(db, make: str, model: str) -> Dict[str, Any]:
    """One customer model, inside the caller's (bounded) read-only transaction."""
    await db.execute(text("SET TRANSACTION READ ONLY"))
    await db.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
    terms, fwd = vm.candidate_terms([model])
    rows = (await db.execute(text(
        "SELECT LOWER(TRIM(model)), count(*) FROM part_vehicle_fitment "
        "WHERE (LOWER(TRIM(manufacturer)) = :mk OR LOWER(TRIM(manufacturer)) LIKE '%' || :mk || '%') "
        "AND (LOWER(TRIM(model)) = ANY(:t) OR LOWER(TRIM(model)) LIKE :f || ' %') GROUP BY 1"),
        {"mk": make, "t": terms, "f": fwd[0] if fwd else ""})).all()
    counts = {m: int(n) for m, n in rows if m}
    series = {vm.norm(m): n for m, n in counts.items()}
    registered = await vm._registered_names(db, [make])
    mfr_sql = ["(LOWER(TRIM(manufacturer)) = :mk0 OR LOWER(TRIM(manufacturer)) LIKE '%' || :mk0 || '%')"]
    gen = await vm._generation_letters_for(db, mfr_sql, {"mk0": make}, [model], list(counts))
    out = {"make": make, "model": model, "registry_names": len(registered),
           "baseline_rows": sum(counts.values()), "previous_rows": 0, "corrected_rows": 0,
           "removed": [], "previous_removed": [], "kept_reasons": {}}
    for m, n in sorted(counts.items(), key=lambda x: -x[1]):
        if previous_candidate_fits(m, model, registered):
            out["previous_rows"] += n
        else:
            out["previous_removed"].append([m, n])
        ok, why = vm.explain(m, model, registered, series, [make], gen)
        if ok:
            out["corrected_rows"] += n
            key = why.split(":")[0]
            out["kept_reasons"][key] = out["kept_reasons"].get(key, 0) + n
        else:
            out["removed"].append([m, n, why])
    return out


async def run(pairs: List[Tuple[str, str]], session_factory: Callable, out_path: Optional[str],
              sleep_s: float = 0.05, log: Callable = print) -> Dict[str, Any]:
    stats = {"models": 0, "errors": 0, "max_txn_s": 0.0, "sessions_opened": 0, "sessions_closed": 0}
    fh = open(out_path, "w", encoding="utf-8") if out_path else None
    try:
        for i, (make, model) in enumerate(pairs, 1):
            t0 = time.monotonic()
            stats["sessions_opened"] += 1
            try:
                async with session_factory() as db:          # one short transaction per model
                    rec = await evaluate_model(db, make, model)
            except Exception as exc:                          # recorded, never retried in a loop
                stats["errors"] += 1
                rec = {"make": make, "model": model, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            finally:
                stats["sessions_closed"] += 1
            stats["max_txn_s"] = max(stats["max_txn_s"], time.monotonic() - t0)
            stats["models"] += 1
            if fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if i % 250 == 0:
                log(f"[impact] {i}/{len(pairs)} models, errors={stats['errors']}, "
                    f"max_txn={stats['max_txn_s']:.1f}s")
            if sleep_s:
                await asyncio.sleep(sleep_s)
    finally:
        if fh:
            fh.close()
    return stats


async def _customer_models(session_factory) -> List[Tuple[str, str]]:
    async with session_factory() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        rows = (await db.execute(text(
            "SELECT DISTINCT lower(trim(manufacturer)), lower(trim(model)) FROM vehicles "
            "WHERE manufacturer IS NOT NULL AND model IS NOT NULL AND trim(model) <> '' "
            "ORDER BY 1, 2"))).all()
    return [(a, b) for a, b in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description="Read-only fitment rule impact check")
    ap.add_argument("--out", required=True, help="JSONL output path")
    ap.add_argument("--probe", action="append", default=[], help="make|model (repeatable)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.05)
    a = ap.parse_args()
    from BACKEND_DATABASE_MODELS import async_session_factory

    async def go():
        pairs = ([tuple(p.lower().split("|", 1)) for p in a.probe] if a.probe
                 else await _customer_models(async_session_factory))
        if a.limit:
            pairs = pairs[:a.limit]
        stats = await run(pairs, async_session_factory, a.out, a.sleep)
        print("[impact] DONE", json.dumps(stats), flush=True)
    asyncio.run(go())


if __name__ == "__main__":
    main()
