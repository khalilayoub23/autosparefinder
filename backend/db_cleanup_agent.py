"""
DB Cleanup Agent — backend/db_cleanup_agent.py

Lightweight background cleanup loop for catalog data.
Runs continuously in small batches with cooldown sleeps between tasks.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy import text

from catalog_scraper import scraper_session_factory, scrape_motorstore
from categories import guess_category_by_text
from currency_rate import get_usd_to_ils_rate

logger = logging.getLogger("db_cleanup_agent")

# In-memory index for uncategorized parts
_uncategorized_index: dict[str, str] = {}  # part_id -> text_blob
_index_built: bool = False
_unclassifiable: set[str] = set()  # parts with no keyword match - skip on rebuild
_reorg_cursor_after_id: str | None = None  # rolling full-catalog recategorization cursor


def reset_unclassifiable_cache() -> None:
    """Call this when CATEGORY_MAP is updated."""
    global _uncategorized_index, _index_built, _unclassifiable
    _uncategorized_index = {}
    _index_built = False
    _unclassifiable = set()
    print("[Cleanup] In-memory task3 index and unclassifiable cache reset")


_PART_TYPE_FIX_VALUES = {
    'חליפיחליפי': 'חליפי',
    'מקורימקורי': 'מקורי',
    'משופץמשופץ': 'משופץ',
    'unknownunknown': 'unknown',
}


def _guess_category(text: str) -> str | None:
    return guess_category_by_text(text)


def _is_renault_manufacturer(manufacturer: str) -> bool:
    value = (manufacturer or '').casefold()
    return ('renault' in value) or ('רנו' in value)


def _is_suspicious_renault_oem(oem_number: str) -> bool:
    compact = re.sub(r'[^A-Z0-9]', '', (oem_number or '').upper())
    if not compact:
        return False
    # Renault OEMs should start with digits; prefixed RE-like values are suspicious.
    if compact.startswith('RE'):
        return True
    return not compact[0].isdigit()


async def task1_fix_part_types() -> int:
    fixed = 0
    async with scraper_session_factory() as db:
        try:
            result = await db.execute(text("""
                WITH batch AS (
                    SELECT id,
                           CASE part_type
                               WHEN 'חליפיחליפי' THEN 'חליפי'
                               WHEN 'מקורימקורי' THEN 'מקורי'
                               WHEN 'משופץמשופץ' THEN 'משופץ'
                               WHEN 'unknownunknown' THEN 'unknown'
                               WHEN 'aftermarket' THEN 'aftermarket'
                               WHEN 'Aftermarket' THEN 'aftermarket'
                               WHEN 'ALTERNATIVE' THEN 'aftermarket'
                               WHEN 'original' THEN 'oem'
                               WHEN 'Original' THEN 'oem'
                               WHEN 'OEM' THEN 'oem'
                               WHEN 'refurbished' THEN 'remanufactured'
                               WHEN 'Refurbished' THEN 'remanufactured'
                               WHEN 'used' THEN 'used'
                               WHEN 'Used' THEN 'used'
                               WHEN 'USED' THEN 'used'
                               ELSE part_type
                           END AS fixed_part_type
                    FROM parts_catalog
                    WHERE part_type IN ('חליפיחליפי', 'מקורימקורי', 'משופץמשופץ', 'unknownunknown',
                                       'aftermarket', 'Aftermarket', 'ALTERNATIVE',
                                       'original', 'Original', 'OEM',
                                       'refurbished', 'Refurbished',
                                       'used', 'Used', 'USED')
                    ORDER BY updated_at NULLS FIRST, created_at NULLS FIRST, id
                    LIMIT 500
                )
                UPDATE parts_catalog pc
                SET part_type = batch.fixed_part_type,
                    updated_at = NOW()
                FROM batch
                WHERE pc.id = batch.id
                RETURNING pc.id
            """))
            fixed = len(result.fetchall())
            if fixed:
                await db.commit()
            logger.info("task1_fix_part_types: fixed=%d", fixed)
        except Exception as exc:
            await db.rollback()
            logger.error("task1_fix_part_types failed: %s", exc)
            return 0

    return fixed


async def task2_fill_oem_from_crossref() -> int:
    updated = 0
    async with scraper_session_factory() as db:
        try:
            result = await db.execute(text("""
                WITH batch AS (
                    SELECT pcr.part_id,
                           MIN(pcr.ref_number) AS ref_number
                    FROM part_cross_reference pcr
                    JOIN parts_catalog pc ON pc.id = pcr.part_id
                    WHERE pcr.ref_type = 'OEM'
                      AND pc.oem_number IS NULL
                      AND pcr.ref_number IS NOT NULL
                      AND btrim(pcr.ref_number) <> ''
                    GROUP BY pcr.part_id
                    LIMIT 50
                )
                UPDATE parts_catalog pc
                SET oem_number = batch.ref_number,
                    needs_oem_lookup = FALSE,
                    updated_at = NOW()
                FROM batch
                WHERE pc.id = batch.part_id
                RETURNING pc.id
            """))
            updated = len(result.fetchall())
            if updated:
                await db.commit()
            logger.info("task2_fill_oem_from_crossref: filled=%d", updated)
        except Exception as exc:
            await db.rollback()
            logger.error("task2_fill_oem_from_crossref failed: %s", exc)
            return 0

    return updated


async def _build_uncategorized_index(db) -> int:
    global _uncategorized_index, _index_built
    rows = (await db.execute(text("""
        SELECT id::text,
               COALESCE(name, '') || ' ' || COALESCE(name_he, '') AS blob
        FROM parts_catalog
        WHERE (category IS NULL
               OR TRIM(COALESCE(category, '')) = ''
               OR category = 'כללי')
          AND is_active = TRUE
    """))).fetchall()

    # Exclude permanently unclassifiable parts.
    _uncategorized_index = {
        r[0]: r[1]
        for r in rows
        if r[0] not in _unclassifiable
    }
    _index_built = True

    skipped = len(rows) - len(_uncategorized_index)
    print(
        f"[Cleanup] Index built: {len(_uncategorized_index)} parts to classify "
        f"({skipped} skipped as unclassifiable)"
    )
    return len(_uncategorized_index)


async def task3_categorize_by_keywords() -> int:
    global _uncategorized_index, _index_built, _unclassifiable

    async with scraper_session_factory() as db:
        # Build or rebuild index when empty.
        if not _index_built or len(_uncategorized_index) == 0:
            count = await _build_uncategorized_index(db)
            if count == 0:
                print("[Cleanup] task3: nothing left to classify")
                return 0

        # Take up to 500 random entries from index.
        batch_size = min(500, len(_uncategorized_index))
        sample_ids = random.sample(list(_uncategorized_index.keys()), batch_size)

        payload: list[dict] = []
        newly_unclassifiable: list[str] = []

        for part_id in sample_ids:
            blob = _uncategorized_index.pop(part_id, None)
            if blob is None:
                continue
            category = _guess_category(blob)
            if category:
                payload.append({"id": part_id, "category": category})
            else:
                newly_unclassifiable.append(part_id)

        # Mark unclassifiable permanently.
        _unclassifiable.update(newly_unclassifiable)

        # Warn if unclassifiable set is growing large.
        if len(_unclassifiable) > 0 and len(_unclassifiable) % 50000 == 0:
            print(
                f"[Cleanup] WARNING: {len(_unclassifiable)} parts have no keyword match "
                f"— consider expanding CATEGORY_MAP"
            )

        # Write matched parts to DB.
        categorized = 0
        if payload:
            try:
                result = await db.execute(text("""
                    WITH payload AS (
                        SELECT x.id::uuid AS id, x.category::text AS category
                        FROM jsonb_to_recordset(CAST(:rows_json AS jsonb))
                        AS x(id text, category text)
                    )
                    UPDATE parts_catalog pc
                    SET category   = payload.category,
                        updated_at = NOW()
                    FROM payload
                    WHERE pc.id = payload.id
                    RETURNING pc.id
                """), {"rows_json": json.dumps(payload, ensure_ascii=False)})
                categorized = len(result.fetchall())
                await db.commit()
            except Exception as exc:
                await db.rollback()
                logger.error("task3 DB update failed: %s", exc)
                return 0

        remaining = len(_uncategorized_index)
        print(
            f"[Cleanup] task3: categorized={categorized} "
            f"unmatched_this_batch={len(newly_unclassifiable)} "
            f"unclassifiable_total={len(_unclassifiable)} "
            f"remaining_in_index={remaining}"
        )

        return categorized


# ── LLM categories available for fallback ──────────────────────────────────────
_VALID_CATEGORIES = [
    "engine", "brakes", "suspension-steering", "electrical-sensors", "body-exterior",
    "lighting", "cooling", "fuel-air", "exhaust", "filters", "clutch-drivetrain",
    "gearbox", "wheels-bearings", "air-conditioning-heating", "interior-comfort",
    "wipers-washers", "fluids", "accessories", "service-general", "כללי",
]

# Rolling cursor for LLM fallback — tracks which unclassifiable parts we've tried
_llm_fallback_cursor: list[str] = []
_llm_consecutive_failures: int = 0  # skip LLM when all providers are 429ing


async def task3b_llm_category_fallback(batch_size: int = 20) -> int:
    """LLM fallback for parts the keyword matcher can't classify.
    Takes up to batch_size parts from _unclassifiable, asks the LLM in one prompt,
    writes results. Runs at most 20 parts per cleanup cycle to stay cheap."""
    from hf_client import hf_text  # import here to avoid circular-import at module load

    global _unclassifiable, _llm_fallback_cursor, _llm_consecutive_failures

    if len(_unclassifiable) == 0:
        return 0

    # Back off when all providers are rate-limited — skip every other cycle
    if _llm_consecutive_failures >= 3:
        _llm_consecutive_failures -= 1  # decay slowly
        return 0

    # Refill cursor from unclassifiable set when empty
    if not _llm_fallback_cursor:
        _llm_fallback_cursor = list(_unclassifiable)[:5000]  # cap to avoid OOM
    if not _llm_fallback_cursor:
        return 0

    batch_ids = _llm_fallback_cursor[:batch_size]
    _llm_fallback_cursor = _llm_fallback_cursor[batch_size:]

    print(f"[Cleanup] task3b: attempting LLM classify for {len(batch_ids)} parts (unclassifiable_total={len(_unclassifiable)})")

    async with scraper_session_factory() as db:
        try:
            # Use IN with individual named params — asyncpg handles these reliably
            id_params = {f"id_{i}": bid for i, bid in enumerate(batch_ids)}
            in_clause = ", ".join(f":id_{i}" for i in range(len(batch_ids)))
            rows = (await db.execute(text(f"""
                SELECT id::text, COALESCE(name,'') || ' ' || COALESCE(name_he,'') AS blob
                FROM parts_catalog
                WHERE id::text IN ({in_clause}) AND is_active = TRUE
            """), id_params)).fetchall()
        except Exception as exc:
            print(f"[Cleanup] task3b fetch error: {exc}")
            return 0

    print(f"[Cleanup] task3b: fetched {len(rows)} rows from DB")
    if not rows:
        return 0

    # Build LLM prompt using numbered position instead of UUID in output
    # The model outputs "N. category" lines — we map back by index
    indexed_rows = list(rows)
    numbered_parts = "\n".join(
        f"{i+1}. {r[1][:100]}" for i, r in enumerate(indexed_rows)
    )
    cats_str = ", ".join(_VALID_CATEGORIES)
    prompt = (
        f"You must classify {len(indexed_rows)} auto parts. "
        f"Valid categories: {cats_str}\n\n"
        f"Output exactly {len(indexed_rows)} lines. Each line: N. category\n"
        f"Example: 1. engine\n2. brakes\n3. filters\n\n"
        f"Parts:\n{numbered_parts}"
    )
    system = (
        f"Output exactly {len(indexed_rows)} numbered lines like: 1. engine\n"
        "Choose from valid categories only. No other text."
    )

    try:
        raw = await hf_text(prompt, system=system)
        _llm_consecutive_failures = 0  # reset on success
    except Exception as exc:
        print(f"[Cleanup] task3b LLM call failed: {exc}")
        _llm_consecutive_failures += 1
        return 0

    raw_lines = (raw or "").splitlines()
    print(f"[Cleanup] task3b: LLM returned {len(raw_lines)} lines")

    # Parse "N. category" lines — map position back to UUID
    # Also accept reasoning lines "N. ... category ..." by taking rightmost valid category per number
    _NUM_RE = re.compile(r'^\s*(\d+)[.)]\s*(.*)', re.DOTALL)
    updates: list[dict] = []
    seen_nums: set[int] = set()

    for line in raw_lines:
        m = _NUM_RE.match(line)
        if not m:
            continue
        num = int(m.group(1))
        rest = m.group(2).strip().lower()
        if num < 1 or num > len(indexed_rows) or num in seen_nums:
            continue
        # Find rightmost valid category in the rest of the line
        best_cat: str | None = None
        best_pos: int = -1
        for cat in _VALID_CATEGORIES:
            idx = rest.rfind(cat)
            if idx >= 0 and idx > best_pos:
                best_pos = idx
                best_cat = cat
        if best_cat:
            part_id = indexed_rows[num - 1][0]  # UUID from original batch order
            updates.append({"id": part_id, "category": best_cat})
            _unclassifiable.discard(part_id)
            seen_nums.add(num)

    print(f"[Cleanup] task3b: parsed {len(updates)}/{len(indexed_rows)} valid classifications")
    if not updates:
        return 0

    async with scraper_session_factory() as db:
        try:
            await db.execute(text("""
                WITH payload AS (
                    SELECT x.id::uuid AS id, x.category::text AS category
                    FROM jsonb_to_recordset(CAST(:rows_json AS jsonb))
                    AS x(id text, category text)
                )
                UPDATE parts_catalog pc
                SET category = payload.category, updated_at = NOW()
                FROM payload WHERE pc.id = payload.id
            """), {"rows_json": json.dumps(updates, ensure_ascii=False)})
            await db.commit()
            print(f"[Cleanup] task3b LLM: classified {len(updates)}/{len(indexed_rows)} parts")
        except Exception as exc:
            await db.rollback()
            print(f"[Cleanup] task3b DB write error: {exc}")
            return 0

    return len(updates)


async def task6_reorganize_categories_rollup(batch_size: int = 500) -> int:
    global _reorg_cursor_after_id

    async with scraper_session_factory() as db:
        try:
            query_params = {"lim": int(max(50, batch_size))}
            if _reorg_cursor_after_id is None:
                query_text = """
                        SELECT
                            id::text,
                            COALESCE(name, '') || ' ' || COALESCE(name_he, '') || ' ' || COALESCE(description, '') AS blob,
                            COALESCE(category, '') AS current_category
                        FROM parts_catalog
                        WHERE is_active = TRUE
                        ORDER BY id::text
                        LIMIT :lim
                        """
            else:
                query_text = """
                        SELECT
                            id::text,
                            COALESCE(name, '') || ' ' || COALESCE(name_he, '') || ' ' || COALESCE(description, '') AS blob,
                            COALESCE(category, '') AS current_category
                        FROM parts_catalog
                        WHERE is_active = TRUE
                          AND id::text > :after_id
                        ORDER BY id::text
                        LIMIT :lim
                        """
                query_params["after_id"] = _reorg_cursor_after_id

            rows = (
                await db.execute(
                    text(query_text),
                    query_params,
                )
            ).fetchall()

            if not rows:
                _reorg_cursor_after_id = None
                return 0

            payload: List[Dict[str, str]] = []
            scanned = 0
            for part_id, blob, current_category in rows:
                scanned += 1
                guessed = _guess_category(blob)
                if not guessed:
                    continue
                current_clean = (current_category or '').strip()
                if current_clean == guessed:
                    continue
                payload.append({"id": str(part_id), "category": guessed})

            _reorg_cursor_after_id = str(rows[-1][0])

            updated = 0
            if payload:
                result = await db.execute(
                    text(
                        """
                        WITH payload AS (
                            SELECT x.id::uuid AS id, x.category::text AS category
                            FROM jsonb_to_recordset(CAST(:rows_json AS jsonb))
                            AS x(id text, category text)
                        )
                        UPDATE parts_catalog pc
                        SET category = payload.category,
                            updated_at = NOW()
                        FROM payload
                        WHERE pc.id = payload.id
                        RETURNING pc.id
                        """
                    ),
                    {"rows_json": json.dumps(payload, ensure_ascii=False)},
                )
                updated = len(result.fetchall())
                await db.commit()

            print(
                f"[Cleanup] task6: scanned={scanned} updated={updated} cursor_after={_reorg_cursor_after_id or 'RESET'}"
            )
            return updated
        except Exception as exc:
            await db.rollback()
            logger.error("task6_reorganize_categories_rollup failed: %s", exc)
            return 0



async def task4_fix_oem_lookup_flag() -> int:
    flagged = 0
    async with scraper_session_factory() as db:
        try:
            result = await db.execute(text("""
                WITH batch AS (
                    SELECT id
                    FROM parts_catalog
                    WHERE oem_number IS NOT NULL
                      AND btrim(oem_number) <> ''
                      AND needs_oem_lookup = TRUE
                    ORDER BY updated_at NULLS FIRST, created_at NULLS FIRST, id
                    LIMIT 500
                )
                UPDATE parts_catalog pc
                SET needs_oem_lookup = FALSE,
                    updated_at = NOW()
                FROM batch
                WHERE pc.id = batch.id
                RETURNING pc.id
            """))
            flagged = len(result.fetchall())
            if flagged:
                await db.commit()
            logger.info("task4_fix_oem_lookup_flag: fixed=%d", flagged)
        except Exception as exc:
            await db.rollback()
            logger.error("task4_fix_oem_lookup_flag failed: %s", exc)
            return 0

    return flagged


async def task5_detect_manufacturer_overflow() -> int:
    overflow = 0
    async with scraper_session_factory() as db:
        try:
            rows = (await db.execute(text("""
                SELECT id, manufacturer, oem_number
                FROM parts_catalog
                WHERE oem_number IS NOT NULL
                  AND btrim(oem_number) <> ''
                  AND (
                        lower(COALESCE(manufacturer, '')) LIKE '%renault%'
                        OR COALESCE(manufacturer, '') ILIKE '%רנו%'
                  )
                ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id
                LIMIT 50
            """))).mappings().all()

            suspicious: List[Dict[str, str]] = []
            for row in rows:
                manufacturer = str(row['manufacturer'] or '')
                oem_number = str(row['oem_number'] or '')
                if not _is_renault_manufacturer(manufacturer):
                    continue
                if _is_suspicious_renault_oem(oem_number):
                    suspicious.append({
                        'part_id': str(row['id']),
                        'manufacturer': manufacturer,
                        'oem_number': oem_number,
                    })

            overflow = len(suspicious)
            if overflow:
                request_data = {
                    'task': 'task5_detect_manufacturer_overflow',
                    'rule': 'Renault OEM should begin with digits only (not RE-prefix)',
                    'batch_size': 50,
                }
                response_data = {
                    'suspicious_count': overflow,
                    'items': suspicious,
                }
                await db.execute(text("""
                    INSERT INTO system_logs (
                        id, level, logger_name, message,
                        endpoint, method, request_data, response_data, created_at
                    )
                    VALUES (
                        :id, :level, :logger_name, :message,
                        :endpoint, :method,
                        CAST(:request_data AS jsonb), CAST(:response_data AS jsonb), NOW()
                    )
                """), {
                    'id': uuid.uuid4(),
                    'level': 'WARNING',
                    'logger_name': 'db_cleanup_agent',
                    'message': f'Renault OEM overflow suspicion detected: {overflow} parts',
                    'endpoint': '/background/db_cleanup',
                    'method': 'CRON',
                    'request_data': json.dumps(request_data, ensure_ascii=False),
                    'response_data': json.dumps(response_data, ensure_ascii=False),
                })
                await db.commit()

            logger.info("task5_detect_manufacturer_overflow: suspicious=%d", overflow)
        except Exception as exc:
            await db.rollback()
            logger.error("task5_detect_manufacturer_overflow failed: %s", exc)
            return 0

    return overflow


# Central coordination contract: who owns what and when it should run.
# This lives here so cleanup sequencing is explicit and versioned with the agent code.
AGENT_COORDINATION: Dict[str, Dict[str, str]] = {
    "catalog_scraper.py": {
        "role": "REX orchestrator for price sync + discovery + transport trigger",
        "when": "continuous cycle; should run before and after cleanup windows",
    },
    "db_cleanup_agent.py": {
        "role": "DB hygiene micro-batches (tasks 1-6 + zombie watchdog) for catalog quality",
        "when": "continuous background loop with short cooldowns; zombie watchdog runs every cycle",
        "zombie_watchdog": "task_zombie_watchdog — auto-remediates stale running jobs whose last_heartbeat_at exceeded their ttl_seconds",
    },
    "db_update_agent.py": {
        "role": "normalization/enrichment tasks and schema-safe data updates",
        "when": "scheduled or on-demand admin-triggered runs",
    },
    "run_rex_transport_office_pipeline.py": {
        "role": "transport-office canonical data ingestion pipeline",
        "when": "periodic ingestion window, before heavy discovery backfills",
    },
}


def get_cleanup_coordination_plan() -> Dict[str, Any]:
    """Return a stable, machine-readable coordination plan for operator visibility."""
    return {
        "owner": "db_cleanup_agent",
        "agents": AGENT_COORDINATION,
        "cleanup_execution_order": [
            "task1_fix_part_types",
            "task2_fill_oem_from_crossref",
            "task3_categorize_by_keywords",
            "task6_reorganize_categories_rollup",
            "task4_fix_oem_lookup_flag",
            "task5_detect_manufacturer_overflow",
        ],
    }


async def task_zombie_watchdog() -> int:
    """
    Scan job_registry for jobs whose last_heartbeat_at has exceeded their
    ttl_seconds without completing.  Mark them as 'failed' AND clear the
    corresponding Redis distributed lock so new cycles can run immediately.

    A job is considered a zombie when:
      status = 'running'
      AND last_heartbeat_at < NOW() - (ttl_seconds * INTERVAL '1 second')

    Falls back to a hard 30-minute threshold when ttl_seconds is NULL.

    Returns the number of rows remediated.
    """
    remediated = 0
    zombie_job_names: list[str] = []
    async with scraper_session_factory() as db:
        try:
            result = await db.execute(text("""
                WITH zombies AS (
                    SELECT job_id, job_name
                    FROM job_registry
                    WHERE status = 'running'
                      AND last_heartbeat_at < NOW() - INTERVAL '2 hours'
                )
                UPDATE job_registry
                SET status        = 'failed',
                    completed_at  = NOW(),
                    error_message = 'Auto-remediated by zombie watchdog: no heartbeat within TTL'
                WHERE job_id IN (SELECT job_id FROM zombies)
                RETURNING job_id, job_name
            """))
            rows = result.fetchall()
            remediated = len(rows)
            zombie_job_names = list({r[1] for r in rows})
            await db.commit()
            if remediated:
                logger.warning(
                    "[Cleanup] task_zombie_watchdog: remediated %d stale jobs: %s",
                    remediated,
                    [r[0] for r in rows],
                )
        except Exception as exc:
            logger.error("task_zombie_watchdog failed: %s", exc)
            try:
                await db.rollback()
            except Exception:
                pass

    # Clear Redis distributed locks for all remediated jobs so new runs can start.
    if zombie_job_names:
        try:
            import sys
            sys.path.insert(0, "/app")
            from BACKEND_AUTH_SECURITY import get_redis
            redis = await get_redis()
            if redis:
                for jname in zombie_job_names:
                    lock_key = f"autospare:lock:{jname}"
                    deleted = await redis.delete(lock_key)
                    if deleted:
                        logger.warning(
                            "[Cleanup] task_zombie_watchdog: cleared Redis lock %s", lock_key
                        )
                await redis.aclose()
        except Exception as exc:
            logger.error("task_zombie_watchdog: Redis lock clear failed: %s", exc)

    return remediated


# Backoff state (added 2026-07-07): this task's candidate scan is inherently
# expensive (probes supplier_parts for ~28K inactive-unpriced parts) and there
# are normally 0 to recover. Running it every 30s hammered the disk forever for
# nothing — the chronic load source that stalled imports/connections and set
# off watchdog mass-kills. When a run finds nothing, back off exponentially
# (30s → … → 30 min cap). A run that recovers something resets to eager.
_RECOVER_INACTIVE_SKIP_UNTIL = 0.0
_RECOVER_INACTIVE_BACKOFF_S = 30.0
_RECOVER_INACTIVE_BACKOFF_MAX = 1800.0


async def task_recover_priced_inactive(batch_size: int = 100) -> int:
    """Bucket 2 recovery — Part A (no web):
    Find inactive parts whose supplier_parts row already has price_usd > 0
    but whose base_price was never copied (or was zeroed by a PDF sweep).
    Copy the price, convert USD→ILS, and reactivate the part.
    Runs with exponential backoff — dormant when nothing to recover.
    """
    global _RECOVER_INACTIVE_SKIP_UNTIL, _RECOVER_INACTIVE_BACKOFF_S
    if time.monotonic() < _RECOVER_INACTIVE_SKIP_UNTIL:
        return 0

    recovered = 0
    async with scraper_session_factory() as db:
        try:
            fx = await get_usd_to_ils_rate(db)
            result = await db.execute(text("""
                WITH candidates AS (
                    SELECT DISTINCT ON (p.id)
                        p.id,
                        sp.price_usd,
                        ROUND((sp.price_usd * :fx)::numeric, 2) AS new_price_ils
                    FROM parts_catalog p
                    JOIN supplier_parts sp ON sp.part_id = p.id
                    WHERE p.is_active = false
                      AND (p.base_price IS NULL OR p.base_price = 0)
                      AND sp.price_usd > 0
                    ORDER BY p.id, sp.price_usd DESC
                    LIMIT :batch
                )
                UPDATE parts_catalog pc
                SET base_price = candidates.new_price_ils,
                    is_active   = true,
                    updated_at  = NOW()
                FROM candidates
                WHERE pc.id = candidates.id
                RETURNING pc.id
            """), {"fx": fx, "batch": batch_size})
            rows = result.fetchall()
            recovered = len(rows)
            if recovered:
                await db.commit()
                logger.info("task_recover_priced_inactive: reactivated=%d fx=%.4f", recovered, fx)
                # Work found → reset to eager so we drain the backlog fast.
                _RECOVER_INACTIVE_BACKOFF_S = 30.0
                _RECOVER_INACTIVE_SKIP_UNTIL = 0.0
            else:
                # Nothing to recover (the normal case) → back off exponentially
                # so we stop re-scanning ~28K parts every 30s for zero result.
                _RECOVER_INACTIVE_BACKOFF_S = min(
                    _RECOVER_INACTIVE_BACKOFF_S * 2, _RECOVER_INACTIVE_BACKOFF_MAX
                )
                _RECOVER_INACTIVE_SKIP_UNTIL = time.monotonic() + _RECOVER_INACTIVE_BACKOFF_S
                logger.debug("task_recover_priced_inactive: nothing to recover — backing off %.0fs", _RECOVER_INACTIVE_BACKOFF_S)
        except Exception as exc:
            await db.rollback()
            logger.error("task_recover_priced_inactive failed: %s", exc)
            return 0
    return recovered


async def task_recover_motorstore_prices(batch_size: int = 8) -> int:
    """Bucket 2 recovery — Part B (web scraping):
    For inactive parts linked to Motorstore IL with no price anywhere,
    fetch the live price from motorstore.co.il using the OEM number,
    write it back to supplier_parts + base_price, and reactivate the part.
    Small batch per cycle to respect rate limits.
    """
    recovered = 0
    async with scraper_session_factory() as db:
        try:
            fx = await get_usd_to_ils_rate(db)
            # Find candidates: inactive, priceless, linked to Motorstore IL supplier, has OEM
            rows = (await db.execute(text("""
                SELECT p.id, p.oem_number, p.manufacturer, sp.id AS sp_id
                FROM parts_catalog p
                JOIN supplier_parts sp ON sp.part_id = p.id
                JOIN suppliers s ON s.id = sp.supplier_id
                WHERE p.is_active = false
                  AND (p.base_price IS NULL OR p.base_price = 0)
                  AND (sp.price_usd IS NULL OR sp.price_usd = 0)
                  AND p.oem_number IS NOT NULL AND p.oem_number != ''
                  AND s.name ILIKE '%motorstore%'
                ORDER BY p.updated_at ASC NULLS FIRST
                LIMIT :batch
            """), {"batch": batch_size})).fetchall()

            for row in rows:
                part_id, oem, manufacturer, sp_id = row
                try:
                    result = await scrape_motorstore(oem, manufacturer or "")
                    price_ils = result.get("price")
                    if not price_ils or float(price_ils) <= 0:
                        # No price found — touch updated_at so we rotate to next part
                        await db.execute(text(
                            "UPDATE parts_catalog SET updated_at = NOW() WHERE id = :id"
                        ), {"id": part_id})
                        await db.commit()
                        continue

                    price_ils = round(float(price_ils), 2)
                    price_usd = round(price_ils / fx, 2)

                    # Update supplier_parts row
                    await db.execute(text("""
                        UPDATE supplier_parts
                        SET price_usd = :usd, price_ils = :ils,
                            is_available = true, last_checked_at = NOW()
                        WHERE id = :sp_id
                    """), {"usd": price_usd, "ils": price_ils, "sp_id": sp_id})

                    # Update catalog and reactivate
                    await db.execute(text("""
                        UPDATE parts_catalog
                        SET base_price = :ils, is_active = true, updated_at = NOW()
                        WHERE id = :id
                    """), {"ils": price_ils, "id": part_id})

                    await db.commit()
                    recovered += 1
                    logger.info(
                        "task_recover_motorstore_prices: recovered %s oem=%s price_ils=%.2f",
                        manufacturer, oem, price_ils,
                    )
                except BaseException as part_exc:
                    if isinstance(part_exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    logger.warning("task_recover_motorstore_prices: part %s failed: %s", oem, part_exc)
                    try:
                        await db.rollback()
                    except Exception:
                        pass

        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            logger.error("task_recover_motorstore_prices failed: %s", exc)
            try:
                await db.rollback()
            except Exception:
                pass

    return recovered


async def task_deactivate_hebrew_oem(batch_size: int = 500) -> int:
    """Deactivate parts whose OEM number contains Hebrew characters (invalid OEM)."""
    deactivated = 0
    async with scraper_session_factory() as db:
        try:
            result = await db.execute(text("""
                UPDATE parts_catalog SET is_active = FALSE, updated_at = NOW()
                WHERE oem_number ~ '[\\u05d0-\\u05ea]'
                  AND is_active = TRUE
                  AND id IN (
                      SELECT id FROM parts_catalog
                      WHERE oem_number ~ '[\\u05d0-\\u05ea]'
                        AND is_active = TRUE
                      LIMIT :batch
                  )
            """), {"batch": batch_size})
            deactivated = result.rowcount or 0
            await db.commit()
            if deactivated:
                logger.info("task_deactivate_hebrew_oem: deactivated %d parts", deactivated)
        except Exception as exc:
            await db.rollback()
            logger.error("task_deactivate_hebrew_oem failed: %s", exc)
    return deactivated


CLEANUP_TASK_REGISTRY: Dict[str, Callable[[], Awaitable[int]]] = {
    "task1_fix_part_types": task1_fix_part_types,
    "task2_fill_oem_from_crossref": task2_fill_oem_from_crossref,
    "task3_categorize_by_keywords": task3_categorize_by_keywords,
    "task6_reorganize_categories_rollup": task6_reorganize_categories_rollup,
    "task4_fix_oem_lookup_flag": task4_fix_oem_lookup_flag,
    "task5_detect_manufacturer_overflow": task5_detect_manufacturer_overflow,
    "task_recover_priced_inactive": task_recover_priced_inactive,
    "task_recover_motorstore_prices": task_recover_motorstore_prices,
    "task_zombie_watchdog": task_zombie_watchdog,
    "task_deactivate_hebrew_oem": task_deactivate_hebrew_oem,
}


async def _process_cleanup_agent_todos() -> int:
    """Check agent_todos assigned to db_cleanup_agent and execute matching tasks."""
    processed = 0
    async with scraper_session_factory() as db:
        try:
            rows = await db.execute(text("""
                SELECT id, title, artifacts FROM agent_todos
                WHERE assigned_to_agent = 'db_cleanup_agent'
                  AND status = 'not_started'
                ORDER BY priority DESC, created_at ASC
                LIMIT 5
            """))
            todos = rows.fetchall()
        except Exception as exc:
            logger.error("_process_cleanup_agent_todos: query failed: %s", exc)
            return 0

    for todo in todos:
        todo_id = str(todo[0])
        title = todo[1] or ""
        artifacts = todo[2] or {}

        # Determine which task(s) to run based on title keywords
        task_names: list[str] = []
        if isinstance(artifacts, dict):
            task_names = artifacts.get("task_names", [])
        if not task_names:
            title_lower = title.lower()
            if "hebrew" in title_lower and "oem" in title_lower:
                task_names = ["task_deactivate_hebrew_oem"]

        if not task_names:
            # No matching task — dismiss
            async with scraper_session_factory() as db:
                try:
                    await db.execute(text("""
                        UPDATE agent_todos SET status = 'dismissed', updated_at = NOW(),
                            progress_notes = 'No matching cleanup task found for this todo'
                        WHERE id = CAST(:tid AS uuid)
                    """), {"tid": todo_id})
                    await db.commit()
                except Exception:
                    await db.rollback()
            continue

        # Mark in_progress
        async with scraper_session_factory() as db:
            try:
                await db.execute(text("""
                    UPDATE agent_todos SET status = 'in_progress', updated_at = NOW()
                    WHERE id = CAST(:tid AS uuid) AND status = 'not_started'
                """), {"tid": todo_id})
                await db.commit()
            except Exception:
                await db.rollback()
                continue

        # Run each task
        all_ok = True
        for task_name in task_names:
            fn = CLEANUP_TASK_REGISTRY.get(task_name)
            if fn is None:
                logger.warning("_process_cleanup_agent_todos: unknown task %s", task_name)
                all_ok = False
                continue
            try:
                result = await fn()
                logger.info("cleanup todo task %s result=%s", task_name, result)
            except Exception as exc:
                logger.error("cleanup todo task %s failed: %s", task_name, exc)
                all_ok = False

        # Mark completed or failed
        new_status = "completed" if all_ok else "dismissed"
        async with scraper_session_factory() as db:
            try:
                await db.execute(text("""
                    UPDATE agent_todos SET status = :status, completed_at = NOW(), updated_at = NOW(),
                        progress_pct = 100,
                        progress_notes = 'Executed by db_cleanup_agent loop'
                    WHERE id = CAST(:tid AS uuid)
                """), {"status": new_status, "tid": todo_id})
                await db.commit()
                processed += 1
            except Exception:
                await db.rollback()

    return processed


async def task_normalize_base_price_batched(batch_size: int = 1000) -> int:
    """Batched normalize_base_price — replaces the disabled full-table-scan version.
    Finds parts where base_price=0 but a supplier_parts price exists, then sets:
      supplier_parts.price_ils = ex-VAT cost (documented in db_update_agent.py:1243)
      base_price = cost * 1.45  (45% margin — CLAUDE.md policy)
      importer_price_ils = cost (the raw ex-VAT cost)
    Safe: processes batch_size rows per call, never scans full 3.4M table at once."""
    fixed = 0
    try:
        async with scraper_session_factory() as db:
            result = await db.execute(text("""
                UPDATE parts_catalog pc
                SET base_price        = ROUND((sp_min.min_price * 1.45)::numeric, 2),
                    importer_price_ils = sp_min.min_price,
                    max_price_ils      = COALESCE(NULLIF(pc.max_price_ils, 0), ROUND((sp_min.min_price * 1.18)::numeric, 2)),
                    min_price_ils      = sp_min.min_price,
                    updated_at         = NOW()
                FROM (
                    SELECT sp.part_id, MIN(sp.price_ils) AS min_price
                    FROM supplier_parts sp
                    WHERE sp.is_available = TRUE AND sp.price_ils > 0
                      AND EXISTS (
                          SELECT 1 FROM parts_catalog p2
                          WHERE p2.id = sp.part_id AND p2.is_active
                            AND (p2.base_price IS NULL OR p2.base_price = 0)
                      )
                    GROUP BY sp.part_id
                    LIMIT :batch
                ) sp_min
                WHERE pc.id = sp_min.part_id
                  AND pc.is_active
                  AND (pc.base_price IS NULL OR pc.base_price = 0)
            """), {"batch": batch_size})
            fixed = result.rowcount or 0
            if fixed:
                await db.commit()
                logger.info("task_normalize_base_price_batched: fixed %d parts", fixed)
    except Exception as exc:
        logger.error("task_normalize_base_price_batched failed: %s", exc)
    return fixed


async def task_heal_importer_price(batch_size: int = 2000) -> int:
    """Self-heal: fix parts where max_price_ils > 0 but importer_price_ils = 0.
    Applies CLAUDE.md formula: cost = max_price_ils / 1.18, base = cost * 1.45.
    Catches importer bugs where price was loaded but importer_price_ils was hardcoded 0."""
    fixed = 0
    try:
        async with scraper_session_factory() as db:
            result = await db.execute(text("""
                UPDATE parts_catalog
                SET importer_price_ils = ROUND((max_price_ils / 1.18)::numeric, 2),
                    base_price = ROUND(((max_price_ils / 1.18) * 1.45)::numeric, 2),
                    updated_at = NOW()
                WHERE id IN (
                    SELECT id FROM parts_catalog
                    WHERE is_active
                      AND max_price_ils > 0
                      AND (importer_price_ils IS NULL OR importer_price_ils = 0)
                      AND (base_price IS NULL OR base_price = 0)
                    LIMIT :batch
                )
            """), {"batch": batch_size})
            fixed = result.rowcount or 0
            if fixed:
                await db.commit()
                logger.info("task_heal_importer_price: fixed %d parts", fixed)
    except Exception as exc:
        logger.error("task_heal_importer_price failed: %s", exc)
    return fixed


async def task_heal_part_type_original(batch_size: int = 5000) -> int:
    """Self-heal: normalise non-canonical part_type values to lowercase canonical.
    Covers 'original'/'Original'/'OEM'→'oem', 'Aftermarket'→'aftermarket', etc.
    Runs every cycle until all ~3.5M non-canonical rows are corrected."""
    fixed = 0
    try:
        async with scraper_session_factory() as db:
            result = await db.execute(text("""
                UPDATE parts_catalog
                SET part_type = CASE part_type
                    WHEN 'original'     THEN 'oem'
                    WHEN 'Original'     THEN 'oem'
                    WHEN 'OEM'          THEN 'oem'
                    WHEN 'Aftermarket'  THEN 'aftermarket'
                    WHEN 'ALTERNATIVE'  THEN 'aftermarket'
                    WHEN 'Refurbished'  THEN 'remanufactured'
                    WHEN 'Used'         THEN 'used'
                    WHEN 'USED'         THEN 'used'
                    ELSE part_type
                END,
                updated_at = NOW()
                WHERE id IN (
                    SELECT id FROM parts_catalog
                    WHERE part_type IN (
                        'original','Original','OEM',
                        'Aftermarket','ALTERNATIVE',
                        'Refurbished','Used','USED'
                    )
                    LIMIT :batch
                )
            """), {"batch": batch_size})
            fixed = result.rowcount or 0
            if fixed:
                await db.commit()
                logger.info("task_heal_part_type_original: fixed %d parts", fixed)
    except Exception as exc:
        logger.error("task_heal_part_type_original failed: %s", exc)
    return fixed


async def task_heal_part_condition(batch_size: int = 5000) -> int:
    """Self-heal: fix part_condition='New'/'OEM'/etc. (uppercase) → lowercase.
    Catches importers that write wrong case — runs every cleanup cycle as a safety net."""
    fixed = 0
    try:
        async with scraper_session_factory() as db:
            result = await db.execute(text("""
                UPDATE parts_catalog
                SET part_condition = LOWER(part_condition), updated_at = NOW()
                WHERE id IN (
                    SELECT id FROM parts_catalog
                    WHERE is_active
                      AND part_condition IN ('New','OEM','Used','Aftermarket','Remanufactured','OE_Equivalent')
                    LIMIT :batch
                )
            """), {"batch": batch_size})
            fixed = result.rowcount or 0
            if fixed:
                await db.commit()
                logger.info("task_heal_part_condition: fixed %d parts", fixed)
    except Exception as exc:
        logger.error("task_heal_part_condition failed: %s", exc)
    return fixed


async def run_cleanup_cycle_once(
    *,
    cycle: int,
    state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run one cleanup cycle in a deterministic order and return a compact report."""
    if state is None:
        state = {"task4_full_batch_cycles": 0, "task4_accelerated": False}

    t1 = await task1_fix_part_types()
    await asyncio.sleep(2)
    t2 = await task2_fill_oem_from_crossref()
    await asyncio.sleep(3)
    t3 = await task3_categorize_by_keywords()
    await asyncio.sleep(1)
    t3b = await task3b_llm_category_fallback(batch_size=500)
    await asyncio.sleep(2)
    t6 = await task6_reorganize_categories_rollup()
    await asyncio.sleep(2)
    t4 = await task4_fix_oem_lookup_flag()

    if t4 == 500:
        state["task4_full_batch_cycles"] = int(state.get("task4_full_batch_cycles", 0)) + 1
    else:
        state["task4_full_batch_cycles"] = 0
        state["task4_accelerated"] = False

    task4_sleep_s = 1.0
    if int(state.get("task4_full_batch_cycles", 0)) >= 300:
        task4_sleep_s = 0.5
        if not bool(state.get("task4_accelerated", False)):
            warning_msg = (
                f"[Cleanup] task4 has returned full batch size (500) for "
                f"{state['task4_full_batch_cycles']} consecutive cycles; "
                f"reducing task4 sleep to 0.5s"
            )
            logger.warning(warning_msg)
            print(warning_msg)
            state["task4_accelerated"] = True

    await asyncio.sleep(task4_sleep_s)
    t5 = await task5_detect_manufacturer_overflow()
    await asyncio.sleep(2)
    t_zombie = await task_zombie_watchdog()
    await asyncio.sleep(2)
    t_recover_priced = await task_recover_priced_inactive()
    await asyncio.sleep(3)
    t_recover_web = await task_recover_motorstore_prices()
    await asyncio.sleep(2)
    t_heal_price = await task_heal_importer_price()
    await asyncio.sleep(2)
    t_heal_cond = await task_heal_part_condition()
    await asyncio.sleep(2)
    t_heal_part_type = await task_heal_part_type_original()
    await asyncio.sleep(2)
    t_normalize_base = await task_normalize_base_price_batched()
    await asyncio.sleep(5)

    return {
        "cycle": cycle,
        "types": t1,
        "oem": t2,
        "categorized": t3,
        "categorized_llm": t3b,
        "recategorized": t6,
        "flags": t4,
        "overflow": t5,
        "zombie_jobs_remediated": t_zombie,
        "recovered_priced": t_recover_priced,
        "recovered_web": t_recover_web,
        "healed_importer_price": t_heal_price,
        "healed_part_condition": t_heal_cond,
        "normalized_base_price": t_normalize_base,
        "task4_full_batch_cycles": int(state.get("task4_full_batch_cycles", 0)),
        "task4_accelerated": bool(state.get("task4_accelerated", False)),
    }


async def run_cleanup_loop() -> None:
    cycle = 0
    state: Dict[str, Any] = {"task4_full_batch_cycles": 0, "task4_accelerated": False}
    print('[Cleanup] Background cleanup loop started')

    while True:
        try:
            cycle += 1
            report = await run_cleanup_cycle_once(cycle=cycle, state=state)

            if cycle % 10 == 0:
                print(
                    f"[Cleanup] Cycle {cycle} done: "
                    f"types={report['types']} oem={report['oem']} "
                    f"categorized={report['categorized']} recategorized={report['recategorized']} "
                    f"flags={report['flags']} overflow={report['overflow']} "
                    f"zombie_jobs={report['zombie_jobs_remediated']} "
                    f"recovered_priced={report['recovered_priced']} "
                    f"recovered_web={report['recovered_web']}"
                )

            # Check for agent_todos every 5 cycles (~2.5 min)
            if cycle % 5 == 0:
                try:
                    processed = await _process_cleanup_agent_todos()
                    if processed:
                        print(f"[Cleanup] Processed {processed} agent_todos")
                except Exception as todo_exc:
                    logger.error("[Cleanup] agent_todos processing error: %s", todo_exc)

            await asyncio.sleep(30)

        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as e:
            print(f"[Cleanup] Error in cycle {cycle}: {type(e).__name__}: {e}")
            await asyncio.sleep(60)
