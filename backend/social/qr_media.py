"""
Script: social/qr_media.py
Purpose: QR-code media for NOA social posts (goal G8, 2026-07-20). Every post carries a
         scannable QR that lands on the channel-picker hub page (/api/v1/go) where the
         user chooses WhatsApp / Telegram / Website / Facebook / Instagram themselves.
         This REPLACES the old 5-link text footer that made every post look identical.
Process:
  1. hub_url(src)          → hub link with a src attribution tag (e.g. qr_tiktok_w29).
  2. build_qr_png(src)     → raw PNG bytes of the QR (qrcode lib, high error correction).
  3. build_post_media(thumb_url, src) → 1080×1080 JPEG: part thumbnail (or clean brand
     canvas when no thumbnail exists) + white QR badge bottom-right + Hebrew scan strip.
     Uploaded content-addressed to the part-thumbnails bucket (thumbs/qr/<sha256>.jpg)
     and served through the existing /api/v1/thumbnails/ proxy (thumbs/ prefix allowed).
Data Imported/Modified: S3 objects under thumbs/qr/ only — no DB writes.
Data Sources: our own thumbnail bucket URLs (THUMB_PUBLIC_BASE) — never supplier images.
Missing Data Delegation: any failure returns the original thumb_url (possibly None) so
                         the caller can still publish text-only; never raises.
Last Updated: 2026-07-20
"""
import asyncio
import hashlib
import io
import os
import re

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://autosparefinder.co.il").rstrip("/")

CANVAS = 1080
QR_BADGE = 300          # white badge square, QR drawn inside with padding
STRIP_H = 96            # bottom caption strip
SCAN_CAPTION = "סרקו את הקוד — ובחרו איפה נוח לכם לדבר איתנו"


def _clean_src(src: str) -> str:
    return re.sub(r"[^a-z0-9_\-]", "", (src or "qr").lower())[:40]


def hub_url(src: str) -> str:
    return f"{FRONTEND_URL}/api/v1/go?src={_clean_src(src)}"


def build_qr_png(src: str) -> bytes:
    """PNG bytes of a QR pointing at the channel-picker hub. Sync (CPU-bound)."""
    import qrcode
    from qrcode.constants import ERROR_CORRECT_Q
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_Q, box_size=10, border=2)
    qr.add_data(hub_url(src))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _find_font(size: int):
    from PIL import ImageFont
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _shape_hebrew(text: str) -> str:
    # PIL draws LTR; reversing the whole RTL string renders readable Hebrew.
    # Words are space-separated so reversing word order + each word works for pure-Hebrew.
    return " ".join(w[::-1] for w in reversed(text.split(" ")))


def _compose(thumb_bytes: "bytes | None", src: str) -> bytes:
    """Sync compose: base image (thumbnail or brand canvas) + QR badge + caption strip."""
    from PIL import Image, ImageDraw

    canvas = Image.new("RGB", (CANVAS, CANVAS), "white")
    if thumb_bytes:
        try:
            part = Image.open(io.BytesIO(thumb_bytes)).convert("RGB")
            part.thumbnail((CANVAS, CANVAS - STRIP_H), Image.LANCZOS)
            canvas.paste(part, ((CANVAS - part.width) // 2, (CANVAS - STRIP_H - part.height) // 2))
        except Exception:
            thumb_bytes = None
    if not thumb_bytes:
        draw0 = ImageDraw.Draw(canvas)
        draw0.rectangle([0, 0, CANVAS, CANVAS], fill=(15, 23, 42))
        brand_font = _find_font(72)
        draw0.text((CANVAS // 2, CANVAS // 2 - 120), "AutoSpareFinder",
                   font=brand_font, fill="white", anchor="mm")
        sub_font = _find_font(44)
        draw0.text((CANVAS // 2, CANVAS // 2 - 30), _shape_hebrew("חלקי חילוף לרכב — לפי מספר רישוי"),
                   font=sub_font, fill=(148, 197, 253), anchor="mm")

    # QR badge bottom-right (above the strip)
    qr_img_bytes = build_qr_png(src)
    from PIL import Image as _I
    qr_img = _I.open(io.BytesIO(qr_img_bytes)).convert("RGB").resize((QR_BADGE - 24, QR_BADGE - 24))
    badge = Image.new("RGB", (QR_BADGE, QR_BADGE), "white")
    badge.paste(qr_img, (12, 12))
    canvas.paste(badge, (CANVAS - QR_BADGE - 24, CANVAS - STRIP_H - QR_BADGE - 24))

    # Caption strip
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, CANVAS - STRIP_H, CANVAS, CANVAS], fill=(15, 23, 42))
    font = _find_font(40)
    draw.text((CANVAS // 2, CANVAS - STRIP_H // 2), _shape_hebrew(SCAN_CAPTION),
              font=font, fill="white", anchor="mm")

    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=85, optimize=True, progressive=True)
    return out.getvalue()


async def build_post_media(thumb_url: "str | None", src: str) -> "str | None":
    """Compose thumbnail+QR (or brand-canvas+QR), upload to S3, return the public URL.
    On ANY failure returns thumb_url unchanged (caller-safe)."""
    try:
        import s3_storage
        if not s3_storage.s3_enabled():
            return thumb_url

        thumb_bytes = None
        if thumb_url:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as c:
                    r = await c.get(thumb_url)
                if r.status_code == 200 and r.content:
                    thumb_bytes = r.content
            except Exception:
                thumb_bytes = None

        jpeg = await asyncio.to_thread(_compose, thumb_bytes, src)
        key = f"thumbs/qr/{hashlib.sha256(jpeg).hexdigest()}.jpg"
        if not s3_storage.object_exists(key):
            if not await asyncio.to_thread(s3_storage.upload_bytes, key, jpeg, "image/jpeg"):
                return thumb_url
        return s3_storage.url_for_key(key)
    except Exception as exc:
        print(f"[qr_media] build_post_media failed: {exc}")
        return thumb_url
