"""
POC: Playwright + stealth vs car-parts.ie Cloudflare challenge.
Reports: challenge type, page title, cookies (cf_clearance), and HTTP status.
Also verifies that the extracted cookie works with plain urllib.
"""
import asyncio
import time
import subprocess
import urllib.request

async def test_playwright_stealth():
    from playwright.async_api import async_playwright
    try:
        from playwright_stealth import stealth_async
        stealth_available = True
    except ImportError:
        stealth_available = False
        print("playwright_stealth NOT available — testing vanilla only")

    print(f"=== Playwright + stealth (stealth_available={stealth_available}) POC ===")
    chrome_before = int(subprocess.run(
        "ps -e | grep -c chrome || true", shell=True, capture_output=True, text=True
    ).stdout.strip() or "0")
    print(f"Chrome processes before: {chrome_before}")

    t_start = time.time()
    result = {"title": None, "cf_clearance": None, "status": None, "error": None, "all_cookies": []}

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-blink-features=AutomationControlled"]
            )
            try:
                ctx = await browser.new_context(
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 900},
                    locale="en-IE",
                    timezone_id="Europe/Dublin",
                )
                page = await ctx.new_page()
                if stealth_available:
                    await stealth_async(page)

                resp = await page.goto("https://www.car-parts.ie/", timeout=90000,
                                       wait_until="domcontentloaded")
                result["status"] = resp.status if resp else None
                result["title"] = await page.title()

                # Wait up to 30s for challenge to complete
                for _ in range(6):
                    await asyncio.sleep(5)
                    title = await page.title()
                    result["title"] = title
                    if "moment" not in title.lower():
                        break

                cookies = await ctx.cookies()
                result["all_cookies"] = [c["name"] for c in cookies]
                cf = [c for c in cookies if c["name"] == "cf_clearance"]
                if cf:
                    result["cf_clearance_val"] = cf[0]["value"]
                    result["cf_clearance"] = cf[0]["value"][:40] + "…"
                    # Build full cookie string
                    result["cookie_str"] = "; ".join(c["name"] + "=" + c["value"] for c in cookies)
                    result["ua"] = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

                html = await page.content()
                result["html_snippet"] = html[:300]

            finally:
                await ctx.close()
                await browser.close()

    except Exception as e:
        result["error"] = str(e)

    elapsed = time.time() - t_start
    chrome_after = int(subprocess.run(
        "ps -e | grep -c chrome || true", shell=True, capture_output=True, text=True
    ).stdout.strip() or "0")

    print(f"Elapsed: {elapsed:.1f}s")
    print(f"HTTP status: {result['status']}")
    print(f"Page title: {result['title']}")
    print(f"cf_clearance: {result.get('cf_clearance', 'NONE')}")
    print(f"All cookies: {result['all_cookies']}")
    print(f"Chrome processes after: {chrome_after}")
    if result["error"]:
        print(f"ERROR: {result['error']}")
    print(f"HTML snippet: {result.get('html_snippet','')[:200]}")

    # If we got a cf_clearance, verify it works with plain urllib
    if result.get("cookie_str"):
        print("\n--- Verifying cookie works with plain urllib ---")
        try:
            req = urllib.request.Request(
                "https://www.car-parts.ie/car-parts/toyota/corolla/",
                headers={"User-Agent": result["ua"], "Cookie": result["cookie_str"],
                         "Accept": "text/html", "Accept-Language": "en-IE,en;q=0.9"}
            )
            r = urllib.request.urlopen(req, timeout=30)
            html = r.read(1000).decode("utf-8", "replace")
            print(f"urllib status: {r.status} — {len(html)} chars received")
            # Check if it's the real page (not Cloudflare challenge)
            is_real = "just a moment" not in html.lower() and "car-parts" in html.lower()
            print(f"Real page (not CF challenge): {is_real}")
            print(f"Snippet: {html[:200]}")
        except Exception as ex:
            print(f"urllib verify failed: {ex}")

    return result

if __name__ == "__main__":
    asyncio.run(test_playwright_stealth())
