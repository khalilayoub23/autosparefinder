#!/usr/bin/env python3
"""
Script:  maintenance/fix_reversed_hebrew_names.py
Purpose: Repair part names whose Hebrew was stored in VISUAL (reversed) order,
         and lift misplaced warranty text out of name_he into metadata.

Why (owner-confirmed 2026-07-28): the owner identified
`L הדימ ןבל םודא םישנ תצלוח` as a real product — "חולצת נשים אדום לבן מידה L".
The CONTENT is valid; only the character order is wrong. So these rows must be
REPAIRED, not discarded.

DETECTION — do not use a keyword list. Hebrew final forms (ך ם ן ף ץ) can only
appear at the END of a word. A word that STARTS with one is proof the string was
stored in visual order. That test is exact and language-based, not a guess; it
found 3,674 rows where a keyword scan had found 1,048.

REVERSAL — reverse ONLY the Hebrew runs, never the whole string. A naive
`s[::-1]` corrupts every number and Latin token: `24` → `42`, `GS330` → `033SG`.
Verified on real rows before writing.

TWO POPULATIONS, TWO TREATMENTS:
  1. name_he == a reversed WARRANTY string ('םישדוח 24' = 24 חודשים).
     These are 1,048 Porsche rows whose `name` is a perfectly good part name —
     the importer wrote the price list's warranty COLUMN into name_he. Nothing
     is delisted: the warranty is moved to specifications.warranty_months and
     name_he is cleared. (Owner: "convert them into metadata if they represent
     the warranty of a real product" — they do.)
  2. Everything else — a genuinely reversed NAME. Un-reverse it in place.

Data Modified: parts_catalog.name, parts_catalog.name_he, parts_catalog.specifications
Usage:  python3 /app/maintenance/fix_reversed_hebrew_names.py [--dry-run] [--limit N]
Last Updated:  2026-07-28
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time

import asyncpg

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

# A Hebrew final letter at the start of a word ⇒ the string is in visual order.
REVERSED_RE = re.compile(r"(?:^|\s)[ךםןףץ][֐-׿]")
# A Latin/numeric token, including the separators used inside part codes.
# Must contain at least one alphanumeric so bare punctuation is never touched.
_LATIN_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9/.+\-]*")
# A reversed warranty string: 'םישדוח <n>' == '<n> חודשים'
_WARRANTY_RE = re.compile(r"^\s*םישדוח\s*(\d+)\s*$")


# A word ENDING in a final form is proof that word is in CORRECT order.
CORRECT_RE = re.compile(r"[֐-׿][ךםןףץ](?=$|[\s,.\-\"'׳״])")


def looks_reversed(text: str) -> bool:
    return bool(text) and bool(REVERSED_RE.search(text))


def is_mixed(text: str) -> bool:
    """True when the string contains BOTH reversed and correctly-ordered Hebrew.

    Some rows are half-corrupted: 'םלוב UESSITROM זעזועים קד' has `םלוב`
    (reversed 'בולם') next to `זעזועים` (already correct). A whole-string
    reversal would repair the first half and DESTROY the second. There is no
    safe automatic repair for these, so they are skipped and reported for a
    human — silently corrupting good data is far worse than leaving it.
    """
    t = text or ""
    if not REVERSED_RE.search(t):
        return False
    if CORRECT_RE.search(t):
        return True
    # A final-form ending is only ONE proof of correctness; a string can be part
    # correct without containing any ('N בורג למשקולות קליפר אחורי ףל' — every
    # word but `ףל` is fine). category_map already holds hand-written Hebrew part
    # words, so use it as a dictionary: a real Hebrew part word standing as its
    # own WORD means part of that string is NOT reversed.
    #
    # Must be whole-word, not substring — a substring test fires on reversed text
    # by coincidence and wrongly quarantined the owner's shirt row.
    vocab = _hebrew_vocab()
    return any(w in vocab for w in re.findall(r"[֐-׿]{3,}", t))


_VOCAB: frozenset[str] | None = None


def _hebrew_vocab() -> frozenset[str]:
    """Hebrew keywords from category_map, ≥3 chars (shorter ones false-match)."""
    global _VOCAB
    if _VOCAB is None:
        words: set[str] = set()
        try:
            import category_map as cm
            for _canon, he_list, _en in cm.RULES:
                for kw in he_list:
                    for w in str(kw).split():
                        if len(w) >= 3 and re.fullmatch(r"[֐-׿]+", w):
                            words.add(w)
        except Exception:
            pass
        _VOCAB = frozenset(words)
    return _VOCAB


def unreverse(text: str) -> str:
    """Un-do visual-order storage.

    The WHOLE string was stored reversed — that is what also puts the words in
    the wrong order — so the repair is a full reversal. But a full reversal also
    flips Latin and numeric tokens ('24'→'42', 'GS330'→'033SG'), which would
    silently corrupt part identifiers. So each non-Hebrew run is flipped BACK
    afterwards.

    Only call this on strings that pass `looks_reversed()`. On correct Hebrew it
    would happily produce garbage — it has no way to tell on its own.
    """
    if not text:
        return text
    flipped = text[::-1]
    # Re-flip maximal runs of Latin/digits (plus the separators that live inside
    # part codes) so 033SG → GS330 and 06/01 → 10/60.
    return _LATIN_RUN.sub(lambda m: m.group(0)[::-1], flipped)


def warranty_months(text: str) -> int | None:
    m = _WARRANTY_RE.match(text or "")
    return int(m.group(1)) if m else None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    conn = await asyncpg.connect(DB, statement_cache_size=0)
    await conn.execute("SET statement_timeout = '300s'")
    t0 = time.monotonic()

    where = (r"is_active AND (name ~ '(^|\s)[ךםןףץ][֐-׿]' "
             r"OR name_he ~ '(^|\s)[ךםןףץ][֐-׿]')")
    lim = f"LIMIT {a.limit}" if a.limit else ""
    rows = await conn.fetch(
        f"SELECT id, COALESCE(name,'') n, COALESCE(name_he,'') nh, "
        f"COALESCE(specifications, '{{}}'::jsonb) sp FROM parts_catalog WHERE {where} {lim}")
    print(f"[revfix] {len(rows):,} rows detected as visual-order Hebrew", flush=True)

    warranty, renames, samples, mixed = [], [], [], []
    n_mixed = 0
    for r in rows:
        n, nh = r["n"], r["nh"]
        if is_mixed(n) or is_mixed(nh):
            n_mixed += 1                      # count ALL, sample only a few
            if len(mixed) < 8:
                mixed.append(n if is_mixed(n) else nh)
            continue
        wm = warranty_months(nh)
        if wm is not None and not looks_reversed(n):
            # Real part name + misplaced warranty column → metadata, keep the part.
            sp = r["sp"] if isinstance(r["sp"], dict) else json.loads(r["sp"] or "{}")
            sp["warranty_months"] = wm
            warranty.append((r["id"], json.dumps(sp, ensure_ascii=False)))
            continue
        n2 = unreverse(n) if looks_reversed(n) else n
        nh2 = "" if wm is not None else (unreverse(nh) if looks_reversed(nh) else nh)
        if n2 != n or nh2 != nh:
            renames.append((r["id"], n2, nh2))
            if len(samples) < 12:
                # Show the field that actually CHANGED. Showing `name` when the
                # edit is in name_he makes every sample look like a no-op.
                fld, b, af = ("name", n, n2) if n2 != n else ("name_he", nh, nh2)
                samples.append((fld, b, af))

    print(f"[revfix] warranty→metadata : {len(warranty):,}")
    print(f"[revfix] names un-reversed : {len(renames):,}")
    print(f"[revfix] SKIPPED (mixed order, needs a human): {n_mixed:,}\n")
    for x in mixed:
        print(f"   [skip] {x[:60]!r}")
    if mixed:
        print()
    for fld, b, af in samples:
        print(f"   [{fld}] {b[:50]!r}\n        -> {af[:50]!r}")

    if a.dry_run:
        print("\n[revfix] DRY RUN — nothing written")
        await conn.close()
        return

    for i in range(0, len(warranty), 1000):
        await conn.executemany(
            "UPDATE parts_catalog SET specifications=$2::jsonb, name_he=NULL, "
            "updated_at=NOW() WHERE id=$1", warranty[i:i + 1000])
        print(f"[revfix] warranty {min(i+1000,len(warranty)):,}/{len(warranty):,}", flush=True)
    for i in range(0, len(renames), 1000):
        await conn.executemany(
            "UPDATE parts_catalog SET name=$2, name_he=NULLIF($3,''), updated_at=NOW() "
            "WHERE id=$1", renames[i:i + 1000])
        print(f"[revfix] names {min(i+1000,len(renames)):,}/{len(renames):,}", flush=True)

    print(f"[revfix] DONE in {time.monotonic()-t0:.0f}s")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
