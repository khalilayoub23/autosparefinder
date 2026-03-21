import os
from typing import Any

import httpx

HF_TOKEN = os.getenv("HF_TOKEN", "")
HF_API_URL = "https://api-inference.huggingface.co/models"

# Model constants
HF_TEXT_MODEL = os.getenv("HF_TEXT_MODEL", "Qwen/Qwen2.5-72B-Instruct")
HF_VISION_MODEL = os.getenv("HF_VISION_MODEL", "Qwen/Qwen2-VL-7B-Instruct")
HF_EMBED_MODEL = os.getenv(
    "HF_EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
)
HF_AUDIO_MODEL = os.getenv("HF_AUDIO_MODEL", "openai/whisper-large-v3")
HF_CLIP_MODEL = os.getenv("HF_CLIP_MODEL", "openai/clip-vit-large-patch14")


def _headers() -> dict[str, str]:
    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN not set in .env")
    return {"Authorization": f"Bearer {HF_TOKEN}"}


async def hf_text(prompt: str, system: str = "", timeout: float = 30.0) -> str:
    """Call HF text model - replaces Ollama chat completions."""
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{HF_API_URL}/{HF_TEXT_MODEL}/v1/chat/completions",
            headers=_headers(),
            json={
                "messages": messages,
                "max_tokens": 1000,
                "stream": False,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def hf_embed(text: str, timeout: float = 10.0) -> list[float]:
    """Call HF embedding model - replaces Ollama nomic-embed-text."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{HF_API_URL}/{HF_EMBED_MODEL}",
            headers=_headers(),
            json={"inputs": text},
        )
        resp.raise_for_status()
        data: Any = resp.json()
        if isinstance(data, list) and data and isinstance(data[0], list):
            return data[0]
        return data


async def hf_vision(
    image_b64: str,
    prompt: str,
    mime: str = "image/jpeg",
    timeout: float = 30.0,
) -> str:
    """Call HF vision model - replaces Ollama vision."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{HF_API_URL}/{HF_VISION_MODEL}/v1/chat/completions",
            headers=_headers(),
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                            },
                        ],
                    }
                ],
                "max_tokens": 500,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def hf_audio(audio_bytes: bytes, timeout: float = 60.0) -> str:
    """Call HF Whisper - replaces Ollama whisper."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{HF_API_URL}/{HF_AUDIO_MODEL}",
            headers={**_headers(), "Content-Type": "audio/webm"},
            content=audio_bytes,
        )
        resp.raise_for_status()
        return resp.json().get("text", "")


async def hf_clip(image_b64: str, timeout: float = 15.0) -> list[float]:
    """Call HF CLIP - replaces Ollama clip."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{HF_API_URL}/{HF_CLIP_MODEL}",
            headers=_headers(),
            json={"inputs": {"image": image_b64}},
        )
        resp.raise_for_status()
        return resp.json()[0]
