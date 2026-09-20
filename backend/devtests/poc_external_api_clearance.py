"""
POC: External scraping API as cf_clearance cookie source.
Phase 5 — isolated proof-of-concept for FlareSolverr replacement.

Demonstrates the complete integration path:
  1. _solve_clearance_via_api() calls ScraperAPI/Zyte for a car-parts.ie page
  2. Response contains the cf_clearance cookie
  3. Harvester uses that cookie with plain urllib (no change to http_get())
  4. Full car-parts.ie model page fetches work

Run: docker exec autospare_backend python3 /app/devtests/poc_external_api_clearance.py

Required env vars (set one):
  SCRAPERAPI_KEY=<key>   (scraperapi.com — free tier: 1,000 req/month)
  ZYTE_API_KEY=<key>     (zyte.com — pay-as-you-go: ~$0.001/request)

If neither is set, the POC runs in DRY-RUN mode showing what would be called.
"""
import os
import sys
import time
import json
import urllib.request
import urllib.error
import urllib.parse

BASE = "https://www.car-parts.ie"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")

# ── ScraperAPI integration ────────────────────────────────────────────────────
def _solve_via_scraperapi(api_key: str) -> "tuple[str, str] | None":
    """
    Call ScraperAPI to fetch car-parts.ie homepage.
    ScraperAPI handles CF bypass internally and returns the real page HTML.
    Extract Set-Cookie from response to get cf_clearance.

    API reference: https://scraperapi.com/documentation/
    Cost: ~1 API call per mint → 1,440/month = Free tier covers this.
    """
    # ScraperAPI endpoint — renders with real browser, handles CF
    api_url = (f"https://api.scraperapi.com?"
               f"api_key={api_key}&url={urllib.parse.quote(BASE + '/')}"
               f"&render=true&country_code=ie")

    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=90) as r:
            html = r.read().decode("utf-8", "replace")
            # ScraperAPI passes through Set-Cookie headers
            raw_cookies = r.headers.get_all("Set-Cookie") or []
            cf_cookies = [c for c in raw_cookies if "cf_clearance" in c]
            if cf_cookies:
                # Parse cf_clearance value
                ck_str = "; ".join(c.split(";")[0] for c in raw_cookies)
                return ck_str, UA
            # Even without explicit set-cookie, if page loaded we have implicit access
            if "car-parts.ie" in html and "Just a moment" not in html:
                print("  ScraperAPI: page loaded (no explicit cf_clearance in headers)")
                return None, None  # page accessible but cookie not extractable this way
    except Exception as e:
        print(f"  ScraperAPI error: {e}")
    return None, None


# ── Zyte API integration ──────────────────────────────────────────────────────
def _solve_via_zyte(api_key: str) -> "tuple[str, str] | None":
    """
    Call Zyte API to fetch car-parts.ie homepage with browser rendering.
    Zyte extracts cookies from the browser session and returns them.

    API reference: https://docs.zyte.com/
    Cost: ~$0.001-0.005 per request → 1,440/month = ~$1.44-7.20/month
    """
    payload = json.dumps({
        "url": BASE + "/",
        "browserHtml": True,
        "httpResponseCookies": True,
        "actions": [{"action": "waitForTimeout", "timeout": 10}]
    }).encode("utf-8")

    auth = f"{api_key}:".encode("utf-8")
    import base64
    auth_header = "Basic " + base64.b64encode(auth).decode("utf-8")

    try:
        req = urllib.request.Request(
            "https://api.zyte.com/v1/extract",
            data=payload,
            headers={"Authorization": auth_header, "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=90) as r:
            result = json.loads(r.read())
            cookies = result.get("httpResponseCookies", [])
            cf = [c for c in cookies if c.get("name") == "cf_clearance"]
            if cf:
                ck_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
                return ck_str, UA
            html = result.get("browserHtml", "")
            if "Just a moment" not in html and len(html) > 5000:
                print("  Zyte: page loaded, cf_clearance not in cookies list")
    except Exception as e:
        print(f"  Zyte API error: {e}")
    return None, None


# ── urllib verification (unchanged harvester logic) ───────────────────────────
def verify_cookie(cookie_str: str) -> bool:
    """
    Verify cf_clearance works with plain urllib — the exact same http_get() logic
    the harvester uses. This function is intentionally identical to the harvester's
    http_get() so the POC proves the real code path.
    """
    test_url = f"{BASE}/car-parts/toyota/corolla/"
    try:
        req = urllib.request.Request(test_url, headers={
            "User-Agent": UA,
            "Cookie": cookie_str,
            "Accept-Language": "en-IE,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
            "Referer": BASE + "/"
        })
        with urllib.request.urlopen(req, timeout=40) as r:
            html = r.read().decode("utf-8", "replace")
            if "Just a moment" in html:
                print(f"  Cookie invalid — still getting CF challenge ({len(html)} bytes)")
                return False
            part_count = html.count("rec_products_single_block")
            print(f"  ✅ HTTP {r.status} | {len(html):,} bytes | {part_count} part blocks")
            return True
    except urllib.error.HTTPError as e:
        print(f"  ❌ HTTP {e.code}: cookie rejected")
        return False
    except Exception as e:
        print(f"  ❌ urllib error: {e}")
        return False


# ── Main POC ─────────────────────────────────────────────────────────────────
def main():
    scraperapi_key = os.environ.get("SCRAPERAPI_KEY", "")
    zyte_key = os.environ.get("ZYTE_API_KEY", "")

    print("=" * 65)
    print("POC: External API → cf_clearance → urllib harvesting")
    print("=" * 65)

    if not scraperapi_key and not zyte_key:
        print("\n[DRY RUN — no API keys set]")
        print("\nWhat would happen in production:")
        print("  1. _solve_clearance() calls ScraperAPI or Zyte API (~1s API call)")
        print("  2. API returns cf_clearance cookie (+ other CF cookies)")
        print("  3. Cookie stored in _CLEARANCE dict with timestamp")
        print("  4. http_get() fetches all pages with plain urllib + that cookie")
        print("  5. Cookie refreshed every 25 min (~2×/hour)")
        print()
        print("Integration change in _solve_clearance() (minimal):")
        print("""
  def _solve_clearance() -> bool:
      api_key = os.environ.get('SCRAPERAPI_KEY') or os.environ.get('ZYTE_API_KEY')
      if not api_key:
          return _solve_via_flaresolverr()  # fallback
      ck, ua = _solve_via_scraperapi(api_key) if 'SCRAPERAPI_KEY' in os.environ else _solve_via_zyte(api_key)
      if ck:
          with _CLEARANCE_LOCK:
              _CLEARANCE['cookie'] = ck
              _CLEARANCE['ua'] = ua or _DEFAULT_UA
              _CLEARANCE['ts'] = time.time()
          return True
      return False
        """)
        print("Resource consumption: zero Chrome processes, ~1s per cookie mint")
        print("Cost estimate (ScraperAPI): 1,440 mints/month → FREE tier")
        print("Cost estimate (Zyte): 1,440 mints/month × $0.003 = ~$4.32/month")
        print()
        print("To run the live POC:")
        print("  SCRAPERAPI_KEY=<key> python3 /app/devtests/poc_external_api_clearance.py")
        print("  ZYTE_API_KEY=<key>   python3 /app/devtests/poc_external_api_clearance.py")
        return

    print()
    if scraperapi_key:
        print(f"[LIVE] ScraperAPI key: {scraperapi_key[:8]}…")
        t0 = time.time()
        cookie_str, ua = _solve_via_scraperapi(scraperapi_key)
        elapsed = time.time() - t0
        print(f"  Elapsed: {elapsed:.1f}s")
        if cookie_str:
            print(f"  cf_clearance obtained: {cookie_str[:60]}…")
            print("\nVerifying cookie works with plain urllib:")
            ok = verify_cookie(cookie_str)
            sys.exit(0 if ok else 1)
        else:
            print("  No cookie returned from ScraperAPI")

    if zyte_key:
        print(f"[LIVE] Zyte API key: {zyte_key[:8]}…")
        t0 = time.time()
        cookie_str, ua = _solve_via_zyte(zyte_key)
        elapsed = time.time() - t0
        print(f"  Elapsed: {elapsed:.1f}s")
        if cookie_str:
            print(f"  cf_clearance obtained: {cookie_str[:60]}…")
            print("\nVerifying cookie works with plain urllib:")
            ok = verify_cookie(cookie_str)
            sys.exit(0 if ok else 1)
        else:
            print("  No cookie returned from Zyte API")

    sys.exit(1)


if __name__ == "__main__":
    main()
