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
import re as _re
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

CEREBRAS_API_KEY      = os.getenv("CEREBRAS_API_KEY", "")
CEREBRAS_TEXT_MODEL   = os.getenv("CEREBRAS_TEXT_MODEL", "gpt-oss-120b")
CEREBRAS_FALLBACK_MODEL = os.getenv("CEREBRAS_FALLBACK_MODEL", "zai-glm-4.7")
CEREBRAS_BASE         = "https://api.cerebras.ai/v1"
GEMINI_API_KEY      = os.getenv("GEMINI_API_KEY", "")
WHATSAPP_GEMINI_KEY = os.getenv("WHATSAPP_GEMINI_API_KEY", GEMINI_API_KEY)  # dedicated key for webhook
GEMINI_VIS_MODEL    = os.getenv("GEMINI_VIS_MODEL", "gemini-2.0-flash")
GEMINI_BASE         = "https://generativelanguage.googleapis.com/v1beta/models"
GROQ_API_KEY        = os.getenv("GROQ_API_KEY", "")
GROQ_AUDIO_MODEL    = os.getenv("GROQ_AUDIO_MODEL", "whisper-large-v3-turbo")
GROQ_VIS_MODEL      = os.getenv("GROQ_VIS_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
GROQ_BASE           = "https://api.groq.com/openai/v1"

# ── Gemini circuit breaker ─────────────────────────────────────────────────────
# Trips for 1 hour on quota/daily-limit 429; avoids hammering an exhausted key.
import time as _time
_GEMINI_CB_COOLDOWN   = 3600   # seconds
_gemini_cb_open_until: float = 0.0  # epoch; 0 = closed (Gemini usable)

def _gemini_cb_is_open() -> bool:
    return _time.time() < _gemini_cb_open_until

def _gemini_cb_trip() -> None:
    global _gemini_cb_open_until
    _gemini_cb_open_until = _time.time() + _GEMINI_CB_COOLDOWN
    logger.warning("Gemini circuit breaker TRIPPED — skipping Gemini for 1 hour")


ROUTER_BASE  = "https://router.huggingface.co/v1"
INFER_BASE   = "https://router.huggingface.co/hf-inference/models"

# Model used for background enrichment tasks (part naming, Hebrew translation, category suggestions).
# Phi-3-mini via Featherless AI provider — use :featherless-ai suffix to route to the correct provider.
# Confirmed on HF Router inference providers page (huggingface.co/{model}?inference_provider=featherless-ai)
# Fallback chain: Phi-3-mini → Groq llama-3.1-8b-instant (configured in hf_router_text).
HF_ENRICH_MODEL = os.getenv("HF_ENRICH_MODEL", "microsoft/Phi-3-mini-4k-instruct:featherless-ai")

# Cache TTL (seconds).  0 = disabled.
_TEXT_CACHE_TTL  = int(os.getenv("HF_TEXT_CACHE_TTL",  "3600"))   # 1 hour
_EMBED_CACHE_TTL = int(os.getenv("HF_EMBED_CACHE_TTL", "86400"))  # 24 hours

# Retry: max attempts for 503/429.  Back-off: 2^attempt seconds (1 → 2 → 4 → …).
_MAX_RETRIES = int(os.getenv("HF_MAX_RETRIES", "3"))
_BASE_BACKOFF_SECONDS = float(os.getenv("HF_BASE_BACKOFF_SECONDS", "0.8"))
_MAX_BACKOFF_SECONDS = float(os.getenv("HF_MAX_BACKOFF_SECONDS", "6"))
_RETRY_AFTER_CAP_SECONDS = float(os.getenv("HF_RETRY_AFTER_CAP_SECONDS", "8"))


# Background job concurrency limiter — limits Rex/scraper to 1 concurrent AI call.
# Webhook calls bypass this with hf_text(priority=True) to ensure fast response.
_BG_SEMAPHORE = asyncio.Semaphore(1)

# Priority (agent) concurrency limiter — prevents mass concurrent agent calls from
# saturating Gemini when many agents fire simultaneously (e.g. during load tests).
_PRIORITY_SEMAPHORE = asyncio.Semaphore(4)

# Gemini concurrency limiter — Gemini free-tier allows ~60 RPM but bursts trip 429.
# Cap at 2 concurrent Gemini calls to stay well within the rate limit.
_GEMINI_SEMAPHORE = asyncio.Semaphore(2)

# Gemini per-minute token bucket: 50 calls/min max (leaves headroom vs 60 RPM limit).
_GEMINI_CALL_TIMESTAMPS: list[float] = []
_GEMINI_RPM_LIMIT = 50

def _gemini_rate_check() -> bool:
    """Return True if a Gemini call is allowed under the per-minute token bucket."""
    now = time.time()
    global _GEMINI_CALL_TIMESTAMPS
    _GEMINI_CALL_TIMESTAMPS = [t for t in _GEMINI_CALL_TIMESTAMPS if now - t < 60]
    if len(_GEMINI_CALL_TIMESTAMPS) >= _GEMINI_RPM_LIMIT:
        return False
    _GEMINI_CALL_TIMESTAMPS.append(now)
    return True


def _bounded_backoff(attempt: int) -> float:
    return min(_BASE_BACKOFF_SECONDS * (2 ** attempt), _MAX_BACKOFF_SECONDS)


def _parse_retry_after_seconds(value: str | None, fallback: float) -> float:
    """Parse Retry-After header; if invalid/non-numeric use fallback backoff."""
    if not value:
        return fallback
    try:
        seconds = float(value)
        if seconds >= 0:
            return seconds
    except (TypeError, ValueError):
        pass
    return fallback

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


def _clean_response(text: str) -> str:
    """
    Clean AI response:
    - Remove CJK (Chinese/Japanese/Korean) characters
    - Remove other non-relevant unicode blocks
    - Fix spacing issues from mixed RTL/LTR text
    - Allow: Hebrew, Arabic, Latin, numbers, punctuation, emojis
    """
    # Remove CJK ideographs (base + extensions).
    # Use \UXXXXXXXX for supplementary planes to avoid corrupting other scripts.
    text = _re.sub(
        r'['
        r'\u3400-\u4DBF'
        r'\u4E00-\u9FFF'
        r'\U00020000-\U0002A6DF'
        r'\U0002A700-\U0002B73F'
        r'\U0002B740-\U0002B81F'
        r'\U0002B820-\U0002CEAF'
        r'\U0002CEB0-\U0002EBEF'
        r'\U00030000-\U0003134F'
        r']',
        '',
        text,
    )
    # Remove Japanese kana
    text = _re.sub(r'[\u3040-\u30ff]', '', text)
    # Remove Korean hangul
    text = _re.sub(r'[\uac00-\ud7af]', '', text)
    # Remove Thai, Devanagari
    text = _re.sub(r'[\u0e00-\u0e7f\u0900-\u097f]', '', text)
    # Clean multiple spaces
    text = _re.sub(r' {2,}', ' ', text)
    # Clean lines that became empty after cleaning
    lines = [l.strip() for l in text.splitlines()]
    text = '\n'.join(l for l in lines if l)
    return text.strip()


# ── Retry wrapper ─────────────────────────────────────────────────────────────
async def _post_with_retry(
    url: str,
    headers: dict,
    body: bytes,
    timeout: float,
    label: str,
) -> httpx.Response:
    """POST with bounded back-off on 503 (cold-start) and 429 (rate-limit)."""
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
            wait = _bounded_backoff(attempt)
            logger.warning("hf_client [%s] network error (attempt %d/%d), retry in %.1fs: %s",
                           label, attempt + 1, _MAX_RETRIES, wait, exc)
            await asyncio.sleep(wait)
            attempt += 1
            continue

        elapsed_ms = round((time.monotonic() - t0) * 1000)

        if resp.status_code in (429, 503) and attempt < _MAX_RETRIES:
            # 503 = model still loading  |  429 = rate limit
            wait = _bounded_backoff(attempt)
            retry_after = _parse_retry_after_seconds(resp.headers.get("retry-after"), wait)
            bounded_wait = max(0.2, min(retry_after, _RETRY_AFTER_CAP_SECONDS))
            logger.warning(
                "hf_client [%s] HTTP %d (attempt %d/%d) — waiting %.1fs",
                label, resp.status_code, attempt + 1, _MAX_RETRIES, bounded_wait,
            )
            await asyncio.sleep(bounded_wait)
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


# ── Cerebras response helpers ─────────────────────────────────────────────────

def _extract_cerebras_content(data: dict) -> str:
    """Extract text from a Cerebras chat completion response.

    Standard models return message.content.
    Reasoning models (e.g. zai-glm-4.7) set content=None and put the output
    in message.reasoning instead.  We prefer content and fall back to reasoning.
    """
    try:
        msg = data["choices"][0]["message"]
        content = msg.get("content") or ""
        if content:
            return content
        # Reasoning model fallback
        reasoning = msg.get("reasoning") or ""
        return reasoning
    except (KeyError, IndexError, TypeError):
        return ""


async def _cerebras_call(
    prompt: str,
    system: str,
    model: str,
    timeout: float,
    priority: bool,
) -> str:
    """Single Cerebras chat completion call — does NOT cache or fall back."""
    if not CEREBRAS_API_KEY:
        raise RuntimeError("CEREBRAS_API_KEY not set in .env")
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = _json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": 1000,
        "stream": False,
    }, ensure_ascii=False).encode()
    _acquire = not priority
    if _acquire:
        await _BG_SEMAPHORE.acquire()
    try:
        resp = await _post_with_retry(
            f"{CEREBRAS_BASE}/chat/completions",
            {"Authorization": f"Bearer {CEREBRAS_API_KEY}", "Content-Type": "application/json"},
            payload,
            timeout,
            f"cerebras/{model}",
        )
    finally:
        if _acquire:
            _BG_SEMAPHORE.release()
    resp.raise_for_status()
    return _clean_response(_extract_cerebras_content(resp.json()))


# ── Public API ────────────────────────────────────────────────────────────────

async def hf_text(prompt: str, system: str = "", timeout: float = 90.0, priority: bool = False, model: str | None = None, max_tokens: int = 2000) -> str:
    """Chat completion via HF Router. Cached in Redis for _TEXT_CACHE_TTL seconds.
    priority=True bypasses the background-job semaphore (use for webhook/realtime calls).
    max_tokens: raise for large structured outputs — reasoning models (gpt-oss)
    spend part of the budget on chain-of-thought before the answer; 1000 was
    too small for JSON campaign plans (found 2026-07-05).
    """
    if not CEREBRAS_API_KEY:
        raise RuntimeError("CEREBRAS_API_KEY not set in .env")

    selected_model = (model or CEREBRAS_TEXT_MODEL).strip()
    cache_key = _cache_key("txt", selected_model, system, prompt)
    cached = await _cache_get(cache_key)
    if cached is not None:
        logger.debug("hf_client [text] cache hit")
        cleaned_cached = _clean_response(cached)
        if cleaned_cached != cached:
            await _cache_set(cache_key, cleaned_cached, _TEXT_CACHE_TTL)
        return cleaned_cached

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = _json.dumps({
        "model": selected_model,
        "messages": messages,
        "max_tokens": max(256, int(max_tokens)),
        "stream": False,
    }, ensure_ascii=False).encode()

    _acquire = not priority
    if _acquire:
        await _BG_SEMAPHORE.acquire()
    else:
        await _PRIORITY_SEMAPHORE.acquire()
    try:
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
    finally:
        if _acquire:
            _BG_SEMAPHORE.release()
        else:
            _PRIORITY_SEMAPHORE.release()
    if resp.status_code == 429:
        # Try Cerebras fallback model (reasoning model zai-glm-4.7) before external providers
        if CEREBRAS_FALLBACK_MODEL and CEREBRAS_FALLBACK_MODEL != selected_model:
            logger.warning("hf_text: Cerebras primary 429 — trying fallback model %s", CEREBRAS_FALLBACK_MODEL)
            try:
                result = await _cerebras_call(prompt, system, CEREBRAS_FALLBACK_MODEL, timeout, priority)
                await _cache_set(cache_key, result, _TEXT_CACHE_TTL)
                return result
            except Exception as fb_err:
                logger.warning("hf_text: Cerebras fallback also failed: %s", fb_err)
        if GEMINI_API_KEY and not _gemini_cb_is_open():
            logger.warning("hf_text: Cerebras 429 — falling back to Gemini")
            async with _GEMINI_SEMAPHORE:
                if not _gemini_rate_check():
                    logger.warning("hf_text: Gemini RPM bucket full — skipping to GROQ")
                else:
                    try:
                        result = await gemini_text(prompt=prompt, system=system, timeout=timeout)
                        return result
                    except Exception as gemini_err:
                        err_str = str(gemini_err)
                        logger.warning(f"hf_text: Gemini also failed ({gemini_err}) — falling back to GROQ")
                        if "429" in err_str or "quota" in err_str.lower() or "limit" in err_str.lower():
                            _gemini_cb_trip()
        elif _gemini_cb_is_open():
            logger.debug("hf_text: Gemini circuit breaker open — skipping directly to GROQ")
        if GROQ_API_KEY:
            logger.warning("hf_text: falling back to GROQ llama-3.3-70b-versatile")
            return await groq_text(prompt=prompt, system=system, timeout=timeout)
    resp.raise_for_status()
    result: str = _extract_cerebras_content(resp.json())
    result = _clean_response(result)
    await _cache_set(cache_key, result, _TEXT_CACHE_TTL)
    return result


async def hf_text_fast(prompt: str, system: str = "", timeout: float = 90.0, priority: bool = False, model: str | None = None) -> str:
    """Compatibility wrapper used by agents code-paths."""
    return await hf_text(prompt=prompt, system=system, timeout=timeout, priority=priority, model=model)


async def hf_router_text(prompt: str, system: str = "", timeout: float = 45.0, model: str | None = None) -> str:
    """Chat completion via HF Router with PRO token.

    Designed for background enrichment jobs (NOT user-facing) that need a
    reliable, multilingual model without competing with the Cerebras chatbot quota.
    HF PRO gives 20× inference credits → high rate limits on the router.
    """
    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN not set — cannot use HF Router")

    selected_model = model or HF_ENRICH_MODEL
    cache_key = _cache_key("rtr", selected_model, system, prompt)
    cached = await _cache_get(cache_key)
    if cached is not None:
        logger.debug("hf_router_text cache hit")
        return _clean_response(cached)

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = _json.dumps({
        "model": selected_model,
        "messages": messages,
        "max_tokens": 512,
        "stream": False,
    }, ensure_ascii=False).encode()

    await _BG_SEMAPHORE.acquire()
    try:
        resp = await _post_with_retry(
            f"{ROUTER_BASE}/chat/completions",
            {"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "application/json"},
            payload,
            timeout,
            f"hf_router/{selected_model}",
        )
    finally:
        _BG_SEMAPHORE.release()

    if resp.status_code == 429:
        # Router quota hit — fall back to Groq as last resort
        if GROQ_API_KEY:
            logger.warning("hf_router_text: HF Router 429 — falling back to Groq")
            return await groq_text(prompt=prompt, system=system, timeout=timeout,
                                   model="llama-3.1-8b-instant")
        resp.raise_for_status()

    resp.raise_for_status()
    result: str = resp.json()["choices"][0]["message"]["content"]
    result = _clean_response(result)
    await _cache_set(cache_key, result, _TEXT_CACHE_TTL)
    return result


# ── Hebrew query normalizer (Option 3 / DistilBERT via HF Inference API) ─────
# Classifies Hebrew/English auto-part search queries using zero-shot classification.
# Uses distilbert-base-multilingual-cased via HF Inference API — zero RAM on server.
# Falls back gracefully if API unavailable. Used by search normalization pipeline.

_HF_ZSC_MODEL = os.getenv(
    "HF_ZSC_MODEL",
    "facebook/bart-large-mnli"   # BART MNLI: best zero-shot classification; multilingual via mBART family
)

_AUTO_CATEGORIES = [
    "brakes", "engine", "suspension", "body exterior", "electrical sensors",
    "filters", "cooling", "exhaust", "lighting", "wipers",
    "transmission gearbox", "belts chains", "wheels bearings",
    "interior comfort", "fuel air", "air conditioning heating", "clutch drivetrain",
]

# Hebrew model/brand shortcuts → expanded English for better search
_HE_EXPANSIONS: dict[str, str] = {
    "קורולה": "corolla", "אקורה": "corolla", "קמרי": "camry",
    "טוסון": "tucson", "סנטה פה": "santa fe", "אי 20": "i20",
    "אי 30": "i30", "אי 35": "i35", "אי 40": "i40",
    "גולף": "golf", "פולו": "polo", "פאסאט": "passat",
    "אוקטביה": "octavia", "סופרב": "superb", "פביה": "fabia",
    "רפידות": "brake pads", "דיסק": "brake disc", "מסנן": "filter",
    "שמן": "oil", "צמיג": "tyre tire", "בולם": "shock absorber",
    "פנס": "headlight", "ממחק": "wiper", "מצבר": "battery",
    "מזגן": "air conditioning", "קלאץ": "clutch", "גיר": "gearbox",
    "פגוש": "bumper", "כנף": "fender", "שמשה": "windshield",
    "מאיין": "muffler exhaust", "מנוע": "engine", "קפיץ": "spring",
}


async def hf_classify_query(query: str, top_k: int = 3) -> list[dict]:
    """
    Zero-shot classify a search query into auto-part categories.
    Uses distilbert-base-multilingual-cased via HF Inference API.
    Returns [{label, score}] sorted by confidence. Falls back to [] on error.
    No server memory impact — pure API call.
    """
    if not HF_TOKEN or not query.strip():
        return []

    cache_key = _cache_key("zsc", _HF_ZSC_MODEL, "", query)
    cached = await _cache_get(cache_key)
    if cached:
        try:
            import json as _json
            return _json.loads(cached)
        except Exception:
            pass

    url = f"{INFER_BASE}/{_HF_ZSC_MODEL}"
    payload = {
        "inputs": query,
        "parameters": {"candidate_labels": _AUTO_CATEGORIES, "multi_label": False},
    }
    headers = {"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "application/json"}

    try:
        async with _client() as c:
            resp = await c.post(url, json=payload, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                labels = data.get("labels", [])
                scores = data.get("scores", [])
                result = sorted(
                    [{"label": l, "score": round(s, 4)} for l, s in zip(labels, scores)],
                    key=lambda x: -x["score"]
                )[:top_k]
                import json as _json
                await _cache_set(cache_key, _json.dumps(result), 3600)
                return result
    except Exception:
        pass
    return []


# ── Arabic → English query expansion (added 2026-07-20) ──────────────────────
# The catalog is English + Hebrew: only ONE active part contains any Arabic text, so an
# Arabic search matched nothing at all (verified live: "فلتر زيت" -> 0 results) even
# though the platform serves Arabic-speaking customers. Hebrew search works because
# name_he exists AND Hebrew is expanded to English; Arabic needs the same bridge.
_AR_EXPANSIONS = {
    # ── parts ──
    "فلتر زيت": "oil filter", "فلتر هواء": "air filter", "فلتر بنزين": "fuel filter",
    "فلتر مكيف": "cabin filter", "فلتر": "filter", "زيت": "oil",
    "فحمات فرامل": "brake pads", "تيل فرامل": "brake pads", "فحمات": "brake pads",
    "فرامل": "brake", "فرملة": "brake", "دسك": "brake disc", "هوب": "brake disc",
    "بطارية": "battery", "مساحات": "wiper blades", "مساحة": "wiper",
    "شمعات": "spark plugs", "بوجيهات": "spark plugs", "بوجيه": "spark plug",
    "كويل": "ignition coil", "بخاخ": "fuel injector", "بخاخات": "fuel injectors",
    "ردياتير": "radiator", "رادياتير": "radiator", "مبرد": "radiator",
    "ثرموستات": "thermostat", "مكيف": "air conditioning", "كمبروسر": "compressor",
    "دينامو": "alternator", "سلف": "starter motor", "مارش": "starter motor",
    "طرمبة": "pump", "مضخة": "pump", "طرمبة ماء": "water pump",
    "كلتش": "clutch", "دبرياج": "clutch", "جير": "gearbox", "ناقل حركة": "transmission",
    "محرك": "engine", "موتور": "engine", "مكينة": "engine",
    "عفشة": "suspension", "مقص": "control arm", "مساعد": "shock absorber",
    "مساعدات": "shock absorbers", "صدام": "bumper", "رفرف": "fender",
    "كبوت": "hood", "باب": "door", "زجاج": "glass", "مرآة": "mirror", "مراية": "mirror",
    "كشاف": "headlight", "مصباح": "lamp", "لمبة": "bulb", "اشارة": "turn signal",
    "حزام": "belt", "سير": "belt", "سير مكيف": "ac belt", "طقم": "kit",
    "حساس": "sensor", "حساسات": "sensors", "كاتم": "muffler", "عادم": "exhaust",
    "كرنك": "crankshaft", "كامة": "camshaft", "جوان": "gasket", "طارة": "steering wheel",
    "دركسون": "steering", "رمان بلي": "wheel bearing", "بلية": "bearing",
    "قطع غيار": "spare parts", "قطعة": "part",
    # ── position / qualifiers ──
    "امامي": "front", "أمامي": "front", "خلفي": "rear", "يمين": "right", "يسار": "left",
    # ── brands / models ──
    "تويوتا": "toyota", "كورولا": "corolla", "كامري": "camry", "يارس": "yaris",
    "هيونداي": "hyundai", "توسان": "tucson", "النترا": "elantra", "اكسنت": "accent",
    "كيا": "kia", "سبورتاج": "sportage", "بيكانتو": "picanto", "ريو": "rio",
    "مازدا": "mazda", "نيسان": "nissan", "قشقاي": "qashqai", "هوندا": "honda",
    "سيفيك": "civic", "مرسيدس": "mercedes", "بي ام دبليو": "bmw", "بيإمدبليو": "bmw",
    "سكودا": "skoda", "اوكتافيا": "octavia", "فولكس": "volkswagen", "جولف": "golf",
    "شيفروليه": "chevrolet", "فورد": "ford", "بيجو": "peugeot", "رينو": "renault",
    "سوزوكي": "suzuki", "ميتسوبيشي": "mitsubishi", "سوبارو": "subaru", "لكزس": "lexus",
}


def _normalize_arabic(text: str) -> str:
    """Fold the Arabic orthographic variants customers actually type: alef forms
    (أ إ آ ٱ -> ا), taa marbuta (ة -> ه), alef maqsura (ى -> ي), and strip harakat."""
    if not text:
        return ""
    out = []
    for ch in text:
        if "\u064b" <= ch <= "\u0652":      # harakat / tanween — drop
            continue
        if ch in "\u0623\u0625\u0622\u0671":
            out.append("\u0627")
        elif ch == "\u0629":
            out.append("\u0647")
        elif ch == "\u0649":
            out.append("\u064a")
        else:
            out.append(ch)
    return "".join(out)


def expand_arabic_query(query: str) -> str:
    """Arabic→English expansion, mirroring expand_hebrew_query. Longest keys first so
    'فلتر زيت' yields 'oil filter' rather than the generic 'filter' + 'oil'."""
    q = (query or "").strip()
    if not q:
        return q
    q_norm = _normalize_arabic(q)
    hits = []
    for ar in sorted(_AR_EXPANSIONS, key=len, reverse=True):
        if _normalize_arabic(ar) in q_norm:
            hits.append(_AR_EXPANSIONS[ar])
    if not hits:
        return q
    # ENGLISH-ONLY on a hit. Unlike Hebrew (name_he really exists in the catalog, so
    # keeping the Hebrew helps), only ONE active part contains Arabic — leaving the
    # Arabic words in would make Meilisearch look for tokens no document has and
    # return nothing, which is exactly why Arabic search matched 0 results.
    # Any non-Arabic tokens the user typed (a model name, a year) are preserved.
    kept = [t for t in q.split() if not any("\u0600" <= ch <= "\u06ff" for ch in t)]
    return " ".join(dict.fromkeys(hits + kept))


def expand_query(query: str) -> str:
    """Single entry point used by search: applies Hebrew AND Arabic expansion, so any of
    the platform's three languages reaches the English catalog."""
    out = expand_hebrew_query(query)
    return expand_arabic_query(out) if out else out


def expand_hebrew_query(query: str) -> str:
    """
    Lightweight Hebrew→English query expansion using a static dictionary.
    Zero API cost, zero latency. Runs before any ML call to improve search recall.
    Example: 'רפידות לקורולה 2019' → 'brake pads corolla 2019'
    """
    q = query.strip()
    expanded_terms = [q]
    q_lower = q.lower()
    for he, en in _HE_EXPANSIONS.items():
        if he in q:
            expanded_terms.append(en)
    return " ".join(dict.fromkeys(expanded_terms))  # deduplicate, preserve order


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
    """Text embedding via Gemini API (384-dim, multilingual He/En/Ar).

    HF router changed sentence-transformer models to SentenceSimilarityPipeline
    which returns similarity scores, not embedding vectors.  Gemini embedding-001
    supports outputDimensionality=384 so vectors match the existing pgvector schema.
    Falls back to empty list — search degrades to text-only, never crashes.
    """
    cache_key = _cache_key("emb", "gemini-embed-001-384", text)
    cached = await _cache_get(cache_key)
    if cached is not None:
        try:
            return _json.loads(cached)
        except Exception:
            pass
    if not GEMINI_API_KEY:
        logger.warning("hf_client [embed] GEMINI_API_KEY not set — returning empty embedding")
        return []
    try:
        t0 = time.monotonic()
        http = _get_http()
        resp = await asyncio.wait_for(
            http.post(
                f"{GEMINI_BASE}/gemini-embedding-001:embedContent?key={GEMINI_API_KEY}",
                headers={"Content-Type": "application/json"},
                json={
                    "content": {"parts": [{"text": text}]},
                    "outputDimensionality": 384,
                },
            ),
            timeout=timeout,
        )
        resp.raise_for_status()
        result: list[float] = resp.json().get("embedding", {}).get("values", [])
        logger.debug("hf_client [embed] latency_ms=%d dims=%d",
                     round((time.monotonic() - t0) * 1000), len(result))
        await _cache_set(cache_key, _json.dumps(result), _EMBED_CACHE_TTL)
        return result
    except Exception as exc:
        logger.warning("hf_client [embed] Gemini embed failed: %s", exc)
        return []

async def groq_vision(
    image_b64: str,
    prompt: str,
    mime: str = "image/jpeg",
    timeout: float = 60.0,
) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set in .env")

    data_url = f"data:{mime};base64,{image_b64}"
    payload = _json.dumps({
        "model": GROQ_VIS_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
        "max_tokens": 1000,
    }, ensure_ascii=False).encode()

    resp = await _post_with_retry(
        f"{GROQ_BASE}/chat/completions",
        {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        payload,
        timeout,
        "groq_vision",
    )
    resp.raise_for_status()
    result: str = resp.json()["choices"][0]["message"]["content"]
    return _clean_response(result)


async def hf_vision(
    image_b64: str,
    prompt: str,
    mime: str = "image/jpeg",
    timeout: float = 60.0,
) -> str:
    if not image_b64:
        raise RuntimeError("hf_vision requires non-empty image base64 payload")

    if GEMINI_API_KEY and not _gemini_cb_is_open():
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

        try:
            resp.raise_for_status()
            result = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            return _clean_response(result)
        except Exception as gemini_err:
            err_str = str(gemini_err)
            logger.warning("hf_vision: Gemini failed (%s) — trying GROQ vision fallback", gemini_err)
            if "429" in err_str or "quota" in err_str.lower() or "limit" in err_str.lower():
                _gemini_cb_trip()
            if GROQ_API_KEY:
                return await groq_vision(
                    image_b64=image_b64,
                    prompt=prompt,
                    mime=mime,
                    timeout=timeout,
                )
            raise

    if _gemini_cb_is_open():
        logger.debug("hf_vision: Gemini circuit breaker open — using GROQ vision fallback")

    if GROQ_API_KEY:
        return await groq_vision(
            image_b64=image_b64,
            prompt=prompt,
            mime=mime,
            timeout=timeout,
        )

    raise RuntimeError("No vision provider available (Gemini unavailable and GROQ_API_KEY missing)")


async def gemini_text(
    prompt: str,
    system: str = "",
    timeout: float = 60.0,
    model: str = "gemini-2.0-flash",
) -> str:
    """
    Creative text generation via Gemini Flash.
    Optimized for Hebrew marketing content — faster and more creative than Qwen.
    Used by NOA for daily social media posts.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set in .env")

    cache_key = _cache_key("gem", model, system, prompt)
    cached = await _cache_get(cache_key)
    if cached is not None:
        logger.debug("gemini_text cache hit")
        return cached

    parts = []
    if system:
        parts.append({"text": f"[System]: {system}\n\n[User]: {prompt}"})
    else:
        parts.append({"text": prompt})

    payload = _json.dumps({
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.9,
            "maxOutputTokens": 1000,
        },
    }, ensure_ascii=False).encode()

    resp = await _post_with_retry(
        f"{GEMINI_BASE}/{model}:generateContent?key={GEMINI_API_KEY}",
        {"Content-Type": "application/json"},
        payload,
        timeout,
        "gemini_text",
    )
    resp.raise_for_status()
    result = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    result = _clean_response(result)
    await _cache_set(cache_key, result, _TEXT_CACHE_TTL)
    return result


async def whatsapp_gemini_text(prompt: str, system: str = "", timeout: float = 60.0) -> str:
    """Gemini call using the dedicated WHATSAPP_GEMINI_API_KEY — isolated from background job quota."""
    model = "gemini-2.0-flash"
    if not WHATSAPP_GEMINI_KEY:
        raise RuntimeError("WHATSAPP_GEMINI_API_KEY not set")
    cache_key = _cache_key("wagem", model, system, prompt)
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached
    parts = []
    if system:
        parts.append({"text": f"[System]: {system}\n\n[User]: {prompt}"})
    else:
        parts.append({"text": prompt})
    payload = _json.dumps({
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1500},
    }, ensure_ascii=False).encode()
    resp = await _post_with_retry(
        f"{GEMINI_BASE}/{model}:generateContent?key={WHATSAPP_GEMINI_KEY}",
        {"Content-Type": "application/json"},
        payload,
        timeout,
        "whatsapp_gemini",
    )
    resp.raise_for_status()
    result = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    result = _clean_response(result)
    await _cache_set(cache_key, result, _TEXT_CACHE_TTL)
    return result


async def groq_text(prompt: str, system: str = "", timeout: float = 60.0, model: str = "") -> str:
    """Chat completion via GROQ API. Used as fallback when Cerebras+Gemini are both 429."""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set in .env")
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    _model = model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    payload = _json.dumps({
        "model": _model,
        "messages": messages,
        "max_tokens": 1000,
    }, ensure_ascii=False).encode()
    resp = await _post_with_retry(
        f"{GROQ_BASE}/chat/completions",
        {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        payload,
        timeout,
        "groq_text",
    )
    resp.raise_for_status()
    result: str = resp.json()["choices"][0]["message"]["content"]
    return _clean_response(result)


# Vocabulary hint for the transcriber (Groq/OpenAI Whisper `prompt`): biases spelling
# toward automotive Hebrew part names + common IL car models WITHOUT forcing a language,
# so English requests still transcribe. Fixes short-term mis-hearings surfaced in testing
# (e.g. מצת "spark plug" → מצאת, קיה ספורטג' → קיאס). Overridable via WHISPER_VOCAB_PROMPT.
_AUDIO_VOCAB_PROMPT = os.getenv(
    "WHISPER_VOCAB_PROMPT",
    "חלקי חילוף לרכב. רפידות בלם, דיסקיות בלם, מצת, מצתים, מסנן שמן, מסנן אוויר, מסנן דלק, "
    "פנס קדמי, פנס אחורי, בולם זעזועים, אלטרנטור, מצמד, משאבת מים, רדיאטור, מדחס מזגן, "
    "מראה צד, זרוע הגה, ווסת לחץ, חיישן, קואיל הצתה, משאבת ABS, תיבת הילוכים. "
    "דגמים: טויוטה קורולה, הונדה סיוויק, מאזדה 3, קיה ספורטג', יונדאי i35, פולקסווגן גולף, "
    "סיטרואן ברלינגו, ב.מ.וו, מספר שלדה VIN.",
)


async def hf_audio(audio_bytes: bytes, timeout: float = 60.0) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set in .env")

    req = httpx.Request(
        "POST",
        f"{GROQ_BASE}/audio/transcriptions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        data={"model": GROQ_AUDIO_MODEL, "prompt": _AUDIO_VOCAB_PROMPT},
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
    result = resp.json().get("text", "")
    return _clean_response(result)


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
        cleaned_cached = _clean_response(cached)
        if cleaned_cached != cached:
            await _cache_set(cache_key, cleaned_cached, _TEXT_CACHE_TTL)
        return cleaned_cached

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
                result = _clean_response(merged.strip() or query)
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
