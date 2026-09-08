#!/usr/bin/env python3
"""
freesbe_recovery.py
Recovery tool for parts_catalog rows corrupted by the historical FREESBE
VAT-inclusive-storage bug (forensic reports, 2026-09-05/06).

DEFAULT MODE IS DRY-RUN. Write mode (--apply) exists but requires BOTH
--apply AND --confirm-production-recovery, plus a prior dry-run report to
re-verify against (--from-report) — see apply_recovery() below. This tool
was authored and tested, but --apply has NEVER been invoked against
Production; every run described in the accompanying forensic report was
--dry-run (the default).

Contract (verified live against the FREESBE API):
  price               = VAT-inclusive
  priceWithoutVat     = EX-VAT (authoritative supplier cost)
  importer_price_ils  = priceWithoutVat
  base_price          = priceWithoutVat * 1.45
  max_price_ils       = price

Why a dedicated tool instead of re-running freesbe_importer.py: the FREESBE
checkpoint reports 1785/1785 pages complete, so a normal re-run is a no-op;
a checkpoint reset would reapply the SAME OEM-only matching that caused the
original damage, with no source-ownership or ambiguity gates. The original
~1.2288 VAT-corruption ratio signature has already been erased catalog-wide
by autonomous normalize_base_price executions that folded every mismatched
row into the ordinary 1.45 ratio — so ratio-based detection on stored data
is not viable. This tool instead re-derives correctness live: a row is a
corruption candidate only if its CURRENT importer_price_ils EXACTLY equals
a freshly-fetched FREESBE `price` (the raw, wrong, VAT-inclusive value) — a
coincidence no unrelated importer would independently produce.

PROVEN LINEAGE FINDING (2026-09-06 investigation, backend/importers/
mixed_brands_import.py read in full): that importer's price-field writes
are GUARDED ("CASE WHEN importer_price_ils IS NULL OR = 0 THEN ... ELSE
importer_price_ils END" — never overwrites a pre-existing non-zero price)
but its `specifications` write is UNCONDITIONAL (jsonb `||` merge, always
sets source='mixed_brands_xlsx'). Verified against 145,583 live rows
carrying that label: only 56 (0.04%) have importer_price_ils consistent
with mixed_brands_import.py's OWN recorded price (its `consumer_price_ils`
field, divided by its own hardcoded 1.18). The other 145,527 (99.96%) do
not — meaning the label is overwhelmingly a specifications side-effect,
not evidence of price-field ownership. This tool therefore does NOT treat
specifications.source='mixed_brands_xlsx' as an automatic exclusion; it
checks whether the row's price actually matches that importer's own
formula (MISLABEL_PRONE_SOURCES below) before deciding. No other source
label has been verified this way — they remain conservative exclusions
(TIER C, not TIER D) until someone reads their importer's own SQL the same
way this file's docstring documents doing for mixed_brands_xlsx.

Usage:
    python3 /app/importers/freesbe_recovery.py [--max-pages N] [--out FILE]
    python3 /app/importers/freesbe_recovery.py --apply --confirm-production-recovery \
        --from-report FILE [--max-rows N]
"""
import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/importers")

import asyncpg
import httpx

API_BASE = "https://admin.freesbe.com/api/parts"
PAGE_SIZE = 100
CONCURRENCY = int(os.getenv("FREESBE_RECOVERY_CONCURRENCY", "4"))
LARGE_DELTA_THRESHOLD_ILS = Decimal(os.getenv("FREESBE_RECOVERY_LARGE_DELTA_ILS", "1000"))

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

# Importers PROVEN (by reading their own SQL) to write `specifications`
# unconditionally while guarding price fields — meaning their source label
# can outlive/overwrite the label of whoever actually owns the stored price.
# Each entry gives the importer's OWN price-derivation formula so a row's
# CURRENT importer_price_ils can be tested against it: if it doesn't match,
# the label is very likely a mislabel, not genuine ownership.
MISLABEL_PRONE_SOURCES = {
    "mixed_brands_xlsx": {
        "own_price_field": "consumer_price_ils",
        "own_vat_divisor": Decimal("1.18"),  # backend/importers/mixed_brands_import.py: VAT = 0.18
    },
}

CLASSIFICATIONS = {
    "SAFE_AUTOMATIC_RECOVERY",
    "SAFE_BUT_LARGE_DELTA",
    "MANUAL_REVIEW",
    "NO_CHANGE",
    "PROVEN_OTHER_SOURCE",
    "PRICE_CONFLICT",
    "OEM_AMBIGUITY",
    "CROSS_MANUFACTURER",
    "INACTIVE",
    "NO_MATCH",
}
TIERS = {"A", "B", "C", "D", "E"}

# Apply-time outcomes (2026-09-07 hardening) — a candidate that clears the
# report-level dry-run classification must ALSO clear a fresh, per-candidate
# live FREESBE check immediately before write. Only APPLY_ELIGIBLE rows are
# ever written; every other outcome is a recorded skip, never a write.
APPLY_OUTCOMES = {
    "APPLY_ELIGIBLE",
    "SKIP_DB_STATE_DRIFT",
    "SKIP_LIVE_MATCH_MISSING",
    "SKIP_LIVE_OEM_AMBIGUITY",
    "SKIP_LIVE_IDENTITY_CHANGED",
    "SKIP_LIVE_MANUFACTURER_CONFLICT",
    "SKIP_LIVE_PRICE_CHANGED",
    "SKIP_LARGE_DELTA",
    "SKIP_ALREADY_CORRECT",
    "SKIP_MALFORMED_LIVE_RECORD",
    "SKIP_RECLASSIFIED",
    "ERROR",
}

LIVE_REVALIDATION_CONCURRENCY = int(os.getenv("FREESBE_RECOVERY_LIVE_REVALIDATION_CONCURRENCY", "15"))


# ─────────────────────────── pure functions (no I/O) ───────────────────────

def normalize_oem(oem: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', (oem or '').upper())


def _to_decimal(value) -> "Decimal | None":
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def parse_freesbe_item(item: dict) -> "dict | None":
    """Pure parser using Decimal throughout for exact equality against
    Postgres numeric columns. Raises TypeError for a non-dict item — the
    caller isolates this per item, matching freesbe_importer.py's own
    convention of never letting one bad record abort a whole batch."""
    if not isinstance(item, dict):
        raise TypeError(f"expected dict item, got {type(item)}")
    attrs = item.get("attributes", item)
    part_id = attrs.get("partId")
    if not part_id or "-" not in part_id:
        return None
    prefix, raw_oem = part_id.split("-", 1)
    raw_oem = raw_oem.strip()
    if not raw_oem:
        return None
    price = _to_decimal(attrs.get("price"))
    if price is None or price <= 0:
        return None
    price_wo_vat = _to_decimal(attrs.get("priceWithoutVat"))
    if price_wo_vat is None or price_wo_vat <= 0:
        price_wo_vat = (price / Decimal("1.18")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "part_id": part_id,
        "prefix": prefix.upper(),
        "raw_oem": raw_oem,
        "price": price,
        "price_without_vat": price_wo_vat,
    }


def expected_values(record: dict) -> dict:
    base = (record["price_without_vat"] * Decimal("1.45")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "expected_importer_price": record["price_without_vat"],
        "expected_base_price": base,
        "expected_max_price": record["price"],
    }


def resolve_specifications(specifications) -> "tuple[dict | None, bool]":
    """Returns (specs_dict, invalid). specifications may already be a dict
    (asyncpg sometimes decodes jsonb automatically), a JSON string, or None."""
    if specifications is None:
        return None, False
    if isinstance(specifications, dict):
        return specifications, False
    if isinstance(specifications, str):
        if not specifications.strip():
            return None, False
        try:
            d = json.loads(specifications)
        except (json.JSONDecodeError, TypeError):
            return None, True
        if not isinstance(d, dict):
            return None, True
        return d, False
    return None, True


def check_source_ownership(source: "str | None", specs: "dict | None", current_importer_price: "Decimal | None") -> str:
    """Returns a confidence tier for the SOURCE signal alone (not the full
    row classification): 'A' (first-party, proven), 'B' (no competing claim,
    or a proven-mislabel-prone source whose own formula does NOT match —
    meaning the label is very likely stale, not real ownership), 'D' (a
    mislabel-prone source whose own formula DOES match — genuinely theirs),
    or 'C' (some other named source we have not individually verified —
    conservatively ambiguous, never auto-promoted)."""
    if source in (None, ""):
        return "B"
    if source == "freesbe_importer":
        return "A"
    if source in MISLABEL_PRONE_SOURCES and specs is not None and current_importer_price is not None:
        cfg = MISLABEL_PRONE_SOURCES[source]
        own_price = _to_decimal(specs.get(cfg["own_price_field"]))
        if own_price is not None and own_price > 0:
            own_cost = (own_price / cfg["own_vat_divisor"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if abs(own_cost - current_importer_price) <= Decimal("0.02"):
                return "D"  # genuinely this importer's own price
            return "B"      # label present, but its OWN formula doesn't match what's stored — mislabel
        return "C"  # mislabel-prone source but we can't test it (missing its own price field)
    return "C"  # a named source we have not individually verified


def classify_row(
    *,
    is_active: bool,
    current_importer_price,
    current_base_price,
    live_price,
    expected_base_price,
    source: "str | None",
    specs: "dict | None" = None,
    source_invalid: bool = False,
    n_freesbe_candidates: int = 1,
    n_catalog_matches_for_record: int = 1,
    manufacturers_for_record: "set | None" = None,
) -> "tuple[str, str, str]":
    """Pure classification — no I/O. Returns (classification, tier, reason).

    Order: an already-correct row (NO_CHANGE) is detected before the
    corruption-signature check, since a row can be correct without ever
    having shown the raw VAT-inclusive value. A row matching neither
    "already correct" nor "known corrupted" is PRICE_CONFLICT — excluded,
    not guessed at. Source ownership is evaluated via check_source_ownership
    (tiers, not a blind string exclusion) — see that function's docstring.
    """
    manufacturers_for_record = manufacturers_for_record or set()

    if not is_active:
        return "INACTIVE", "E", "catalog row is not active"

    if current_importer_price is None:
        return "NO_MATCH", "E", "matched row has no importer_price_ils"

    if source_invalid:
        return "MANUAL_REVIEW", "C", "specifications field could not be parsed"

    if current_base_price is not None and current_base_price == expected_base_price and current_importer_price != live_price:
        return "NO_CHANGE", "A", "current values already match the expected corrected values"

    if current_importer_price != live_price:
        return "PRICE_CONFLICT", "E", (
            f"current importer_price_ils ({current_importer_price}) is neither the live "
            f"FREESBE price ({live_price}) nor the already-corrected value — provenance "
            f"cannot be established, excluded rather than guessed"
        )

    tier = check_source_ownership(source, specs, current_importer_price)
    if tier == "D":
        return "PROVEN_OTHER_SOURCE", "D", (
            f"specifications.source='{source}' AND that importer's own recorded price "
            f"independently matches the stored importer_price_ils — genuinely theirs, not FREESBE"
        )

    if n_catalog_matches_for_record > 1 and len(manufacturers_for_record) > 1:
        return "CROSS_MANUFACTURER", "E", (
            f"this FREESBE record matches {n_catalog_matches_for_record} catalog rows "
            f"across manufacturers {sorted(manufacturers_for_record)}"
        )

    if n_freesbe_candidates > 1:
        return "OEM_AMBIGUITY", "E", f"{n_freesbe_candidates} FREESBE records map to this catalog row"

    if tier == "C":
        return "MANUAL_REVIEW", "C", (
            f"specifications.source='{source}' is a named importer not yet verified as "
            f"mislabel-prone or genuinely authoritative — excluded pending that verification"
        )

    delta_base = expected_base_price - current_base_price if current_base_price is not None else None
    if delta_base is not None and abs(delta_base) > LARGE_DELTA_THRESHOLD_ILS:
        return "SAFE_BUT_LARGE_DELTA", tier, f"|base_price delta| = {abs(delta_base)} exceeds ₪{LARGE_DELTA_THRESHOLD_ILS}"

    return "SAFE_AUTOMATIC_RECOVERY", tier, "exact live-price match, ownership resolved, unambiguous, bounded delta"


# ─────────────────────────── I/O: fetch ─────────────────────────────────────

async def fetch_page(client: httpx.AsyncClient, page: int) -> "dict | None":
    # sort=id:asc (2026-09-07 hardening): the API has NO stable default order
    # (verified live — the same page/pageSize called twice returns completely
    # unrelated records with no sort param). id:asc gives a deterministic,
    # immutable-key ordering. This does NOT make a full multi-hour crawl
    # perfectly consistent — FREESBE's own catalog is under continuous
    # concurrent write activity, proven live: even WITH sort=id:asc, the same
    # page called seconds apart returns a shifted (but internally sequential)
    # id window, consistent with OFFSET/LIMIT pagination racing live inserts
    # on their side. This is a real, external, unfixable-from-our-side
    # instability — sort=id:asc still helps (eliminates pure randomness,
    # keeps drift small and monotonic) but apply-time targeted revalidation
    # (targeted_freesbe_lookup below) remains mandatory regardless.
    url = f"{API_BASE}?pagination[page]={page}&pagination[pageSize]={PAGE_SIZE}&sort=id:asc"
    for attempt in range(3):
        try:
            resp = await client.get(url, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == 2:
                print(f"  ERROR fetching page {page}: {e}", file=sys.stderr)
                return None
            await asyncio.sleep(2 ** attempt)


async def fetch_all_records(max_pages: "int | None" = None) -> "list[dict]":
    records: list[dict] = []
    async with httpx.AsyncClient() as client:
        first = await fetch_page(client, 1)
        if not first:
            raise RuntimeError("failed to fetch page 1 — aborting (read-only, no fallback)")
        total_pages = first["meta"]["pagination"]["pageCount"]
        if max_pages:
            total_pages = min(total_pages, max_pages)
        print(f"FREESBE total pages: {first['meta']['pagination']['pageCount']} "
              f"(fetching {total_pages})", file=sys.stderr)

        pages = list(range(1, total_pages + 1))
        sem = asyncio.Semaphore(CONCURRENCY)
        raw_by_page: dict[int, dict] = {1: first}

        async def _fetch(p: int):
            if p == 1:
                return
            async with sem:
                data = await fetch_page(client, p)
            if data:
                raw_by_page[p] = data

        BATCH = CONCURRENCY * 5
        for i in range(0, len(pages), BATCH):
            batch = pages[i:i + BATCH]
            await asyncio.gather(*[_fetch(p) for p in batch])

        invalid = 0
        raw_count = 0
        for p in pages:
            data = raw_by_page.get(p)
            if not data:
                continue
            for idx, item in enumerate(data.get("data", [])):
                raw_count += 1
                try:
                    parsed = parse_freesbe_item(item)
                except TypeError as exc:
                    invalid += 1
                    print(f"  WARNING malformed item page={p} idx={idx}: {exc}", file=sys.stderr)
                    continue
                if parsed:
                    parsed["_page"] = p
                    parsed["_idx"] = idx
                    records.append(parsed)
    print(f"Fetched {raw_count} raw items, {len(records)} valid parsed records, {invalid} invalid",
          file=sys.stderr)
    return records


async def targeted_freesbe_lookup(client: httpx.AsyncClient, normalized_oem: str, exact_part_id: str) -> dict:
    """Authoritative, real-time check for ONE candidate — the mechanism this
    tool actually relies on for write-time safety (see fetch_page's docstring
    on why a full paginated crawl cannot be trusted as a live snapshot).
    Two live queries, both cheap (small, targeted result sets, no pagination
    involved so no offset-drift risk): an exact match on the previously-known
    partId (proves that specific record still exists with the same identity)
    and a case-insensitive contains on the RAW OEM SUFFIX (reveals whether
    ANY OTHER live record now also matches this OEM — new ambiguity).

    BUG FIXED 2026-09-07 (found via a full-population, not sample-only,
    verification run): the broad query must search on exact_part_id's own
    raw suffix (everything after the first '-', exactly as FREESBE's own
    partId format stores it — e.g. "824013NA1A" for "NI-824013NA1A"), NOT
    on `normalized_oem`. Some catalog rows store oem_number WITH the prefix
    baked in (e.g. "NI824013NA1A", matching the RE631464459R pattern found
    earlier) — searching $containsi for that dash-free, prefix-included
    string against a live partId that DOES contain a dash ("NI-824013NA1A")
    never matches, because the dash breaks literal substring containment.
    The consequence was silent under-detection of ambiguity (a false
    "stable" instead of "ambiguous") for exactly that subset of candidates
    — verified directly: a full 13,970-candidate run showed 100% exact-match
    "still live" but the broad check spuriously returned zero matches for
    97.7% of them, which is what surfaced this. Deriving the search term
    from exact_part_id's own suffix instead is immune to this, since it is
    guaranteed to be the literal substring FREESBE's own API will contain.
    Returns:
      status: 'stable' | 'match_missing' | 'ambiguous' | 'malformed' | 'error'
      exact_record: the parsed record for exact_part_id, if still present
      all_matches: parsed records for every live item matching the OEM
    """
    raw_suffix = exact_part_id.split("-", 1)[1] if "-" in exact_part_id else normalized_oem
    try:
        for attempt in range(3):
            try:
                r_exact = await client.get(
                    API_BASE, params={"filters[partId][$eq]": exact_part_id, "pagination[pageSize]": 5},
                    timeout=20,
                )
                r_exact.raise_for_status()
                r_broad = await client.get(
                    API_BASE, params={"filters[partId][$containsi]": raw_suffix, "pagination[pageSize]": 20},
                    timeout=20,
                )
                r_broad.raise_for_status()
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(1.5 ** attempt)

        exact_items = r_exact.json().get("data", [])
        broad_items = r_broad.json().get("data", [])

        exact_record = None
        if exact_items:
            try:
                exact_record = parse_freesbe_item(exact_items[0])
            except TypeError:
                return {"status": "malformed", "exact_record": None, "all_matches": []}

        all_matches = []
        for item in broad_items:
            try:
                parsed = parse_freesbe_item(item)
            except TypeError:
                continue
            if parsed:
                all_matches.append(parsed)

        if exact_record is None:
            return {"status": "match_missing", "exact_record": None, "all_matches": all_matches}
        if len(all_matches) > 1:
            return {"status": "ambiguous", "exact_record": exact_record, "all_matches": all_matches}
        return {"status": "stable", "exact_record": exact_record, "all_matches": all_matches}
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:200], "exact_record": None, "all_matches": []}


async def batch_live_revalidate(candidates: "list[dict]") -> "dict[str, dict]":
    """Bounded-concurrency live revalidation for a batch of report candidates
    (keyed by catalog_row_id). Caches by normalized_oem so the 19 known
    same-manufacturer duplicate-SKU cases (two catalog rows -> one FREESBE
    record) issue one live query, not two. Never falls back to stale report
    data on a transient failure — an 'error' status is a caller-side skip,
    not a value to substitute with the old report."""
    sem = asyncio.Semaphore(LIVE_REVALIDATION_CONCURRENCY)
    cache: dict[str, dict] = {}
    cache_lock = asyncio.Lock()
    results: dict[str, dict] = {}

    async with httpx.AsyncClient() as client:
        async def _one(cand):
            oem = cand["normalized_oem"]
            async with cache_lock:
                cached = cache.get(oem)
            if cached is not None:
                results[cand["catalog_row_id"]] = cached
                return
            async with sem:
                res = await targeted_freesbe_lookup(client, oem, cand["freesbe_part_id"])
            async with cache_lock:
                cache[oem] = res
            results[cand["catalog_row_id"]] = res

        await asyncio.gather(*[_one(c) for c in candidates])
    return results


# ─────────────────────────── orchestration: dry-run ─────────────────────────

async def run_dry_run(max_pages: "int | None" = None) -> dict:
    fetch_started_at = datetime.utcnow().isoformat()
    records = await fetch_all_records(max_pages=max_pages)
    # Snapshot identity — NOT a guarantee of completeness/stability (see
    # fetch_page's docstring: the upstream catalog is proven to be under
    # continuous concurrent write activity, so no full crawl can be treated
    # as a stable point-in-time truth). Recorded so a later apply attempt can
    # see how old/different the snapshot it's working from is, and so drift
    # is visible rather than silently assumed away.
    snapshot_identity = sorted((r["part_id"], str(r["price"])) for r in records)
    freesbe_snapshot_sha256 = __import__("hashlib").sha256(
        json.dumps(snapshot_identity, separators=(',', ':')).encode()
    ).hexdigest()

    lookup_map: dict[str, list[dict]] = {}
    for rec in records:
        keys = {normalize_oem(rec["raw_oem"]), normalize_oem(rec["prefix"] + rec["raw_oem"])}
        for k in keys:
            lookup_map.setdefault(k, []).append(rec)
    all_keys = list(lookup_map.keys())

    conn = await asyncpg.connect(DB_URL, timeout=15)
    await conn.execute("SET statement_timeout = '600000'")
    try:
        db_rows = await conn.fetch(
            """
            SELECT id, sku, oem_number, manufacturer, importer_price_ils, base_price,
                   max_price_ils, specifications, is_active
            FROM parts_catalog
            WHERE regexp_replace(upper(COALESCE(oem_number, '')), '[^A-Z0-9]', '', 'g') = ANY($1::text[])
            """,
            all_keys,
        )
    finally:
        await conn.close()

    matched_by_key: dict[str, list] = {}
    for db_row in db_rows:
        norm = normalize_oem(db_row["oem_number"] or "")
        matched_by_key.setdefault(norm, []).append(db_row)

    record_match_counts: dict[str, dict] = {}
    for norm, rows in matched_by_key.items():
        candidates = lookup_map.get(norm, [])
        for rec in candidates:
            info = record_match_counts.setdefault(rec["part_id"], {"rows": [], "manus": set()})
            existing_ids = {x["id"] for x in info["rows"]}
            for r in rows:
                if r["id"] not in existing_ids:
                    info["rows"].append(r)
                    info["manus"].add((r["manufacturer"] or "").strip())
                    existing_ids.add(r["id"])

    results = []
    seen_freesbe_with_match = set()

    for db_row in db_rows:
        norm = normalize_oem(db_row["oem_number"] or "")
        candidates = lookup_map.get(norm, [])
        if not candidates:
            continue
        candidates_sorted = sorted(candidates, key=lambda r: (r["_page"], r["_idx"]))
        winner = candidates_sorted[-1]
        seen_freesbe_with_match.add(winner["part_id"])

        exp = expected_values(winner)
        specs, source_invalid = resolve_specifications(db_row["specifications"])
        source = specs.get("source") if specs else None

        cur_importer = _to_decimal(db_row["importer_price_ils"])
        cur_base = _to_decimal(db_row["base_price"])
        cur_max = _to_decimal(db_row["max_price_ils"])

        match_info = record_match_counts.get(
            winner["part_id"], {"rows": [db_row], "manus": {(db_row["manufacturer"] or "").strip()}}
        )

        classification, tier, reason = classify_row(
            is_active=bool(db_row["is_active"]),
            current_importer_price=cur_importer,
            current_base_price=cur_base,
            live_price=winner["price"],
            expected_base_price=exp["expected_base_price"],
            source=source,
            specs=specs,
            source_invalid=source_invalid,
            n_freesbe_candidates=len(candidates_sorted),
            n_catalog_matches_for_record=len(match_info["rows"]),
            manufacturers_for_record=match_info["manus"],
        )

        importer_delta = (exp["expected_importer_price"] - cur_importer) if cur_importer is not None else None
        base_delta = (exp["expected_base_price"] - cur_base) if cur_base is not None else None
        max_delta = (exp["expected_max_price"] - cur_max) if cur_max is not None else None

        results.append({
            "catalog_row_id": str(db_row["id"]),
            "catalog_sku": db_row["sku"],
            "catalog_manufacturer": db_row["manufacturer"],
            "normalized_oem": norm,
            "catalog_source": source,
            "freesbe_part_id": winner["part_id"],
            "live_price": str(winner["price"]),
            "live_price_without_vat": str(winner["price_without_vat"]),
            "current_importer_price": str(cur_importer) if cur_importer is not None else None,
            "current_base_price": str(cur_base) if cur_base is not None else None,
            "current_max_price": str(cur_max) if cur_max is not None else None,
            "expected_importer_price": str(exp["expected_importer_price"]),
            "expected_base_price": str(exp["expected_base_price"]),
            "expected_max_price": str(exp["expected_max_price"]),
            "importer_delta": str(importer_delta) if importer_delta is not None else None,
            "base_delta": str(base_delta) if base_delta is not None else None,
            "max_price_delta": str(max_delta) if max_delta is not None else None,
            "classification": classification,
            "confidence_tier": tier,
            "cross_manufacturer_flag": len(match_info["manus"]) > 1 and len(match_info["rows"]) > 1,
            "duplicate_match_flag": len(candidates_sorted) > 1,
            "large_delta_flag": base_delta is not None and abs(base_delta) > LARGE_DELTA_THRESHOLD_ILS,
            "reason": reason,
        })

    no_catalog_match_count = len({r["part_id"] for r in records} - seen_freesbe_with_match)

    summary = {
        "api_records_fetched": len(records),
        "catalog_matches": len(results),
        "no_catalog_match": no_catalog_match_count,
    }
    for c in CLASSIFICATIONS:
        summary[c] = sum(1 for r in results if r["classification"] == c)
    for t in TIERS:
        summary[f"tier_{t}"] = sum(1 for r in results if r["confidence_tier"] == t)

    manifest_identity = sorted(
        (r["catalog_row_id"], r["freesbe_part_id"]) for r in results if r["classification"] == "SAFE_AUTOMATIC_RECOVERY"
    )
    candidate_manifest_sha256 = __import__("hashlib").sha256(
        json.dumps(manifest_identity, separators=(',', ':')).encode()
    ).hexdigest()

    return {
        "run_id": f"freesbe_recovery_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
        "generated_at": datetime.utcnow().isoformat(),
        "fetch_started_at": fetch_started_at,
        "freesbe_snapshot_sha256": freesbe_snapshot_sha256,
        "candidate_manifest_sha256": candidate_manifest_sha256,
        "mode": "dry_run",
        "summary": summary,
        "rows": results,
    }


def print_human_summary(report: dict) -> None:
    s = report["summary"]
    print("=== FREESBE RECOVERY — DRY-RUN SUMMARY (NO WRITES PERFORMED) ===")
    print(f"run_id: {report['run_id']}")
    print(f"API records fetched: {s['api_records_fetched']}")
    print(f"Catalog matches: {s['catalog_matches']}  |  No catalog match: {s['no_catalog_match']}")
    for c in sorted(CLASSIFICATIONS):
        print(f"  {c}: {s.get(c, 0)}")
    print("Confidence tiers:")
    for t in sorted(TIERS):
        print(f"  TIER {t}: {s.get(f'tier_{t}', 0)}")
    print()
    rows = report["rows"]
    top = sorted((r for r in rows if r["base_delta"]), key=lambda r: abs(float(r["base_delta"])), reverse=True)[:25]
    print("Top 25 largest |base_price| deltas:")
    for r in top:
        print(f"  sku={r['catalog_sku']} manu={r['catalog_manufacturer']} class={r['classification']} "
              f"tier={r['confidence_tier']} cur_base={r['current_base_price']} exp_base={r['expected_base_price']} "
              f"delta={r['base_delta']}")
    print()
    cross = [r for r in rows if r["classification"] == "CROSS_MANUFACTURER"]
    print(f"All CROSS_MANUFACTURER rows ({len(cross)}), sample of 20:")
    for r in cross[:20]:
        print(f"  sku={r['catalog_sku']} manu={r['catalog_manufacturer']} freesbe={r['freesbe_part_id']}")
    print()
    proven_other = [r for r in rows if r["classification"] == "PROVEN_OTHER_SOURCE"]
    print(f"PROVEN_OTHER_SOURCE rows ({len(proven_other)}), sample of 10:")
    for r in proven_other[:10]:
        print(f"  sku={r['catalog_sku']} source={r['catalog_source']}")


# ─────────────────────────── write path (never invoked against Production in this task) ──

def _live_gate(cand: dict, live: dict) -> "tuple[str, str, dict | None]":
    """Pure function: given one report candidate and its batch_live_revalidate
    result, decide the apply-time outcome BEFORE any DB access. Returns
    (outcome, reason, live_record_to_use_or_None). live_record is the
    freshly-fetched record whose price is used for the actual write —
    the report's OLD live_price/expected_* values are NEVER trusted for the
    write itself, only for comparison/logging.
    """
    status = live.get("status")
    if status == "error":
        return "ERROR", f"live revalidation failed: {live.get('error')}", None
    if status == "malformed":
        return "SKIP_MALFORMED_LIVE_RECORD", "live record for the known partId could not be parsed", None
    if status == "match_missing":
        return "SKIP_LIVE_MATCH_MISSING", "the specific FREESBE record this candidate was based on no longer exists live", None
    if status == "ambiguous":
        return "SKIP_LIVE_OEM_AMBIGUITY", (
            f"{len(live['all_matches'])} live FREESBE records now match this OEM "
            f"(was 1 at dry-run time) — ambiguity has newly appeared"
        ), None

    rec = live["exact_record"]
    if rec["part_id"] != cand["freesbe_part_id"]:
        return "SKIP_LIVE_IDENTITY_CHANGED", "live record's partId no longer matches the candidate's recorded identity", None
    if str(rec["price"]) != cand["live_price"]:
        return "SKIP_LIVE_PRICE_CHANGED", (
            f"live price ({rec['price']}) differs from the price recorded at dry-run time ({cand['live_price']})"
        ), None
    return "LIVE_STABLE", "live record confirmed stable", rec


async def apply_recovery(from_report: str, confirm: bool, max_rows: "int | None" = None, dry_run: bool = False) -> dict:
    """Write path. Requires confirm=True (the caller's --confirm-production-
    recovery flag). Candidates are loaded ONLY from --from-report's
    SAFE_AUTOMATIC_RECOVERY rows — the report defines the maximum candidate
    universe; nothing outside it can ever be added here.

    Two phases, in order:
      Phase A (network only, no DB transaction held): batch_live_revalidate()
      re-checks every candidate against FREESBE live, right now — not the
      report's hours-old snapshot. A full paginated crawl is proven unstable
      (see fetch_page's docstring); this per-candidate targeted check is the
      actual safety mechanism, not a formality.
      Phase B (DB only, per-row transaction, no network calls inside a held
      transaction): for each candidate whose Phase-A result was LIVE_STABLE,
      re-read the current DB row, check it still matches the report's
      recorded "before" snapshot (SKIP_DB_STATE_DRIFT otherwise), re-run the
      full classification gate (SKIP_RECLASSIFIED / SKIP_LARGE_DELTA /
      SKIP_ALREADY_CORRECT otherwise), and only then write — using the
      FRESH live values from Phase A, never the report's stale ones.

    Idempotent: re-running against already-corrected rows finds them
    SKIP_ALREADY_CORRECT and writes nothing further. A transient live-check
    failure is ERROR (skip) — it never falls back to the stale report value.

    dry_run=True (added 2026-09-07 for the Apply Preflight process): runs
    the IDENTICAL Phase A/B gate logic — same live revalidation, same
    DB-state-drift check (plain SELECT, no FOR UPDATE lock, since nothing
    will be written), same reclassification — but the final UPDATE is never
    issued. A row that clears every gate is recorded as APPLY_ELIGIBLE with
    its full fresh expected values instead of being written. This exists so
    a preflight can prove exactly which candidates are eligible RIGHT NOW
    using the real, unduplicated recovery logic, without any write path
    being reachable — confirm is still required even in dry_run mode, to
    keep this function's calling contract uniform.
    """
    if not confirm:
        raise RuntimeError("apply_recovery() called without confirm=True — refusing to write")

    with open(from_report) as f:
        report = json.load(f)
    candidates = [r for r in report["rows"] if r["classification"] == "SAFE_AUTOMATIC_RECOVERY"]
    if max_rows is not None:
        candidates = candidates[:max_rows]

    print(f"Phase A: live-revalidating {len(candidates)} candidates against FREESBE "
          f"(concurrency={LIVE_REVALIDATION_CONCURRENCY})...", file=sys.stderr)
    live_results = await batch_live_revalidate(candidates)

    outcomes = {k: 0 for k in [
        "applied", "skip_db_state_drift", "skip_live_match_missing", "skip_live_oem_ambiguity",
        "skip_live_identity_changed", "skip_live_manufacturer_conflict", "skip_live_price_changed",
        "skip_large_delta", "skip_already_correct", "skip_malformed_live_record",
        "skip_inactive", "skip_proven_other_source", "skip_reclassified", "error",
    ]}
    detail = []

    conn = await asyncpg.connect(DB_URL, timeout=15)
    try:
        for cand in candidates:
            live = live_results.get(cand["catalog_row_id"], {"status": "error", "error": "no live result computed"})
            gate_outcome, gate_reason, live_rec = _live_gate(cand, live)

            if gate_outcome != "LIVE_STABLE":
                key = "error" if gate_outcome == "ERROR" else gate_outcome.lower()
                outcomes[key] = outcomes.get(key, 0) + 1
                detail.append({"id": cand["catalog_row_id"], "outcome": gate_outcome, "reason": gate_reason})
                continue

            async def _fetch_row(txn_conn):
                query = (
                    "SELECT importer_price_ils, base_price, max_price_ils, manufacturer, is_active, "
                    "specifications FROM parts_catalog WHERE id = $1"
                    + ("" if dry_run else " FOR UPDATE")
                )
                return await txn_conn.fetchrow(query, cand["catalog_row_id"])

            async def _process(row):
                """Shared logic for BOTH dry_run and real-write modes — the
                2026-09-07 bug this replaces had this entire block nested
                ONLY inside the real-write branch, so every dry_run candidate
                that reached this point (i.e. was LIVE_STABLE) silently
                recorded NO outcome at all. Found via a full 41,990-candidate
                run whose outcome counts summed to far less than 41,990 —
                proof this must never be sampled-only in the future either.
                Returns (outcome_key_or_None, detail_dict, write_needed:bool, fresh_exp_or_None).
                """
                if row is None:
                    return "error", {"id": cand["catalog_row_id"], "outcome": "ERROR", "reason": "row no longer exists"}, False, None

                cur_importer = _to_decimal(row["importer_price_ils"])
                cur_base = _to_decimal(row["base_price"])
                expected_cur_importer = _to_decimal(cand["current_importer_price"])
                expected_cur_base = _to_decimal(cand["current_base_price"])

                if cur_importer != expected_cur_importer or cur_base != expected_cur_base or not row["is_active"]:
                    return "skip_db_state_drift", {"id": cand["catalog_row_id"], "outcome": "SKIP_DB_STATE_DRIFT",
                                                    "reason": "current DB values no longer match the dry-run snapshot"}, False, None

                fresh_exp = expected_values(live_rec)
                specs, invalid = resolve_specifications(row["specifications"])
                source = specs.get("source") if specs else None
                reclass, tier, reason = classify_row(
                    is_active=bool(row["is_active"]), current_importer_price=cur_importer,
                    current_base_price=cur_base, live_price=live_rec["price"],
                    expected_base_price=fresh_exp["expected_base_price"],
                    source=source, specs=specs, source_invalid=invalid,
                )
                if reclass == "NO_CHANGE":
                    return "skip_already_correct", {"id": cand["catalog_row_id"], "outcome": "SKIP_ALREADY_CORRECT", "reason": reclass}, False, None
                if reclass == "SAFE_BUT_LARGE_DELTA":
                    return "skip_large_delta", {"id": cand["catalog_row_id"], "outcome": "SKIP_LARGE_DELTA", "reason": reclass}, False, None
                if reclass == "INACTIVE":
                    return "skip_inactive", {"id": cand["catalog_row_id"], "outcome": "SKIP_INACTIVE", "reason": reclass}, False, None
                if reclass == "PROVEN_OTHER_SOURCE":
                    return "skip_proven_other_source", {"id": cand["catalog_row_id"], "outcome": "SKIP_PROVEN_OTHER_SOURCE", "reason": reclass}, False, None
                if reclass != "SAFE_AUTOMATIC_RECOVERY":
                    return "skip_reclassified", {"id": cand["catalog_row_id"], "outcome": "SKIP_RECLASSIFIED", "reason": reclass}, False, None

                if dry_run:
                    return "applied", {
                        "id": cand["catalog_row_id"], "outcome": "APPLY_ELIGIBLE",
                        "catalog_sku": cand.get("catalog_sku"),
                        "catalog_manufacturer": row["manufacturer"],
                        "normalized_oem": cand.get("normalized_oem"),
                        "live_part_id": live_rec["part_id"],
                        "current_importer_price_ils": str(cur_importer),
                        "current_base_price": str(cur_base),
                        "expected_importer_price_ils": str(fresh_exp["expected_importer_price"]),
                        "expected_base_price": str(fresh_exp["expected_base_price"]),
                        "expected_max_price_ils": str(fresh_exp["expected_max_price"]),
                        "delta_base": str(fresh_exp["expected_base_price"] - cur_base),
                    }, False, None
                return "applied", {"id": cand["catalog_row_id"], "outcome": "applied"}, True, fresh_exp

            try:
                if dry_run:
                    row = await _fetch_row(conn)
                    key, entry, write_needed, fresh_exp = await _process(row)
                else:
                    async with conn.transaction():  # per-row savepoint — no network calls inside
                        row = await _fetch_row(conn)
                        key, entry, write_needed, fresh_exp = await _process(row)
                        if write_needed:
                            await conn.execute(
                                """UPDATE parts_catalog
                                   SET importer_price_ils = $1, base_price = $2, max_price_ils = $3, updated_at = NOW()
                                   WHERE id = $4""",
                                fresh_exp["expected_importer_price"], fresh_exp["expected_base_price"],
                                fresh_exp["expected_max_price"], cand["catalog_row_id"],
                            )
                outcomes[key] = outcomes.get(key, 0) + 1
                detail.append(entry)
            except Exception as exc:
                outcomes["error"] += 1
                detail.append({"id": cand["catalog_row_id"], "outcome": "ERROR", "reason": str(exc)[:200]})
    finally:
        await conn.close()

    return {"candidates_considered": len(candidates), "outcomes": outcomes, "detail": detail}


# ─────────────────────────── CLI ────────────────────────────────────────────

async def _main():
    parser = argparse.ArgumentParser(description="FREESBE recovery tool. Default mode is dry-run.")
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--out", type=str, default=None)
    parser.add_argument("--apply", action="store_true", help="Enter write mode (still requires --confirm-production-recovery).")
    parser.add_argument("--confirm-production-recovery", action="store_true")
    parser.add_argument("--from-report", type=str, default=None, help="Prior dry-run JSON report to apply against.")
    parser.add_argument("--max-rows", type=int, default=None, help="Row-count guard for --apply.")
    args = parser.parse_args()

    if args.apply:
        if not args.confirm_production_recovery or not args.from_report:
            print("ERROR: --apply requires both --confirm-production-recovery and --from-report.", file=sys.stderr)
            sys.exit(2)
        print("=== FREESBE RECOVERY TOOL — APPLY MODE ===")
        result = await apply_recovery(args.from_report, confirm=True, max_rows=args.max_rows)
        print(json.dumps(result["outcomes"], indent=2))
        if args.out:
            with open(args.out, "w") as f:
                json.dump(result, f, indent=2, default=str)
        return

    print("=== FREESBE RECOVERY TOOL — DRY-RUN MODE (default; no write path taken) ===")
    report = await run_dry_run(max_pages=args.max_pages)
    print_human_summary(report)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nFull JSON report written to {args.out}")


if __name__ == "__main__":
    asyncio.run(_main())
