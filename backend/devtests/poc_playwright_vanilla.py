"""
POC: Playwright vanilla (no stealth) vs car-parts.ie Cloudflare challenge.
Reports: challenge type, page title, cookies, and HTTP status.
"""
import asyncio
import time
import os
import subprocess

async def test_playwright_vanilla():
    from playwright.async_api import async_playwright

    print("=== Playwright Vanilla POC ===")
    chrome_before = int(subprocess.run(
        "ps -e | grep -c chrome || true", shell=True, capture_output=True, text=True
    ).stdout.strip() or "0")
    print(f"Chrome processes before: {chrome_before}")

    t_start = time.time()
    result = {"title": None, "cf_clearance": None, "status": None, "html_snippet": "", "error": None}

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            try:
                ctx = await browser.new_context(
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
                page = await ctx.new_page()

                resp = await page.goto("https://www.car-parts.ie/", timeout=60000,
                                       wait_until="domcontentloaded")
                result["status"] = resp.status if resp else None
                result["title"] = await page.title()

                # Wait up to 30s for Cloudflare to redirect
                await asyncio.sleep(5)
                result["title"] = await page.title()

                # Check for cf_clearance
                cookies = await ctx.cookies()
                cf = [c for c in cookies if c["name"] == "cf_clearance"]
                result["cf_clearance"] = cf[0]["value"][:40] + "…" if cf else None
                result["cookies_all"] = [c["name"] for c in cookies]

                html = await page.content()
                result["html_snippet"] = html[:400]

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
    print(f"cf_clearance: {result['cf_clearance']}")
    print(f"All cookies: {result.get('cookies_all', [])}")
    print(f"Chrome processes after: {chrome_after}")
    if result["error"]:
        print(f"ERROR: {result['error']}")
    print("HTML snippet:")
    print(result["html_snippet"][:300])
    return result

if __name__ == "__main__":
    asyncio.run(test_playwright_vanilla())
