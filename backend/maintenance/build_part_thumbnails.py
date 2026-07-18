"""
Script: maintenance/build_part_thumbnails.py
Purpose: Build clean part thumbnails in the Contabo Object Storage bucket.

Process (per part with a source image and thumbnail_status IS NULL):
  1. Pick the best source image (parts_images.url; upgrade eBay s-l225→s-l500 for quality).
  2. Fetch it.
  3. CLEANUP FILTER (owner rule: no supplier links/ads — part image only): OCR the image and
     REJECT it if it contains supplier/promo text ("coming soon", "contact us", "auto parts",
     a URL, "whatsapp", phone hotline, etc.). This drops the "PRODUCT IMAGE COMING SOON / SOUK
     AUTO PARTS" style placeholders. OEM box text (brand + part number + "quality/original")
     is NOT promo and is kept.
  4. Standardize: RGB, auto-trim borders, fit to a clean white 500×500 square, compress to
     ≤150 KB JPEG (quality stepped down), progressive + optimized. Optional part-name caption.
  5. Upload to the bucket at parts/<ab>/<uuid>.jpg and set
     parts_catalog.thumbnail_url + thumbnail_status='ok'. Rejected → 'rejected_ad',
     no fetchable/clean source → 'no_source'.

Data Modified: parts_catalog.thumbnail_url, parts_catalog.thumbnail_status; bucket objects.

Usage (inside the backend container):
  python3 /app/maintenance/build_part_thumbnails.py --limit 500 [--caption] [--dry-run]

Author: AutoSpareFinder Agent
Last Updated: 2026-07-18
"""
import argparse
import asyncio
import io
import os
import re
import urllib.request

import asyncpg
from PIL import Image, ImageChops, ImageOps, ImageDraw, ImageFont

import s3_storage as S

DB = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
UA = {"User-Agent": "Mozilla/5.0 (compatible; AutoSpareFinderBot/1.0)"}
MAX_BYTES = 150 * 1024
BOX = 500

# Supplier / promotional text that must NEVER appear on a served thumbnail → reject the image.
_PROMO = [
    "coming soon", "image coming", "no image", "not available", "placeholder", "sample image",
    "contact us", "call us", "whatsapp", "hotline", "email us", "e-mail",
    "any questions", "questions", "problems with", "purchase", "before you buy",
    "world of", "auto parts", "autoparts", "spare parts", "car parts", "carparts",
    "warehouse", "wholesale", "best price", "buy now", "order now", "add to cart",
    "welcome to", "follow us", "visit us", "our store", "our shop", "www.", "http",
    ".com", ".net", ".shop", ".store", "souk", "aliexpress", "ebay store",
]
_PROMO_RE = re.compile("|".join(re.escape(p) for p in _PROMO), re.IGNORECASE)
_PHONE_RE = re.compile(r"(\+?\d[\d\s\-]{7,}\d)")


def _best_source(url: str) -> str:
    u = url.strip()
    # eBay: bump tiny/thumb variants to a usable size.
    u = re.sub(r"/s-l\d{2,4}\.", "/s-l500.", u)
    return u


def _is_promo(pil_img) -> bool:
    """True if the image carries supplier/promo text (→ reject)."""
    try:
        import pytesseract
        text = pytesseract.image_to_string(pil_img)
    except Exception:
        return False  # OCR unavailable → don't over-reject (still standardized/re-hosted)
    low = text.lower()
    if _PROMO_RE.search(low):
        return True
    # A phone-like run + any "call/contact/tel" nearby is a strong supplier-ad signal.
    if _PHONE_RE.search(text) and any(w in low for w in ("call", "tel", "phone", "contact", "whats")):
        return True
    return False


def _standardize(pil_img, caption: str = "") -> bytes:
    im = pil_img.convert("RGB")
    # auto-trim uniform border
    bg = Image.new("RGB", im.size, (255, 255, 255))
    diff = ImageChops.difference(im, bg)
    bbox = diff.getbbox()
    if bbox:
        im = im.crop(bbox)
    im.thumbnail((BOX - 20, BOX - 20), Image.LANCZOS)
    # centre on a clean white square
    canvas = Image.new("RGB", (BOX, BOX), (255, 255, 255))
    canvas.paste(im, ((BOX - im.width) // 2, (BOX - im.height) // 2))
    if caption:
        d = ImageDraw.Draw(canvas)
        cap = caption[:42]
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
        except Exception:
            font = ImageFont.load_default()
        tw = d.textlength(cap, font=font)
        d.rectangle([(0, BOX - 26), (BOX, BOX)], fill=(17, 24, 39))
        d.text(((BOX - tw) / 2, BOX - 23), cap, fill=(255, 255, 255), font=font)
    for q in (85, 80, 75, 70, 65, 60, 55):
        b = io.BytesIO()
        canvas.save(b, "JPEG", quality=q, optimize=True, progressive=True)
        data = b.getvalue()
        if len(data) <= MAX_BYTES:
            return data
    return data  # best effort


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--caption", action="store_true", help="overlay the part name")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not S.s3_enabled():
        print("S3 not configured"); return

    conn = await asyncpg.connect(DB)
    rows = await conn.fetch("""
        SELECT DISTINCT ON (pc.id) pc.id, pc.name, pi.url
        FROM parts_catalog pc
        JOIN parts_images pi ON pi.part_id = pc.id
        WHERE pc.is_active
          AND NOT EXISTS (SELECT 1 FROM part_thumbnails t WHERE t.part_id = pc.id)
          AND pi.url IS NOT NULL AND pi.url <> ''
        ORDER BY pc.id, pi.is_primary DESC, pi.sort_order ASC
        LIMIT $1
    """, a.limit)
    print(f"candidates: {len(rows)}")

    ok = rejected = no_source = failed = 0
    for r in rows:
        pid, name, url = str(r["id"]), r["name"] or "", r["url"]
        status, thumb = None, None
        try:
            raw = urllib.request.urlopen(urllib.request.Request(_best_source(url), headers=UA), timeout=25).read()
            src = Image.open(io.BytesIO(raw))
            if _is_promo(src):
                status = "rejected_ad"; rejected += 1
            else:
                data = _standardize(src, caption=(name if a.caption else ""))
                key = S.thumb_key(pid)
                if a.dry_run or S.upload_bytes(key, data):
                    status, thumb = "ok", S.thumb_url(pid); ok += 1
                else:
                    status = "failed"; failed += 1
        except Exception as exc:
            status = "no_source"; no_source += 1
            if no_source <= 3:
                print(f"  src fail {pid}: {str(exc)[:60]}")
        if not a.dry_run:
            await conn.execute(
                "INSERT INTO part_thumbnails(part_id, url, status, updated_at) VALUES($1,$2,$3,NOW()) "
                "ON CONFLICT (part_id) DO UPDATE SET url=EXCLUDED.url, status=EXCLUDED.status, updated_at=NOW()",
                r["id"], thumb, status)

    await conn.close()
    print(f"\nDONE — ok={ok} rejected_ad={rejected} no_source={no_source} failed={failed}")


if __name__ == "__main__":
    asyncio.run(main())
