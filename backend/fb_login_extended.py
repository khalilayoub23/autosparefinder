"""One-off: fb_browser_login with 10-min GUI wait via VNC."""
import asyncio, json, logging, os, pathlib, sys, time
sys.path.insert(0, '/app')
logging.basicConfig(level=logging.INFO, format="%(asctime)s [fb_login] %(message)s")
from playwright.async_api import async_playwright

_STATE_DIR = pathlib.Path("/app/state/fb_browser_session")
_COOKIES_FILE = _STATE_DIR / "cookies.json"
_LOGIN_URL = "https://www.facebook.com/login"
_HOME_URL = "https://www.facebook.com/"
log = logging.getLogger("fb_login")

async def _ss(page, label):
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(_STATE_DIR / f"{label}.png"), full_page=False)
    log.info("Screenshot → %s.png", label)

async def main():
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--no-sandbox","--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled","--start-maximized"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="he-IL",
        )
        page = await context.new_page()
        log.info("Navigating to Facebook login…")
        await page.goto(_LOGIN_URL, wait_until="networkidle", timeout=45_000)
        await asyncio.sleep(2)
        await _ss(page, "login_screen")
        log.info("=" * 60)
        log.info("  VNC is READY — connect to view the browser:")
        log.info("  SSH tunnel: ssh -L 5900:127.0.0.1:5900 root@161.97.158.177 -p 63159")
        log.info("  VNC viewer: localhost:5900  (no password)")
        log.info("  Log in to Facebook in the browser window.")
        log.info("  Waiting 10 MINUTES for you to complete login…")
        log.info("=" * 60)
        # Check every 30s for login completion during the 10-minute window
        for tick in range(20):  # 20 × 30s = 10 minutes
            await asyncio.sleep(30)
            try:
                cookies = await context.cookies()
                names = [c["name"] for c in cookies]
                if "c_user" in names and "xs" in names:
                    log.info("✅ Login DETECTED at tick %d (c_user + xs cookies present)!", tick+1)
                    break
                log.info("Tick %d/20 — still waiting (cookies so far: %s)…", tick+1, names)
            except Exception as e:
                log.warning("Tick %d error: %s", tick+1, e)

        log.info("Running health check…")
        try:
            await page.goto(_HOME_URL, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(3)
            content = await page.content()
            await _ss(page, "health_check_result")
        except Exception as e:
            log.warning("Health nav error: %s", e)
            content = ""
        cookies = await context.cookies()
        names = [c["name"] for c in cookies]
        c_user_ok = "c_user" in names
        xs_ok = "xs" in names
        login_form = 'id="email"' in content or 'name="login"' in content
        ok = not login_form and c_user_ok and xs_ok
        log.info("Health: c_user=%s xs=%s form=%s → %s", c_user_ok, xs_ok, login_form, "✅ OK" if ok else "❌ FAIL")
        _COOKIES_FILE.write_text(json.dumps(cookies, indent=2))
        log.info("Saved %d cookies → %s", len(cookies), _COOKIES_FILE)
        log.info("Cookie names: %s", names)
        await browser.close()
        return ok

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
