#!/usr/bin/env python3
"""
Script:  maintenance/expand_hebrew_abbreviations.py
Purpose: Rewrite Hebrew gershayim abbreviations in part names to their full words.

Why (owner directive 2026-07-28): "instead of בורג לת\"ה it should be the full name
בורג לתיבת הילוכים". The gershayim (" / ״) splits the token, so `לת"ה` surfaced to
the categorizer as a meaningless `לת` fragment. Curing the DATA fixes it once for
every consumer — categorization, search, chat and display — instead of teaching
each one to read shorthand.

Scope measured on the live catalog: 7,007 active parts, 157 distinct abbreviations.
The gearbox family alone is 6,160 occurrences.

EXPANSIONS ARE EVIDENCE-BASED, NOT GUESSED. Each was confirmed by reading real
rows. Two corrections came out of that:
  • `גג"ש` is NOT `גג שמש` (sunroof) — the rows read `גג"ש הילוך 3`,
    `גג"ש הילוך אחור`, i.e. `גלגל שיניים` (GEAR WHEEL). Reading it by letters
    would have filed 136 gear wheels under body-exterior.
  • `א"ש` is not a part word at all — `מצבר 66 א"ש` is Amp-hours, a UNIT.

NOT EXPANDED — units keep their standard notation:
    מ"מ (mm) · ס"מ (cm) · ק"ג (kg) · מ"ל (ml) · סמ"ק (cc) · סל"ד (rpm) · א"ש (Ah)
NOT EXPANDED — genuinely ambiguous, left for a human:
    ח"ח (חלונות חשמל on a switch panel, but something else on a bumper) ·
    רד"ס · מד"א (looks like a supplier tag) · שנ"פ · ה"ט · ברל"מ · את"ן ·
    מכ"ם (already a normal Hebrew word for radar)

Data Modified: parts_catalog.name, parts_catalog.name_he
Usage:  python3 /app/maintenance/expand_hebrew_abbreviations.py [--dry-run] [--limit N]
Last Updated:  2026-07-28
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import time

import asyncpg

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

# LONGEST FIRST — `לתה"ל` must be replaced before `תה"ל`, or the prefix is left
# stranded. dict order is preserved in py3.7+, and the loop applies in order.
EXPANSIONS = {
    # ── gearbox family (6,160 occurrences) ────────────────────────────────────
    'לתה"ל': "לתיבת הילוכים",
    'בתה"ל': "בתיבת הילוכים",
    'תיה"ל': "תיבת הילוכים",
    'לת"ה':  "לתיבת הילוכים",
    'תה"ל':  "תיבת הילוכים",
    'ת"ה':   "תיבת הילוכים",
    # ── transfer case ─────────────────────────────────────────────────────────
    'לתה"ע': "לתיבת העברה",
    'בתה"ע': "בתיבת העברה",
    'תה"ע':  "תיבת העברה",
    # ── gear wheel (NOT sunroof — proven from 'גג"ש הילוך 3') ────────────────
    'לגלגל"ש': "לגלגל שיניים",
    'גלגל"ש':  "גלגל שיניים",
    'גלג"ש':   "גלגל שיניים",
    'לג"ש':    "לגלגל שיניים",
    'גג"שים':  "גלגלי שיניים",
    'גג"ש':    "גלגל שיניים",
    # ── oil seal / return ─────────────────────────────────────────────────────
    'מחז"ש': "מחזיר שמן",
    'מח"ש':  "מחזיר שמן",
    # ── power steering (confirmed: 'צינור שמן ה"כ', 'חגורה למנוע ה"כ') ────────
    'ה"כ': "הגה כוח",
    # ── work tool (confirmed: 'כ"ע חולץ מרססים', 'כ"ע לנעילת הגה') ───────────
    'כ"ע': "כלי עבודה",
    # ── one-way (confirmed: 'בית מיסב חד"כ') ─────────────────────────────────
    'חד"כ': "חד כיווני",
    # ── power windows (owner-confirmed 2026-07-28) ────────────────────────────
    # Appears in two contexts and means the same thing in both: on a Mercedes
    # switch panel ('פאנל מתגים ח"ח ומראות') and inside an Alfa Giulia bumper's
    # TRIM SPEC ('פגוש אח ... ח"ח'), where it describes the vehicle's equipment
    # rather than the bumper. Verified not to move either group's category.
    'ח"ח': "חלונות חשמל",
    # ── misc, unambiguous ─────────────────────────────────────────────────────
    'מק"ט': "מספר קטלוגי",
    'טמ"פ': "טמפרטורה",
    'חו"ל': "חוץ לארץ",
}

# The same abbreviations written with the Hebrew gershayim U+05F4 instead of ".
_ALL = dict(EXPANSIONS)
for k, v in list(EXPANSIONS.items()):
    _ALL[k.replace('"', "״")] = v

# Match an abbreviation only when it is not glued to another Hebrew letter, so
# a longer word containing the same letters is never rewritten.
_PATTERNS = [
    (re.compile(r"(?<![֐-׿])" + re.escape(a) + r"(?![֐-׿])"), full)
    for a, full in sorted(_ALL.items(), key=lambda kv: -len(kv[0]))
]


def expand(text: str) -> str:
    if not text:
        return text
    out = text
    for pat, full in _PATTERNS:
        out = pat.sub(full, out)
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    args = ap.parse_args()

    conn = await asyncpg.connect(DB, statement_cache_size=0)
    await conn.execute("SET statement_timeout = '300s'")
    t0 = time.monotonic()

    where = ("is_active AND (name ~ '[֐-׿]+[\"׳״][֐-׿]' "
             "OR name_he ~ '[֐-׿]+[\"׳״][֐-׿]')")
    total = await conn.fetchval(f"SELECT COUNT(*) FROM parts_catalog WHERE {where}")
    print(f"[abbrev] {total:,} active parts contain a gershayim abbreviation", flush=True)

    lim = f"LIMIT {args.limit}" if args.limit else ""
    rows = await conn.fetch(
        f"SELECT id, COALESCE(name,'') n, COALESCE(name_he,'') nh "
        f"FROM parts_catalog WHERE {where} {lim}")

    changed, samples = [], []
    for r in rows:
        n2, nh2 = expand(r["n"]), expand(r["nh"])
        if n2 != r["n"] or nh2 != r["nh"]:
            changed.append((r["id"], n2, nh2))
            if len(samples) < 15:
                before = r["nh"] or r["n"]
                after = nh2 or n2
                samples.append((before[:52], after[:60]))

    print(f"[abbrev] {len(changed):,} rows would change\n")
    for b, a in samples:
        print(f"   {b!r}\n     -> {a!r}")

    if args.dry_run:
        print("\n[abbrev] DRY RUN — nothing written")
        await conn.close()
        return

    done = 0
    for i in range(0, len(changed), 2000):
        chunk = changed[i:i + 2000]
        await conn.executemany(
            "UPDATE parts_catalog SET name=$2, name_he=$3, updated_at=NOW() WHERE id=$1",
            chunk)
        done += len(chunk)
        print(f"[abbrev] updated {done:,}/{len(changed):,}", flush=True)

    print(f"[abbrev] DONE: {done:,} rows in {time.monotonic()-t0:.0f}s")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
