"""
POC: nodriver (undetected Chrome) vs car-parts.ie Cloudflare Managed Challenge.
nodriver patches at the CDP level, doesn't set --enable-automation or navigator.webdriver.
Reports: page title, cookies (cf_clearance), and urllib verification.
"""
import asyncio
import time
import subprocess
import urllib.request

async def test_nodriver():
    import nodriver as uc

    print("=== nodriver (undetected Chrome) POC ===")
    chrome_before = int(subprocess.run(
        "ps -e | grep -c chrome || true", shell=True, capture_output=True, text=True
    ).stdout.strip() or "0")
    print(f"Chrome processes before: {chrome_before}")

    t_start = time.time()
    result = {"title": None, "cf_clearance": None, "status": None, "error": None, "all_cookies": []}

    try:
        browser = await uc.start(
            headless=True,
            browser_args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            page = await browser.get("https://www.car-parts.ie/")
            result["title"] = await page.evaluate("() => document.title")
            print(f"Initial title: {result['title']}")

            # Wait up to 30s for challenge to complete
            for i in range(6):
                await asyncio.sleep(5)
                title = await page.evaluate("() => document.title")
                result["title"] = title
                print(f"  t+{(i+1)*5}s: '{title}'")
                if "moment" not in title.lower():
                    print("  → Challenge passed!")
                    break

            # Extract cookies
            cookies = await browser.cookies()
            result["all_cookies"] = [c.get("name", c) for c in cookies]
            cf = [c for c in cookies if c.get("name") == "cf_clearance"]
            if cf:
                result["cf_clearance_val"] = cf[0].get("value", "")
                result["cf_clearance"] = result["cf_clearance_val"][:40] + "…"
                result["cookie_str"] = "; ".join(
                    c.get("name","") + "=" + c.get("value","") for c in cookies
                )
                result["ua"] = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

            html = await page.evaluate("() => document.body ? document.body.innerHTML.substring(0,300) : 'no body'")
            result["html_snippet"] = html

        finally:
            await browser.stop()

    except Exception as e:
        result["error"] = str(e)
        import traceback; traceback.print_exc()

    elapsed = time.time() - t_start
    chrome_after = int(subprocess.run(
        "ps -e | grep -c chrome || true", shell=True, capture_output=True, text=True
    ).stdout.strip() or "0")

    print(f"\n--- Results ---")
    print(f"Elapsed: {elapsed:.1f}s")
    print(f"Final title: {result['title']}")
    print(f"cf_clearance: {result.get('cf_clearance', 'NONE — CHALLENGE NOT SOLVED')}")
    print(f"All cookies: {result['all_cookies']}")
    print(f"Chrome processes after: {chrome_after} (delta: {chrome_after - chrome_before})")
    if result["error"]:
        print(f"ERROR: {result['error']}")

    # If we got a cf_clearance, verify it works with plain urllib
    if result.get("cookie_str"):
        print("\n--- Verifying cookie works with plain urllib ---")
        try:
            req = urllib.request.Request(
                "https://www.car-parts.ie/car-parts/toyota/corolla/",
                headers={"User-Agent": result["ua"], "Cookie": result["cookie_str"],
                         "Accept": "text/html,application/xhtml+xml",
                         "Accept-Language": "en-IE,en;q=0.9",
                         "Referer": "https://www.car-parts.ie/"}
            )
            r = urllib.request.urlopen(req, timeout=30)
            html = r.read(2000).decode("utf-8", "replace")
            print(f"urllib status: {r.status} — {len(html)} chars received")
            is_real = "just a moment" not in html.lower() and "toyota" in html.lower()
            print(f"Real page content (not CF challenge): {is_real}")
            print(f"Snippet: {html[:250]}")
        except Exception as ex:
            print(f"urllib verify failed: {ex}")
    else:
        print("\nNo cf_clearance — cannot verify urllib path")

    return result

if __name__ == "__main__":
    asyncio.run(test_nodriver())
