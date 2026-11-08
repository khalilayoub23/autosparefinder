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
    build_post_reel(thumb_url, headline, car_name, part_text, src) -> Awaitable[dict|None]
        NOA draft-time hook: 15 s reel (optional SkyReels motion shot + real-photo shot + QR
        end card) stored in S3 as thumbs/video/<sha256>.mp4. SkyReels runs on the owner's
        ZeroGPU Space and is OFF unless SKYREELS_ENABLED=1 (flat parts only, daily cap
        SKYREELS_DAILY_MAX); every failure degrades to the all-photo reel.

Notes:
- Hebrew/Arabic text rendered RTL-correctly via python-bidi + arabic-reshaper.
- Vertical 1080×1920 + ≤60s + #Shorts in the upload title => YouTube treats it as a Short.
- Stitch integration: set NOA_TIKTOK_USE_STITCH=1 to generate AI-designed product cards
  via Google Stitch MCP before animating (Phase 2 — falls back to template when unavailable).
Last Updated: 2026-11-03
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


# ── NOA reel: SkyReels AI motion (guarded) + real-photo shots + QR end card ──────
#
# One post = one part. 15 s, 1080x1920: [motion shot 4.8s] [photo shot 5.6s] [QR end card 5.4s]
# joined by 0.4s crossfades. The motion shot is SkyReels-V2 image-to-video on the owner's
# ZeroGPU Space ONLY for flat parts (SKYREELS_SAFE_PARTS) — measured 2026-11-03: brake pads
# survive, a spark plug turned into another object and an oil filter into a gold mesh, which a
# parts store must never show. Everything else (and every failure) is a slow zoom of the REAL
# photo, so a reel is always produced and never depicts a part that is not the one in the post.

SKYREELS_SAFE_PARTS = ("brake pad", "brake disc", "brake rotor")
SKYREELS_NEGATIVE = ("text change, morphing logo, warped letters, garbled text, overexposed, "
                     "washed out, flicker, blur, extra parts, deformed")
_REEL_FPS = 24
_REEL_SHOTS = (4.8, 5.6, 5.4)   # seconds; minus two 0.4s crossfades = 15.0
_REEL_XFADE = 0.4


def skyreels_enabled() -> bool:
    """Off unless explicitly switched on AND a token exists (a paid GPU quota is behind it)."""
    return os.getenv("SKYREELS_ENABLED", "0") == "1" and bool(os.getenv("HF_TOKEN", "").strip())


def skyreels_allowed_for(part_text: str) -> bool:
    t = (part_text or "").lower()
    return any(k in t for k in SKYREELS_SAFE_PARTS)


async def _skyreels_quota_ok() -> bool:
    """Daily ceiling on Space calls (each is ~5 min of the owner's ZeroGPU quota). Counted per
    ATTEMPT — the quota is spent even when a call fails. Fails CLOSED when Redis is unreachable."""
    try:
        import datetime as _dt
        from BACKEND_API_ROUTES import get_redis  # in-process only (same pattern as noa_ops)
        r = await get_redis()
        key = f"autospare:skyreels:day:{_dt.date.today().isoformat()}"
        n = await r.incr(key)
        await r.expire(key, 172800)
        return n <= int(os.getenv("SKYREELS_DAILY_MAX", "3"))
    except Exception as exc:
        logger.warning("video_gen: skyreels quota check failed (%s) — using photo shot", exc)
        return False


async def skyreels_clip(image_png: bytes, prompt: str) -> "bytes | None":
    """One image-to-video call (544x960, 97 frames, ~4s) on the owner's Space. None on any failure."""
    import httpx
    import json as _json
    base = os.getenv("SKYREELS_SPACE_URL", "https://autospare-autospare-video-test.hf.space").rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ['HF_TOKEN'].strip()}"}
    read_s = float(os.getenv("SKYREELS_TIMEOUT_S", "720"))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=read_s), headers=headers,
                                     follow_redirects=True) as c:
            up = await c.post(f"{base}/gradio_api/upload",
                              files={"files": ("part.png", image_png, "image/png")})
            up.raise_for_status()
            sub = await c.post(f"{base}/gradio_api/call/generate", json={"data": [
                {"path": up.json()[0], "meta": {"_type": "gradio.FileData"}},
                prompt, SKYREELS_NEGATIVE, 5.0, 97]})
            sub.raise_for_status()
            event, data = None, None
            async with c.stream("GET", f"{base}/gradio_api/call/generate/{sub.json()['event_id']}") as r:
                async for line in r.aiter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:") and event in ("complete", "error"):
                        if event == "error":
                            logger.warning("video_gen: skyreels space error: %s", line[5:200])
                            return None
                        data = _json.loads(line[5:])
                        break
            if not data:
                return None
            vid = data[0]["video"] if isinstance(data[0], dict) else data[0]
            # NOT vid["url"]: behind the HF proxy Gradio reports it with an extra "/gr/" root, which
            # redirects to the Space home page (HTML) — build the file URL from the path instead.
            v = await c.get(f"{base}/gradio_api/file={vid['path']}")
            v.raise_for_status()
            if v.content[4:8] != b"ftyp":      # an MP4 starts with an "ftyp" box
                logger.warning("video_gen: skyreels returned %d bytes that are not an MP4", len(v.content))
                return None
            return v.content
    except Exception as exc:
        logger.warning("video_gen: skyreels call failed (%s) — using photo shot", exc)
        return None


def _reel_photo_canvas(image_bytes: bytes) -> bytes:
    """Part photo centred on a white 1080x1920 canvas — the whole part stays in frame."""
    from PIL import Image, ImageOps
    photo = ImageOps.contain(Image.open(io.BytesIO(image_bytes)).convert("RGB"), (W, 1500), Image.LANCZOS)
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    canvas.paste(photo, ((W - photo.width) // 2, (H - photo.height) // 2 - 60))
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def _reel_caption_overlay(headline: str, car_name: str) -> bytes:
    """Transparent 1080x1920 PNG: caption box near the bottom (RTL-safe)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lines = [(_rtl(t), _font(size)) for t, size in
             [(ln, 60) for ln in _wrap(headline or BRAND, 22)[:2]] + ([(car_name, 44)] if car_name else [])]
    heights = [f.size + 18 for _, f in lines]
    top = 1760 - sum(heights)
    d.rectangle([60, top - 28, W - 60, 1760 + 20], fill=(*NAVY, 190))
    y = top
    for (txt, font), h in zip(lines, heights):
        d.text((W // 2, y + h // 2), txt, font=font, fill=WHITE, anchor="mm")
        y += h
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _reel_end_card(qr_png: "bytes | None") -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    d.text((W // 2, 420), BRAND, font=_font(88), fill=WHITE, anchor="mm")
    d.line([(W // 2 - 220, 500), (W // 2 + 220, 500)], fill=ACCENT, width=4)
    if qr_png:
        qr = Image.open(io.BytesIO(qr_png)).convert("RGB").resize((520, 520), Image.NEAREST)
        badge = Image.new("RGB", (580, 580), WHITE)
        badge.paste(qr, (30, 30))
        img.paste(badge, ((W - 580) // 2, 640))
    try:
        from social.qr_media import SCAN_CAPTION
        d.text((W // 2, 1340), _rtl(SCAN_CAPTION), font=_font(44, bold=False), fill=LIGHT, anchor="mm")
    except Exception:
        pass
    d.text((W // 2, 1460), SITE, font=_font(56), fill=LIGHT, anchor="mm")
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _ff(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, capture_output=True, timeout=240)


def _compose_reel(out_path: str, canvas_png: bytes, overlay_png: bytes, end_png: bytes,
                  ai_clip: "bytes | None") -> str:
    """Pure ffmpeg assembly (sync, CPU). ai_clip=None -> the first shot is a photo zoom."""
    s1, s2, s3 = _REEL_SHOTS
    zoom_in = ("scale=2160:3840:flags=lanczos,zoompan=z='min(1.0+0.0009*on,1.18)':"
               "x='iw/2-(iw/zoom/2)':y='ih*0.42-(ih/zoom/2)':d=1:s=1080x1920:fps=24")
    zoom_out = ("scale=2160:3840:flags=lanczos,zoompan=z='max(1.18-0.0009*on,1.0)':"
                "x='iw/2-(iw/zoom/2)':y='ih*0.42-(ih/zoom/2)':d=1:s=1080x1920:fps=24")
    with tempfile.TemporaryDirectory(prefix="reel_") as td:
        p = lambda n: os.path.join(td, n)  # noqa: E731
        for name, blob in (("canvas.png", canvas_png), ("overlay.png", overlay_png), ("end.png", end_png)):
            with open(p(name), "wb") as f:
                f.write(blob)
        if ai_clip:
            with open(p("ai.mp4"), "wb") as f:
                f.write(ai_clip)
            _ff(["-i", p("ai.mp4"), "-i", p("overlay.png"), "-filter_complex",
                 f"[0:v]scale=1080:1920:flags=lanczos,tpad=stop_mode=clone:stop_duration=2,trim=duration={s1},"
                 f"setpts=PTS-STARTPTS,fps={_REEL_FPS}[v];[v][1:v]overlay=0:0,format=yuv420p",
                 "-c:v", "libx264", "-crf", "18", p("s1.mp4")])
        else:
            _ff(["-loop", "1", "-t", str(s1), "-r", str(_REEL_FPS), "-i", p("canvas.png"),
                 "-i", p("overlay.png"), "-filter_complex",
                 f"[0:v]{zoom_in}[v];[v][1:v]overlay=0:0,format=yuv420p", "-c:v", "libx264", "-crf", "18", p("s1.mp4")])
        _ff(["-loop", "1", "-t", str(s2), "-r", str(_REEL_FPS), "-i", p("canvas.png"), "-i", p("overlay.png"),
             "-filter_complex", f"[0:v]{zoom_out}[v];[v][1:v]overlay=0:0,format=yuv420p",
             "-c:v", "libx264", "-crf", "18", p("s2.mp4")])
        _ff(["-loop", "1", "-t", str(s3), "-r", str(_REEL_FPS), "-i", p("end.png"),
             "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "18", p("s3.mp4")])
        o1 = s1 - _REEL_XFADE
        o2 = o1 + s2 - _REEL_XFADE
        _ff(["-i", p("s1.mp4"), "-i", p("s2.mp4"), "-i", p("s3.mp4"),
             "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
             "-filter_complex",
             f"[0:v][1:v]xfade=transition=fade:duration={_REEL_XFADE}:offset={o1:.2f}[a];"
             f"[a][2:v]xfade=transition=fade:duration={_REEL_XFADE}:offset={o2:.2f}[v]",
             "-map", "[v]", "-map", "3:a", "-t", "15", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-shortest",
             "-movflags", "+faststart", out_path])
    return out_path


async def make_noa_reel(
    out_path: str,
    image_bytes: bytes,
    headline: str,
    car_name: str = "",
    part_text: str = "",
    qr_src: str = "tiktok",
) -> dict:
    """15 s reel for ONE part. Returns {"path": out_path, "ai": bool}; raises only if even the
    all-photo composition fails (callers then fall back to the template video)."""
    canvas = await asyncio.to_thread(_reel_photo_canvas, image_bytes)
    overlay = await asyncio.to_thread(_reel_caption_overlay, headline, car_name)
    try:
        from social.qr_media import build_qr_png
        qr_png = await asyncio.to_thread(build_qr_png, qr_src)
    except Exception as exc:
        logger.debug("video_gen: reel QR failed: %s", exc)
        qr_png = None
    end = await asyncio.to_thread(_reel_end_card, qr_png)

    ai_clip = None
    if skyreels_enabled() and skyreels_allowed_for(part_text) and await _skyreels_quota_ok():
        from PIL import Image
        small = io.BytesIO()
        Image.open(io.BytesIO(canvas)).resize((544, 960), Image.LANCZOS).save(small, format="PNG")
        prompt = (f"{part_text} on a clean white studio floor, slow smooth camera push-in with a gentle orbit, "
                  "soft studio light and subtle reflections, product stays sharp and unchanged")
        ai_clip = await skyreels_clip(small.getvalue(), prompt)
    try:
        await asyncio.to_thread(_compose_reel, out_path, canvas, overlay, end, ai_clip)
    except subprocess.CalledProcessError as exc:
        if not ai_clip:
            raise
        logger.warning("video_gen: motion shot unusable (%s) — all-photo reel",
                       (exc.stderr or b"")[-200:].decode("utf-8", "replace"))
        ai_clip = None
        await asyncio.to_thread(_compose_reel, out_path, canvas, overlay, end, None)
    return {"path": out_path, "ai": bool(ai_clip)}


async def build_post_reel(thumb_url: "str | None", headline: str, car_name: str, part_text: str,
                          src: str) -> "dict | None":
    """Draft-time hook for NOA: fetch the clean part photo, compose the reel, store it in S3.
    Returns {"video_key", "video_url", "ai"} or None (the post then ships without a stored reel,
    exactly as before). Never raises."""
    if not thumb_url:
        return None
    out = None
    try:
        import hashlib
        import httpx
        import s3_storage
        if not s3_storage.s3_enabled():
            return None
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as c:
            r = await c.get(thumb_url)
        if r.status_code != 200 or not r.content:
            return None
        out = tempfile.mktemp(suffix=".mp4")
        info = await make_noa_reel(out, r.content, headline, car_name, part_text, src)
        with open(out, "rb") as f:
            blob = f.read()
        key = f"thumbs/video/{hashlib.sha256(blob).hexdigest()}.mp4"
        if not s3_storage.object_exists(key):
            if not await asyncio.to_thread(s3_storage.upload_bytes, key, blob, "video/mp4"):
                return None
        return {"video_key": key, "video_url": s3_storage.url_for_key(key), "ai": info["ai"]}
    except Exception as exc:
        logger.warning("video_gen: build_post_reel failed (%s) — post ships without reel", exc)
        return None
    finally:
        if out:
            try:
                os.remove(out)
            except Exception:
                pass


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
