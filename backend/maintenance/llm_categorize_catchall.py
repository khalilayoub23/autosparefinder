"""
Script: maintenance/llm_categorize_catchall.py
Purpose: Cure the catch-all residue the keyword rules structurally cannot place —
         parts whose names carry a real noun that is only meaningful IN CONTEXT
         ("Trans Pan", "Reading Lmp Assembly", "גומי משקוף קד' ימין"). Asks the
         LLM about the WHOLE PART (name + origin context), not about a token.
Process:
  1. Claim a keyset-cursor page of active parts still in the catch-all.
  2. Send them to the LLM in batches WITH their origin context (vehicle make,
     source, description, supplier SKU) and a hard instruction that UNKNOWN is a
     good answer.
  3. Write ONLY canonical categories, stamping provenance so every write is
     exactly reversible.
  4. Feed the accepted answers into the existing keyword learner, so a shape the
     LLM taught us once is handled FREE by the deterministic matcher next time.
Data Imported/Modified: parts_catalog.category (catch-all rows only, never a row
  that already holds a real category), plus specifications.category_by /
  category_prev for provenance. Nothing else.
Data Sources: the LLM via hf_client.hf_text. No external HTTP.
Missing Data Delegation: a part the LLM answers UNKNOWN for is LEFT in the
  catch-all — that is a correct outcome, not a failure.
Last Updated: 2026-08-06

WHY PER-PART AND NOT PER-TOKEN (measured 2026-08-05, do not undo this):
  The keyword learner had 16,278 proposals queued and the top ones were
  `key→electrical`, `line→electrical`, `air→filters`, `duct→interior-comfort`.
  They are not approvable, and not because the LLM was wrong — because the
  ABSTRACTION is wrong. A duct can belong to five families; one token cannot
  carry one category. The full name can. Token learning is right for the head of
  the distribution and structurally incapable of curing its tail.

WHY NOT THE LOCAL EMBEDDING MODEL (measured on 1,500 real catch-all rows):
  At a 0.75 floor it fires on 20.9% with roughly 40-50% precision on THIS
  population — it filed `אומים` (nuts) as body-exterior at 0.94 and `הסעה
  מנתב"ג` (an airport shuttle service, not a part) as gearbox at 0.92. Its
  published 94-96% precision was measured on the general catalogue, and the
  residue is by definition what every cheaper method already failed on, so a
  general-population benchmark OVERSTATES accuracy on a backlog. The model
  cannot abstain: nearest-exemplar always returns something, and MIN_SCORE is a
  similarity floor, not a competence signal. The LLM's UNKNOWN is what makes it
  safe here — measured 70% abstention, and the abstentions were correct
  (fasteners, codes, supersessions, apparel, manuals).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")

import asyncpg  # noqa: E402

from category_map import CANONICAL, CATCH_ALL  # noqa: E402
import hf_client  # noqa: E402

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
STATE_DIR = Path(__file__).resolve().parent.parent / "state"
CURSOR_PATH = STATE_DIR / "llm_categorize_cursor.json"

# The three mandatory limits for any LLM-calling job (CLAUDE.md). This is a
# BOUNDED backfill, not a background loop, but the limits still apply — the
# 2026-07-27 quota blowout was an unbounded caller, and a bounded one is only
# bounded while the ceilings exist.
BATCH = int(os.getenv("LLM_CAT_BATCH", "60"))            # parts per LLM call
MAX_CALLS = int(os.getenv("LLM_CAT_MAX_CALLS", "60"))    # per invocation
MIN_INTERVAL_S = float(os.getenv("LLM_CAT_MIN_INTERVAL_S", "1.0"))
MAX_SECONDS = int(os.getenv("LLM_CAT_MAX_SECONDS", "0"))  # 0 = no time budget
# How many consecutive REST cycles to tolerate before concluding the provider is
# genuinely down rather than flaky. Each cycle already contains 4 retries.
MAX_CONSEC_FAIL = int(os.getenv("LLM_CAT_MAX_CONSEC_FAIL", "8"))

CATS = sorted(CANONICAL)

PROMPT_HEAD = (
    "You are categorizing CAR PARTS for a parts marketplace.\n"
    f"Allowed categories (choose EXACTLY one, or UNKNOWN):\n{', '.join(CATS)}\n\n"
    "RULES:\n"
    "- Answer UNKNOWN when the name is a bare fastener (bolt/nut/washer/screw/rivet/shim),\n"
    "  a pure code, a supersession pointer, a position word, or genuinely ambiguous.\n"
    "  UNKNOWN is a GOOD answer — a wrong category is worse than none.\n"
    "- vehicle_make is the CAR brand, never the part's category.\n"
    "- Some names are Hebrew or Arabic. Some are not car parts at all (services,\n"
    "  apparel, manuals, merchandise); answer UNKNOWN for those unless a listed\n"
    "  category genuinely fits.\n"
    "- A name that is ONLY a car brand ('Land Rover'), or a brand plus a position\n"
    "  word ('Land Rover Outer'), is ALWAYS UNKNOWN. It names no part.\n"
    "- Answer for what the part IS, not what it sits near: a side WINDOW is\n"
    "  body-exterior (not lighting); a SYNCHRONIZER RING is gearbox (not\n"
    "  belts-chains); a bare 'Air Pump' with no context is UNKNOWN.\n"
    "- Be CONSISTENT: the same name must always get the same answer.\n\n"
    'Return ONLY a JSON array: [{"n":0,"cat":"brakes"}, {"n":1,"cat":"UNKNOWN"}]\n\n'
    "PARTS:\n"
)


def _load_cursor() -> str:
    try:
        return json.loads(CURSOR_PATH.read_text()).get("last_id") or ""
    except Exception:
        return ""


def _save_cursor(last_id: str) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        CURSOR_PATH.write_text(json.dumps({"last_id": last_id, "at": time.time()}))
    except Exception as exc:  # a cursor we cannot persist is not fatal
        print(f"[llmcat] cursor save failed: {type(exc).__name__}: {exc}")


async def _fetch_page(conn, last_id: str, limit: int):
    # Keyset pagination: a page that yields no WRITES must still advance, or the
    # job re-reads the same head forever (the exact defect that stalled the
    # improve-only recheck).
    return await conn.fetch(
        """
        SELECT pc.id, pc.name, pc.description, pc.manufacturer,
               pc.specifications->>'source' AS source,
               sp.supplier_sku
        FROM parts_catalog pc
        LEFT JOIN LATERAL (
            SELECT supplier_sku FROM supplier_parts s
            WHERE s.part_id = pc.id LIMIT 1) sp ON TRUE
        WHERE pc.is_active AND pc.category = $1
          AND pc.name ~ '[A-Za-zא-ת]{3,}'
          AND ($2 = '' OR pc.id > CAST($2 AS uuid))
        ORDER BY pc.id
        LIMIT $3
        """,
        CATCH_ALL, last_id, limit,
    )


async def _load_brands(conn) -> set:
    """The live brand list, split on spaces AND hyphens (so 'Mercedes-Benz'
    blocks both halves) — the same shape the keyword learner's blocklist uses."""
    brands = set()
    rows = await conn.fetch("""
        SELECT DISTINCT lower(manufacturer) m FROM parts_catalog
        WHERE manufacturer IS NOT NULL AND btrim(manufacturer) <> ''
    """)
    for r in rows:
        for w in re.split(r"[\s\-]+", r["m"] or ""):
            if len(w) > 2:
                brands.add(w)
    return brands


def _is_brand_only(name: str, brands: set) -> bool:
    """A name that is nothing but a car brand (+ position/generic filler) can
    never be categorized. The LLM was ASKED to refuse these and still wrote
    'Land Rover' three times, to two different categories — so this is enforced
    in code. A prompt rule is not a guard (CLAUDE.md: defense in depth)."""
    toks = [t for t in re.split(r"[^A-Za-zא-ת]+", (name or "").lower()) if len(t) > 1]
    if not toks:
        return True
    return all(t in brands or t in _FILLER for t in toks)


_FILLER = {"outer", "inner", "front", "rear", "left", "right", "upper", "lower",
           "side", "set", "kit", "assy", "assembly", "sub", "genuine", "oem",
           "new", "part", "parts", "for", "and", "the", "with"}


def _context(rows):
    items = []
    for i, r in enumerate(rows):
        ctx = {"n": i, "name": r["name"]}
        if r["manufacturer"]:
            ctx["vehicle_make"] = r["manufacturer"]
        if r["source"]:
            ctx["source"] = r["source"]
        if (r["description"] or "").strip():
            ctx["desc"] = r["description"][:120]
        if r["supplier_sku"]:
            ctx["sku"] = r["supplier_sku"]
        items.append(ctx)
    return items


async def _classify(items, attempts: int = 4):
    """-> {n: category} for canonical answers only. UNKNOWN/invalid are dropped.

    Returns None only after `attempts` consecutive provider failures. The
    provider throws transient 500/502/429 several times an hour; a backfill that
    aborts on the first one would need babysitting for its whole run, and the
    work already done would look like a stall rather than a pause.
    """
    prompt = PROMPT_HEAD + json.dumps(items, ensure_ascii=False)
    out = None
    for attempt in range(attempts):
        try:
            out = await hf_client.hf_text(prompt, max_tokens=3000)
            break
        except Exception as exc:
            wait = 2 ** attempt
            print(f"[llmcat] LLM call failed ({attempt + 1}/{attempts}): "
                  f"{type(exc).__name__}: {str(exc)[:90]} — retrying in {wait}s")
            if attempt == attempts - 1:
                return None
            await asyncio.sleep(wait)
    txt = (out or "").strip()
    s, e = txt.find("["), txt.rfind("]")
    if s < 0 or e < 0:
        print("[llmcat] no JSON array in reply")
        return {}
    try:
        ans = json.loads(txt[s:e + 1])
    except Exception:
        print("[llmcat] unparseable JSON in reply")
        return {}
    good = {}
    for a in ans:
        if not isinstance(a, dict):
            continue
        cat = (a.get("cat") or "").strip()
        n = a.get("n")
        if isinstance(n, int) and cat in CANONICAL:
            good[n] = cat
    return good


async def _write(conn, part_id, cat, dry: bool):
    if dry:
        return
    # Provenance is what makes this survivable: category_by names the decider and
    # category_prev records where the part came from, so `undo` is exact.
    await conn.execute(
        """
        UPDATE parts_catalog
           SET category = $2,
               specifications = COALESCE(specifications, '{}'::jsonb)
                   || jsonb_build_object('category_by', 'llm_catchall',
                                         'category_prev', COALESCE(category, '')),
               updated_at = NOW()
         WHERE id = $1 AND category = $3
        """,
        part_id, cat, CATCH_ALL,
    )


async def undo(conn) -> int:
    """Reverse every write this script made. One statement, exactly scoped."""
    res = await conn.execute(
        """
        UPDATE parts_catalog
           SET category = specifications->>'category_prev',
               specifications = specifications - 'category_by' - 'category_prev',
               updated_at = NOW()
         WHERE specifications->>'category_by' = 'llm_catchall'
        """
    )
    return int(res.split()[-1]) if res else 0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N parts examined (0 = use --max-calls)")
    ap.add_argument("--dry-run", action="store_true",
                    help="classify and report, write nothing")
    ap.add_argument("--undo", action="store_true",
                    help="revert every category this script wrote, then exit")
    ap.add_argument("--restart", action="store_true", help="reset the cursor")
    args = ap.parse_args()

    conn = await asyncpg.connect(DB, statement_cache_size=0)
    await conn.execute("SET statement_timeout='300s'")

    if args.undo:
        n = await undo(conn)
        await conn.close()
        print(f"[llmcat] reverted {n:,} parts")
        return 0

    if args.restart and CURSOR_PATH.exists():
        CURSOR_PATH.unlink()

    brands = await _load_brands(conn)
    print(f"[llmcat] brand guard loaded: {len(brands)} brand words")
    last_id = _load_cursor()
    started = time.time()
    calls = examined = written = unknown = blocked = consec_fail = 0
    max_calls = MAX_CALLS
    if args.limit:
        max_calls = max(1, -(-args.limit // BATCH))

    while calls < max_calls:
        if MAX_SECONDS and time.time() - started > MAX_SECONDS:
            print("[llmcat] time budget reached — stopping cleanly")
            break

        rows = await _fetch_page(conn, last_id, BATCH)
        if not rows:
            print("[llmcat] cursor reached the end of the catch-all — restarting next run")
            if CURSOR_PATH.exists():
                CURSOR_PATH.unlink()
            break

        items = _context(rows)
        good = await _classify(items)
        calls += 1
        if good is None:
            # A multi-hour backfill WILL meet provider outages (measured: this
            # run died after 3.2h and 404 calls on one 500). Stopping loses the
            # remaining hours; the cursor is already durable, so the correct
            # response is to rest and continue. Only a sustained outage
            # (MAX_CONSEC_FAIL rest cycles) ends the run.
            consec_fail += 1
            if consec_fail >= MAX_CONSEC_FAIL:
                print(f"[llmcat] provider down for {consec_fail} consecutive "
                      f"cycles — stopping; rerun resumes from the cursor")
                break
            rest = min(30 * (2 ** consec_fail), 900)
            print(f"[llmcat] provider failure {consec_fail}/{MAX_CONSEC_FAIL} — "
                  f"resting {rest}s, then continuing from the same page")
            await asyncio.sleep(rest)
            continue              # same page: nothing was written or skipped
        consec_fail = 0

        for i, r in enumerate(rows):
            cat = good.get(i)
            if cat and _is_brand_only(r["name"], brands):
                cat = None        # code overrules the model on a name-less name
                blocked += 1
            if cat:
                await _write(conn, r["id"], cat, args.dry_run)
                written += 1
            else:
                unknown += 1
        examined += len(rows)

        # Advance the cursor on EVERY page, including one where nothing was
        # written — otherwise UNKNOWN-heavy pages would be re-read forever.
        last_id = str(rows[-1]["id"])
        _save_cursor(last_id)

        print(f"[llmcat] call {calls}/{max_calls} · examined {examined:,} · "
              f"categorized {written:,} · unknown {unknown:,}")
        if MIN_INTERVAL_S:
            await asyncio.sleep(MIN_INTERVAL_S)

    await conn.close()
    rate = (100 * written / examined) if examined else 0
    print(f"\n[llmcat] {'DRY RUN — ' if args.dry_run else ''}"
          f"examined {examined:,} · categorized {written:,} ({rate:.1f}%) · "
          f"left in catch-all {unknown:,} · brand-guard blocked {blocked:,} · "
          f"{calls} LLM calls · {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
