"""
System — /api/v1/system/* endpoints extracted from BACKEND_API_ROUTES.py.

Endpoints:
  GET /api/v1/system/health    (public)
  GET /api/v1/system/settings  (public)
  GET /api/v1/system/version   (public)
  GET /api/v1/system/metrics   (admin)
"""
import os
from datetime import datetime

import clamd as _clamd
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from BACKEND_DATABASE_MODELS import (
    get_db, async_session_factory, pii_session_factory,
    SystemSetting,
)
from BACKEND_AUTH_SECURITY import get_current_admin_user, get_redis

router = APIRouter()


@router.get("/api/v1/system/health")
async def health_check():
    import time as _time
    results: dict = {}

    # ── PostgreSQL catalog ────────────────────────────────────────────────────
    try:
        _t = _time.monotonic()
        async with async_session_factory() as _db:
            await _db.execute(text("SELECT 1"))
        results["postgres_catalog"] = {"status": "ok", "latency_ms": round((_time.monotonic() - _t) * 1000, 1)}
    except Exception as _e:
        results["postgres_catalog"] = {"status": "error", "error": str(_e)}

    # ── PostgreSQL PII ────────────────────────────────────────────────────────
    try:
        _t = _time.monotonic()
        async with pii_session_factory() as _db:
            await _db.execute(text("SELECT 1"))
        results["postgres_pii"] = {"status": "ok", "latency_ms": round((_time.monotonic() - _t) * 1000, 1)}
    except Exception as _e:
        results["postgres_pii"] = {"status": "error", "error": str(_e)}

    # ── Redis ─────────────────────────────────────────────────────────────────
    try:
        _r = await get_redis()
        if _r is None:
            raise RuntimeError("redis_unavailable")
        await _r.ping()
        results["redis"] = {"status": "ok"}
    except Exception as _e:
        results["redis"] = {"status": "error", "error": str(_e)}

    # ── Meilisearch ───────────────────────────────────────────────────────────
    _meili_url = os.getenv("MEILI_URL", "")
    if _meili_url:
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=3) as _hc:
                _resp = await _hc.get(f"{_meili_url}/health")
            results["meilisearch"] = {"status": "ok"} if _resp.status_code == 200 else {"status": "error", "code": _resp.status_code}
        except Exception as _e:
            results["meilisearch"] = {"status": "error", "error": str(_e)}
    else:
        results["meilisearch"] = {"status": "ok", "note": "not_configured"}

    # ── Hugging Face Inference API ────────────────────────────────────────────
    _hf_token = os.getenv("HF_TOKEN", "")
    if _hf_token:
        results["huggingface"] = {"status": "ok"}
    else:
        results["huggingface"] = {"status": "error", "error": "HF_TOKEN not configured"}

    # ── ClamAV ────────────────────────────────────────────────────────────────
    try:
        _clam_ok = False
        for _make_scanner in (
            lambda: _clamd.ClamdUnixSocket(),
            lambda: _clamd.ClamdNetworkSocket(host=os.getenv("CLAMD_HOST", "clamav"), port=3310),
        ):
            try:
                _make_scanner().ping()
                _clam_ok = True
                break
            except Exception:
                continue
        results["clamav"] = {"status": "ok"} if _clam_ok else {"status": "error", "error": "daemon unreachable"}
    except Exception as _e:
        results["clamav"] = {"status": "error", "error": str(_e)}

    # ── Stripe ────────────────────────────────────────────────────────────────
    _stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    if _stripe_key and not _stripe_key.startswith("sk_test_xxxxx"):
        results["stripe"] = {"status": "ok"}
    else:
        results["stripe"] = {"status": "error", "error": "key not configured"}

    # ── Aggregate ─────────────────────────────────────────────────────────────
    critical = ["postgres_catalog", "postgres_pii"]
    critical_ok = all(results.get(s, {}).get("status") == "ok" for s in critical)
    all_ok = all(v.get("status") == "ok" for v in results.values())
    if all_ok:
        overall = "healthy"
    elif critical_ok:
        overall = "degraded"
    else:
        overall = "unhealthy"

    return {
        "status": overall,
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0",
        "services": results,
    }


@router.get("/api/v1/system/settings")
async def get_public_settings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SystemSetting).where(SystemSetting.is_public == True))
    settings = result.scalars().all()
    return {s.key: s.value for s in settings}


@router.get("/api/v1/system/version")
async def get_version():
    return {"version": "1.0.0", "build": "2026.02.28", "environment": os.getenv("ENVIRONMENT", "development")}


@router.get("/api/v1/system/metrics")
async def get_system_metrics(
    current_user=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Real-time operational health snapshot for admins."""
    rows = (await db.execute(text("""
        SELECT
            COUNT(*)                                                          AS total_parts,
            COUNT(*) FILTER (WHERE is_available)                             AS active_parts,
            COUNT(*) FILTER (WHERE needs_oem_lookup)                         AS pending_enrichment
        FROM parts_catalog
    """))).fetchone()

    embed_pending = (await db.execute(text(
        "SELECT COUNT(*) FROM parts_images WHERE embedding IS NULL"
    ))).scalar()

    approval_pending = (await db.execute(text(
        "SELECT COUNT(*) FROM approval_queue WHERE status = 'pending'"
    ))).scalar()

    search_misses = (await db.execute(text(
        "SELECT COUNT(*) FROM search_misses WHERE triggered_scrape = FALSE"
    ))).scalar()

    bulk_deals = (await db.execute(text(
        "SELECT COUNT(*) FROM approval_queue WHERE entity_type = 'bulk_deal' AND status = 'pending'"
    ))).scalar()

    # Queue monitoring: detect stuck jobs (running > TTL without heartbeat)
    stuck_jobs = (await db.execute(text("""
        SELECT
            COUNT(*)                                       AS stuck_count,
            ARRAY_AGG(job_name)                           AS job_names,
            ARRAY_AGG(EXTRACT(EPOCH FROM (NOW() - last_heartbeat_at))::INTEGER) AS stale_seconds
        FROM job_registry
        WHERE status = 'running'
          AND ttl_seconds IS NOT NULL
          AND (NOW() - last_heartbeat_at) > (ttl_seconds * INTERVAL '1 second')
    """))).fetchone()

    stuck_details = {
        "count": stuck_jobs.stuck_count or 0,
        "jobs": [],
    }
    if stuck_jobs and stuck_jobs.stuck_count and stuck_jobs.stuck_count > 0:
        for job_name, stale_sec in zip(stuck_jobs.job_names or [], stuck_jobs.stale_seconds or []):
            stuck_details["jobs"].append({
                "name": job_name,
                "stale_seconds": stale_sec,
            })

    from db_update_agent import _last_report, _agent_running
    return {
        "catalog": {
            "total_parts":        rows.total_parts if rows else 0,
            "active_parts":       rows.active_parts if rows else 0,
            "pending_enrichment": rows.pending_enrichment if rows else 0,
            "pending_embedding":  embed_pending,
        },
        "queues": {
            "approval_pending":           approval_pending,
            "bulk_deals_pending":         bulk_deals,
            "search_misses_untriggered": search_misses,
        },
        "workers": {
            "db_agent_running":     _agent_running,
            "db_agent_last_report": _last_report,
        },
        "jobs": stuck_details,  # Queue monitoring
    }
