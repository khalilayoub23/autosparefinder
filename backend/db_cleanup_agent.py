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
                               ELSE part_type
                           END AS fixed_part_type
                    FROM parts_catalog
                    WHERE part_type IN ('חליפיחליפי', 'מקורימקורי', 'משופץמשופץ', 'unknownunknown')
                    ORDER BY updated_at NULLS FIRST, created_at NULLS FIRST, id
                    LIMIT 100
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
    ttl_seconds without completing.  Mark them as 'failed' so the distributed
    lock is no longer blocked and new cycles can run.

    A job is considered a zombie when:
      status = 'running'
      AND last_heartbeat_at < NOW() - (ttl_seconds * INTERVAL '1 second')

    Falls back to a hard 30-minute threshold when ttl_seconds is NULL.

    Returns the number of rows remediated.
    """
    remediated = 0
    async with scraper_session_factory() as db:
        try:
            result = await db.execute(text("""
                WITH zombies AS (
                    SELECT job_id
                    FROM job_registry
                    WHERE status = 'running'
                      AND last_heartbeat_at < NOW() - (
                          COALESCE(ttl_seconds, 1800) * INTERVAL '1 second'
                      )
                )
                UPDATE job_registry
                SET status        = 'failed',
                    completed_at  = NOW(),
                    error_message = 'Auto-remediated by zombie watchdog: no heartbeat within TTL'
                WHERE job_id IN (SELECT job_id FROM zombies)
                RETURNING job_id
            """))
            rows = result.fetchall()
            remediated = len(rows)
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
    return remediated


async def task_recover_priced_inactive(batch_size: int = 100) -> int:
    """Bucket 2 recovery — Part A (no web):
    Find inactive parts whose supplier_parts row already has price_usd > 0
    but whose base_price was never copied (or was zeroed by a PDF sweep).
    Copy the price, convert USD→ILS, and reactivate the part.
    Runs in micro-batches every cleanup cycle.
    """
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
            else:
                logger.debug("task_recover_priced_inactive: nothing to recover")
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
                except Exception as part_exc:
                    logger.warning("task_recover_motorstore_prices: part %s failed: %s", oem, part_exc)
                    try:
                        await db.rollback()
                    except Exception:
                        pass

        except Exception as exc:
            logger.error("task_recover_motorstore_prices failed: %s", exc)
            try:
                await db.rollback()
            except Exception:
                pass

    return recovered


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
}


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
    await asyncio.sleep(5)

    return {
        "cycle": cycle,
        "types": t1,
        "oem": t2,
        "categorized": t3,
        "recategorized": t6,
        "flags": t4,
        "overflow": t5,
        "zombie_jobs_remediated": t_zombie,
        "recovered_priced": t_recover_priced,
        "recovered_web": t_recover_web,
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

            await asyncio.sleep(30)

        except Exception as e:
            print(f"[Cleanup] Error in cycle {cycle}: {e}")
            await asyncio.sleep(60)
