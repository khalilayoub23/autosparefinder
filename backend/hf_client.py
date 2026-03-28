import os
import json as _json
from typing import Any

import httpx

HF_TOKEN = os.getenv("HF_TOKEN", "")

HF_TEXT_MODEL   = os.getenv("HF_TEXT_MODEL",   "moonshotai/Kimi-K2-Instruct-0905")
HF_VISION_MODEL = os.getenv("HF_VISION_MODEL", "moonshotai/Kimi-K2-Instruct-0905")
HF_EMBED_MODEL  = os.getenv("HF_EMBED_MODEL",  "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
HF_AUDIO_MODEL  = os.getenv("HF_AUDIO_MODEL",  "openai/whisper-large-v3")
HF_CLIP_MODEL   = os.getenv("HF_CLIP_MODEL",   "openai/clip-vit-large-patch14")

ROUTER_BASE = "https://router.huggingface.co/v1"


def _headers() -> dict[str, str]:
    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN not set in .env")
    return {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
    }


async def hf_text(prompt: str, system: str = "", timeout: float = 60.0) -> str:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": HF_TEXT_MODEL,
        "messages": messages,
        "max_tokens": 1000,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{ROUTER_BASE}/chat/completions",
            headers=_headers(),
            content=_json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# Lazy-loaded local embedding model
_embed_model = None


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer(HF_EMBED_MODEL)
    return _embed_model


async def hf_embed(text: str, timeout: float = 10.0) -> list[float]:
    """Local embedding - free, no API call needed."""
    import asyncio
    loop = asyncio.get_event_loop()
    model = _get_embed_model()
    embedding = await loop.run_in_executor(None, lambda: model.encode(text).tolist())
    return embedding


async def hf_vision(
    image_b64: str,
    prompt: str,
    mime: str = "image/jpeg",
    timeout: float = 30.0,
) -> str:
    payload = {
        "model": HF_VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                ],
            }
        ],
        "max_tokens": 500,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{ROUTER_BASE}/chat/completions",
            headers=_headers(),
            content=_json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def hf_audio(audio_bytes: bytes, timeout: float = 60.0) -> str:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"https://router.huggingface.co/hf-inference/models/{HF_AUDIO_MODEL}",
            headers={"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "audio/webm"},
            content=audio_bytes,
        )
        resp.raise_for_status()
        return resp.json().get("text", "")


async def hf_clip(image_b64: str, timeout: float = 15.0) -> list[float]:
    payload = {"inputs": {"image": image_b64}}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"https://router.huggingface.co/hf-inference/models/{HF_CLIP_MODEL}",
            headers={"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "application/json"},
            content=_json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        resp.raise_for_status()
        return resp.json()[0]
