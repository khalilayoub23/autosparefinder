"""
Script: maintenance/fitment_decision_package.py
Purpose: Turn the impact JSONL (maintenance/fitment_impact_check.py) into the model-relationship
         decision package: every exclusion grouped by relationship, with an EVIDENCE-BASED proposal.
         FIXES_TRACKER #69 §9. READ-ONLY.
Process:
  1. group every removed fitment model by (reason, make, base, extension)
  2. evidence per group, from part_vehicle_fitment (one short read-only transaction per group):
     chassis/platform codes (tokens with a digit) of the extension's own series vs the base's
       - no shared code, both have codes  -> "distinct"  (different platform)
       - shared codes                       -> "same_platform" (sub-series / trim of the base)
       - no codes on one side               -> "ambiguous" (no platform evidence)
     junk prefixes are proposed "not_a_model"
  3. write JSON + a Markdown summary
Data Imported/Modified: NONE.
Data Sources: impact JSONL, part_vehicle_fitment.
Last Updated: 2026-10-19
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import re
import sys

sys.path.insert(0, "/app")

from sqlalchemy import text  # noqa: E402

import vehicle_model_match as vm  # noqa: E402


_NOT_A_PLATFORM_CODE = re.compile(r"^((19|20)\d\d|\d+d|\d+dr|\d\.\d|\d{1,3})$")   # years, doors, litres, plain numbers


def _codes(model: str):
    return {w for w in vm.norm(model).split()
            if any(ch.isdigit() for ch in w) and len(w) >= 2 and not _NOT_A_PLATFORM_CODE.match(w)}


def group_removals(records):
    groups = {}
    for r in records:
        if "error" in r:
            continue
        for fm, rows, why in r["removed"]:
            kind, _, detail = why.partition(":")
            reg = re.sub(r"\(\d+\)$", "", detail)
            c, f = vm.norm(r["model"]), vm.norm(fm)
            if kind == "fitment_is_distinct_model":
                base, ext = c, reg[len(c):].strip()
            elif kind == "customer_is_distinct_model_with_own_series":
                base, ext = f, reg[len(f):].strip()
            else:                                            # generic_is_junk_prefix
                base, ext = f, ""
            key = (kind, r["make"], base, ext)
            g = groups.setdefault(key, {"reason": kind, "make": r["make"], "base": base, "extension": ext,
                                        "registry_name": reg or None, "customer_models": set(),
                                        "fitment_models": collections.Counter(), "rows_removed": 0})
            g["customer_models"].add(r["model"])
            g["fitment_models"][fm] = max(g["fitment_models"][fm], rows)
            g["rows_removed"] += rows
    return groups


async def platform_evidence(db, make, base, ext):
    await db.execute(text("SET TRANSACTION READ ONLY"))
    await db.execute(text("SET LOCAL statement_timeout = '60s'"))
    yrows = (await db.execute(text(
        "SELECT LOWER(TRIM(model)), min(year_from) FROM part_vehicle_fitment "
        "WHERE LOWER(TRIM(manufacturer)) LIKE '%' || :mk || '%' "
        "AND (LOWER(TRIM(model)) = :b OR LOWER(TRIM(model)) LIKE :b || ' %') GROUP BY 1"),
        {"mk": make, "b": base})).all()
    rows = [m for m, _ in yrows]
    first_year = {m: int(y) for m, y in yrows if y}
    full = f"{base} {ext}".strip()
    own = [m for m in rows if vm.norm(m) == full or vm.norm(m).startswith(full)]
    parent = [m for m in rows if m not in own]
    name_words = set(full.split())                    # "c3", "x5", "sx4" are names, not platforms
    oc = (set().union(*(_codes(m) for m in own)) if own else set()) - name_words
    pc = (set().union(*(_codes(m) for m in parent)) if parent else set()) - name_words
    if len(ext.split()) == 1 and len(ext) == 1:
        letters = {vm.norm(m)[len(base):].split()[0] for m in first_year
                   if vm._word_extends(vm.norm(m), base) and len(vm.norm(m)[len(base):].split()[0]) == 1}
        if len(letters) >= 2:
            # several letter series (Opel Corsa A-F) but their first years do not separate them:
            # D1 relies on the year filter, which these rows cannot support (1990-2030 placeholders)
            verdict = "generation_letter_year_unverified"
        else:
            verdict = "single_letter_model_or_trim"          # Prius C, X5 M, Cooper S
    elif not oc or not pc:
        verdict = "ambiguous"
    elif oc & pc:
        verdict = "same_platform"
    else:
        verdict = "distinct"
    return verdict, sorted(oc)[:8], sorted(pc)[:8], sorted(oc & pc)[:8]


# Removals that earlier analysis proved WRONG (restored by the corrected rule). If any of these is
# removed again, it is PROVEN_INCORRECT.
KNOWN_WRONG = [("chevrolet", "silverado"), ("honda", "accord"), ("jaguar", "xk"), ("volvo", "s60"),
               ("mitsubishi", "lancer"), ("mini", "mini")]


def classify(g):
    """Exactly one of PROVEN_CORRECT / UNRESOLVED / PROVEN_INCORRECT, with the reason."""
    for mk, base in KNOWN_WRONG:
        if g["make"] == mk and g["base"] == base and g["reason"] != "fitment_is_distinct_model":
            return "PROVEN_INCORRECT", f"previously proven wrong drop of the generic {base!r} rows"
    if g["make"] == "honda" and g["base"] == "civic" and g["extension"] == "type r" \
            and g["reason"] == "customer_is_distinct_model_with_own_series":
        return "PROVEN_INCORRECT", "Civic Type R has 24 own rows (< 50): generic Civic rows must stay"
    p = g["proposal"]
    if p == "not_a_model":
        return "PROVEN_CORRECT", ("the fitment row names no vehicle: the registry uses it only as the start of "
                                  "other model names and it is not the make; no compatibility claim is possible")
    if p == "distinct":
        return "PROVEN_CORRECT", ("different vehicle: the extension's own series and the base series carry "
                                  f"disjoint platform/chassis codes ({g['ext_codes'][:3]} vs {g['base_codes'][:3]})")
    reasons = {
        "same_platform": "D2: shares platform codes with the base — a shared platform is not proof of part compatibility",
        "ambiguous": "D3: no platform/chassis codes on one side — name similarity only, no part-level evidence",
        "generation_letter_year_unverified": "D1: generation letters, but the fitment year filter cannot separate them "
                                             "(identical placeholder first years, e.g. 1990-2030)",
        "single_letter_model_or_trim": "single-letter extension with no sibling generations (e.g. Prius C, X5 M): model or trim unknown",
    }
    why = reasons.get(p, f"no evidence class for proposal {p!r}")
    if g["make"] == "toyota" and g["base"] == "land cruiser" and g["extension"].startswith("prado"):
        why = "D5: generic Land Cruiser rows for Prado — not validated at part/generation level; " + why
    return "UNRESOLVED", why


async def main(inp, out_json, out_md):
    from BACKEND_DATABASE_MODELS import async_session_factory
    records = [json.loads(l) for l in open(inp, encoding="utf-8")]
    groups = group_removals(records)
    for g in groups.values():
        if g["reason"] == "generic_is_junk_prefix":
            g.update(proposal="not_a_model", ext_codes=[], base_codes=[], shared_codes=[])
            continue
        async with async_session_factory() as db:               # one short transaction per group
            v, oc, pc, sc = await platform_evidence(db, g["make"], g["base"], g["extension"])
        g.update(proposal=v, ext_codes=oc, base_codes=pc, shared_codes=sc)
    for g in groups.values():
        g["classification"], g["classification_reason"] = classify(g)
    out = []
    for g in sorted(groups.values(), key=lambda x: -x["rows_removed"]):
        g["customer_models"] = sorted(g["customer_models"])
        g["fitment_models"] = g["fitment_models"].most_common(6)
        out.append(g)
    json.dump(out, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    by = collections.defaultdict(lambda: [0, 0, 0])
    for g in out:
        b = by[(g["classification"], g["proposal"], g["reason"])]
        b[0] += 1; b[1] += len(g["customer_models"]); b[2] += g["rows_removed"]
    jsonl_rows = sum(n for r in records if "error" not in r for _, n, _ in r["removed"])
    json_rows = sum(g["rows_removed"] for g in out)
    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write(f"Source: {inp} ({len(records)} models; removed rows in JSONL = {jsonl_rows:,}; "
                 f"in groups = {json_rows:,}; consistent = {jsonl_rows == json_rows})\n\n")
        fh.write("| classification | evidence | rule reason | groups | customer models | fitment rows removed |\n"
                 "|---|---|---|---|---|---|\n")
        for (cl, prop, reason), (n, cm, rows) in sorted(by.items(), key=lambda x: (x[0][0], -x[1][2])):
            fh.write(f"| {cl} | {prop} | {reason} | {n} | {cm} | {rows:,} |\n")
        for cl in ("PROVEN_INCORRECT", "PROVEN_CORRECT", "UNRESOLVED"):
            gs = [g for g in out if g["classification"] == cl]
            fh.write(f"\n## {cl} — {len(gs)} groups, {sum(g['rows_removed'] for g in gs):,} rows\n")
            for g in gs:
                fh.write(f"- `{g['make']} | {g['base']} + {g['extension'] or '-'}` ({g['reason']}) "
                         f"rows {g['rows_removed']:,}, customers {len(g['customer_models'])} — {g['classification_reason']}\n")
    assert jsonl_rows == json_rows, (jsonl_rows, json_rows)
    print(open(out_md).read()[:3000])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--md", required=True)
    a = ap.parse_args()
    asyncio.run(main(a.inp, a.json, a.md))
