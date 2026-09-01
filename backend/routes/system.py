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
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    # X-Collect-Secret must be allowed so the owner's cross-origin browser
    # harvester (running on rockauto.com) can authenticate the relay + feed.
    "Access-Control-Allow-Headers": "Content-Type, X-Collect-Secret",
}

@router.options("/api/v1/system/collect")
async def collect_preflight():
    return JSONResponse(content={}, headers=CORS_HEADERS)

_COLLECT_SECRET = os.environ.get("COLLECT_SECRET", "")


@router.options("/api/v1/system/unpriced-oems")
async def unpriced_oems_preflight():
    return JSONResponse(content={}, headers=CORS_HEADERS)


@router.options("/api/v1/system/asap-collect")
async def asap_collect_preflight():
    return JSONResponse(content={}, headers=CORS_HEADERS)


@router.post("/api/v1/system/asap-collect")
async def asap_collect(request: Request):
    """Owner-browser relay for ASAP Network data sheets.

    ASAP's CSV load sheets are behind the owner's asapnetwork.org login, so our server
    (no session there) cannot fetch them directly. Instead the owner's browser — which
    HAS the session cookie — fetches an approved brand's CSV and POSTs it here as a CORS
    'simple request' (Content-Type text/plain, secret in the JSON body → no preflight,
    which the global CORSMiddleware would otherwise reject). Same auth as /collect
    (COLLECT_SECRET). We persist it to /app/state/asap/<brand_id>.csv, log the real
    header (so the importer can be mapped to ASAP's exact columns), and — once
    importers/asap_import.py exists — kick it off. Returns ACAO:* so the browser can
    read the reply."""
    import pathlib
    import subprocess
    raw = await request.body()
    data = {}
    try:
        data = json.loads(raw)
    except Exception:
        data = {}
    secret = request.headers.get("X-Collect-Secret", "") or (data.get("secret") if isinstance(data, dict) else "") or ""
    nonce = str((data.get("nonce") if isinstance(data, dict) else "") or "").strip()

    # AUTH: the long-lived COLLECT_SECRET, or a short-lived SINGLE-USE nonce.
    # The nonce exists so an operator driving the owner's browser never has to
    # paste the long-lived shared secret into a page context (where it would be
    # readable by that page and by anything logging the session). Mint one with
    # `python3 /app/maintenance/mint_asap_nonce.py`; it lives in Redis for 15
    # minutes and is DELETED on first successful use.
    authorized = False
    if _COLLECT_SECRET and secret == _COLLECT_SECRET:
        authorized = True
    elif nonce:
        try:
            import redis.asyncio as _redis
            _r = _redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
            key = f"asap:upload_nonce:{nonce}"
            if await _r.delete(key):      # delete returns 1 only if it existed
                authorized = True
            await _r.aclose()
        except Exception as exc:
            print(f"[asap-collect] nonce check failed: {exc}", flush=True)
    elif not _COLLECT_SECRET:
        authorized = True                 # no secret configured at all

    if not authorized:
        return JSONResponse(status_code=403, content={"error": "forbidden"}, headers=CORS_HEADERS)
    brand_id = str((data.get("brand_id") if isinstance(data, dict) else "") or "unknown").strip()
    brand_name = str((data.get("brand_name") if isinstance(data, dict) else "") or "").strip()
    csv_text = (data.get("csv") if isinstance(data, dict) else "") or ""
    if not csv_text or len(csv_text) < 20:
        return JSONResponse(status_code=400, content={"error": "empty csv"}, headers=CORS_HEADERS)
    d = pathlib.Path("/app/state/asap")
    d.mkdir(parents=True, exist_ok=True)
    fpath = d / f"{brand_id}.csv"
    fpath.write_text(csv_text, encoding="utf-8")
    header = csv_text.split("\n", 1)[0][:1500]
    rows = csv_text.count("\n")
    print(f"[asap-collect] brand={brand_name!r}({brand_id}) bytes={len(csv_text)} rows={rows}\n[asap-collect] header={header!r}", flush=True)
    import_pid = None
    importer = pathlib.Path("/app/importers/asap_import.py")
    if importer.exists():
        try:
            logp = pathlib.Path("/app/state/logs"); logp.mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen(
                ["python3", str(importer), str(fpath), "--brand-id", brand_id, "--brand-name", brand_name],
                stdout=open(logp / f"asap_import_{brand_id}.log", "a"), stderr=subprocess.STDOUT)
            import_pid = proc.pid
        except Exception as e:
            print(f"[asap-collect] importer spawn failed: {e}", flush=True)
    return JSONResponse(
        content={"status": "ok", "brand_id": brand_id, "bytes": len(csv_text), "rows": rows, "import_pid": import_pid},
        headers=CORS_HEADERS)


# Rotating keyset cursor so repeated calls sweep the whole unpriced catalog
# instead of returning the same first N every time (module-level, per-process).
_UNPRICED_OEM_CURSOR: dict = {"last_id": "00000000-0000-0000-0000-000000000000"}

# US-market brands where a US retailer (RockAuto) has the best catalog overlap.
# Feeding these first maximises the match rate of the browser harvest.
_ROCKAUTO_FRIENDLY_BRANDS = [
    "toyota", "honda", "ford", "chevrolet", "gmc", "dodge", "jeep", "chrysler",
    "nissan", "subaru", "mazda", "volkswagen", "bmw", "mercedes", "audi",
    "hyundai", "kia", "acura", "lexus", "infiniti", "cadillac", "buick",
]


@router.api_route("/api/v1/system/unpriced-oems", methods=["GET", "POST"])
async def unpriced_oems_feed(request: Request, db: AsyncSession = Depends(get_db)):
    """Authenticated feed of UNPRICED OEM numbers for the owner's RockAuto browser
    harvester to batch-price. Returns active parts with no base_price, swept via a
    rotating id cursor so successive calls cover the whole catalog. Requires the
    same secret as /collect — this enumerates OEM numbers and must never be an open
    catalog-scrape endpoint. GET (same-origin/server): secret via X-Collect-Secret
    header + ?limit/?brands/?reset query. POST (cross-origin browser): text/plain
    body {secret, limit, brands, reset} — a CORS "simple request" (no preflight,
    which the global CORSMiddleware would reject). Response carries ACAO:* so the
    browser can read it. Params: limit (max 500), brands=csv, reset=1."""
    body = {}
    if request.method == "POST":
        try:
            body = json.loads(await request.body())
        except Exception:
            body = {}
    secret = request.headers.get("X-Collect-Secret", "") or (body.get("secret") if isinstance(body, dict) else "") or ""
    if _COLLECT_SECRET and secret != _COLLECT_SECRET:
        from fastapi import HTTPException as _HTTPException
        raise _HTTPException(status_code=403, detail="Forbidden")

    def _param(name, default=""):
        if isinstance(body, dict) and body.get(name) is not None:
            return str(body.get(name))
        return request.query_params.get(name, default)

    # Heartbeat: a browser harvester hitting the feed = it's ALIVE (even if Cloudflare
    # is blocking it and 0 parts come back). The monitor uses this to alert only when
    # the harvester is genuinely stopped, not merely idle-output.
    try:
        import harvest_heartbeat
        _hb_source = _param("source") or "unknown"
        harvest_heartbeat.record(_hb_source)
        print(f"[unpriced-oems] source={_hb_source} limit={_param('limit','200')}", flush=True)
    except Exception:
        pass

    try:
        limit = max(1, min(500, int(_param("limit", "200"))))
    except Exception:
        limit = 200
    if _param("reset") == "1":
        _UNPRICED_OEM_CURSOR["last_id"] = "00000000-0000-0000-0000-000000000000"

    brands_param = (_param("brands", "") or "").strip()
    brands = [b.strip().lower() for b in brands_param.split(",") if b.strip()] if brands_param else _ROCKAUTO_FRIENDLY_BRANDS

    from sqlalchemy import text as _text
    rows = (await db.execute(_text("""
        SELECT id, oem_number, manufacturer
        FROM parts_catalog
        WHERE is_active
          AND (base_price IS NULL OR base_price = 0)
          AND oem_number IS NOT NULL AND oem_number <> ''
          AND id > CAST(:last_id AS uuid)
          AND LOWER(manufacturer) = ANY(:brands)
        ORDER BY id
        LIMIT :limit
    """), {"last_id": _UNPRICED_OEM_CURSOR["last_id"], "brands": brands, "limit": limit})).fetchall()

    if rows:
        _UNPRICED_OEM_CURSOR["last_id"] = str(rows[-1][0])
    else:
        # swept to the end — wrap around for the next call
        _UNPRICED_OEM_CURSOR["last_id"] = "00000000-0000-0000-0000-000000000000"

    oems = [r[1] for r in rows]
    return JSONResponse(
        {"count": len(oems), "oems": oems, "next_cursor": _UNPRICED_OEM_CURSOR["last_id"],
         "wrapped": not rows},
        headers=CORS_HEADERS,
    )

@router.post("/api/v1/system/collect")
async def collect_scrape_data(request: Request):
    ct = request.headers.get("content-type", "")
    _vehicle_slug = ""  # captured from any branch
    body_secret = ""    # secret may arrive in the body (text/plain relay path)

    # Parse body FIRST so the secret can come from the body — the cross-origin
    # browser harvester posts as text/plain (a CORS "simple request") to avoid a
    # preflight that the global CORSMiddleware rejects; a custom X-Collect-Secret
    # header would force that preflight, so the secret rides in the body instead.
    if "multipart/form-data" in ct or "application/x-www-form-urlencoded" in ct:
        form = await request.form()
        brand = form.get("brand", "") or ""
        _vehicle_slug = form.get("vehicle", "") or ""
        done = form.get("done", "false").lower() == "true"
        raw = form.get("data") or form.get("chunk", "[]")
        chunk = json.loads(raw) if raw else []
        data = {}
        body_secret = form.get("secret", "") or ""
    elif "text/plain" in ct:
        raw_body = await request.body()
        try:
            data = json.loads(raw_body)
        except Exception:
            data = {}
        brand = data.get("brand", "") or ""
        _vehicle_slug = data.get("vehicle", "") or ""
        chunk = data.get("chunk", data.get("parts", []))
        done = data.get("done", False)
        body_secret = data.get("secret", "") or ""
    else:
        data = await request.json()
        brand = data.get("brand", "") or ""
        _vehicle_slug = data.get("vehicle", "") or ""
        chunk = data.get("chunk", data.get("parts", []))
        done = data.get("done", False)
        body_secret = data.get("secret", "") or ""

    if _COLLECT_SECRET:
        token = request.headers.get("X-Collect-Secret", "") or body_secret
        if token != _COLLECT_SECRET:
            from fastapi import HTTPException as _HTTPException
            raise _HTTPException(status_code=403, detail="Forbidden")

    print(f"[collect] ct={ct!r} brand={brand!r} vehicle={_vehicle_slug!r} done={done} parts={len(chunk)}", flush=True)
    # Extract brand from vehicle slug — browser harvester sends "vehicle":"toyota/corolla-ke" not "brand"
    if not brand and _vehicle_slug:
        brand = _vehicle_slug.split("/")[0]
    if not brand:
        brand = "unknown"

    vehicle_slug = _vehicle_slug  # already captured above from any branch
    # Key by brand+vehicle, not brand alone — multiple vehicles of the same brand
    # (e.g. several Mercedes models) routinely finish around the same time, and a
    # brand-only key let them clobber each other's buffer and /tmp file.
    buf_key = f"{brand}::{vehicle_slug}" if vehicle_slug else brand
    file_tag = buf_key.replace("/", "_").replace(":", "_")

    if buf_key not in _collect_buffers:
        _collect_buffers[buf_key] = {"meta": {}, "parts": []}

    if "meta" in data:
        _collect_buffers[buf_key]["meta"] = data["meta"]
    if chunk:
        _collect_buffers[buf_key]["parts"].extend(chunk)

    if done:
        buf = _collect_buffers[buf_key]
        total = len(buf["parts"])
        del _collect_buffers[buf_key]

        # Route to correct importer based on DATA FORMAT, not just brand name.
        brand_base = brand.lower().split("_")[0]
        OEM_BRANDS = {"infiniti", "lexus", "acura", "mopar", "toyota", "honda", "nissan",
                      "ford", "bmw", "hyundai", "kia", "mazda", "subaru", "mitsubishi",
                      "volvo", "jaguar", "landrover", "porsche", "audi", "volkswagen", "vw", "gm"}
        sample_part = buf["parts"][0] if buf["parts"] else {}
        is_cpie_data = "price_eur" in sample_part or "source_url" in sample_part
        # RockAuto price-fill harvest (browser-relayed; source/brand='rockauto').
        # Matches by normalized OEM to existing unpriced parts and fills the price.
        if brand_base == "rockauto":
            path = f"/tmp/{file_tag}_rockauto.json"
            with open(path, "w") as f:
                json.dump(buf["parts"], f)
            log_path = f"/tmp/{file_tag}_rockauto_import.log"
            proc = subprocess.Popen(
                ["python3", "/app/importers/rockauto_price_import.py", path],
                stdout=open(log_path, "w"),
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
            print(f"[collect] RockAuto price import started: pid={proc.pid} log={log_path}")
        elif brand_base == "amayama":
            # Amayama OEM price-fill harvest (browser-relayed; brand='amayama').
            path = f"/tmp/{file_tag}_amayama.json"
            with open(path, "w") as f:
                json.dump(buf["parts"], f)
            log_path = f"/tmp/{file_tag}_amayama_import.log"
            proc = subprocess.Popen(
                ["python3", "/app/importers/amayama_price_import.py", path],
                stdout=open(log_path, "w"),
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
            print(f"[collect] Amayama price import started: pid={proc.pid} log={log_path}")
        elif brand_base in OEM_BRANDS and not is_cpie_data:
            path = f"/tmp/{file_tag}_oem.json"
            with open(path, "w") as f:
                json.dump(buf["parts"], f)
            log_path = f"/tmp/{file_tag}_oem_import.log"
            proc = subprocess.Popen(
                ["python3", "/app/oempartsonline_importer.py", "--file", path, "--brand", brand_base],
                stdout=open(log_path, "w"),
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
            print(f"[collect] OEM import started for {brand}: pid={proc.pid} log={log_path}")
        else:
            # Include vehicle slug in JSON so importer can write fitment rows
            out = {**buf["meta"], "parts": buf["parts"]}
            if vehicle_slug:
                out["vehicle_slug"] = vehicle_slug
            path = f"/tmp/{file_tag}_cpie.json"
            with open(path, "w") as f:
                json.dump(out, f)
            log_path = f"/tmp/{file_tag}_cpie_import.log"
            cmd = ["python3", "/app/importers/car_parts_ie_import_generic.py", "--brand", brand, "--file", path]
            if vehicle_slug:
                cmd += ["--vehicle-slug", vehicle_slug]
            proc = subprocess.Popen(
                cmd,
                stdout=open(log_path, "w"),
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
            print(f"[collect] auto-import started for {brand} vehicle={vehicle_slug}: pid={proc.pid} log={log_path}")

        return JSONResponse({"status": "saved", "path": path, "total": total, "import_pid": proc.pid}, headers=CORS_HEADERS)

    return JSONResponse(
        {"status": "ok", "brand": brand, "total": len(_collect_buffers[buf_key]["parts"])},
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


@router.get("/api/v1/system/job-queue")
async def system_job_queue():
    """Status of the sequenced catalogue pipeline (job_queue.py).

    The owner's "we only observe" view: which step is running, how much work is
    genuinely LEFT (measured from the database, never self-reported), and how
    old that measurement is. `remaining_at` matters — the counts are 60-90s
    full scans, so they are refreshed periodically rather than every batch, and
    a stale number must be visible AS stale.
    """
    try:
        import job_queue as _jq
        async with async_session_factory() as db:
            st = await _jq.status(db)
        st["text"] = _jq.render_status(st)
        # The step rows carry timestamps (remaining_at/started_at/…). JSONResponse
        # cannot serialise a datetime and answers 500 — which is how this endpoint
        # failed while the queue underneath it was perfectly healthy. Stringify.
        for s in st.get("steps", []):
            for k, v in list(s.items()):
                if isinstance(v, datetime):
                    s[k] = v.isoformat()
        return JSONResponse(st)
    except Exception as exc:
        return JSONResponse(
            {"error": f"{type(exc).__name__}: {exc}", "enabled": False}, status_code=500)


@router.get("/api/v1/system/tasks")
async def system_tasks():
    """Live state of every supervised background loop.

    Why this exists: the ONLY way to tell a healthy quiet loop from a dead one was
    to grep docker logs and guess — a loop with a long interval and a loop that
    crashed look identical from the outside. Counting log lines is not proof
    (verified 2026-07-29: 19 of 29 loops showed zero log hits, none of which meant
    what a naive read would suggest). This reads the actual asyncio task registry.

    `_SUPERVISED_TASKS` lives in BACKEND_API_ROUTES, which imports this module —
    so the import is LAZY, inside the handler, to avoid a circular import at
    module load.
    """
    try:
        from BACKEND_API_ROUTES import _SUPERVISED_TASKS
    except Exception as exc:      # pragma: no cover
        return JSONResponse(status_code=503,
                            content={"error": f"task registry unavailable: {exc}"})

    out = []
    for name, t in sorted(_SUPERVISED_TASKS.items()):
        exc_txt = None
        state = "running"
        if t.cancelled():
            state = "cancelled"
        elif t.done():
            state = "DEAD"
            try:
                e = t.exception()
                exc_txt = f"{type(e).__name__}: {e}" if e else None
            except Exception:
                pass
        out.append({"name": name, "state": state, "error": exc_txt})

    dead = [x for x in out if x["state"] == "DEAD"]
    return {
        "total": len(out),
        "running": sum(1 for x in out if x["state"] == "running"),
        "dead": len(dead),
        "dead_names": [x["name"] for x in dead],
        "tasks": out,
    }


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


# ── Browser-based Facebook group post ingest ───────────────────────────────────

_INGEST_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
}
# Compile once — matches ONLY facebook.com group URLs
_FB_GROUP_URL_RE = __import__("re").compile(
    r"^https://(www\.)?facebook\.com/groups/[A-Za-z0-9._%-]+/?$"
)
# Strip invisible/control chars that can be used for prompt injection
_CONTROL_RE = __import__("re").compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]|"
                                       r"[​-‏‪-‮﻿⁠-⁤]")


def _sanitize_for_llm(text: str, maxlen: int) -> str:
    """Strip control/invisible chars and truncate. Defence against prompt injection."""
    return _CONTROL_RE.sub("", text).strip()[:maxlen]


@router.post("/api/v1/system/ingest-group-posts")
async def ingest_group_posts(request: Request):
    """Accept pre-scraped Facebook group posts from the browser scanner.

    Uses the text/plain CORS simple-request pattern (same as /collect) so the
    browser can POST cross-origin without a preflight.

    Expected body (JSON string in a text/plain POST): {
        "secret": "<COLLECT_SECRET>",
        "group_id": "<uuid from group_targets>",
        "group_name": "...",
        "group_url": "https://www.facebook.com/groups/...",
        "posts": [{"text": "...", "post_url": "..."}]
    }

    Returns: {"ok": true, "drafted": N, "skipped": N}
    """
    from fastapi.responses import JSONResponse as _JSONResponse

    if request.method == "OPTIONS":
        return _JSONResponse({}, headers=_INGEST_CORS_HEADERS)

    # ── Auth ──────────────────────────────────────────────────────────────────
    secret = os.environ.get("COLLECT_SECRET", "")
    try:
        raw = await request.body()
        data = json.loads(raw)
    except Exception:
        return _JSONResponse({"ok": False, "error": "bad json"}, status_code=400,
                             headers=_INGEST_CORS_HEADERS)

    if not secret or data.get("secret") != secret:
        return _JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403,
                             headers=_INGEST_CORS_HEADERS)

    # ── Rate limit: 20 calls / hour per IP (protects LLM cost) ───────────────
    client_ip = (
        request.headers.get("X-Real-IP")
        or request.headers.get("CF-Connecting-IP")
        or (request.client.host if request.client else "unknown")
    )
    rl_key = f"rl:ingest_group_posts:{client_ip}"
    try:
        import redis as _redis_mod
        _rc = _redis_mod.Redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"),
                                        decode_responses=True)
        pipe = _rc.pipeline()
        pipe.incr(rl_key)
        pipe.expire(rl_key, 3600)
        count, _ = pipe.execute()
        if count > 20:
            return _JSONResponse({"ok": False, "error": "rate_limited"}, status_code=429,
                                 headers=_INGEST_CORS_HEADERS)
    except Exception:
        pass  # Redis unavailable — degrade gracefully, don't block the call

    # ── Input validation ──────────────────────────────────────────────────────
    group_id  = str(data.get("group_id", "")).strip()
    group_url = str(data.get("group_url", "")).strip().rstrip("/") + "/"
    # group_name is caller-supplied; sanitize before it touches any LLM prompt
    group_name = _sanitize_for_llm(str(data.get("group_name", "")), 255)
    posts = data.get("posts", [])

    if not group_id or not posts:
        return _JSONResponse({"ok": True, "drafted": 0, "skipped": 0, "reason": "no posts"},
                             headers=_INGEST_CORS_HEADERS)

    # Validate group_url is a real Facebook group URL (not an arbitrary string
    # that could inject into the LLM prompt via the discovery dict)
    if not _FB_GROUP_URL_RE.match(group_url.rstrip("/")):
        return _JSONResponse({"ok": False, "error": "invalid group_url"}, status_code=400,
                             headers=_INGEST_CORS_HEADERS)

    try:
        from social.facebook_browser.group_agent import GroupAgent, _relevance_score
        from social.facebook_browser.group_scanner import _save_draft, _get_db
    except Exception as exc:
        return _JSONResponse({"ok": False, "error": f"import: {exc}"}, status_code=500,
                             headers=_INGEST_CORS_HEADERS)

    # ── Pre-validate group_id exists — saves wasting LLM calls on a bad FK ───
    db = await _get_db()
    try:
        import sqlalchemy as _sa
        row = await db.execute(
            _sa.text("SELECT id FROM group_targets WHERE id = CAST(:gid AS uuid) LIMIT 1"),
            {"gid": group_id},
        )
        if not row.fetchone():
            return _JSONResponse({"ok": False, "error": "unknown group_id"}, status_code=404,
                                 headers=_INGEST_CORS_HEADERS)
    except Exception as exc:
        await db.close()
        return _JSONResponse({"ok": False, "error": f"db: {exc}"}, status_code=500,
                             headers=_INGEST_CORS_HEADERS)

    # ── Process posts ─────────────────────────────────────────────────────────
    agent   = GroupAgent()
    drafted = 0
    skipped = 0
    try:
        for p in posts[:15]:  # hard cap: 15 posts per call
            # Sanitize ALL caller-supplied strings before they enter the LLM prompt
            raw_text = _sanitize_for_llm(str(p.get("text") or ""), 400)
            raw_url  = str(p.get("post_url") or group_url).strip()[:500]

            # Ensure post_url is at least facebook.com (not arbitrary)
            try:
                from urllib.parse import urlparse as _up
                _host = _up(raw_url).hostname or ""
                if not (_host.endswith("facebook.com") or _host.endswith("fb.com")):
                    raw_url = group_url
            except Exception:
                raw_url = group_url

            if not raw_text or len(raw_text) < 15:
                skipped += 1
                continue
            score = _relevance_score(raw_text)
            if score < 0.25:
                skipped += 1
                continue

            discovery = {
                "group_id":        group_id,
                "group_name":      group_name,     # already sanitized above
                "group_url":       group_url,
                "post_url":        raw_url,
                "post_text":       raw_text,        # already sanitized above
                "relevance_score": score,
            }
            try:
                draft = await agent.draft_group_comment(discovery)
            except Exception:
                skipped += 1
                continue
            if not draft:
                skipped += 1
                continue
            saved = await _save_draft(db, group_id=group_id, post_url=raw_url,
                                      post_text=raw_text, draft=draft, score=score)
            if saved:
                drafted += 1
            else:
                skipped += 1
    finally:
        await db.close()

    return _JSONResponse({"ok": True, "drafted": drafted, "skipped": skipped},
                         headers=_INGEST_CORS_HEADERS)
