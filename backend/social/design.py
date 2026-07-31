"""
Script: social/design.py
Purpose: NOA AI design engine — generate branded post images for social media via HF
    text-to-image (FLUX.1-schnell) + PIL brand overlay + S3 content-addressed cache.
    The AI generates a clean automotive background image; text/price is overlaid by
    PIL only — never baked into the AI prompt (truth-only rule: AI has no prices).

Process:
  1. build_prompt(part_name, car)  → clean image prompt (no text, no prices in scene).
  2. generate_post_image(...)      → call hf_image() → bytes. Falls back to PIL-only on fail.
  3. _compose(...)                 → composite: AI image + brand overlay (logo strip,
     optional part-name label, optional price badge from catalog).
  4. Content-addressed upload to S3 thumbs/design/<sha256>.jpg; served via thumbnails proxy.

Rules:
  - NEVER bake a price or supplier name into the AI prompt or the generated image pixels;
    only PIL overlays may show a price, and only when supplied from the canonical
    _customer_price_fields() result (never invented).
  - Falls back gracefully: HF quota/timeout → PIL-only brand canvas (same as qr_media).
  - Uses the same S3 bucket + thumbnails proxy as qr_media and build_part_thumbnails.

Data Imported/Modified: S3 objects under thumbs/design/ only — no DB writes.
Data Sources: HF Inference API (FLUX.1-schnell), PIL (brand canvas fallback).
Last Updated: 2026-07-26
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import re
from typing import Optional

logger = logging.getLogger("social.design")

# ── Constants ──────────────────────────────────────────────────────────────────
W, H = 1080, 1080      # square post (Instagram / Facebook standard)
BRAND_NAME = "AutoSpareFinder"
BRAND_SITE = "autosparefinder.co.il"
_NAVY = (11, 17, 32)
_BLUE = (56, 160, 235)
_LIGHT = (148, 197, 253)

# ── Font helper ───────────────────────────────────────────────────────────────
def _font(size: int):
    from PIL import ImageFont
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _shape_he(text: str) -> str:
    """Reverse Hebrew words for PIL LTR rendering (no python-bidi needed for pure HE)."""
    return " ".join(w[::-1] for w in reversed(text.split()))


# ── Prompt builder ────────────────────────────────────────────────────────────
_BRAND_STYLE = (
    "cinematic automotive photography, studio lighting, clean dark navy background, "
    "professional product shot, sharp focus, 4K, no text, no watermarks, no logos, no labels"
)

_FALLBACK_THEMES = [
    "sleek modern car engine bay, chrome parts, professional automotive photography",
    "close-up of high-performance brake caliper, dark studio background",
    "car suspension and wheel assembly, detailed mechanical photography",
    "modern automotive headlight unit, dramatic side lighting",
    "engine components on dark surface, precision engineering photography",
]


def build_prompt(part_name: str = "", car: str = "") -> str:
    """
    Build a FLUX image prompt from a part name + car.
    Keeps the scene generic enough to be reusable across similar parts.
    Never includes brand names, prices, or text strings.
    """
    part_clean = re.sub(r"\b(is|are|the|a|an|for|of|with|by|from)\b", "", part_name, flags=re.I).strip()
    part_clean = re.sub(r"\s+", " ", part_clean).strip()[:60]
    car_clean = re.sub(r"\d{4}", "", car).strip()[:30]   # drop year for reuse

    parts = []
    if part_clean:
        parts.append(part_clean)
    if car_clean:
        parts.append(f"{car_clean} car")
    subject = ", ".join(parts) if parts else "car spare part, automotive component"

    return f"{subject}, {_BRAND_STYLE}"


# ── PIL brand canvas (CPU-only, always works) ─────────────────────────────────
def _brand_canvas(part_name: str = "", price_label: str = "") -> bytes:
    """Render a branded 1080×1080 canvas with no AI. Used as fallback."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), _NAVY)
    d = ImageDraw.Draw(img)

    # gradient bands
    for y in range(H):
        t = y / H
        r = int(_NAVY[0] + (30 - _NAVY[0]) * t)
        g = int(_NAVY[1] + (40 - _NAVY[1]) * t)
        b = int(_NAVY[2] + (60 - _NAVY[2]) * t)
        d.line([(0, y), (W, y)], fill=(r, g, b))

    # decorative horizontal lines
    for pct, clr, thick in ((0.38, _BLUE, 3), (0.62, _BLUE, 3)):
        yy = int(H * pct)
        d.rectangle([60, yy, W - 60, yy + thick], fill=clr)

    # brand name
    d.text((W // 2, int(H * 0.30)), BRAND_NAME, font=_font(72), fill="white", anchor="mm")
    d.text((W // 2, int(H * 0.38) + 20), BRAND_SITE, font=_font(36), fill=_LIGHT, anchor="mm")

    # part name (Hebrew reversed for PIL)
    if part_name:
        label = _shape_he(part_name) if any("֐" <= c <= "׿" for c in part_name) else part_name
        d.text((W // 2, int(H * 0.55)), label[:50], font=_font(54), fill="white", anchor="mm")

    # price badge
    if price_label:
        bx, by = W // 2 - 160, int(H * 0.67)
        d.rounded_rectangle([bx, by, bx + 320, by + 80], radius=12, fill=_BLUE)
        d.text((W // 2, by + 40), price_label, font=_font(48), fill="white", anchor="mm")

    # slogan footer
    slogan = _shape_he("מצא את החלק המתאים לרכב שלך")
    d.text((W // 2, int(H * 0.82)), slogan, font=_font(38), fill=_LIGHT, anchor="mm")

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88, optimize=True, progressive=True)
    return out.getvalue()


# ── Compose: overlay brand strip on AI-generated image ───────────────────────
def _compose_overlay(ai_bytes: bytes, part_name: str = "", price_label: str = "") -> bytes:
    """Paste a brand strip at the bottom of the AI image. Returns JPEG bytes."""
    from PIL import Image, ImageDraw
    base = Image.open(io.BytesIO(ai_bytes)).convert("RGB")
    # Fit to square
    base = base.resize((W, W), Image.LANCZOS)

    d = ImageDraw.Draw(base)
    strip_h = 140

    # semi-transparent footer strip (navy overlay)
    strip = Image.new("RGBA", (W, strip_h), (*_NAVY, 220))
    base_rgba = base.convert("RGBA")
    base_rgba.paste(strip, (0, H - strip_h), strip)
    base = base_rgba.convert("RGB")
    d = ImageDraw.Draw(base)

    # brand + site
    d.text((24, H - strip_h + 18), BRAND_NAME, font=_font(44), fill="white")
    d.text((24, H - strip_h + 72), BRAND_SITE, font=_font(28), fill=_LIGHT)

    # price badge (right-aligned)
    if price_label:
        px = W - 280
        d.rounded_rectangle([px, H - strip_h + 20, px + 256, H - strip_h + 96],
                             radius=10, fill=_BLUE)
        d.text((px + 128, H - strip_h + 58), price_label, font=_font(40), fill="white", anchor="mm")

    # part name bar (above footer)
    if part_name:
        label = _shape_he(part_name) if any("֐" <= c <= "׿" for c in part_name) else part_name
        label = label[:45]
        bar_y = H - strip_h - 56
        bar = Image.new("RGBA", (W, 56), (0, 0, 0, 140))
        base_rgba2 = base.convert("RGBA")
        base_rgba2.paste(bar, (0, bar_y), bar)
        base = base_rgba2.convert("RGB")
        d2 = ImageDraw.Draw(base)
        d2.text((W // 2, bar_y + 28), label, font=_font(36), fill="white", anchor="mm")

    out = io.BytesIO()
    base.save(out, format="JPEG", quality=88, optimize=True, progressive=True)
    return out.getvalue()


# ── Main public API ───────────────────────────────────────────────────────────
async def generate_post_image(
    part_name: str = "",
    car: str = "",
    price_label: str = "",
    thumbnail_url: Optional[str] = None,
    use_ai: bool = True,
) -> Optional[str]:
    """
    Generate a branded post image for a NOA social post and upload to S3.
    Returns the proxy URL (thumbs/design/<hash>.jpg) or None on failure.

    Priority order:
      1. HF AI image (FLUX.1-schnell) + brand overlay  [use_ai=True, HF configured]
      2. Part thumbnail from catalog + brand overlay    [thumbnail_url provided]
      3. PIL-only brand canvas (always works)           [fallback]

    price_label: canonical price string from _customer_price_fields() e.g. "₪238"
                 — only overlaid by PIL, NEVER passed to the AI prompt.
    """
    try:
        import s3_storage
        if not s3_storage.s3_enabled():
            return None
    except Exception:
        return None

    prompt = build_prompt(part_name, car)
    # Cache key covers all composition inputs so re-runs are free
    cache_input = f"{prompt}|{price_label}|ai={use_ai}"
    cache_key = f"thumbs/design/{hashlib.sha256(cache_input.encode()).hexdigest()}.jpg"

    try:
        import s3_storage
        if s3_storage.object_exists(cache_key):
            return s3_storage.url_for_key(cache_key)
    except Exception:
        pass

    # ── Step 1: Acquire a background image ───────────────────────────────────
    ai_bytes: Optional[bytes] = None

    if use_ai:
        try:
            import hf_client
            ai_bytes = await hf_client.hf_image(prompt, width=1024, height=1024, steps=4, timeout=80.0)
        except Exception as exc:
            logger.warning("design: hf_image failed (%s) — falling back", exc)

    if ai_bytes is None and thumbnail_url:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
                r = await c.get(thumbnail_url)
            if r.status_code == 200 and r.content:
                ai_bytes = r.content
                logger.debug("design: using catalog thumbnail as base")
        except Exception:
            pass

    # ── Step 2: Compose brand overlay ────────────────────────────────────────
    if ai_bytes:
        jpeg = await asyncio.to_thread(_compose_overlay, ai_bytes, part_name, price_label)
    else:
        jpeg = await asyncio.to_thread(_brand_canvas, part_name, price_label)

    # ── Step 3: Upload to S3 ─────────────────────────────────────────────────
    try:
        import s3_storage
        if not s3_storage.object_exists(cache_key):
            await asyncio.to_thread(s3_storage.upload_bytes, cache_key, jpeg, "image/jpeg")
        return s3_storage.url_for_key(cache_key)
    except Exception as exc:
        logger.warning("design: S3 upload failed: %s", exc)
        return None
