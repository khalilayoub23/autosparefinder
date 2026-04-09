"""
hf_client.py — HuggingFace API client

Design:
  • Single persistent httpx.AsyncClient (connection pool) — shared across all calls.
    Saves TCP + TLS overhead on every request.
  • Retry with exponential back-off on 503 (model cold-start) and 429 (rate-limit).
  • Structured logging for every request: model, latency_ms, status, tokens.
  • Redis cache for text and embed responses — repeated identical prompts are free.
  • Local embedding runs in a thread-pool executor so it never blocks the event loop.
"""

import asyncio
import base64
import hashlib
import json as _json
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger("hf_client")

# ── Env config ────────────────────────────────────────────────────────────────
HF_TOKEN        = os.getenv("HF_TOKEN", "")
HF_TEXT_MODEL   = os.getenv("HF_TEXT_MODEL",   "moonshotai/Kimi-K2-Instruct-0905")
HF_VISION_MODEL = os.getenv("HF_VISION_MODEL", "moonshotai/Kimi-K2-Instruct-0905")
HF_EMBED_MODEL  = os.getenv("HF_EMBED_MODEL",  "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
HF_AUDIO_MODEL  = os.getenv("HF_AUDIO_MODEL",  "openai/whisper-large-v3")
HF_CLIP_MODEL   = os.getenv("HF_CLIP_MODEL",   "openai/clip-vit-large-patch14")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")
CEREBRAS_TEXT_MODEL = os.getenv("CEREBRAS_TEXT_MODEL", "qwen-3-235b-a22b-instruct-2507")

INFER_BASE   = "https://router.huggingface.co/hf-inference/models"
GROQ_BASE    = "https://api.groq.com/openai/v1"
CEREBRAS_BASE = "https://api.cerebras.ai/v1/chat/completions"

# Cache TTL (seconds).  0 = disabled.
_TEXT_CACHE_TTL  = int(os.getenv("HF_TEXT_CACHE_TTL",  "3600"))   # 1 hour
_EMBED_CACHE_TTL = int(os.getenv("HF_EMBED_CACHE_TTL", "86400"))  # 24 hours

# Retry: max attempts for 503/429.  Back-off: 2^attempt seconds (1 → 2 → 4 → …).
_MAX_RETRIES = int(os.getenv("HF_MAX_RETRIES", "3"))

# ── Shared httpx client ───────────────────────────────────────────────────────
# One TCP connection pool for all HF calls — reused across requests.
_http: httpx.AsyncClient | None = None


def _get_http() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=5.0),
        )
    return _http


async def close_http() -> None:
    """Call on app shutdown to cleanly close the connection pool."""
    global _http
    if _http and not _http.is_closed:
        await _http.aclose()
        _http = None


def _headers(content_type: str = "application/json") -> dict[str, str]:
    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN not set in .env")
    return {"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": content_type}


def _groq_headers(content_type: str | None = "application/json") -> dict[str, str]:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set in .env")
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _cerebras_headers(content_type: str = "application/json") -> dict[str, str]:
    if not CEREBRAS_API_KEY:
        raise RuntimeError("CEREBRAS_API_KEY not set in .env")
    return {"Authorization": f"Bearer {CEREBRAS_API_KEY}", "Content-Type": content_type}


# ── Redis cache helper ────────────────────────────────────────────────────────
def _cache_key(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]
    return f"hf:{prefix}:{digest}"


async def _cache_get(key: str) -> str | None:
    try:
        from BACKEND_AUTH_SECURITY import get_redis
        r = await get_redis()
        val = await r.get(key)
        return val.decode() if isinstance(val, (bytes, bytearray)) else val
    except Exception:
        return None


async def _cache_set(key: str, value: str, ttl: int) -> None:
    if ttl <= 0:
        return
    try:
        from BACKEND_AUTH_SECURITY import get_redis
        r = await get_redis()
        await r.set(key, value, ex=ttl)
    except Exception:
        pass


# ── Retry wrapper ─────────────────────────────────────────────────────────────
async def _post_with_retry(
    url: str,
    headers: dict,
    body: bytes | None,
    timeout: float,
    label: str,
    **request_kwargs: Any,
) -> httpx.Response:
    """POST with exponential back-off on 503 (cold-start) and 429 (rate-limit)."""
    client = _get_http()
    attempt = 0
    t0 = time.monotonic()
    while True:
        try:
            kwargs: dict[str, Any] = {
                "headers": headers,
                "timeout": timeout,
                **request_kwargs,
            }
            if body is not None:
                kwargs["content"] = body
            resp = await client.post(url, **kwargs)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.PoolTimeout) as exc:
            if attempt >= _MAX_RETRIES:
                logger.error("hf_client [%s] network error after %d attempts: %s",
                             label, attempt + 1, exc)
                raise
            wait = 2 ** attempt
            logger.warning("hf_client [%s] network error (attempt %d/%d), retry in %ds: %s",
                           label, attempt + 1, _MAX_RETRIES, wait, exc)
            await asyncio.sleep(wait)
            attempt += 1
            continue

        elapsed_ms = round((time.monotonic() - t0) * 1000)

        if resp.status_code in (429, 503) and attempt < _MAX_RETRIES:
            # 503 = model still loading  |  429 = rate limit
            wait = 2 ** attempt
            retry_after = min(int(resp.headers.get("retry-after", wait)), 5)
            logger.warning(
                "hf_client [%s] HTTP %d (attempt %d/%d) — waiting %ds",
                label, resp.status_code, attempt + 1, _MAX_RETRIES, retry_after,
            )
            await asyncio.sleep(retry_after)
            attempt += 1
            continue

        # Log every completed request
        tokens = None
        if resp.status_code == 200:
            try:
                usage = resp.json().get("usage", {})
                tokens = usage.get("total_tokens")
            except Exception:
                pass
        logger.info(
            "hf_client [%s] status=%d latency_ms=%d tokens=%s attempts=%d",
            label, resp.status_code, elapsed_ms, tokens, attempt + 1,
        )
        return resp


# ── Public API ────────────────────────────────────────────────────────────────

async def hf_text(
    prompt: str,
    system: str = "",
    timeout: float = 90.0,
    model: str | None = None,
) -> str:
    """Chat completion via Google Gemini Flash 2.0. Cached in Redis."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set in .env")
    cache_key = _cache_key("txt", "gemini-flash", system, prompt)
    cached = await _cache_get(cache_key)
    if cached is not None:
        logger.debug("hf_client [text] cache hit")
        return cached

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

    contents = []
    if system:
        contents.append({"role": "user", "parts": [{"text": f"[SYSTEM INSTRUCTIONS]\n{system}\n[/SYSTEM INSTRUCTIONS]"}]})
        contents.append({"role": "model", "parts": [{"text": "הבנתי. אפעל לפי ההוראות."}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    payload = _json.dumps({
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": 1000,
            "temperature": 0.7,
        }
    }, ensure_ascii=False).encode()

    t0 = time.monotonic()
    http = _get_http()
    try:
        resp = await http.post(url, content=payload, headers={"Content-Type": "application/json"}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        result = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        logger.debug("hf_client [text] latency_ms=%d", round((time.monotonic() - t0) * 1000))
        await _cache_set(cache_key, result, _TEXT_CACHE_TTL)
        return result
    except Exception as exc:
        logger.warning("hf_client [text] Gemini failed, falling back to Cerebras: %s", exc)
        # Fallback to Cerebras
        cerebras_url = f"{CEREBRAS_BASE}"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload2 = _json.dumps({
            "model": CEREBRAS_TEXT_MODEL,
            "messages": messages,
            "max_tokens": 1000,
        }, ensure_ascii=False).encode()
        resp2 = await _post_with_retry(cerebras_url, _cerebras_headers(), payload2, timeout, "text")
        resp2.raise_for_status()
        result = resp2.json()["choices"][0]["message"]["content"]
        await _cache_set(cache_key, result, _TEXT_CACHE_TTL)
        return result


async def hf_text_fast(
    prompt: str,
    system: str = "",
    timeout: float = 90.0,
) -> str:
    """Chat completion via Cerebras — for background jobs and routing."""
    cache_key = _cache_key("txt_fast", CEREBRAS_TEXT_MODEL, system, prompt)
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = _json.dumps({
        "model": CEREBRAS_TEXT_MODEL,
        "messages": messages,
        "max_tokens": 1000,
    }, ensure_ascii=False).encode()
    try:
        resp = await _post_with_retry(
            CEREBRAS_BASE, _cerebras_headers(), payload, timeout, "text_fast"
        )
        resp.raise_for_status()
        result = resp.json()["choices"][0]["message"]["content"]
        await _cache_set(cache_key, result, _TEXT_CACHE_TTL)
        return result
    except Exception as exc:
        logger.warning("hf_client [text_fast] failed: %s", exc)
        return ""

async def hf_embed(text: str, timeout: float = 10.0) -> list[float]:
    """Text embedding via Google Gemini embeddings API."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set in .env")
    cache_key = _cache_key("emb", "gemini-embedding", text)
    cached = await _cache_get(cache_key)
    if cached:
        return _json.loads(cached)
    t0 = time.monotonic()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent?key={GEMINI_API_KEY}"
    payload = {
        "model": "models/gemini-embedding-001",
        "content": {"parts": [{"text": text}]}
    }
    try:
        http = _get_http()
        resp = await http.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        result = data["embedding"]["values"]
        logger.debug("hf_client [embed] latency_ms=%d", round((time.monotonic() - t0) * 1000))
        await _cache_set(cache_key, _json.dumps(result), _EMBED_CACHE_TTL)
        return result
    except Exception as exc:
        logger.warning("hf_client [embed] failed: %s", exc)
        return []


async def hf_vision(
    image_b64: str,
    prompt: str,
    mime: str = "image/jpeg",
    timeout: float = 60.0,
) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set in .env")

    def _run_gemini() -> str:
        try:
            from google import genai
        except Exception as exc:
            raise RuntimeError("google-genai not installed") from exc

        img_payload = image_b64.split(",", 1)[-1] if image_b64.startswith("data:") else image_b64
        image_bytes = base64.b64decode(img_payload)

        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                prompt,
                genai.types.Part.from_bytes(data=image_bytes, mime_type=mime),
            ],
        )

        text = (getattr(response, "text", "") or "").strip()
        if text:
            return text

        # Fallback path for SDK responses without .text convenience attribute.
        try:
            parts = (response.candidates[0].content.parts or [])
            return "".join(getattr(p, "text", "") for p in parts).strip()
        except Exception:
            return ""

    return await asyncio.wait_for(asyncio.to_thread(_run_gemini), timeout=timeout)


async def hf_audio(audio_bytes: bytes, timeout: float = 60.0) -> str:
    resp = await _post_with_retry(
        f"{GROQ_BASE}/audio/transcriptions",
        _groq_headers(content_type=None),
        None,
        timeout,
        "audio",
        data={"model": "whisper-large-v3"},
        files={"file": ("audio.webm", audio_bytes, "audio/webm")},
    )
    resp.raise_for_status()
    return resp.json().get("text", "")


async def hf_clip(image_b64: str, timeout: float = 15.0) -> list[float]:
    payload = _json.dumps({"inputs": {"image": image_b64}}, ensure_ascii=False).encode()
    resp = await _post_with_retry(
        f"{INFER_BASE}/{HF_CLIP_MODEL}",
        _headers(),
        payload,
        timeout,
        "clip",
    )
    resp.raise_for_status()
    return resp.json()[0]
