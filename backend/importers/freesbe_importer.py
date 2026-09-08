#!/usr/bin/env python3
"""
freesbe_importer.py
Import IL prices from the freesbe.com open Strapi API (admin.freesbe.com).
177K parts from Israeli importer covering Renault/Dacia (RE-), Nissan/Infiniti (NI-),
Chery (CH-), Xpeng (XP-), JAC/JAECOO (JM-), and others.

Strategy:
  1. Page through all parts via pagination
  2. For each part: strip the brand prefix to get raw OEM
  3. Try matching DB with and without prefix
  4. Update max_price_ils for matches
  5. Insert new Dacia/Renault parts for unmatched RE- OEMs

Price fields (verified live against the API 2026-09-05 — 20/20 sampled items
across pages 1/500/1000/1785 all carry both fields, ratio price/priceWithoutVat
consistently ~1.18):
  - price            = ILS INCL. VAT (Israeli-supplier reference/consumer price)
  - priceWithoutVat  = ILS EXCL. VAT (the authoritative supplier cost)

Pricing-policy fix 2026-09-05 (forensic audit: FREESBE was violating the
platform's authoritative pricing contract — CLAUDE.md "Import formula" /
BACKEND_AI_AGENTS.get_supplier_vat_rate / db_update_agent.normalize_base_price
all assume `importer_price_ils` holds the ex-VAT supplier cost). Freesbe is an
Israeli supplier, so the policy is:
    supplier_cost_ex_vat = priceWithoutVat            (from the API directly)
    importer_price_ils   = supplier_cost_ex_vat        (ex-VAT cost, per contract)
    base_price            = supplier_cost_ex_vat × 1.45 (45% markup, per policy)
    max_price_ils         = price                       (VAT-inclusive reference)
Previously: the INSERT branch stored the VAT-INCLUSIVE `price` directly into
`importer_price_ils` (only compensating inside this file's own `/1.18`
normalization formula, now corrected below), and the UPDATE branch — which touches far more
rows (173,852 in the 2026-09-05 run vs. 2,674 inserted, matching by OEM number
across EVERY manufacturer in the catalog, not just Renault/Dacia/Nissan/etc.)
— wrote the raw VAT-inclusive price into `importer_price_ils` with NO
conversion at all and never touched `base_price`. Both are fixed below to use
`priceWithoutVat` (falling back to `price/1.18` only if the API ever omits it)
and to set `importer_price_ils`/`base_price`/`max_price_ils` together, in one
write, matching the same contract every other IL importer already follows
(colmobil_import_v2.py, kia_israel_harvester.py, etc.). This makes freesbe's
own post-loop `normalize_base_price_batched()` a pure redundant safety net for
its own writes going forward (kept for defense-in-depth, formula corrected to
match db_update_agent's canonical `importer_price_ils × 1.45` — it no longer
divides by 1.18, since `importer_price_ils` is now already ex-VAT).

Hardened 2026-09-05 (forensic investigation of a 2.5-month-hung invocation
found on the same day the P1 stuck-orders fix was deployed):
  - `asyncpg.connect()` had no timeout and could hang indefinitely if the DB
    were unreachable — this is the strongly-indicated cause of the hang.
  - The post-loop base_price backfill was one unbounded, unbatched, no-timeout
    UPDATE across the whole parts_catalog table — a lock-contention risk
    against concurrently-writing harvester/scraper pipelines (see CLAUDE.md
    "Two heavy writers on one table contend").
  - The `agent_todos` completion INSERT had no idempotency guard: 16 prior
    clean runs produced 32 duplicate rows, one pair per run, regardless of
    whether any new work existed.
  - Nothing detected an already-complete checkpoint before doing this work;
    a rerun of a 100%-complete import still executed the full normalization
    + todo-queueing tail every time.
  - No single-instance protection existed; two concurrent invocations could
    race on the same checkpoint file and DB rows.
All four are addressed below. The core matching/update/insert logic in
process_page() is unchanged — it was already correctly idempotent.
"""
import asyncio
import asyncpg
import httpx
import json
import os
import re
import sys
import uuid
from datetime import datetime

# ONE category source of truth — categorize at INGEST so parts never land
# with a NULL category and depend on the self-healing task to find them.
from category_map import categorize_on_ingest

sys.path.insert(0, '/app')

from distributed_lock import acquire_lock  # noqa: E402 — reuse the project-wide Redis lock
from BACKEND_AUTH_SECURITY import get_redis  # noqa: E402

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
API_BASE = "https://admin.freesbe.com/api/parts"
PAGE_SIZE = 100
CONCURRENCY = 4
PROGRESS_FILE = "/app/state/freesbe_import_progress.json"

# Hardening knobs (env-overridable, safe defaults for this project's box).
DB_CONNECT_TIMEOUT_S = float(os.getenv("FREESBE_DB_CONNECT_TIMEOUT_S", "15"))
NORMALIZE_BATCH_SIZE = int(os.getenv("FREESBE_NORMALIZE_BATCH_SIZE", "2000"))
NORMALIZE_STATEMENT_TIMEOUT_MS = int(os.getenv("FREESBE_NORMALIZE_STATEMENT_TIMEOUT_MS", "30000"))
LOCK_NAME = "freesbe_importer"
LOCK_TTL_SECONDS = int(os.getenv("FREESBE_LOCK_TTL_SECONDS", "7200"))  # 2h — crash-safe expiry

# Map freesbe prefix → our DB manufacturer name(s) for new-part insertion
PREFIX_TO_MANUFACTURER = {
    "RE": "Renault",    # RE- parts serve Renault + Dacia (same group parts)
    "NI": "Nissan",     # NI- serves Nissan + Infiniti
    "CH": "Chery",
    "XP": "Xpeng",
    "JM": "JAC",
}


def load_progress():
    try:
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"completed_pages": [], "updated": 0, "inserted": 0, "not_found": 0}


def save_progress(progress):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f)


def compute_pages_to_process(completed: set, total_pages: int) -> list:
    """Pages 1..total_pages not yet marked complete, in order.

    Pure function — a checkpoint gap (e.g. 1..100 + 102..1771, page 101
    missing) always leaves the gap page eligible; nothing here can mark a
    page complete that isn't explicitly in ``completed``.
    """
    return [p for p in range(1, total_pages + 1) if p not in completed]


def is_import_complete(completed: set, total_pages: int) -> bool:
    """True only when every page 1..total_pages is already recorded complete."""
    return total_pages > 0 and len(compute_pages_to_process(completed, total_pages)) == 0


def _extract_attrs(item: dict) -> dict:
    """Freesbe/Strapi has used two response shapes for the same item:

      legacy (Strapi v4, nested): {"id": 1, "attributes": {"partId": "...", "price": "...", ...}}
      current (Strapi v5, flat):  {"id": 1, "partId": "...", "price": "...", ...}

    Root cause of the 2026-09-05 Production crash (KeyError: 'attributes'):
    a live, read-only schema audit that day sampled 451 real items across
    7 pages (1, 100, 500, 900, 1000, 1300, 1772-1774, 1785) and found the
    API now returns ONLY the flat shape — the "attributes" key does not
    exist anywhere in the current response. This helper accepts either:
    if "attributes" is present, use it (the legacy nested case, preserved
    for forward/backward compatibility in case the API ever reverts or
    mixes formats); otherwise every field parse_part() needs already lives
    directly on `item` (the current flat case), so falling back to `item`
    itself is correct.
    """
    return item.get("attributes", item)


def parse_part(item):
    """Extract fields from a Strapi part item (handles both the legacy
    nested v4 shape and the current flat v5 shape — see _extract_attrs).
    Returns None for a record that isn't a usable priced part (missing/
    malformed partId, no dash prefix, missing/non-positive price) — this
    is the pre-existing convention for "not an actionable part", not an
    error condition. A genuinely malformed item (e.g. not a dict at all)
    still raises visibly to the caller, which isolates and logs it per
    item rather than silently continuing (see fetch_one())."""
    attrs = _extract_attrs(item)
    part_id = attrs.get("partId")  # e.g. "RE-7701053319"
    if not part_id or "-" not in part_id:
        return None
    prefix, raw_oem = part_id.split("-", 1)
    raw_oem = raw_oem.strip()
    if not raw_oem:
        return None
    price_str = attrs.get("price")
    try:
        price_ils = float(price_str) if price_str else None
    except (ValueError, TypeError):
        price_ils = None
    if not price_ils or price_ils <= 0:
        return None
    # `priceWithoutVat` is the API's own ex-VAT supplier cost — verified live
    # 2026-09-05 present on every sampled item (ratio to `price` ~1.18). This
    # is the authoritative cost per the platform pricing policy (Israeli
    # supplier: supplier_cost_ex_vat × 1.45 = base_price). Fall back to
    # deriving it from `price` only if the API ever omits/malforms it.
    price_wo_vat_str = attrs.get("priceWithoutVat")
    try:
        price_ex_vat = round(float(price_wo_vat_str), 2) if price_wo_vat_str else None
    except (ValueError, TypeError):
        price_ex_vat = None
    if not price_ex_vat or price_ex_vat <= 0:
        price_ex_vat = round(price_ils / 1.18, 2)
    return {
        "part_id": part_id,
        "prefix": prefix.upper(),
        "raw_oem": raw_oem,
        "price_ils": price_ils,        # VAT-inclusive — reference price, maps to max_price_ils
        "price_ex_vat": price_ex_vat,  # ex-VAT supplier cost — maps to importer_price_ils
        "description": attrs.get("description", ""),
        "is_original": attrs.get("isOriginal", True),
        "is_available": attrs.get("isAvailable", True),
    }


def normalize_oem(oem: str) -> str:
    """Strip all non-alphanumeric chars to match idx_parts_oem_normalized."""
    return re.sub(r'[^A-Z0-9]', '', oem.upper())


async def get_brand_id(conn, manufacturer: str) -> str | None:
    """Get car_brands.id for a manufacturer name."""
    row = await conn.fetchrow(
        "SELECT id FROM car_brands WHERE lower(name) = lower($1) AND is_active = TRUE LIMIT 1",
        manufacturer,
    )
    return str(row["id"]) if row else None


async def process_page(conn, parts: list, stats: dict, brand_id_cache: dict,
                       lock: asyncio.Lock | None = None):
    """Match and update/insert a batch of parts. Idempotency shape unchanged
    (2026-09-05 hardening): the UPDATE targets a single row by primary key,
    and the INSERT is an ON CONFLICT (sku) DO UPDATE upsert with the
    project's mandated CASE guard. Pricing math CHANGED 2026-09-05 (see the
    module docstring's "Pricing-policy fix" section) — both branches now
    write importer_price_ils/base_price/max_price_ils together from the
    API's own ex-VAT `priceWithoutVat` field, per the platform contract.

    ``conn`` must be a dedicated connection (from a pool), not shared.
    ``lock`` (optional) serializes writes to shared ``stats`` and ``brand_id_cache``.
    """
    if not parts:
        return

    # Build normalized OEM lookup keys for this batch
    lookup_map = {}  # norm_key -> [part dict, ...]
    for p in parts:
        raw = p["raw_oem"]
        prefix = p["prefix"]
        keys = [
            normalize_oem(raw),           # "7701053319"
            normalize_oem(prefix + raw),  # "RE7701053319"
        ]
        for k in keys:
            if k not in lookup_map:
                lookup_map[k] = []
            lookup_map[k].append(p)

    all_keys = list(lookup_map.keys())

    # Batch DB lookup — uses idx_parts_oem_normalized (partial index on is_active=true)
    db_rows = await conn.fetch(
        """
        SELECT id, oem_number, manufacturer, importer_price_ils, base_price
        FROM parts_catalog
        WHERE regexp_replace(upper(COALESCE(oem_number, '')), '[^A-Z0-9]', '', 'g') = ANY($1::text[])
          AND is_active = TRUE
        """,
        all_keys,
    )

    matched_oems = set()
    for db_row in db_rows:
        norm = normalize_oem(db_row["oem_number"] or "")
        source_parts = lookup_map.get(norm, [])
        for sp in source_parts:
            matched_oems.add(sp["raw_oem"])
            new_cost_ex_vat = sp["price_ex_vat"]
            old_price = db_row["importer_price_ils"]
            # Skip if existing importer price is already higher (prefer fresher/higher data).
            # Both sides are ex-VAT cost now — previously this compared an ex-VAT
            # old_price against a VAT-inclusive new_price, an 18%-skewed comparison.
            if old_price and old_price > 0 and old_price > new_cost_ex_vat * 2:
                continue
            new_base_price = round(new_cost_ex_vat * 1.45, 2)
            await conn.execute(
                """UPDATE parts_catalog
                   SET importer_price_ils = $1, base_price = $2, max_price_ils = $3, updated_at = NOW()
                   WHERE id = $4""",
                new_cost_ex_vat, new_base_price, sp["price_ils"], db_row["id"],
            )
            stats["updated"] += 1

    # Insert unmatched RE- parts as new Renault parts (Dacia uses same Renault OEMs)
    for p in parts:
        if p["raw_oem"] in matched_oems:
            continue
        if p["prefix"] not in ("RE",):
            stats["not_found"] += 1
            continue
        manufacturer = PREFIX_TO_MANUFACTURER[p["prefix"]]
        if manufacturer not in brand_id_cache:
            brand_id_cache[manufacturer] = await get_brand_id(conn, manufacturer)
        brand_id = brand_id_cache.get(manufacturer)
        if not brand_id:
            stats["not_found"] += 1
            continue
        new_id = str(uuid.uuid4())
        name = p["description"] or p["raw_oem"]
        price_inc_vat = p["price_ils"]      # VAT-inclusive reference price -> max_price_ils
        cost_ex_vat = p["price_ex_vat"]     # ex-VAT supplier cost -> importer_price_ils
        # base_price = cost (ex-VAT) × 1.45 margin per pricing policy
        base_price = round(cost_ex_vat * 1.45, 2)
        sku = f"RE-{p['raw_oem'].lstrip('0') or p['raw_oem']}"
        await conn.execute(
            """
            INSERT INTO parts_catalog (
                id, sku, oem_number, name, manufacturer, manufacturer_id, category,
                specifications,
                importer_price_ils, base_price, max_price_ils, is_active,
                master_enriched, needs_oem_lookup, created_at, updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$10,$11::jsonb,$7,$8,$9,TRUE,FALSE,FALSE,NOW(),NOW())
            ON CONFLICT (sku) DO UPDATE SET
                importer_price_ils = CASE WHEN EXCLUDED.importer_price_ils > 0 THEN EXCLUDED.importer_price_ils ELSE parts_catalog.importer_price_ils END,
                base_price = CASE WHEN EXCLUDED.importer_price_ils > 0 THEN EXCLUDED.base_price ELSE parts_catalog.base_price END,
                max_price_ils = CASE WHEN EXCLUDED.importer_price_ils > 0 THEN EXCLUDED.max_price_ils ELSE parts_catalog.max_price_ils END,
                updated_at = NOW()
            """,
            new_id, sku, p["raw_oem"], name, manufacturer, brand_id,
            cost_ex_vat, base_price, price_inc_vat,
            categorize_on_ingest(name=name),
            json.dumps({"source": "freesbe_importer", "source_url": API_BASE,
                        "raw_oem": p.get("raw_oem")}),
        )
        stats["inserted"] += 1


async def fetch_page(client: httpx.AsyncClient, page: int) -> dict | None:
    url = f"{API_BASE}?pagination[page]={page}&pagination[pageSize]={PAGE_SIZE}"
    for attempt in range(3):
        try:
            resp = await client.get(url, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == 2:
                print(f"  ERROR page {page}: {e}")
                return None
            await asyncio.sleep(2 ** attempt)


async def connect_with_timeout(db_url: str, timeout_s: float = DB_CONNECT_TIMEOUT_S):
    """asyncpg.connect() has no timeout by default and can hang indefinitely
    if the database is unreachable. Root-cause of a Freesbe invocation found
    hung for 2.5+ months (2026-09-05 forensic investigation) — no checkpoint
    write and no completion record existed after the point a connection
    would have been attempted, and the source had no bound on this call.
    Bounded here with both asyncpg's own connect-timeout and an outer
    asyncio.wait_for backstop, so a stuck connection surfaces as a clean
    diagnostic and a clean exit instead of an indefinite hang.
    """
    try:
        return await asyncio.wait_for(
            asyncpg.connect(db_url, timeout=timeout_s),
            timeout=timeout_s + 5,
        )
    except asyncio.TimeoutError:
        print(f"ERROR: database connection timed out after {timeout_s}s — aborting cleanly.")
        return None
    except Exception as e:
        print(f"ERROR: database connection failed: {e}")
        return None


async def normalize_base_price_batched(
    conn,
    batch_size: int = NORMALIZE_BATCH_SIZE,
    statement_timeout_ms: int = NORMALIZE_STATEMENT_TIMEOUT_MS,
) -> tuple[int, bool]:
    """Backfill base_price in small, short-lived transactions.

    Replaces a single unbounded UPDATE across the whole parts_catalog table
    (no LIMIT, no statement_timeout) that the 2026-09-05 forensic
    investigation flagged as a lock-contention risk against the
    concurrently-writing harvester/scraper pipelines this table always has
    running (see CLAUDE.md "Two heavy writers on one table contend" and
    "Batched writes on harvested tables: always FOR UPDATE SKIP LOCKED").
    Each batch is its own transaction with an explicit statement_timeout —
    a failed/timed-out batch rolls back on its own and never touches rows
    already committed by earlier batches, and the loop can be re-run safely
    at any point since it always re-selects whatever still qualifies.

    NOTE (2026-09-05 production preflight): the candidate predicate has no
    supporting index — `base_price IS NULL OR base_price = 0` alone matched
    2,262,459 of 4,584,792 rows (49.3%) in production, far too unselective
    for any existing single-column index to help, and an EXISTS-wrapped
    version of the same predicate produces an identical full sequential
    scan in its worst (zero-match) case. No safe query-level or
    application-level optimization exists without adding a new composite
    index, which is out of scope here — the batching above already bounds
    the WRITE side; only the read-side candidate scan can still be slow
    when zero (or few) rows qualify. Hence the timeout handling below.

    Returns (rows_updated, complete). complete=False means a batch timed
    out or failed and normalization stopped early WITHOUT crashing the
    caller — already-committed batches remain valid (each batch is its own
    transaction), and a future call safely picks up wherever this left off
    since it always re-selects whatever still qualifies rather than trusting
    a cursor or offset.
    """
    total = 0
    while True:
        try:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}")
                rows = await conn.fetch(
                    """
                    UPDATE parts_catalog
                    SET base_price = ROUND(importer_price_ils * 1.45, 2), updated_at = NOW()
                    WHERE id IN (
                        SELECT id FROM parts_catalog
                        WHERE importer_price_ils > 0
                          AND (base_price IS NULL OR base_price = 0)
                          AND is_active = TRUE
                        LIMIT $1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING id
                    """,
                    batch_size,
                )
        except (asyncpg.exceptions.QueryCanceledError, asyncpg.PostgresError) as exc:
            # The failed/timed-out batch's transaction was rolled back by the
            # `async with conn.transaction()` block above before this
            # exception reached us — nothing here needs to undo anything.
            print(
                f"  ERROR: base_price normalization batch failed after {total} rows "
                f"already committed: {type(exc).__name__}: {exc}"
            )
            print("  base_price normalization INCOMPLETE — safe to retry on next run.")
            return total, False
        n = len(rows)
        total += n
        if n:
            print(f"  base_price batch: {n} rows (total {total})")
        if n < batch_size:
            break
    return total, True


async def todo_already_pending(conn, title: str) -> bool:
    """True if a not-started todo with this exact title is already queued."""
    row = await conn.fetchrow(
        "SELECT 1 FROM agent_todos WHERE title = $1 AND status = 'not_started' LIMIT 1",
        title,
    )
    return row is not None


async def queue_pipeline_todos_if_needed(conn, stats: dict) -> bool:
    """Idempotent version of the original unconditional INSERT.

    The 2026-09-05 forensic investigation found 32 duplicate agent_todos
    rows (16 identical pairs) from 16 prior clean runs, because this insert
    had no guard at all. No schema change is needed — the existing
    (title, status) columns are sufficient to detect an already-pending
    completion todo and skip re-inserting.
    """
    title_a = "Freesbe import: normalize + categorize new parts"
    title_b = "Freesbe import: enrich new Renault/Dacia parts"
    if await todo_already_pending(conn, title_a) or await todo_already_pending(conn, title_b):
        print("Pipeline todos already pending — skipping duplicate insert.")
        return False
    await conn.execute(
        """
        INSERT INTO agent_todos(id, assigned_to_agent, title, description, priority, status, artifacts, created_at, updated_at)
        VALUES
        (gen_random_uuid(), 'db_update_agent', $1, $2, 'high', 'not_started',
         '{"task_names": ["normalize_categories", "fix_base_prices", "normalize_base_price", "fill_car_brands"]}'::jsonb,
         NOW(), NOW()),
        (gen_random_uuid(), 'db_update_agent', $3, $4, 'high', 'not_started',
         '{"task_names": ["enrich_pending_parts", "normalize_imported_manufacturers", "sync_models_from_catalog"]}'::jsonb,
         NOW(), NOW())
        """,
        title_a,
        f"Freesbe import complete: {stats['updated']} updated importer_price_ils, {stats['inserted']} new Renault/Dacia parts inserted. Run normalization + category pass.",
        title_b,
        f"Freesbe {stats['inserted']} new parts need enrichment: manufacturer sync, model matching, AI enrichment (master_enriched=FALSE).",
    )
    print("Pipeline todos queued.")
    return True


async def _run_import():
    progress = load_progress()
    completed = set(progress["completed_pages"])
    stats = {
        "updated": progress["updated"],
        "inserted": progress["inserted"],
        "not_found": progress["not_found"],
    }

    # Get total pages — the one API call needed even on an already-complete
    # checkpoint, since completeness is judged against the *current* remote
    # page count (the source catalog can grow), not a value cached at some
    # earlier point in time.
    async with httpx.AsyncClient() as client:
        first = await fetch_page(client, 1)
        if not first:
            print("Failed to fetch first page, aborting.")
            return
        total_pages = first["meta"]["pagination"]["pageCount"]
        print(f"Total parts: {first['meta']['pagination']['total']}, Pages: {total_pages}")

    if is_import_complete(completed, total_pages):
        print("FREESBE import already complete — no work required")
        return

    conn = await connect_with_timeout(DB_URL)
    if conn is None:
        print("Aborting: could not establish a database connection.")
        return

    try:
        brand_id_cache: dict = {}
        http_sem = asyncio.Semaphore(CONCURRENCY)   # limit concurrent HTTP fetches
        db_lock = asyncio.Lock()                     # serialize DB writes on single connection

        fetched: dict[int, list] = {}  # page -> parts list, populated by HTTP coroutines

        async def fetch_one(page: int):
            if page in completed:
                return
            async with http_sem:
                async with httpx.AsyncClient() as client:
                    data = await fetch_page(client, page)
            if data:
                # Root-fixed 2026-09-05: parse_part() raising for a SINGLE
                # malformed item used to abort the whole page's parse via the
                # list comprehension, which in turn aborted the entire
                # concurrent asyncio.gather() batch (up to BATCH=20 pages) —
                # exactly what happened in the blocked Production execution
                # (KeyError: 'attributes'). One bad item must not prevent
                # every other valid item on this page, or every other page
                # in this batch, from being processed. Only a narrow, named
                # set of parse-level exceptions is caught here (not a bare
                # `except Exception`) — anything else (e.g. `data["data"]`
                # itself being malformed) still raises visibly, matching the
                # existing fail-loud behavior for genuinely unexpected
                # structural violations.
                raw_parts = []
                for item in data.get("data", []):
                    try:
                        parsed = parse_part(item)
                    except (KeyError, TypeError, AttributeError) as exc:
                        item_id = item.get("id") if isinstance(item, dict) else "?"
                        print(
                            f"  WARNING: skipping malformed item on page {page} "
                            f"(id={item_id}): {type(exc).__name__}: {exc}"
                        )
                        continue
                    if parsed:
                        raw_parts.append(parsed)
                fetched[page] = raw_parts

        # Process pages in batches: fetch concurrently, then write serially
        pages = compute_pages_to_process(completed, total_pages)
        print(f"Pages to process: {len(pages)} (skipping {len(completed)} already done)")

        BATCH = CONCURRENCY * 5
        for i in range(0, len(pages), BATCH):
            batch = pages[i : i + BATCH]
            fetched.clear()
            await asyncio.gather(*[fetch_one(p) for p in batch])
            # Write fetched pages serially
            for page in batch:
                if page not in fetched:
                    continue
                async with db_lock:
                    await process_page(conn, fetched[page], stats, brand_id_cache)
                    completed.add(page)
                    progress["completed_pages"] = list(completed)
                    progress["updated"] = stats["updated"]
                    progress["inserted"] = stats["inserted"]
                    progress["not_found"] = stats["not_found"]
                    if page % 50 == 0 or page == total_pages:
                        save_progress(progress)
                        print(
                            f"  Page {page}/{total_pages} | updated={stats['updated']} "
                            f"inserted={stats['inserted']} not_found={stats['not_found']}"
                        )

        # This point is only reached when `pages` was non-empty, i.e. real
        # new work happened this run — a no-op (already-complete) run never
        # gets here at all (see the is_import_complete() check above).
        print("\nNormalizing base_price for newly priced parts...")
        n, normalize_complete = await normalize_base_price_batched(conn)
        if normalize_complete:
            print(f"base_price normalized for {n} parts")
        else:
            print(
                f"base_price normalization INCOMPLETE — {n} parts normalized before a "
                f"batch failed/timed out. Page processing above is unaffected and already "
                f"checkpointed; normalization will resume from wherever it left off next run."
            )

        print("\nQueuing pipeline todos...")
        await queue_pipeline_todos_if_needed(conn, stats)

        save_progress(progress)
    finally:
        await conn.close()

    print(f"\n=== Done ===")
    print(f"Updated (importer_price_ils): {stats['updated']}")
    print(f"Inserted (new Renault parts): {stats['inserted']}")
    print(f"Not found:                    {stats['not_found']}")


async def main():
    print("=== Freesbe Importer ===")
    print(f"API: {API_BASE}")

    redis = await get_redis()
    lock = await acquire_lock(redis, LOCK_NAME, ttl_seconds=LOCK_TTL_SECONDS)
    if not lock:
        print("Another Freesbe importer instance is already running — exiting.")
        return
    try:
        await _run_import()
    finally:
        await lock.release()


if __name__ == "__main__":
    asyncio.run(main())
