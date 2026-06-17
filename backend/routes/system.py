"""
System — /api/v1/system/* endpoints extracted from BACKEND_API_ROUTES.py.

Endpoints:
  GET /api/v1/system/health               (public)
  GET /api/v1/system/settings             (public)
  GET /api/v1/system/version              (public)
  GET /api/v1/system/metrics              (admin)
  GET /api/v1/admin/search/sync-status    (admin)
"""
import os
import json
import subprocess
from datetime import datetime
from typing import Any

import clamd as _clamd
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from routes.stripe_config import resolve_stripe_secret_key, is_valid_stripe_secret_key
from BACKEND_DATABASE_MODELS import (
    get_db, async_session_factory, pii_session_factory,
    SystemSetting,
)
from BACKEND_AUTH_SECURITY import get_current_admin_user, get_redis

router = APIRouter()

# ── Temporary scrape-collect endpoint (remove after import) ──────────────────
_collect_buffers: dict = {}

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

@router.options("/api/v1/system/collect")
async def collect_preflight():
    return JSONResponse(content={}, headers=CORS_HEADERS)

@router.post("/api/v1/system/collect")
async def collect_scrape_data(request: Request):
    ct = request.headers.get("content-type", "")
    if "multipart/form-data" in ct or "application/x-www-form-urlencoded" in ct:
        form = await request.form()
        brand = form.get("brand", "unknown")
        done = form.get("done", "false").lower() == "true"
        raw = form.get("data") or form.get("chunk", "[]")
        chunk = json.loads(raw) if raw else []
        data = {}
    elif "text/plain" in ct:
        # mode: no-cors fetch from cross-origin tabs sends body as text/plain
        raw_body = await request.body()
        data = json.loads(raw_body)
        brand = data.get("brand", "unknown")
        chunk = data.get("chunk", data.get("parts", []))
        done = data.get("done", False)
    else:
        data = await request.json()
        brand = data.get("brand", "unknown")
        chunk = data.get("chunk", data.get("parts", []))
        done = data.get("done", False)

    if brand not in _collect_buffers:
        _collect_buffers[brand] = {"meta": {}, "parts": []}

    if "meta" in data:
        _collect_buffers[brand]["meta"] = data["meta"]
    if chunk:
        _collect_buffers[brand]["parts"].extend(chunk)

    if done:
        buf = _collect_buffers[brand]
        total = len(buf["parts"])
        del _collect_buffers[brand]

        # OEM brands use oempartsonline_importer; others use car_parts_ie_import_generic
        # Strip _fallback/_url/_extra suffixes so fallback batches route correctly
        brand_base = brand.lower().split("_")[0]
        OEM_BRANDS = {"infiniti", "lexus", "acura", "mopar", "toyota", "honda", "nissan",
                      "ford", "bmw", "hyundai", "kia", "mazda", "subaru", "mitsubishi",
                      "volvo", "jaguar", "landrover", "porsche", "audi", "volkswagen", "vw", "gm"}
        if brand_base in OEM_BRANDS:
            path = f"/tmp/{brand}_oem.json"
            with open(path, "w") as f:
                json.dump(buf["parts"], f)
            log_path = f"/tmp/{brand}_oem_import.log"
            proc = subprocess.Popen(
                ["python3", "/app/oempartsonline_importer.py", "--file", path, "--brand", brand_base],
                stdout=open(log_path, "w"),
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
            print(f"[collect] OEM import started for {brand}: pid={proc.pid} log={log_path}")
        else:
            out = {**buf["meta"], "parts": buf["parts"]}
            path = f"/tmp/{brand}_cpie.json"
            with open(path, "w") as f:
                json.dump(out, f)
            log_path = f"/tmp/{brand}_cpie_import.log"
            proc = subprocess.Popen(
                ["python3", "/app/car_parts_ie_import_generic.py", "--brand", brand, "--file", path],
                stdout=open(log_path, "w"),
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
            print(f"[collect] auto-import started for {brand}: pid={proc.pid} log={log_path}")

        return JSONResponse({"status": "saved", "path": path, "total": total, "import_pid": proc.pid}, headers=CORS_HEADERS)

    return JSONResponse(
        {"status": "ok", "brand": brand, "total": len(_collect_buffers[brand]["parts"])},
        headers=CORS_HEADERS
    )


@router.get("/api/v1/system/oem-relay")
async def oem_relay_page():
    """HTTP relay page — browser navigates here (bypassing CF) and POSTs same-origin."""
    html = """<!DOCTYPE html>
<html><head><meta charset=utf-8><title>OEM Relay</title></head>
<body>
<pre id=log style="white-space:pre-wrap;font-size:12px">OEM relay loading...</pre>
<script>
const BACKEND = '';
const L = document.getElementById('log');
function log(msg){ L.textContent += '\\n' + new Date().toTimeString().slice(0,8)+' '+msg; }

async function sendAllParts(brand, parts) {
  const CHUNK = 2000;
  const total = parts.length;
  log('Sending ' + total + ' parts for ' + brand + ' in ' + Math.ceil(total/CHUNK) + ' chunks...');
  for (let i = 0; i < parts.length || i === 0; i += CHUNK) {
    const chunk = parts.slice(i, i+CHUNK);
    const done = (i + CHUNK >= parts.length);
    const fd = new FormData();
    fd.append('brand', brand);
    fd.append('data', JSON.stringify(chunk));
    fd.append('done', done ? 'true' : 'false');
    try {
      const r = await fetch(BACKEND + '/api/v1/system/collect', {method:'POST', body:fd});
      const j = await r.json();
      log('chunk ' + (Math.floor(i/CHUNK)+1) + ': ' + JSON.stringify(j));
      if (done) { log('✅ Import triggered! pid=' + j.import_pid); break; }
    } catch(e) { log('ERROR: ' + e); break; }
  }
}

(async () => {
  try {
    const raw = window.name;
    if (!raw || raw.length < 10) { log('No data in window.name (length=' + (raw||'').length + ')'); return; }
    log('window.name size: ' + raw.length + ' bytes');
    const payload = JSON.parse(raw);
    window.name = '';  // clear to free memory
    if (!payload.brand || !payload.parts) { log('Bad payload: ' + Object.keys(payload)); return; }
    await sendAllParts(payload.brand, payload.parts);
  } catch(e) { log('FATAL: ' + e); }
})();
</script>
</body></html>"""
    return HTMLResponse(html)


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
        # HF_TOKEN not configured — AI features degraded but not a critical infrastructure failure
        results["huggingface"] = {"status": "ok", "note": "not_configured"}

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
    _stripe_key, _ = resolve_stripe_secret_key()
    if is_valid_stripe_secret_key(_stripe_key):
        results["stripe"] = {"status": "ok"}
    else:
        results["stripe"] = {"status": "error", "error": "key not configured"}

    # ── Aggregate ─────────────────────────────────────────────────────────────
    # Critical = must be healthy for the system to function.
    # Optional = external/infra services that may not be configured in dev.
    critical = ["postgres_catalog", "postgres_pii", "redis"]
    optional = ["clamav", "stripe", "meilisearch"]
    critical_ok = all(results.get(s, {}).get("status") == "ok" for s in critical)
    non_optional_ok = all(
        results.get(s, {}).get("status") == "ok"
        for s in results
        if s not in optional
    )
    if non_optional_ok:
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


# Alias for load-balancer / uptime monitors that probe /health
@router.get("/health")
async def health_alias():
    return {"status": "ok"}


@router.get("/api/v1/system/settings")
async def get_public_settings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SystemSetting).where(
            (SystemSetting.is_public == True) | (SystemSetting.is_public.is_(None))
        )
    )
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


@router.get("/api/v1/admin/search/sync-status", tags=["Admin – Search"])
async def search_sync_status(
    current_user=Depends(get_current_admin_user),
):
    """
    Returns Meilisearch index status, document count, last-updated timestamp,
    and whether the DB catalog count is in sync with the search index.
    """
    import httpx as _httpx
    meili_url = os.getenv("MEILI_URL", "")
    meili_key = os.getenv("MEILI_MASTER_KEY", "")

    if not meili_url:
        return {"status": "not_configured", "meili_url": None}

    headers = {"Authorization": f"Bearer {meili_key}"} if meili_key else {}

    result: dict = {"meili_url": meili_url}

    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            # 1. Overall health
            health = await client.get(f"{meili_url}/health", headers=headers)
            result["health"] = health.json() if health.status_code == 200 else {"status": "error", "code": health.status_code}

            # 2. Index stats
            stats = await client.get(f"{meili_url}/indexes/parts/stats", headers=headers)
            if stats.status_code == 200:
                sd = stats.json()
                result["index"] = {
                    "number_of_documents": sd.get("numberOfDocuments", 0),
                    "is_indexing": sd.get("isIndexing", False),
                    "field_distribution_sample": dict(list((sd.get("fieldDistribution") or {}).items())[:5]),
                }
            else:
                result["index"] = {"status": "error", "code": stats.status_code}

            # 3. Tasks (last 3)
            tasks = await client.get(f"{meili_url}/tasks?limit=3&indexUids=parts", headers=headers)
            if tasks.status_code == 200:
                td = tasks.json()
                result["recent_tasks"] = [
                    {
                        "uid": t.get("uid"),
                        "type": t.get("type"),
                        "status": t.get("status"),
                        "enqueuedAt": t.get("enqueuedAt"),
                        "finishedAt": t.get("finishedAt"),
                    }
                    for t in (td.get("results") or [])
                ]
            else:
                result["recent_tasks"] = []

    except Exception as exc:
        result["error"] = str(exc)[:200]

    # 4. DB parity check
    try:
        async with async_session_factory() as db:
            db_count = (await db.execute(
                text("SELECT COUNT(*) FROM parts_catalog WHERE is_active = TRUE")
            )).scalar_one()
        result["db_active_parts"] = db_count
        index_count = (result.get("index") or {}).get("number_of_documents", 0)
        result["parity"] = {
            "db_count": db_count,
            "index_count": index_count,
            "gap": db_count - index_count,
            "in_sync": abs(db_count - index_count) < 1000,
        }
    except Exception as exc:
        result["parity"] = {"error": str(exc)[:200]}

    return result
