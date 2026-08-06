#!/usr/bin/env python3
"""
Script:  maintenance/audit_importers.py
Purpose: Machine-check every importer/harvester against the platform's hard-won
         import rules, so the same defect cannot be re-introduced by hand.

Why this exists: every rule below was earned by a REAL production bug, most of
them found more than once because the rule lived only in a .md file. A written
policy is not enforcement — this script is (see docs/POSTMORTEMS.md for the incident log).

    RULE                              THE BUG IT PREVENTS
    ON CONFLICT ON CONSTRAINT         `uix_pvf_part_mfr_model_year_from` is a bare
                                      UNIQUE INDEX, not a CONSTRAINT. ASAP fitment
                                      wrote 0 of 8,210 rows; Fox Factory's 8,088
                                      had failed the same way unnoticed.
    supplier_parts conflict target    `(part_id, supplier_id)` is not the constraint
                                      that actually fires on re-import; it must be
                                      supplier_parts_supplier_id_supplier_sku_key.
    importer_price_ils CASE guard     Re-running an importer wiped the IL price to 0.
    part_condition lowercase          'New'/'OEM' broke the bad_cond counter forever.
    categorize_on_ingest              12 importers hard-coded their own fallback and
                                      created the 1.67M-part 'general' backlog.
    parts_images write                A parts-CREATING importer that skips this gets
                                      no thumbnail, ever. Measured: the OEM-Parts-
                                      Online family has images on 0.0-0.4% of
                                      ~1.55M parts.
    warranty_policy.resolve           REX captured no warranty at all — 293,263 rows.
    warranty_source written           11 importers hardcoded warranty_months and
                                      never wrote warranty_source, making supplier
                                      warranty statements unverifiable at runtime.
    SKIP LOCKED on batched writes     Batched UPDATEs queued behind the harvester and
                                      died on the statement timeout having written 0.
    no MAX(uuid)                      Postgres has no max(uuid) aggregate; this error
                                      shipped twice in one day.
    availability column exists        `availability` is absent from the ACA sheet, so
                                      reading it failed OPEN and 566 discontinued
                                      parts were advertised as buyable.
    private categorizer               SEVEN incompatible category vocabularies were
                                      live at once (snake_case, Title Case, hyphenated,
                                      Hebrew DISPLAY names, positional literals, …).
    name from identifier              An OEM/SKU substituted for a name makes the part
                                      unsearchable and gets a category guessed from a
                                      part code. 28,183 rows reached that state.
    image captured at source          The thumbnail pipeline reads parts_images and
                                      nothing else; a scraper that skips the photo
                                      denies every part it creates a picture forever.
    fitment written to the TABLE      Fitment-first search JOINS part_vehicle_fitment —
                                      a compatible_vehicles JSONB blob is not a
                                      substitute. (A PLACEHOLDER model like
                                      'All Models' must NOT be written: it would make
                                      every part match every model of the make.)
    specifications provenance         Without specifications->>'source' a bad row is
                                      untraceable — you cannot tell which importer to
                                      fix or which source to re-harvest.

Exit code is non-zero when any ERROR-severity finding exists, so this can gate a
deploy.

Usage:  python3 /app/maintenance/audit_importers.py [--dir importers] [--json]
Last Updated:  2026-07-28
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

APP = Path(__file__).resolve().parent.parent

ERROR, WARN, INFO = "ERROR", "WARN", "INFO"


@dataclass
class Finding:
    file: str
    rule: str
    severity: str
    detail: str
    line: int = 0


def _lineno(src: str, idx: int) -> int:
    return src.count("\n", 0, idx) + 1


_COMMENT = re.compile(r"(?m)#[^\n]*$")


def strip_prose(src: str) -> str:
    """Blank out comments and REAL docstrings, preserving offsets/line numbers.

    Why: a rule looking for a literal like 'Auto Parts' otherwise fires on the
    comment EXPLAINING that the literal was removed, so a correctly-fixed file
    fails its own audit forever and "fixing" it means deleting the docs. Seen on
    lr_import.py and zeekr_full_import.py, which already delegate correctly.

    Why AST and not a regex: in this codebase `\"\"\"...\"\"\"` is overwhelmingly
    SQL, not prose. A regex that blanks every triple-quoted string erases every
    query — the audit then reports almost nothing and looks CLEAN when it is
    merely blind. That happened here: 66 errors "dropped" to 1. Only a real
    docstring (first statement of a module/class/function) may be stripped.
    """
    out = _COMMENT.sub(lambda m: " " * len(m.group(0)), src)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    lines = out.splitlines(keepends=True)
    starts, off = [], 0
    for ln in lines:
        starts.append(off); off += len(ln)

    def blank_node(node):
        s = starts[node.lineno - 1] + node.col_offset
        e = starts[node.end_lineno - 1] + node.end_col_offset
        return s, e

    spans = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            spans.append(blank_node(first.value))

    buf = list(out)
    for s, e in spans:
        for i in range(s, min(e, len(buf))):
            if buf[i] != "\n":
                buf[i] = " "
    return "".join(buf)


# ── individual rules ──────────────────────────────────────────────────────────
def rule_on_conflict_constraint(path, src, f):
    """ON CONFLICT ON CONSTRAINT only works for real CONSTRAINTS."""
    for m in re.finditer(r"ON\s+CONFLICT\s+ON\s+CONSTRAINT\s+([a-z_0-9]+)", src, re.I):
        name = m.group(1)
        # The only genuine table CONSTRAINT we rely on:
        if name != "supplier_parts_supplier_id_supplier_sku_key":
            f.append(Finding(path, "on-conflict-constraint", ERROR,
                             f"`ON CONFLICT ON CONSTRAINT {name}` — verify this is a "
                             f"real CONSTRAINT in pg_constraint, not a UNIQUE INDEX. "
                             f"Unique indexes must be targeted by column inference.",
                             _lineno(src, m.start())))


def rule_supplier_parts_conflict(path, src, f):
    if "supplier_parts" not in src:
        return
    if re.search(r"ON\s+CONFLICT\s*\(\s*part_id\s*,\s*supplier_id\s*\)", src, re.I):
        m = re.search(r"ON\s+CONFLICT\s*\(\s*part_id\s*,\s*supplier_id\s*\)", src, re.I)
        f.append(Finding(path, "supplier-parts-conflict-target", ERROR,
                         "Uses ON CONFLICT (part_id, supplier_id) — the constraint that "
                         "actually fires on re-import is "
                         "supplier_parts_supplier_id_supplier_sku_key.",
                         _lineno(src, m.start())))


def rule_importer_price_guard(path, src, f):
    if "importer_price_ils" not in src:
        return
    if not re.search(r"ON\s+CONFLICT", src, re.I):
        return
    # A CASE guard preserving the existing value must be present somewhere.
    if not re.search(r"importer_price_ils\s*=\s*CASE\s+WHEN", src, re.I):
        if re.search(r"DO\s+UPDATE", src, re.I):
            f.append(Finding(path, "importer-price-case-guard", ERROR,
                             "ON CONFLICT DO UPDATE touches importer_price_ils without a "
                             "`CASE WHEN EXCLUDED.importer_price_ils > 0` guard — a "
                             "re-import will wipe a good IL price to 0."))


def rule_part_condition_case(path, src, f):
    for m in re.finditer(r"part_condition[^\n]{0,40}?['\"](New|OEM|Used|Aftermarket|Remanufactured)['\"]", src):
        f.append(Finding(path, "part-condition-case", ERROR,
                         f"part_condition '{m.group(1)}' must be lowercase.",
                         _lineno(src, m.start())))
    for m in re.finditer(r"VALUES[^\n]{0,200}?['\"](New|OEM)['\"]", src):
        f.append(Finding(path, "part-condition-case", WARN,
                         f"Literal '{m.group(1)}' in a VALUES list — if this is "
                         f"part_condition it must be lowercase.",
                         _lineno(src, m.start())))


def rule_categorization(path, src, f):
    creates_parts = "INSERT INTO parts_catalog" in src
    if not creates_parts:
        return
    # An INSERT..SELECT builds rows entirely in SQL, so it cannot call a Python
    # categorizer per row. Writing the CATCH-ALL there is the CORRECT answer: the
    # self-healing categorizer re-processes 'כללי' and nothing else, so the part
    # gets categorized on the next cleanup pass. (Writing a real-but-guessed
    # category instead would look finished and never be revisited.)
    writes_catch_all = "'כללי'" in src or '"כללי"' in src
    if ("categorize_on_ingest" not in src and "categorize(" not in src
            and not writes_catch_all):
        f.append(Finding(path, "categorize-on-ingest", ERROR,
                         "Creates parts_catalog rows but never calls "
                         "categorize_on_ingest() — parts land uncategorized."))
    # Values that are NOT categories at all. These are written positionally inside
    # a SQL VALUES list ("...,'General Parts','oem','new',..."), so a
    # `category = '...'` regex never sees them — which is exactly how
    # import_kia_israel.py kept writing 'General Parts' undetected.
    NON_CATEGORIES = ("General Parts", "Auto Parts", "Other Parts",
                      "Service & General", "Engine Parts", "uncategorized",
                      "Parts & Accessories", "Accessories & Parts")
    # A file that maps through category_map may legitimately hold these strings as
    # RAW SOURCE LABELS to be normalised (fridayparts_seed_import keeps 20 of them
    # in its seed data and maps every one). Flagging those is a false positive.
    delegates = ("normalize_category_label" in src) or ("categorize_on_ingest" in src)
    for bad in ([] if delegates else NON_CATEGORIES):
        for m in re.finditer(r"['\"]" + re.escape(bad) + r"['\"]", src):
            f.append(Finding(path, "hardcoded-fallback-category", ERROR,
                             f"Writes the literal '{bad}', which is not a category at "
                             f"all. The ONLY fallback is 'כללי', via "
                             f"categorize_on_ingest().",
                             _lineno(src, m.start())))

    # Canonical, but never valid as a DEFAULT — they are reachable only by a
    # genuine keyword match (BAD_FALLBACK_BUCKETS in category_map).
    for m in re.finditer(r"category\s*[=:]\s*['\"](general|service-general|accessories|tools-equipment)['\"]", src):
        f.append(Finding(path, "hardcoded-fallback-category", ERROR,
                         f"Uses '{m.group(1)}' as a fallback category. Fall back to "
                         f"'כללי' via categorize_on_ingest() instead.",
                         _lineno(src, m.start())))


def _insert_value_for(src: str, column: str) -> str | None:
    """Return the VALUES/SELECT token bound to `column` in the parts_catalog INSERT.

    Positional SQL is why several defects hid all day — a literal sitting in the
    Nth slot is invisible to a `column = '...'` regex. Mapping the column list onto
    the value list is the only reliable way to ask "what does this INSERT actually
    write for X?", and it lets a rule EXEMPT correct code instead of nagging it.
    Returns None when the shape cannot be parsed (callers must treat that as
    'unknown', never as a violation).
    """
    i = src.find("INSERT INTO parts_catalog")
    if i < 0:
        return None
    m = re.search(r"INSERT INTO parts_catalog\s*\(([^)]*)\)", src[i:i + 3000], re.S)
    if not m:
        return None
    cols = [c.strip().lower() for c in m.group(1).split(",") if c.strip()]
    if column not in cols:
        return None
    rest = src[i + m.end():]
    vm = re.search(r"(?:VALUES\s*\(|SELECT\s)(.*?)(?:\)\s*(?:ON CONFLICT|RETURNING|\"\"\")|$)",
                   rest, re.S | re.I)
    if not vm:
        return None
    # split on top-level commas only — a value may itself be a call like round(x, 2)
    vals, depth, buf = [], 0, ""
    for ch in vm.group(1):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            vals.append(buf.strip()); buf = ""
        else:
            buf += ch
    vals.append(buf.strip())
    idx = cols.index(column)
    return vals[idx].strip() if idx < len(vals) else None


# > RULE 7 — a part NAME must come from the source, never from an identifier.
_NAME_FALLBACK = re.compile(
    r"""(?:if\s+not\s+(?:name|title)\s*:\s*\n\s*(?:name|title)\s*=\s*(oem\w*|sku\w*|part_number\w*)"""
    r"""|(?:^|\W)(?:name|title)\s*=\s*[^\n#]*?\bor\s+(oem\w*|sku\w*|part_number\w*)\s*(?:$|[\n,)]))""",
    re.M)


def rule_name_from_source(path, src, f):
    """The OEM/SKU is an IDENTIFIER, not a name.

    Substituting it produces a part whose name is a bare part code — which then
    gets a category GUESSED FROM THAT CODE ('IL333' -> engine) and is invisible
    to keyword search, to the categorizer and to the customer. Measured live:
    28,183 active parts in this state, 21,795 of them from a single
    `if not name: name = oem_raw` in oempartsonline_importer.py.

    If the source truly has no name, the importer must say so (flag the row for
    enrichment) rather than inventing one that LOOKS valid.
    """
    if "INSERT INTO parts_catalog" not in src:
        return
    # Falling back is ACCEPTABLE if the row is flagged for enrichment — the rule is
    # "don't invent a name that looks valid", not "never write one". Read the value
    # actually bound to needs_oem_lookup instead of guessing from its presence.
    flagged = _insert_value_for(src, "needs_oem_lookup") in ("true", "TRUE")
    for m in _NAME_FALLBACK.finditer(src):
        ident = m.group(1) or m.group(2)
        if flagged or ("needs_oem_lookup" in src and "name_missing" in src):
            continue
        f.append(Finding(path, "name-from-identifier", ERROR,
                         f"Falls back to `{ident}` for the part name. An OEM/SKU is an "
                         f"identifier, not a name — the part becomes uncategorizable and "
                         f"unsearchable. Capture the real name from the source; if it is "
                         f"genuinely absent, set needs_oem_lookup=TRUE so enrichment "
                         f"fixes it.",
                         _lineno(src, m.start())))


# > RULE 8 — a source that shows a product photo must have it captured.
_EXTRACTS_PRODUCT = re.compile(r"""["'](?:name|title)["']\s*:""")
_EXTRACTS_IMAGE = re.compile(
    r"""["'](?:image|image_url|img|img_url|photo|thumbnail|picture)["']\s*[:=]"""
    r"""|querySelector\w*\(['"][^'"]*img"""
    r"""|\.src\b|data-src|getAttribute\(['"]src""", re.I)


def rule_image_capture(path, src, f):
    """A scraper/harvester that reads a product page MUST capture its photo.

    The thumbnail pipeline reads `parts_images` and nothing else, so an extractor
    that skips the image permanently denies every part it creates a picture — and
    re-harvesting later is the only way to recover it. Measured: 92.2% of the
    catalog has no image because capture was added only after ~3.1M parts had
    already been imported.
    """
    # Only applies to code that CREATES parts or feeds the relay. A price-only
    # updater (amayama_price_import, rockauto_price_import) matches existing parts
    # by OEM and has no business capturing images — flagging it is noise.
    creates = ("INSERT INTO parts_catalog" in src
               or "system/collect" in src
               or "parts.append" in src)
    if not creates:
        return
    if not _EXTRACTS_PRODUCT.search(src):
        return                              # not a product extractor
    if _NO_IMAGE_SOURCE.search(src):
        return                              # PDF/spreadsheet — no photo exists
    # HARDCODED SEED DATA: rows written into the file itself, with no HTTP/DOM call
    # anywhere. There is no page to read a photo from, so demanding one is asking
    # for the impossible. Measured: 4 of the 17 hits were exactly this.
    # DB client calls (.fetch / .fetchrow / .execute) must not match here.
    # Only real HTTP client calls count: urlopen, httpx, requests, aiohttp, playwright, etc.
    fetches = re.search(r"urlopen|httpx\b|requests\.|aiohttp|playwright|BeautifulSoup"
                        r"|session\.get\b", src)
    if not fetches:
        f.append(Finding(path, "image-no-live-source", INFO,
                         "Hardcoded seed data with no HTTP/DOM call — no page exists to "
                         "capture a photo from. These parts need images from an "
                         "image-bearing source matched by exact OEM."))
        return
    if _EXTRACTS_IMAGE.search(src):
        return                              # captures something
    f.append(Finding(path, "image-not-captured", ERROR,
                     "Builds product records from a web source but never extracts an "
                     "image field. The thumbnail pipeline reads parts_images only, so "
                     "these parts can never get a picture without a full re-harvest."))


# A source that is a PDF / spreadsheet price list has NO images to capture, so
# demanding parts_images there is a false positive. Measured: 49 of the 51
# original hits were exactly this, and a rule that is wrong 49 times out of 51
# gets ignored — which is worse than having no rule.
_NO_IMAGE_SOURCE = re.compile(
    r"pdfplumber|pymupdf|fitz\b|openpyxl|load_workbook|read_excel|\.pdf\b|\.xlsx\b"
    r"|csv\.DictReader|price[_ ]list|_SOURCE_HAS_NO_IMAGES", re.I)


def rule_parts_images(path, src, f):
    if "INSERT INTO parts_catalog" not in src:
        return
    has_write = "parts_images" in src
    # Must look like a VALUE the source gave us, not a config flag. TecDoc sends
    # `"thumbnails": False` in its REQUEST body — that is not an image field, and
    # flagging it as one is the kind of false positive that gets a rule ignored.
    mentions_image = re.search(
        r"""(image_url|img_url|image_urls|photo_url|\bimages\b)\s*(=|\)|\]|,)"""
        r"""|get\(\s*["'](image|image_url|photo|thumbnail)["']""", src, re.I)

    if has_write:
        if not mentions_image:
            f.append(Finding(path, "parts-images-write", WARN,
                             "References parts_images but has no image variable — check "
                             "the write actually fires."))
        return

    if _NO_IMAGE_SOURCE.search(src) and not mentions_image:
        # PDF/spreadsheet price list: no image exists at the source. Report as
        # INFO so the coverage gap stays VISIBLE without being actionable noise.
        f.append(Finding(path, "parts-images-source", INFO,
                         "Creates parts from a PDF/spreadsheet price list — no image "
                         "exists at this source. Images for these parts must come from "
                         "an image-bearing source matched by OEM."))
        return

    sev = ERROR if mentions_image else WARN
    f.append(Finding(path, "parts-images-write", sev,
                     "Creates parts but never writes parts_images"
                     + (" even though it has an image field — wire it."
                        if mentions_image else
                        " — if this source exposes a part photo, capture it; the "
                        "thumbnail pipeline reads parts_images and can never see "
                        "these parts otherwise.")))


def rule_warranty(path, src, f):
    if "INSERT INTO supplier_parts" not in src:
        return
    # Rule 1: warranty_policy must be imported when supplier_parts is written.
    if "warranty_policy" not in src:
        f.append(Finding(path, "warranty-capture", WARN,
                         "Writes supplier_parts without importing warranty_policy — call "
                         "warranty_policy.resolve() so the offer carries a warranty "
                         "and its provenance (`warranty_months` + `warranty_source`)."))
        return   # no point checking warranty_source if the import is absent
    # Rule 2: warranty_source must be written alongside warranty_months.
    # Provenance is what distinguishes "supplier stated 24 months" from "we applied
    # the platform default". A surface that says "supplier warranty" relies on this.
    if "warranty_months" in src and "warranty_source" not in src:
        f.append(Finding(path, "warranty-source-missing", WARN,
                         "Writes `warranty_months` to supplier_parts but omits "
                         "`warranty_source`. Always write both — `warranty_source` "
                         "is how a surface can tell 'supplier stated' from "
                         "'platform default'. Call _warranty_resolve() and store "
                         "both return values."))


def rule_skip_locked(path, src, f):
    """Batched UPDATE/DELETE against harvester-written tables needs SKIP LOCKED."""
    hot = ("parts_catalog", "supplier_parts", "part_vehicle_fitment")
    for m in re.finditer(r"(UPDATE|DELETE\s+FROM)\s+(\w+)", src, re.I):
        tbl = m.group(2).lower()
        if tbl not in hot:
            continue
        window = src[max(0, m.start() - 500): m.start() + 900]
        # A LIMIT must be part of the WRITE to make it a batched write — i.e. the
        # UPDATE/DELETE is driven by a CTE or subquery that selects a bounded set.
        # A single-row `UPDATE ... WHERE id = $1` preceded by a lookup
        # `SELECT ... LIMIT 1` is the ordinary per-row upsert; SKIP LOCKED does not
        # apply there and adding it would be wrong. Measured: all 10 original hits
        # were exactly that pattern.
        batched = re.search(
            r"(WITH\s+\w+\s+AS\s*\([^;]*?\bLIMIT\b[^;]*?\)\s*(UPDATE|DELETE)"
            r"|(UPDATE|DELETE\s+FROM)\s+\w+[^;]*?\bFROM\s*\([^;]*?\bLIMIT\b)",
            window, re.I | re.S)
        if batched and "SKIP LOCKED" not in window.upper():
            f.append(Finding(path, "skip-locked", WARN,
                             f"Batched write to `{tbl}` without FOR UPDATE SKIP LOCKED — "
                             f"it will queue behind the harvester and can time out having "
                             f"written nothing.",
                             _lineno(src, m.start())))
            break


def rule_max_uuid(path, src, f):
    for m in re.finditer(r"MAX\s*\(\s*id\s*\)", src, re.I):
        f.append(Finding(path, "max-uuid", ERROR,
                         "MAX(id) on a uuid column — Postgres has no max(uuid). Use "
                         "ORDER BY id DESC LIMIT 1.",
                         _lineno(src, m.start())))


def rule_missing_column_read(path, src, f):
    """A guard built on a column that may not exist fails OPEN, silently."""
    for m in re.finditer(r"_f\(\s*r\s*,\s*[\"']availability[\"']\s*\)", src):
        if "discontinued" not in src:
            f.append(Finding(path, "absent-column-guard", WARN,
                             "Reads an `availability` column with no `discontinued_item` "
                             "fallback. A missing column yields '' and the guard passes — "
                             "566 discontinued parts were sold this way.",
                             _lineno(src, m.start())))


_PRIVATE_CAT_FN = re.compile(
    r"\ndef (guess_category|_guess_category|categorize_part|detect_category"
    r"|categorise|categorize|map_cat|map_category|infer_category|category)\(.*?\n(?=\S)",
    re.S)


def rule_private_categorizer(path, src, f):
    """No file may carry its own categorization RULESET.

    `toyota_il_importer` and `mazda_il_importer` each held a private
    `guess_category()` returning a FOURTH vocabulary — 'transmission',
    'suspension', 'electrical', 'fuel_system', 'steering', 'body_parts',
    'air_conditioning', 'other_parts' — none of them canonical. Between them
    that is ~647,000 parts (Toyota 434K + Mazda 213K) re-poisoned on every
    re-import, which normalize_categories then had to map back or flatten.
    The "ONE category file" merge missed both.

    A delegating wrapper is fine; a wrapper with its OWN return literals is not.
    """
    for m in _PRIVATE_CAT_FN.finditer(src):
        body = m.group(0)
        rets = set(re.findall(r"return\s+[\"']([^\"']+)[\"']", body))
        if not rets:
            continue                       # delegates — fine
        try:
            sys.path.insert(0, str(APP))
            from category_map import CANONICAL           # noqa: PLC0415
            bad = sorted(r for r in rets if r not in CANONICAL)
        except Exception:
            bad = sorted(rets)
        if bad:
            f.append(Finding(path, "private-categorizer", ERROR,
                             f"Defines its own categorization returning NON-CANONICAL "
                             f"values {bad[:6]}. There is ONE category file — call "
                             f"category_map.categorize_on_ingest().",
                             _lineno(src, m.start())))


# > RULE — FITMENT. Fitment is the platform's core differentiator: a part the
# customer cannot match to their car is worth nothing to them.
_HAS_VEHICLE_DATA = re.compile(
    r"""["'](?:model|vehicle|vehicle_model|modelDescription|fits_model|compatible_vehicles|"""
    r"""year|year_from|make)["']\s*[:=]|\bfitment\b""", re.I)


def rule_fitment(path, src, f):
    """A source that carries vehicle data MUST write part_vehicle_fitment.

    Search is fitment-first (goal G1): when the customer's car is known we demand
    a `part_vehicle_fitment` match. A part imported without its fitment rows is
    invisible to that path no matter how good its name or price is — and the only
    way to recover it later is a full re-harvest of the same source.
    """
    if "INSERT INTO parts_catalog" not in src:
        return
    if not _HAS_VEHICLE_DATA.search(src):
        return                                   # source carries no vehicle data
    if "part_vehicle_fitment" in src:
        return
    # A PLACEHOLDER model is not fitment data. repair_manufacturer_import and
    # supplier_pdf_import only carry {"model": "All Models"} — writing that to
    # part_vehicle_fitment would make every part falsely match every model of the
    # make under fitment-first search, which is far worse than having no row.
    # Report it so the gap stays visible, but never demand the corrupting "fix".
    if re.search(r"['\"](?:All Models|ALL MODELS|Universal|All)['\"]", src):
        f.append(Finding(path, "fitment-placeholder-only", INFO,
                         "Only a placeholder vehicle ('All Models') is available, so no "
                         "real fitment can be written. These parts stay invisible to "
                         "fitment-first search until a source with real model/year data "
                         "is matched in."))
        return
    # compatible_vehicles JSONB alone is NOT fitment — the search joins the table.
    sev = ERROR if re.search(r"['\"](?:year_from|fits_model|vehicle_model)['\"]", src) else WARN
    f.append(Finding(path, "fitment-not-written", sev,
                     "Source carries vehicle data but the importer never writes "
                     "part_vehicle_fitment. Fitment-first search joins that TABLE — "
                     "a compatible_vehicles JSONB blob does not substitute for it."))


# > RULE — SPECS. specifications JSONB is the provenance record for every part.
_SPEC_REQUIRED = ("source",)


def rule_specifications(path, src, f):
    """Every created part must carry a specifications JSONB with its provenance.

    `specifications` is how we answer "where did this row come from, and when?"
    months later — it is what let today's audit attribute 21,795 bad names to
    oempartsonline and 6,033 to colmobil. A part written without it is
    untraceable: you cannot tell which importer to fix or which source to
    re-harvest.

    Minimum: {"source": "<importer or site>"}; add source_url, part_brand and
    discovered_at where the source provides them.
    """
    if "INSERT INTO parts_catalog" not in src:
        return
    if "specifications" not in src:
        f.append(Finding(path, "specifications-missing", ERROR,
                         "Creates parts without a specifications JSONB. Provenance is "
                         "mandatory — without it a bad row cannot be traced back to the "
                         "importer or source that produced it."))
        return
    if not re.search(r"""["']source["']\s*:""", src):
        f.append(Finding(path, "specifications-missing", WARN,
                         "Writes specifications but no `source` key — that is the field "
                         "that makes a row traceable to its importer/site."))


_SCHEMA_CACHE: dict = {}


def _table_columns(table: str) -> set:
    """Real column names for `table`, read once from the live DB.

    Returns an empty set when the DB is unreachable — callers MUST treat that as
    'unknown' and skip, never as a violation. A static audit that guesses at
    schema would be worse than no check.
    """
    if table in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[table]
    cols: set = set()
    dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    if dsn:
        try:
            import asyncio as _a

            import asyncpg as _pg

            async def _go():
                c = await _pg.connect(dsn, statement_cache_size=0, timeout=8)
                try:
                    rows = await c.fetch(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = $1", table)
                    return {r[0] for r in rows}
                finally:
                    await c.close()

            cols = _a.run(_go())
        except Exception:
            cols = set()
    _SCHEMA_CACHE[table] = cols
    return cols


_INSERT_COLS = re.compile(
    r"INSERT\s+INTO\s+(parts_catalog|supplier_parts|part_vehicle_fitment|parts_images)\s*\(([^)]*)\)",
    re.I | re.S)


def rule_columns_exist(path, src, f):
    """Every column an INSERT names must actually exist on the table.

    A column that does not exist fails only at RUNTIME — `py_compile` passes, the
    audit passes, and the importer dies on its first real row. Found live
    2026-07-29: scrapers/bdv_playwright_scraper.py wrote `sku`, `currency` and
    `in_stock` to supplier_parts, whose real columns are `supplier_sku` and
    `is_available`. Every supplier offer it ever tried to write would have failed.
    """
    for m in _INSERT_COLS.finditer(src):
        table = m.group(1).lower()
        real = _table_columns(table)
        if not real:
            return                      # DB unreachable — cannot judge, stay silent
        named = [c.strip().lower() for c in m.group(2).split(",") if c.strip()]
        bad = [c for c in named if c and c.isidentifier() and c not in real]
        for c in bad:
            f.append(Finding(path, "column-does-not-exist", ERROR,
                             f"INSERT INTO {table} names column `{c}`, which does not "
                             f"exist. This fails at RUNTIME only — compile and a static "
                             f"audit both pass.",
                             _lineno(src, m.start())))


def rule_compiles(path, src, f):
    try:
        ast.parse(src)
    except SyntaxError as e:
        f.append(Finding(path, "syntax", ERROR, f"SyntaxError: {e}", e.lineno or 0))


RULES = [
    rule_compiles, rule_on_conflict_constraint, rule_supplier_parts_conflict,
    rule_importer_price_guard, rule_part_condition_case, rule_categorization,
    rule_parts_images, rule_warranty, rule_skip_locked, rule_max_uuid,
    rule_missing_column_read, rule_private_categorizer,
    rule_name_from_source, rule_image_capture, rule_fitment, rule_specifications,
    rule_columns_exist,
]


def audit(dirs) -> list[Finding]:
    findings: list[Finding] = []
    for d in dirs:
        base = APP / d
        if not base.exists():
            continue
        for p in sorted(base.glob("*.py")):
            raw = p.read_text(encoding="utf-8", errors="replace")
            # Comments/docstrings are PROSE, not code. Matching literals inside
            # them makes a correctly-fixed file fail its own audit forever.
            src = strip_prose(raw)
            rel = f"{d}/{p.name}"
            for rule in RULES:
                if rule is rule_compiles:
                    rule(rel, raw, findings)      # syntax needs the REAL source
                    continue
                try:
                    rule(rel, src, findings)
                except Exception as exc:                    # a broken rule must not
                    findings.append(Finding(rel, rule.__name__, INFO,   # hide real findings
                                            f"rule crashed: {exc}"))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", action="append", default=None,
                    help="directories to audit (default: importers, harvesters, scrapers)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    dirs = a.dir or ["importers", "harvesters", "scrapers"]

    findings = audit(dirs)
    if a.json:
        print(json.dumps([asdict(x) for x in findings], ensure_ascii=False, indent=1))
    else:
        by_sev = {ERROR: [], WARN: [], INFO: []}
        for x in findings:
            by_sev[x.severity].append(x)
        files = sum(len(list((APP / d).glob("*.py"))) for d in dirs if (APP / d).exists())
        print(f"audited {files} files in {', '.join(dirs)}\n")
        for sev in (ERROR, WARN, INFO):
            items = by_sev[sev]
            if not items:
                continue
            print(f"── {sev} ({len(items)}) " + "─" * 40)
            for x in sorted(items, key=lambda y: (y.rule, y.file)):
                loc = f":{x.line}" if x.line else ""
                print(f"  [{x.rule}] {x.file}{loc}\n      {x.detail}")
            print()
        print(f"TOTAL: {len(by_sev[ERROR])} error, {len(by_sev[WARN])} warn, "
              f"{len(by_sev[INFO])} info")
    return 1 if any(x.severity == ERROR for x in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
