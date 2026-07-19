"""
Script: devtests/thumbnail_pipeline_test.py
Purpose: End-to-end test for the part-thumbnail system (Contabo Object Storage → cleanup
         pipeline → backend serving → search wiring + dedup). Deterministic + fast: the filter
         checks use SYNTHETIC images (no flaky remote fetch/OCR); serving/search hit the live app.

Verifies:
  1. S3 round-trip (upload → read → delete) on the private bucket.
  2. Cleanup FILTER: a clean no-text image PASSES; an ad-text image and a label/brand-text image
     are REJECTED (owner rule: no label/brand name, no supplier ad).
  3. Standardization: output is a ≤150 KB JPEG.
  4. Content-addressing + DEDUP: keys are `thumbs/<sha256>.jpg`; identical bytes → identical key
     (one object); bucket objects == distinct thumbnail urls (no duplicate objects).
  5. Serving: GET /api/v1/thumbnails/{key} returns ≤150 KB image bytes, immutable cache, on OUR
     domain (no supplier host); traversal/non-thumbnail keys are 404.
  6. Search wiring: results expose `primary_image` and NEVER a raw supplier (ebay/…) image url.
  7. Harvest→image connection: the car-parts.ie harvester's _extract_image() yields absolute
     https part photos (and skips placeholders); parts_images (the pipeline INPUT that harvesters
     feed) is populated. This is the harvest→thumbnail link added 2026-07-18.

Usage:  python3 /app/devtests/thumbnail_pipeline_test.py
Author: AutoSpareFinder Agent — Last Updated: 2026-07-18
"""
import asyncio
import io
import json
import os
import urllib.request
import urllib.error

results = []
def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

UA = {"User-Agent": "Mozilla/5.0"}


def _img(draw_text=None, shape=False):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (420, 420), (245, 245, 247))
    d = ImageDraw.Draw(im)
    if shape:
        d.ellipse([90, 90, 330, 330], fill=(95, 96, 102), outline=(28, 28, 30), width=8)
    if draw_text:
        for i, line in enumerate(draw_text):
            d.text((20, 150 + i * 24), line, fill=(0, 0, 0))
    return im


def main():
    import s3_storage as S
    import importlib.util
    spec = importlib.util.spec_from_file_location("bpt", "/app/maintenance/build_part_thumbnails.py")
    bpt = importlib.util.module_from_spec(spec); spec.loader.exec_module(bpt)

    # ── 1. S3 round-trip ──────────────────────────────────────────────
    print("[1] S3 round-trip (private bucket)")
    check("s3 configured", S.s3_enabled(), f"bucket={S.S3_BUCKET}")
    tkey = "thumbs/zz/_e2e_test.jpg"
    b = io.BytesIO(); _img(shape=True).save(b, "JPEG")
    check("upload", S.upload_bytes(tkey, b.getvalue()))
    got = S.get_object(tkey)
    check("read back", got is not None and len(got[0]) > 0)
    try:
        S.get_client().delete_object(Bucket=S.S3_BUCKET, Key=tkey); check("cleanup delete", True)
    except Exception as e:
        check("cleanup delete", False, str(e)[:50])

    # ── 2. Cleanup filter (synthetic — deterministic) ─────────────────
    print("[2] Cleanup filter — no supplier ad, no label/brand")
    check("clean (no-text) part image PASSES", bpt._is_promo(_img(shape=True)) is False)
    check("supplier-ad text image REJECTED",
          bpt._is_promo(_img(["PRODUCT IMAGE COMING SOON", "SOUK AUTO PARTS", "contact us"])) is True)
    check("label/brand-text image REJECTED",
          bpt._is_promo(_img(["Genuine Parts", "Original Mercedes Benz", "Made in Germany"])) is True)
    data = bpt._standardize(_img(shape=True))
    check("standardized ≤150KB JPEG (no caption drawn)", len(data) <= 150 * 1024 and data[:2] == b"\xff\xd8", f"{len(data)}b")

    # ── 3. Content-addressing + dedup ─────────────────────────────────
    print("[3] Content-addressed dedup")
    k1, k2 = S.content_key(data), S.content_key(data)
    check("identical bytes → identical key (dedup)", k1 == k2 and k1.startswith("thumbs/"))
    async def _dedup():
        c = await asyncpg.connect(DB)
        ok = await c.fetchval("SELECT COUNT(*) FROM part_thumbnails WHERE status='ok'")
        dis = await c.fetchval("SELECT COUNT(DISTINCT url) FROM part_thumbnails WHERE status='ok' AND url IS NOT NULL")
        one = await c.fetchrow("SELECT url FROM part_thumbnails WHERE status='ok' AND url IS NOT NULL LIMIT 1")
        await c.close(); return ok, dis, (one["url"] if one else None)
    import asyncpg
    DB = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    ok_parts, distinct_urls, sample_url = asyncio.get_event_loop().run_until_complete(_dedup())
    n_obj = S.get_client().list_objects_v2(Bucket=S.S3_BUCKET, Prefix="thumbs/").get("KeyCount", 0)
    check("bucket objects == distinct thumbnails (no dup objects)", n_obj == distinct_urls, f"{n_obj} objs / {distinct_urls} urls")
    check("distinct thumbnails ≤ ok-parts (dedup possible)", distinct_urls <= max(ok_parts, 1), f"{distinct_urls} ≤ {ok_parts}")

    # ── 4. Serving (live, our domain) ─────────────────────────────────
    print("[4] Serving")
    if sample_url:
        check("thumbnail url on OUR domain (no supplier host)",
              sample_url.startswith("https://autosparefinder.co.il/api/v1/thumbnails/thumbs/") and "ebay" not in sample_url)
        try:
            r = urllib.request.urlopen(urllib.request.Request(sample_url + "?v=e2e", headers=UA), timeout=25)
            body = r.read()
            check("served 200 image ≤150KB", r.status == 200 and r.headers.get("Content-Type", "").startswith("image/") and len(body) <= 150 * 1024, f"{len(body)}b")
            check("immutable cache (Cloudflare-cacheable)", "immutable" in (r.headers.get("Cache-Control") or ""))
        except Exception as e:
            check("served 200 image ≤150KB", False, str(e)[:50])
    # abuse: traversal / non-thumbnail key → 404
    def _code(path):
        try:
            return urllib.request.urlopen(urllib.request.Request("https://autosparefinder.co.il" + path, headers=UA), timeout=15).status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            return -1
    check("non-thumbnail key → 404", _code("/api/v1/thumbnails/etc/passwd") == 404)
    check("traversal key → 404", _code("/api/v1/thumbnails/thumbs/../x") == 404)

    # ── 5. Search wiring: primary_image present, never a supplier url ──
    print("[5] Search wiring")
    try:
        s = urllib.request.urlopen(urllib.request.Request("https://autosparefinder.co.il/api/v1/parts/search?q=oil%20filter", headers=UA), timeout=40).read()
        d = json.loads(s); parts = []
        def walk(o):
            if isinstance(o, dict):
                if o.get("id") and "primary_image" in o: parts.append(o)
                for v in o.values(): walk(v)
            elif isinstance(o, list):
                for v in o: walk(v)
        walk(d)
        check("search parts expose primary_image (wiring present)", len(parts) >= 1, f"{len(parts)} parts")
        check("search NEVER returns a raw supplier image url",
              all("ebay" not in str(p.get("primary_image") or "") and "contabo" not in str(p.get("primary_image") or "") for p in parts))
    except Exception as e:
        check("search parts expose primary_image (wiring present)", False, str(e)[:50])

    # ── 6. Supervisor (status endpoint) ──────────────────────────────
    print("[6] Supervisor")
    try:
        st = json.loads(urllib.request.urlopen(urllib.request.Request(
            "https://autosparefinder.co.il/api/v1/system/thumbnail-import", headers=UA), timeout=20).read())
        check("supervisor status endpoint live", isinstance(st, dict) and "state" in st and "total_ok" in st, st.get("state"))
        cat = st.get("catalog") or {}
        check("supervisor reports catalog coverage", "ok" in cat and "distinct_images" in cat,
              f"ok={cat.get('ok')} distinct={cat.get('distinct_images')} dedup_saved={cat.get('dedup_saved')}")
        check("dedup integrity (distinct ≤ ok, saved ≥ 0)",
              (cat.get("distinct_images", 0) <= cat.get("ok", 0)) and cat.get("dedup_saved", 0) >= 0)
    except Exception as e:
        check("supervisor status endpoint live", False, str(e)[:50])

    # ── 7. Harvest → image connection (the harvesters FEED parts_images) ─────
    print("[7] Harvest→image connection")
    try:
        import importlib.util as _il
        _hs = _il.spec_from_file_location("cph", "/app/harvesters/car_parts_ie_flaresolverr_harvester.py")
        cph = _il.module_from_spec(_hs); _hs.loader.exec_module(cph)
        from bs4 import BeautifulSoup as _BS
        def _blk(html):
            return _BS(f'<div class="rec_products_single_block">{html}</div>', "html.parser").select_one(".rec_products_single_block")
        lazy = cph._extract_image(_blk('<img data-src="//cdn.car-parts.ie/p/1.jpg" src="/spacer.gif">'), "/car-parts/bmw/x/oil-filter")
        rel  = cph._extract_image(_blk('<img src="/media/x/2.jpg">'), "/x")
        none = cph._extract_image(_blk('<img src="/img/no-image.png">'), "/x")
        check("harvester extracts lazy image → absolute https", lazy == "https://cdn.car-parts.ie/p/1.jpg")
        check("harvester absolutizes relative image", rel == "https://www.car-parts.ie/media/x/2.jpg")
        check("harvester skips placeholder/logo images", none is None)
        # parts_images is the pipeline INPUT and is populated (harvest side alive)
        async def _imgcount():
            c = await asyncpg.connect(DB)
            n = await c.fetchval("SELECT COUNT(*) FROM parts_images WHERE url IS NOT NULL")
            await c.close(); return n
        n_img = asyncio.get_event_loop().run_until_complete(_imgcount())
        check("parts_images populated (thumbnail-pipeline input)", n_img > 0, f"{n_img} source images")
    except Exception as e:
        check("harvester extracts lazy image → absolute https", False, str(e)[:60])

    passed = sum(1 for _, ok in results if ok)
    print("\n" + "=" * 56)
    print(f"THUMBNAIL PIPELINE E2E: {passed}/{len(results)} passed")
    fails = [n for n, ok in results if not ok]
    print("FAILURES: " + ", ".join(fails) if fails else "ALL CHECKS PASSED ✅")
    print("=" * 56)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
