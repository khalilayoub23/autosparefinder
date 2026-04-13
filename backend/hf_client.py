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
import hashlib
import json as _json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("hf_client")

# ── Env config ────────────────────────────────────────────────────────────────
HF_TOKEN        = os.getenv("HF_TOKEN", "")
HF_TEXT_MODEL   = os.getenv("HF_TEXT_MODEL",   "moonshotai/Kimi-K2-Instruct-0905")   # chat/text — handles He/En mix
HF_VISION_MODEL = os.getenv("HF_VISION_MODEL", "Qwen/Qwen3-VL-8B-Instruct")           # multimodal image understanding
HF_EMBED_MODEL  = os.getenv("HF_EMBED_MODEL",  "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")  # local multilingual embeddings (He/En/Ar)
HF_AUDIO_MODEL  = os.getenv("HF_AUDIO_MODEL",  "openai/whisper-large-v3")              # speech-to-text, mixed-language aware
HF_CLIP_MODEL   = os.getenv("HF_CLIP_MODEL",   "openai/clip-vit-large-patch14")        # image embeddings
# For mixed He+En query normalization (transliteration, spelling fixes, synonym expansion)
HF_LANG_MODEL   = os.getenv("HF_LANG_MODEL",   "Helsinki-NLP/opus-mt-tc-big-he-en")   # He→En for mixed queries

CEREBRAS_API_KEY    = os.getenv("CEREBRAS_API_KEY", "")
CEREBRAS_TEXT_MODEL = os.getenv("CEREBRAS_TEXT_MODEL", "qwen-3-235b-a22b-instruct-2507")
CEREBRAS_BASE       = "https://api.cerebras.ai/v1"
GEMINI_API_KEY      = os.getenv("GEMINI_API_KEY", "")
GEMINI_VIS_MODEL    = os.getenv("GEMINI_VIS_MODEL", "gemini-1.5-flash-8b")
GEMINI_BASE         = "https://generativelanguage.googleapis.com/v1beta/models"
GROQ_API_KEY        = os.getenv("GROQ_API_KEY", "")
GROQ_AUDIO_MODEL    = os.getenv("GROQ_AUDIO_MODEL", "whisper-large-v3-turbo")
GROQ_BASE           = "https://api.groq.com/openai/v1"

ROUTER_BASE  = "https://router.huggingface.co/v1"
INFER_BASE   = "https://router.huggingface.co/hf-inference/models"

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
    body: bytes,
    timeout: float,
    label: str,
) -> httpx.Response:
    """POST with exponential back-off on 503 (cold-start) and 429 (rate-limit)."""
    client = _get_http()
    attempt = 0
    t0 = time.monotonic()
    while True:
        try:
            resp = await client.post(url, headers=headers, content=body,
                                     timeout=timeout)
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
            retry_after = int(resp.headers.get("retry-after", wait))
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

async def hf_text(prompt: str, system: str = "", timeout: float = 90.0) -> str:
    """Chat completion via HF Router. Cached in Redis for _TEXT_CACHE_TTL seconds."""
    if not CEREBRAS_API_KEY:
        raise RuntimeError("CEREBRAS_API_KEY not set in .env")

    cache_key = _cache_key("txt", CEREBRAS_TEXT_MODEL, system, prompt)
    cached = await _cache_get(cache_key)
    if cached is not None:
        logger.debug("hf_client [text] cache hit")
        return cached

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = _json.dumps({
        "model": CEREBRAS_TEXT_MODEL,
        "messages": messages,
        "max_tokens": 1000,
        "stream": False,
    }, ensure_ascii=False).encode()

    resp = await _post_with_retry(
        f"{CEREBRAS_BASE}/chat/completions",
        {
            "Authorization": f"Bearer {CEREBRAS_API_KEY}",
            "Content-Type": "application/json",
        },
        payload,
        timeout,
        "text",
    )
    resp.raise_for_status()
    result: str = resp.json()["choices"][0]["message"]["content"]
    await _cache_set(cache_key, result, _TEXT_CACHE_TTL)
    return result


async def hf_text_fast(prompt: str, system: str = "", timeout: float = 90.0) -> str:
    """Compatibility wrapper used by agents code-paths."""
    return await hf_text(prompt=prompt, system=system, timeout=timeout)


# ── Local embedding model ─────────────────────────────────────────────────────
_embed_model = None


def _is_model_cached() -> bool:
    """True only when model weights are already on disk — never triggers a download."""
    cache_dirs = [
        os.getenv("HF_HOME", ""),
        os.getenv("TRANSFORMERS_CACHE", ""),
        str(Path.home() / ".cache" / "huggingface"),
        "/root/.cache/huggingface",
    ]
    model_slug = HF_EMBED_MODEL.replace("/", "--")
    for base in cache_dirs:
        if not base:
            continue
        try:
            if (Path(base) / "hub" / f"models--{model_slug}").exists():
                return True
        except (PermissionError, OSError):
            continue
    return False


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer(HF_EMBED_MODEL)
    return _embed_model


async def hf_embed(text: str, timeout: float = 10.0) -> list[float]:
    """Remote text embedding via HF Inference API — no local model needed."""
    cache_key = _cache_key("emb", HF_EMBED_MODEL, text)
    cached = await _cache_get(cache_key)
    if cached is not None:
        try:
            return _json.loads(cached)
        except Exception:
            pass
    try:
        t0 = time.monotonic()
        http = _get_http()
        resp = await asyncio.wait_for(
            http.post(
                f"{INFER_BASE}/{HF_EMBED_MODEL}",
                headers={"Authorization": f"Bearer {HF_TOKEN}"},
                json={"inputs": text},
            ),
            timeout=timeout,
        )
        resp.raise_for_status()
        result = resp.json()
        if isinstance(result, list) and isinstance(result[0], list):
            result = result[0]
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

    payload = _json.dumps({
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": image_b64}},
            ],
        }],
    }, ensure_ascii=False).encode()

    resp = await _post_with_retry(
        f"{GEMINI_BASE}/{GEMINI_VIS_MODEL}:generateContent?key={GEMINI_API_KEY}",
        {"Content-Type": "application/json"},
        payload,
        timeout,
        "vision",
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


async def hf_audio(audio_bytes: bytes, timeout: float = 60.0) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set in .env")

    req = httpx.Request(
        "POST",
        f"{GROQ_BASE}/audio/transcriptions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        data={"model": GROQ_AUDIO_MODEL},
        files={"file": ("audio.webm", audio_bytes, "audio/webm")},
    )

    resp = await _post_with_retry(
        f"{GROQ_BASE}/audio/transcriptions",
        dict(req.headers),
        req.read(),
        timeout,
        "audio",
    )
    resp.raise_for_status()
    return resp.json().get("text", "")


def _is_mostly_hebrew(text: str) -> bool:
    """True if >30% of alpha chars are Hebrew."""
    heb = sum(1 for c in text if '\u05d0' <= c <= '\u05ea')
    alpha = sum(1 for c in text if c.isalpha())
    return alpha > 0 and heb / alpha > 0.3


async def hf_normalize_query(query: str, timeout: float = 10.0) -> str:
    """
    Normalize a mixed Hebrew/English auto-parts search query.

    - If the query is purely English — return as-is (search handles it).
    - If the query contains Hebrew — translate Hebrew words to English using
      Helsinki-NLP He→En model, then merge with any English terms already present.
      Result is a clean English search string the catalog can match against.
    - Falls back to original query on any error.
    """
    if not query or not _is_mostly_hebrew(query):
        return query

    cache_key = _cache_key("lang", HF_LANG_MODEL, query)
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    try:
        payload = _json.dumps(
            {"inputs": query}, ensure_ascii=False
        ).encode()
        resp = await _post_with_retry(
            f"{INFER_BASE}/{HF_LANG_MODEL}",
            _headers(),
            payload,
            timeout,
            "lang",
        )
        if resp.status_code == 200:
            data = resp.json()
            # Response: [{"translation_text": "..."}]
            translated = ""
            if isinstance(data, list) and data:
                translated = data[0].get("translation_text", "")
            elif isinstance(data, dict):
                translated = data.get("translation_text", "")

            if translated:
                # Keep English words from original query + translated Hebrew
                en_words_orig = [w for w in query.split() if all(c.isascii() for c in w) and len(w) > 1]
                merged = " ".join(dict.fromkeys(en_words_orig + translated.split()))
                result = merged.strip() or query
                await _cache_set(cache_key, result, _TEXT_CACHE_TTL)
                logger.debug("hf_client [lang] '%s' → '%s'", query, result)
                return result
    except Exception as exc:
        logger.warning("hf_client [lang] normalize failed: %s", exc)

    return query


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
