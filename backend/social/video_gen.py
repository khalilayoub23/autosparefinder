"""
social/video_gen.py — NOA short-video generator (CPU-only, ffmpeg + PIL).

The server has no GPU, so short promo videos are COMPOSED, not AI-generated: a branded
poster (PIL) → a 15s vertical (1080×1920) MP4 with a slow zoom + fades + silent audio
track (platforms reject video with no audio stream).

Public API:
    make_product_short(out_path, caption, headline, price_ils, car_name,
                       image_bytes, qr_src, seconds) -> path
    make_coming_soon_short(out_path, headline, subline, cta, seconds) -> path
    make_stitch_product_video(out_path, ...) -> Awaitable[path]   (Phase 2 — Stitch AI card)

Notes:
- Hebrew/Arabic text rendered RTL-correctly via python-bidi + arabic-reshaper.
- Vertical 1080×1920 + ≤60s + #Shorts in the upload title => YouTube treats it as a Short.
- Stitch integration: set NOA_TIKTOK_USE_STITCH=1 to generate AI-designed product cards
  via Google Stitch MCP before animating (Phase 2 — falls back to template when unavailable).
Last Updated: 2026-08-13
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import re
import subprocess
import tempfile
from typing import Optional

logger = logging.getLogger("video_gen")

W, H = 1080, 1920          # TikTok/Reels/Shorts vertical
BRAND = "AutoSpareFinder"
SITE = "autosparefinder.co.il"
ACCENT  = (14, 165, 233)   # brand sky-blue (#0EA5E9)
NAVY    = (11, 18, 24)     # base background
CARD_BG = (21, 27, 39)     # card surface
LIGHT   = (148, 197, 253)
WHITE   = (255, 255, 255)


# ── font helpers ─────────────────────────────────────────────────────────────

def _font(size: int, bold: bool = True):
    from PIL import ImageFont
    candidates = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
        if bold else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    )
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _rtl(text: str) -> str:
    """Return visually-correct RTL string for PIL (which renders LTR only)."""
    try:
        from bidi.algorithm import get_display
        try:
            from arabic_reshaper import reshape
            text = reshape(text)
        except Exception:
            pass
        return get_display(text)
    except Exception:
        return text


def _wrap(text: str, max_chars: int = 22) -> list[str]:
    """Word-wrap text into lines of at most max_chars characters."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines or [text]


# ── poster builders ───────────────────────────────────────────────────────────

def _gradient(draw, y0: int, y1: int):
    """Paint a vertical gradient band on draw."""
    for y in range(y0, y1):
        t = (y - y0) / max(y1 - y0, 1)
        r = int(NAVY[0] + (CARD_BG[0] - NAVY[0]) * t)
        g = int(NAVY[1] + (CARD_BG[1] - NAVY[1]) * t)
        b = int(NAVY[2] + (CARD_BG[2] - NAVY[2]) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))


def _product_poster(
    headline: str,
    price_ils: float = 0,
    car_name: str = "",
    oem: str = "",
    image_bytes: bytes | None = None,
    qr_png: bytes | None = None,
) -> str:
    """Render a product-showcase vertical poster PNG; return its tmp path."""
    from PIL import Image, ImageDraw, ImageOps

    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    _gradient(d, 0, H)

    # ── top brand bar ────────────────────────────────────────────────────────
    d.text((W // 2, 120), BRAND, font=_font(56), fill=WHITE, anchor="mm")
    d.line([(W // 2 - 200, 170), (W // 2 + 200, 170)], fill=ACCENT, width=3)

    # ── product image (if provided) ──────────────────────────────────────────
    img_zone_top = 220
    img_zone_h   = 560
    if image_bytes:
        try:
            prod = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
            prod = ImageOps.fit(prod, (820, img_zone_h), method=Image.LANCZOS)
            bg = Image.new("RGB", (860, img_zone_h + 40), CARD_BG)
            bg.paste(prod, (20, 20), prod if prod.mode == "RGBA" else None)
            img.paste(bg, ((W - 860) // 2, img_zone_top))
        except Exception as exc:
            logger.debug("video_gen: product image failed: %s", exc)
            image_bytes = None  # fall through to text-only layout

    text_top = img_zone_top + img_zone_h + 60 if image_bytes else 300

    # ── headline (Hebrew/Arabic RTL-safe) ────────────────────────────────────
    hl_lines = _wrap(headline, 20)
    y = text_top
    for ln in hl_lines[:3]:
        d.text((W // 2, y), _rtl(ln), font=_font(88 if len(ln) < 14 else 72),
               fill=WHITE, anchor="mm")
        y += 110

    # ── price chip ───────────────────────────────────────────────────────────
    if price_ils > 0:
        price_str = f"₪{price_ils:,.0f}"
        y += 20
        chip_w, chip_h = 420, 100
        chip_x = (W - chip_w) // 2
        d.rounded_rectangle([chip_x, y, chip_x + chip_w, y + chip_h],
                             radius=50, fill=ACCENT)
        d.text((W // 2, y + chip_h // 2), price_str,
               font=_font(58), fill=WHITE, anchor="mm")
        y += chip_h + 30

    # ── car fitment ──────────────────────────────────────────────────────────
    if car_name:
        y += 10
        d.text((W // 2, y), _rtl(car_name), font=_font(44, bold=False),
               fill=LIGHT, anchor="mm")
        y += 60

    # ── OEM number ───────────────────────────────────────────────────────────
    if oem:
        d.text((W // 2, y), oem, font=_font(36, bold=False),
               fill=(100, 120, 150), anchor="mm")

    # ── QR code bottom-right ─────────────────────────────────────────────────
    if qr_png:
        try:
            qr = Image.open(io.BytesIO(qr_png)).convert("RGB")
            qr = qr.resize((260, 260), Image.LANCZOS)
            # white badge
            badge = Image.new("RGB", (300, 300), WHITE)
            badge.paste(qr, (20, 20))
            img.paste(badge, (W - 320, H - 420))
            d.text((W - 170, H - 100), "סרוק לחנות", font=_font(32, bold=False),
                   fill=LIGHT, anchor="mm")
        except Exception as exc:
            logger.debug("video_gen: QR paste failed: %s", exc)

    # ── site URL ─────────────────────────────────────────────────────────────
    d.text((W // 2, H - 80), SITE, font=_font(38, bold=False),
           fill=ACCENT, anchor="mm")

    p = tempfile.mktemp(suffix=".png")
    img.save(p)
    return p


def _simple_poster(headline: str, subline: str, cta: str) -> str:
    """Render a simple branded vertical poster PNG; return its path."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    _gradient(d, 0, H)
    d.text((W // 2, 250), BRAND, font=_font(64), fill=WHITE, anchor="mm")
    d.line([(W // 2 - 220, 315), (W // 2 + 220, 315)], fill=ACCENT, width=4)
    hl_lines = _wrap(headline, 12)
    y = H // 2 - len(hl_lines) * 75
    for ln in hl_lines[:3]:
        d.text((W // 2, y), _rtl(ln), font=_font(120), fill=WHITE, anchor="mm")
        y += 150
    d.text((W // 2, y + 40), _rtl(subline), font=_font(46, bold=False),
           fill=LIGHT, anchor="mm")
    d.rounded_rectangle([W // 2 - 300, H - 470, W // 2 + 300, H - 380],
                        radius=45, fill=CARD_BG, outline=ACCENT, width=3)
    d.text((W // 2, H - 425), cta, font=_font(40, bold=False),
           fill=WHITE, anchor="mm")
    d.text((W // 2, H - 150), SITE, font=_font(44, bold=False),
           fill=ACCENT, anchor="mm")
    p = tempfile.mktemp(suffix=".png")
    img.save(p)
    return p


# ── video encoder ─────────────────────────────────────────────────────────────

def _encode(poster_path: str, out_path: str, seconds: int = 15) -> str:
    """ffmpeg: still image → slow-zoom vertical MP4 with silent audio."""
    frames = seconds * 30
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"zoompan=z='min(zoom+0.0007,1.18)':d={frames}:s={W}x{H}:fps=30,"
        f"fade=t=in:st=0:d=0.6,fade=t=out:st={seconds - 0.9:.2f}:d=0.8,format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", poster_path,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(seconds), "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart",
        out_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    finally:
        try:
            os.remove(poster_path)
        except Exception:
            pass
    return out_path


# ── public API ────────────────────────────────────────────────────────────────

def make_product_short(
    out_path: str,
    caption: str = "",
    headline: str = "",
    price_ils: float = 0,
    car_name: str = "",
    oem: str = "",
    image_bytes: bytes | None = None,
    qr_src: str = "tiktok",
    seconds: int = 15,
) -> str:
    """
    Compose a branded product-showcase TikTok/Reels MP4.

    - headline: product name (Hebrew/Arabic/English — RTL-rendered correctly)
    - price_ils: shown as ₪ chip; omitted when 0
    - car_name: fitment line (e.g. "Toyota Corolla 2017")
    - image_bytes: product thumbnail PNG/JPEG bytes (from S3); optional
    - qr_src: attribution tag for the QR hub link (e.g. "tiktok", "instagram")
    - seconds: video length (default 15 for TikTok/Reels)

    Returns out_path.
    """
    if not headline and caption:
        # Extract first non-empty non-hashtag line as headline
        for ln in caption.splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                headline = ln[:60]
                break
    headline = headline or BRAND

    # Build QR PNG
    qr_png: bytes | None = None
    try:
        from social.qr_media import build_qr_png
        qr_png = build_qr_png(qr_src)
    except Exception as exc:
        logger.debug("video_gen: QR build failed: %s", exc)

    poster = _product_poster(
        headline=headline,
        price_ils=price_ils,
        car_name=car_name,
        oem=oem,
        image_bytes=image_bytes,
        qr_png=qr_png,
    )
    return _encode(poster, out_path, seconds)


async def make_stitch_product_video(
    out_path: str,
    headline: str,
    caption: str = "",
    price_ils: float = 0,
    car_name: str = "",
    oem: str = "",
    image_bytes: bytes | None = None,
    qr_src: str = "tiktok",
    seconds: int = 15,
) -> str:
    """
    Phase 2 — AI-designed product card via Google Stitch MCP → animated MP4.

    When NOA_TIKTOK_USE_STITCH=1 is set, this calls the Stitch API to generate
    a polished product card screen, then animates it with ffmpeg.
    Falls back to make_product_short() if Stitch is unavailable.
    """
    use_stitch = os.getenv("NOA_TIKTOK_USE_STITCH", "0") == "1"
    stitch_key = os.getenv("STITCH_API_KEY", "")

    if use_stitch and stitch_key:
        try:
            stitch_card = await _stitch_generate_card(
                headline=headline,
                price_ils=price_ils,
                car_name=car_name,
                stitch_key=stitch_key,
            )
            if stitch_card:
                logger.info("video_gen: using Stitch-generated card for '%s'", headline)
                qr_png: bytes | None = None
                try:
                    from social.qr_media import build_qr_png
                    qr_png = build_qr_png(qr_src)
                except Exception:
                    pass
                # Stitch card IS the image for the video
                poster = _product_poster(
                    headline=headline, price_ils=0,  # Stitch card already has price
                    car_name="", oem="",
                    image_bytes=stitch_card, qr_png=qr_png,
                )
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, _encode, poster, out_path, seconds)
        except Exception as exc:
            logger.warning("video_gen: Stitch failed (%s), falling back to template", exc)

    # Fallback — run sync video_gen in executor so we don't block the event loop
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: make_product_short(
            out_path, caption=caption, headline=headline,
            price_ils=price_ils, car_name=car_name, oem=oem,
            image_bytes=image_bytes, qr_src=qr_src, seconds=seconds,
        )
    )


async def _stitch_generate_card(
    headline: str,
    price_ils: float,
    car_name: str,
    stitch_key: str,
) -> bytes | None:
    """
    Call Google Stitch generate_screen_from_text and return the rendered card as PNG bytes.
    Returns None if unavailable (caller falls back to template).
    """
    import urllib.request, json as _json
    prompt = (
        f"Dark automotive product card, vertical (1080x1920). "
        f"Product: {headline}. "
        + (f"Price: ₪{price_ils:,.0f}. " if price_ils else "")
        + (f"Fits: {car_name}. " if car_name else "")
        + "Brand: AutoSpareFinder. Dark navy background #0F1218, accent blue #0EA5E9. "
        "No white backgrounds. Bold Hebrew + English text. Minimalist automotive style."
    )
    # Stitch MCP REST — generate_screen_from_text tool via HTTP transport
    payload = _json.dumps({
        "method": "tools/call",
        "params": {
            "name": "generate_screen_from_text",
            "arguments": {"description": prompt, "width": 1080, "height": 1920}
        }
    }).encode()
    req = urllib.request.Request(
        "https://stitch.googleapis.com/mcp",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": stitch_key,
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = _json.loads(resp.read())
        # Extract image bytes from Stitch response
        content = data.get("result", {}).get("content", [])
        for item in content:
            if item.get("type") == "image" and item.get("data"):
                import base64
                return base64.b64decode(item["data"])
        logger.debug("video_gen: Stitch returned no image content")
        return None
    except Exception as exc:
        logger.debug("video_gen: Stitch API error: %s", exc)
        return None


def make_coming_soon_short(
    out_path: str,
    headline: str = "COMING SOON",
    subline: str = "Find the right car part. Fast.",
    cta: str = "Search by plate • autosparefinder.co.il",
    seconds: int = 15,
) -> str:
    """Compose a simple branded vertical MP4 (slow zoom + fades + silent audio). Returns out_path."""
    poster = _simple_poster(headline, subline, cta)
    return _encode(poster, out_path, seconds)


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "product"
    if mode == "product":
        out = make_product_short(
            "/tmp/product_short.mp4",
            headline="מסנן שמן טויוטה",
            price_ils=307,
            car_name="Toyota Corolla 2017",
            oem="90915-YZZF2",
        )
    else:
        out = make_coming_soon_short("/tmp/coming_soon.mp4")
    print("made", out, os.path.getsize(out), "bytes")
