"""
Script: devtests/thumbnail_pipeline_test.py
Purpose: End-to-end test for the part-thumbnail system (Contabo Object Storage → cleanup
         pipeline → backend serving → search wiring). Verifies:
           1. S3 round-trip (upload → read → delete) on the bucket.
           2. Cleanup FILTER: a known supplier-ad image is rejected; a clean part image passes.
           3. Serving: GET /api/v1/thumbnails/{key} returns image bytes, ≤150 KB, immutable cache.
           4. Search wiring: a part with a thumbnail is returned with primary_image = the clean
              bucket URL (on OUR domain — no supplier host/link), and it fetches as a valid JPEG.
           5. Standardization: uploaded thumbnails are ≤150 KB JPEGs.

Usage (inside the backend container):
  python3 /app/devtests/thumbnail_pipeline_test.py

Author: AutoSpareFinder Agent
Last Updated: 2026-07-18
"""
import io
import os
import urllib.request

results = []
def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

UA = {"User-Agent": "Mozilla/5.0"}
AD_IMG = "https://i.ebayimg.com/images/g/xb8AAOSwmqFlzMo-/s-l225.jpg"   # SOUK "coming soon" ad
CLEAN_IMG = "https://2024mai.s3.us-east-2.amazonaws.com/L16+(1).jpeg"    # real Mercedes part


def main():
    import s3_storage as S

    # ── 1. S3 round-trip ──────────────────────────────────────────────
    print("[1] S3 round-trip")
    check("s3 configured", S.s3_enabled(), f"bucket={S.S3_BUCKET}")
    key = "parts/zz/_e2e_test.jpg"
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (32, 32), (10, 10, 10)).save(b, "JPEG")
    check("upload", S.upload_bytes(key, b.getvalue()))
    got = S.get_object(key)
    check("read back", got is not None and len(got[0]) > 0)
    try:
        S.get_client().delete_object(Bucket=S.S3_BUCKET, Key=key)
        check("cleanup delete", True)
    except Exception as e:
        check("cleanup delete", False, str(e)[:60])

    # ── 2. Cleanup filter (ad rejected, clean passes) ─────────────────
    print("[2] Cleanup / ad filter")
    import importlib.util
    spec = importlib.util.spec_from_file_location("bpt", "/app/maintenance/build_part_thumbnails.py")
    bpt = importlib.util.module_from_spec(spec); spec.loader.exec_module(bpt)
    try:
        ad = Image.open(io.BytesIO(urllib.request.urlopen(urllib.request.Request(AD_IMG, headers=UA), timeout=25).read()))
        check("supplier-ad image REJECTED", bpt._is_promo(ad) is True)
    except Exception as e:
        check("supplier-ad image REJECTED", False, str(e)[:60])
    try:
        clean = Image.open(io.BytesIO(urllib.request.urlopen(urllib.request.Request(CLEAN_IMG, headers=UA), timeout=25).read()))
        check("clean part image PASSES", bpt._is_promo(clean) is False)
        data = bpt._standardize(clean)
        check("standardized thumb ≤150KB JPEG", len(data) <= 150 * 1024 and data[:2] == b"\xff\xd8", f"{len(data)}b")
    except Exception as e:
        check("clean part image PASSES", False, str(e)[:60])

    # ── 3+4. Serving + search wiring (live HTTP) ──────────────────────
    print("[3+4] Serving + search wiring (live)")
    import json
    # find a part that already has an 'ok' thumbnail
    import asyncio, asyncpg
    DB = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    async def _one():
        c = await asyncpg.connect(DB)
        r = await c.fetchrow("SELECT part_id::text, url FROM part_thumbnails WHERE status='ok' AND url IS NOT NULL LIMIT 1")
        await c.close(); return r
    row = asyncio.get_event_loop().run_until_complete(_one())
    if not row:
        check("a thumbnail exists to serve", False); return
    url = row["url"]
    check("thumbnail url is on OUR domain (no supplier host)",
          url.startswith("https://autosparefinder.co.il/api/v1/thumbnails/") and "ebay" not in url and "contabo" not in url)
    # serve it
    try:
        req = urllib.request.Request(url + "?v=e2e", headers=UA)
        resp = urllib.request.urlopen(req, timeout=25)
        body = resp.read()
        check("served 200 image", resp.status == 200 and resp.headers.get("Content-Type", "").startswith("image/"))
        check("served ≤150KB valid JPEG", len(body) <= 150 * 1024 and body[:2] == b"\xff\xd8", f"{len(body)}b")
        check("immutable cache header (Cloudflare-cacheable)", "immutable" in (resp.headers.get("Cache-Control") or ""))
    except Exception as e:
        check("served 200 image", False, str(e)[:60])

    # search returns the clean thumbnail as primary_image (no raw supplier image)
    try:
        _sreq = urllib.request.Request("https://autosparefinder.co.il/api/v1/parts/search?q=oil%20filter", headers=UA)
        s = urllib.request.urlopen(_sreq, timeout=40).read()
        d = json.loads(s); parts = []
        def walk(o):
            if isinstance(o, dict):
                if o.get("id") and "primary_image" in o: parts.append(o)
                for v in o.values(): walk(v)
            elif isinstance(o, list):
                for v in o: walk(v)
        walk(d)
        thumbed = [p for p in parts if p.get("primary_image") and "thumbnails" in str(p["primary_image"])]
        no_supplier = all("ebay" not in str(p.get("primary_image") or "") for p in parts)
        check("search surfaces clean bucket thumbnails", len(thumbed) >= 1, f"{len(thumbed)}/{len(parts)}")
        check("search returns NO raw supplier image urls", no_supplier)
    except Exception as e:
        check("search surfaces clean bucket thumbnails", False, str(e)[:60])

    passed = sum(1 for _, ok in results if ok)
    print("\n" + "=" * 56)
    print(f"THUMBNAIL PIPELINE E2E: {passed}/{len(results)} passed")
    fails = [n for n, ok in results if not ok]
    print("FAILURES: " + ", ".join(fails) if fails else "ALL CHECKS PASSED ✅")
    print("=" * 56)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
