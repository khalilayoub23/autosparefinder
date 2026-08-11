"""
One-shot: login to Facebook via mbasic.facebook.com.
Handles reCAPTCHA checkbox by clicking it inside the iframe.
Falls back to VNC wait if image-challenge appears.
"""
import asyncio, json, logging, os, pathlib, sys
logging.basicConfig(level=logging.INFO, format="%(asctime)s [fb_mbasic] %(message)s")
log = logging.getLogger("fb_mbasic")

_STATE = pathlib.Path("/app/state/fb_browser_session")
_COOKIES_FILE = _STATE / "cookies.json"
FB_EMAIL = os.environ["FB_EMAIL"]
FB_PASSWORD = os.environ["FB_PASSWORD"]


async def _try_click_recaptcha(page) -> bool:
    """Attempt to click the reCAPTCHA checkbox inside its iframe."""
    try:
        # Wait for the reCAPTCHA iframe
        rc_frame_el = await page.wait_for_selector(
            'iframe[title="reCAPTCHA"], iframe[src*="recaptcha"]', timeout=5_000
        )
        frame = await rc_frame_el.content_frame()
        if not frame:
            log.info("reCAPTCHA iframe frame not available")
            return False
        checkbox = await frame.wait_for_selector('.recaptcha-checkbox-border', timeout=5_000)
        await checkbox.click()
        log.info("Clicked reCAPTCHA checkbox — waiting for verification…")
        await asyncio.sleep(8)
        return True
    except Exception as exc:
        log.info("reCAPTCHA click attempt: %s", exc)
        return False


async def _handle_checkpoint(page) -> bool:
    """Handle the Facebook security checkpoint (reCAPTCHA or save-device page)."""
    url = page.url
    log.info("Checkpoint/2FA URL: %s", url[:120])
    await page.screenshot(path=str(_STATE / "mbasic_03_checkpoint.png"), full_page=False)

    content = await page.content()
    log.info("Page excerpt: %.200s", content[:200])

    # 1. Try save-device / continue buttons first (sometimes appears before reCAPTCHA)
    for skip_sel in [
        'input[name="submit[Continue]"]',
        'input[value="OK"]',
        'input[type="submit"]',
        'button:has-text("Continue")',
        'a:has-text("Skip")',
    ]:
        try:
            el = await page.wait_for_selector(skip_sel, timeout=1_500)
            if el:
                log.info("Clicking skip/continue selector: %s", skip_sel)
                await el.click()
                await asyncio.sleep(6)
                await page.screenshot(path=str(_STATE / "mbasic_04_after_skip.png"), full_page=False)
                log.info("After skip URL: %s", page.url)
                return True
        except Exception:
            pass

    # 2. Try clicking the reCAPTCHA checkbox
    clicked = await _try_click_recaptcha(page)
    if clicked:
        await page.screenshot(path=str(_STATE / "mbasic_04_after_recaptcha.png"), full_page=False)
        log.info("After reCAPTCHA click URL: %s", page.url)
        # Check if we passed — if still on checkpoint, look for a submit button
        if "checkpoint" not in page.url and "two_step" not in page.url:
            log.info("reCAPTCHA passed — no longer on checkpoint page")
            return True
        # reCAPTCHA might have revealed a submit button
        for sel in ['input[type="submit"]', 'button[type="submit"]']:
            try:
                btn = await page.wait_for_selector(sel, timeout=3_000)
                if btn:
                    log.info("Clicking post-reCAPTCHA submit: %s", sel)
                    await btn.click()
                    await asyncio.sleep(5)
                    return True
            except Exception:
                pass

    # 3. VNC fallback — wait 5 minutes for user to solve manually
    log.info("Automated checkpoint handling didn't clear — waiting 5 min for VNC")
    log.info("VNC tunnel: ssh -L 5900:127.0.0.1:5900 root@161.97.158.177 -p 63159")
    log.info("Connect VNC viewer to localhost:5900, solve the checkpoint in the browser")
    for tick in range(10):
        await asyncio.sleep(30)
        current_url = page.url
        if "checkpoint" not in current_url and "two_step" not in current_url:
            log.info("Checkpoint cleared at tick %d", tick + 1)
            return True
        # Try again on current page state
        await _try_click_recaptcha(page)
        log.info("VNC tick %d/10 — still on checkpoint", tick + 1)
    return False


async def main() -> bool:
    from playwright.async_api import async_playwright

    _STATE.mkdir(parents=True, exist_ok=True)

    existing = []
    if _COOKIES_FILE.exists():
        try:
            existing = json.loads(_COOKIES_FILE.read_text())
            log.info("Loaded %d existing cookies: %s", len(existing), [c["name"] for c in existing])
        except Exception:
            pass

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox", "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="he-IL",
        )
        if existing:
            await ctx.add_cookies(existing)
            log.info("Injected existing device cookies")

        page = await ctx.new_page()

        log.info("Navigating to mbasic login…")
        await page.goto("https://mbasic.facebook.com/login", wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3)
        await page.screenshot(path=str(_STATE / "mbasic_01_landing.png"), full_page=False)
        log.info("Landing URL: %s", page.url)

        content = await page.content()
        if "log_out_click" in content or "/logout" in content:
            log.info("Already authenticated — skipping form")
        else:
            # Fill credentials
            try:
                email_el = await page.wait_for_selector('input[name="email"]', timeout=10_000)
                await email_el.fill(FB_EMAIL)
                log.info("Email filled")
                await asyncio.sleep(0.5)

                pass_el = await page.wait_for_selector('input[name="pass"]', timeout=10_000)
                await pass_el.fill(FB_PASSWORD)
                log.info("Password filled — pressing Enter")
                await asyncio.sleep(0.5)
                await pass_el.press("Enter")
                await asyncio.sleep(8)
                await page.screenshot(path=str(_STATE / "mbasic_02_after_submit.png"), full_page=False)
                log.info("After submit URL: %s", page.url)
            except Exception as exc:
                log.error("Form error: %s", exc)
                await page.screenshot(path=str(_STATE / "mbasic_err.png"), full_page=False)
                await browser.close()
                return False

        # Handle checkpoint if needed
        url = page.url
        if any(x in url for x in ("checkpoint", "two_step", "login/device", "save-device", "recaptcha")):
            cleared = await _handle_checkpoint(page)
            if not cleared:
                log.error("Could not clear checkpoint — aborting")
                await browser.close()
                return False

        # Navigate to home to confirm session
        await page.goto("https://mbasic.facebook.com/", wait_until="domcontentloaded", timeout=20_000)
        await asyncio.sleep(3)
        await page.screenshot(path=str(_STATE / "mbasic_05_home.png"), full_page=False)

        cookies = await ctx.cookies()
        names = [c["name"] for c in cookies]
        c_user_ok = "c_user" in names
        xs_ok = "xs" in names
        log.info("Final cookies: %s", names)
        log.info("c_user=%s  xs=%s", c_user_ok, xs_ok)

        if c_user_ok and xs_ok:
            for c in cookies:
                c.setdefault("sameSite", "None")
            _COOKIES_FILE.write_text(json.dumps(cookies, indent=2))
            log.info("✅ SUCCESS — %d cookies saved", len(cookies))
            await browser.close()
            return True
        else:
            log.error("❌ FAILED — c_user/xs missing")
            await browser.close()
            return False


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
