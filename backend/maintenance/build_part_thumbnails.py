"""
Script: maintenance/build_part_thumbnails.py
Purpose: Build clean part thumbnails in the Contabo Object Storage bucket.

Runs continuously under the `_thumbnail_import_loop()` supervisor in BACKEND_API_ROUTES.py
(niced subprocess, batched); can also be run by hand for a one-off batch.

Process (per part with a source image and no part_thumbnails row yet):
  1. Pick the best source image (parts_images.url; upgrade eBay s-l225→s-l500 for quality).
  2. Fetch it (in-run source-url cache so an identical source isn't re-fetched/re-OCR'd).
  3. CLEANUP FILTER (owner rule: no supplier links/ads AND no label/brand name — clean part
     image only): OCR the image and REJECT it if it contains supplier/promo text ("coming
     soon", "contact us", "auto parts", a URL, "whatsapp", phone hotline, …) OR is text-heavy
     (> THUMB_MAX_OCR_WORDS real words ⇒ a label / OEM-box / brand card, not a clean part shot).
  4. Standardize: RGB, auto-trim borders, fit to a clean white 500×500 square, compress to
     ≤150 KB JPEG (quality stepped down), progressive + optimized. NO caption/label ever drawn.
  5. Content-address the final bytes (thumbs/<sha256>.jpg) → identical images map to ONE bucket
     object (dedup: a picture is stored once, reused by every part that shares it). Upload only
     if the object doesn't already exist. Record the result in part_thumbnails(part_id, url,
     status): 'ok' / 'rejected_ad' / 'no_source' / 'failed'.

Data Modified: part_thumbnails (part_id, url, status); bucket objects (thumbs/<sha256>.jpg).

Usage (inside the backend container):
  python3 /app/maintenance/build_part_thumbnails.py --limit 500 [--dry-run]

Author: AutoSpareFinder Agent
Last Updated: 2026-07-18
"""
import argparse
import threading
import asyncio
import io
import os
import re
import urllib.request

import asyncpg
from PIL import Image, ImageChops

import s3_storage as S

DB = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
UA = {"User-Agent": "Mozilla/5.0 (compatible; AutoSpareFinderBot/1.0)"}
MAX_BYTES = 150 * 1024
BOX = 500
# Parallel parts in flight. The work is network-bound (a fetch idles ~0.5s),
# so concurrency collapses wall time without adding CPU. Kept modest on
# purpose: the main image host already 403s this IP, and too many parallel
# streams risk invalidating the clearance cookie.
CONCURRENCY = int(os.getenv("THUMB_CONCURRENCY", "3"))
# Seconds a single OCR may take before it is abandoned.
_OCR_TIMEOUT = int(os.getenv("THUMB_OCR_TIMEOUT_S", "20"))

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
    # eBay: request the LARGEST variant. This used to force "/s-l500." for every size token,
    # which upgraded tiny thumbs (s-l225) but silently DOWNGRADED big ones (s-l1600 -> s-l500)
    # — and that blinded the OCR ad-filter: at 500px tesseract read 0 words from a disclaimer
    # image ("ACTUAL PRODUCT MAY VARY", brand logos) and accepted it, while the same image at
    # s-l1600 (1400x1400) yields 12 words and is correctly rejected. Branded/ad images were
    # leaking into the bucket purely because we fetched them too small to read.
    # Bigger is also a crisper source for the 500x500 thumbnail (same intent as the
    # autoteile-meile m=0 rule below, which pulls the full original).
    u = re.sub(r"/s-l\d{2,4}\.", "/s-l1600.", u)
    # car-parts.ie CDN (autoteile-meile): the harvested URL uses m=2 (108×100). The `m=`
    # param is a size selector — m=0 is the full 1024px original. Pull that so the pipeline
    # downscales a crisp source into the 500×500 thumbnail (verified 2026-07-18: m=0→1024²).
    if "autoteile-meile.de" in u:
        u = re.sub(r"([?&]m=)\d+", r"\g<1>0", u)
    return u


# Max real words (>=3 letters) an accepted image may contain. A genuine PART photo has little/no
# text; a label / OEM-box / brand card / supplier ad is text-heavy. Owner rule 2026-07-18: the
# picture must have NO label or brand name → reject text-heavy images. Tunable via env.
_MAX_WORDS = int(os.getenv("THUMB_MAX_OCR_WORDS", "3"))
_WORD_RE = re.compile(r"[A-Za-z֐-׿؀-ۿ]{3,}")


# ── Cloudflare-protected image CDNs ───────────────────────────────────────────
# media.autoteile-meile.de (the car-parts.ie image CDN) returns 403 to this
# server's IP for ANY plain request — browser UA and Referer make no difference.
# It accounted for 96,714 of the 97,590 parts written off as `no_source`, i.e.
# 99% of the entire image gap was ONE blocked host, not missing pictures.
#
# Same fix the harvester already uses for car-parts.ie itself: FlareSolverr is
# used ONLY to MINT a cf_clearance cookie (verified: sessionless request.get →
# 200 + cf_clearance), then the image is fetched with plain urllib carrying that
# cookie (verified: 200 image/jpeg 99,466 bytes). FlareSolverr never fetches the
# images themselves — routing every image through a headless browser is the leak
# that pinned the box at ~409% CPU in August.
_FS_HOSTS = [h for h in (os.getenv("FLARESOLVERR_URL", "http://flaresolverr:8191/v1"),
                         os.getenv("FLARESOLVERR_URL_2", "")) if h]
_CLEARANCE_TTL = int(os.getenv("THUMB_CLEARANCE_TTL_S", "1500"))
_clearance: dict = {}          # host -> {"cookie": str, "ua": str, "ts": float}


def _mint_clearance(url: str) -> dict | None:
    """Mint a cf_clearance cookie for this URL's host. Cached per host, TTL-bounded."""
    import json as _json
    import time as _time
    from urllib.parse import urlsplit
    host = urlsplit(url).netloc
    got = _clearance.get(host)
    if got and (_time.time() - got["ts"]) < _CLEARANCE_TTL:
        return got
    for fs in _FS_HOSTS:
        try:
            req = urllib.request.Request(
                fs, data=_json.dumps({"cmd": "request.get", "url": url,
                                      "maxTimeout": 60000}).encode(),
                headers={"Content-Type": "application/json"})
            sol = _json.load(urllib.request.urlopen(req, timeout=120)).get("solution", {})
            if sol.get("cookies"):
                got = {"cookie": "; ".join(c["name"] + "=" + c["value"] for c in sol["cookies"]),
                       "ua": sol.get("userAgent") or UA["User-Agent"],
                       "ts": _time.time()}
                _clearance[host] = got
                print(f"  cf_clearance minted for {host}")
                return got
        except Exception:
            continue
    return None


def _fetch_source(url: str) -> bytes:
    """Fetch the best (largest) source variant, falling back to the smaller one if the CDN
    has no such size. Large matters: the OCR ad-filter can only reject text it can READ."""
    candidates = [_best_source(url)]
    fallback = re.sub(r"/s-l\d{2,4}\.", "/s-l500.", url.strip())
    if fallback not in candidates:
        candidates.append(fallback)
    if url.strip() not in candidates:
        candidates.append(url.strip())
    last = None
    for cand in candidates:
        try:
            return urllib.request.urlopen(urllib.request.Request(cand, headers=UA), timeout=25).read()
        except Exception as exc:
            last = exc
            # 403/503 => the host is blocking this IP, not a missing image.
            # Mint a clearance cookie once per host and retry before giving up.
            code = getattr(exc, "code", None)
            if code in (403, 503):
                cl = _mint_clearance(cand)
                if cl:
                    try:
                        return urllib.request.urlopen(urllib.request.Request(
                            cand, headers={"User-Agent": cl["ua"], "Cookie": cl["cookie"],
                                           "Referer": "https://www.car-parts.ie/"}),
                            timeout=30).read()
                    except Exception as exc2:
                        last = exc2
    raise last if last else RuntimeError("no source candidate")


def _is_promo(pil_img) -> bool:
    """True if the image must be REJECTED — it carries supplier/promo text, OR it is a
    label/box/brand-dominated shot (too much text to be a clean part picture)."""
    try:
        import pytesseract
        # HARD TIMEOUT. Observed 2026-08-04: individual tesseract processes stuck
        # on one image for 3+ minutes at ~55% CPU each, so a handful of
        # pathological images can hold whole cores hostage and starve the rest
        # of the box. A slow OCR is not worth a core — treat it as "cannot read"
        # and fall through to the size/word checks.
        text = pytesseract.image_to_string(pil_img, timeout=_OCR_TIMEOUT)
    except Exception:
        return False  # OCR unavailable/too slow → don't over-reject
    low = text.lower()
    if _PROMO_RE.search(low):
        return True
    if _PHONE_RE.search(text) and any(w in low for w in ("call", "tel", "phone", "contact", "whats")):
        return True
    # label / brand-name density: more than _MAX_WORDS real words ⇒ a label/box/brand image.
    if len(_WORD_RE.findall(text)) > _MAX_WORDS:
        return True
    return False


def _standardize(pil_img) -> bytes:
    """Clean part image only — NO caption/label/brand text is ever drawn (owner rule
    2026-07-18): trim uniform border, fit to a clean white 500×500 square, compress ≤150 KB."""
    im = pil_img.convert("RGB")
    # auto-trim uniform border
    bg = Image.new("RGB", im.size, (255, 255, 255))
    diff = ImageChops.difference(im, bg)
    bbox = diff.getbbox()
    if bbox:
        im = im.crop(bbox)
    im.thumbnail((BOX - 20, BOX - 20), Image.LANCZOS)
    # centre on a clean white square (no text overlay)
    canvas = Image.new("RGB", (BOX, BOX), (255, 255, 255))
    canvas.paste(im, ((BOX - im.width) // 2, (BOX - im.height) // 2))
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
    ap.add_argument("--retry-no-source", action="store_true",
                help="also re-process parts previously written off as no_source. They were judged on their PRIMARY image alone, so a dead primary hid working alternates.")
    ap.add_argument("--dry-run", action="store_true")
    # Target a specific slice (e.g. a newly-imported brand) instead of taking
    # whatever the general backlog happens to surface. Without this there is no
    # way to PROVE a fresh import's images reach the bucket — you can only wait
    # for the background supervisor to eventually reach them.
    ap.add_argument("--sku-like", default=None,
                    help="only parts whose sku matches this SQL LIKE pattern, e.g. '%%-BKQC'")
    a = ap.parse_args()

    if not S.s3_enabled():
        print("S3 not configured"); return

    conn = await asyncpg.connect(DB)
    # ALL of a part's images, not just the first.
    #
    # This used to be `DISTINCT ON (pc.id)`, i.e. the primary image only. If that
    # single URL was dead the part was written off as `no_source` — and because
    # the candidate query excludes any part that already has a verdict row, its
    # OTHER images were never tried. Measured 2026-08-03: 97,590 parts sat in
    # no_source, and a sampled one had a dead `imgfoto.synology.me` primary
    # alongside a perfectly good `new.egomotors.lt` image (HTTP 200, 660KB).
    # Now every URL is attempted, in priority order, until one yields a usable
    # image; only if they ALL fail is the part `no_source`.
    retry = " OR t.status = 'no_source'" if a.retry_no_source else ""
    rows = await conn.fetch(f"""
        SELECT pc.id, pc.name,
               array_agg(pi.url ORDER BY pi.is_primary DESC, pi.sort_order ASC) AS urls
        FROM parts_catalog pc
        JOIN parts_images pi ON pi.part_id = pc.id
        LEFT JOIN part_thumbnails t ON t.part_id = pc.id
        WHERE pc.is_active
          AND (t.part_id IS NULL{retry})
          AND pi.url IS NOT NULL AND pi.url <> ''
          AND ($2::text IS NULL OR pc.sku LIKE $2)
        GROUP BY pc.id, pc.name
        LIMIT $1
    """, a.limit, a.sku_like)
    print(f"candidates: {len(rows)} parts "
          f"({sum(len(r['urls']) for r in rows)} images){' [retrying no_source]' if a.retry_no_source else ''}")

    ok = rejected = no_source = failed = deduped = 0
    src_cache: dict = {}  # source-url → (status, url) so an identical source in this run isn't re-fetched/re-OCR'd
    _cache_lock = threading.Lock()

    def _resolve(urls, pid):
        """Resolve ONE part to a verdict. Runs in a worker thread.

        Returns (status, thumb_url, last_error, was_dedup).
        """
        nonlocal_dedup = False
        last_err = ""
        for url in (urls or []):
            if not url:
                continue
            try:
                with _cache_lock:
                    cached = src_cache.get(url)
                if cached is not None:
                    status, thumb = cached
                else:
                    raw = _fetch_source(url)
                    src = Image.open(io.BytesIO(raw))
                    if _is_promo(src):
                        status, thumb = "rejected_ad", None
                    else:
                        data = _standardize(src)
                        key = S.content_key(data)      # content-addressed → dedup
                        if a.dry_run:
                            status, thumb = "ok", S.url_for_key(key)
                        elif S.object_exists(key):     # identical image already stored
                            status, thumb = "ok", S.url_for_key(key); nonlocal_dedup = True
                        elif S.upload_bytes(key, data):
                            status, thumb = "ok", S.url_for_key(key)
                        else:
                            status, thumb = "failed", None
                    with _cache_lock:
                        src_cache[url] = (status, thumb)
                if status == "ok":
                    return status, thumb, "", nonlocal_dedup
                last_status, last_thumb = status, thumb
            except Exception as exc:
                last_err = str(exc)[:60]
                continue                               # this URL is dead, try the next
        try:
            return last_status, last_thumb, last_err, nonlocal_dedup
        except UnboundLocalError:
            return None, None, last_err, nonlocal_dedup

    # CONCURRENCY. Each part costs ~0.59s, and almost all of that is the network
    # fetch sitting idle — measured 176s for 300 images, serially. The work is
    # I/O-bound, so running several parts at once collapses the wall time
    # without adding CPU (OCR is the only CPU cost and it is the smaller half).
    # Deliberately modest: media.autoteile-meile.de already blocks this server's
    # IP by default, so hammering it with dozens of parallel streams risks
    # getting the clearance cookie invalidated. THUMB_CONCURRENCY tunes it.
    sem = asyncio.Semaphore(CONCURRENCY)

    async def _one(r):
        async with sem:
            return r, await asyncio.to_thread(_resolve, r["urls"], str(r["id"]))

    results = await asyncio.gather(*[_one(r) for r in rows], return_exceptions=True)

    writes = []
    for res in results:
        if isinstance(res, Exception):
            failed += 1
            continue
        r, (status, thumb, last_err, was_dedup) = res
        if was_dedup:
            deduped += 1
        if status == "ok":
            ok += 1
        elif status == "rejected_ad":
            rejected += 1                              # a promo image is a real verdict
        elif status == "failed":
            failed += 1
        else:
            status = "no_source"; no_source += 1       # every URL failed
            if no_source <= 3:
                print(f"  src fail {r['id']} ({len(r['urls'] or [])} urls): {last_err}")
        writes.append((r["id"], thumb, status))

    if not a.dry_run and writes:
        # One executemany instead of a round-trip per part.
        await conn.executemany(
            "INSERT INTO part_thumbnails(part_id, url, status, updated_at) VALUES($1,$2,$3,NOW()) "
            "ON CONFLICT (part_id) DO UPDATE SET url=EXCLUDED.url, status=EXCLUDED.status, updated_at=NOW()",
            writes)

    await conn.close()
    print(f"\nDONE — ok={ok} (deduped_reuse={deduped}) rejected_ad={rejected} no_source={no_source} failed={failed}")


if __name__ == "__main__":
    asyncio.run(main())
