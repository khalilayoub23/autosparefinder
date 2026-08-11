"""
Script: social/facebook_browser/fb_browser_login.py
Purpose: One-time interactive Playwright login to establish the Facebook browser session.

         Run this ONCE to create an authenticated cookies.json. The production
         pipeline (GroupAgent, NOA) then reuses those cookies indefinitely until
         Facebook invalidates them.

Process:
  1. Starts Xvfb virtual display (if DISPLAY not already set).
  2. Launches Playwright Chromium in headed mode (needs a display).
  3. Navigates to Facebook login page.
  4. Takes a screenshot → /app/state/fb_browser_session/login_screen.png
  5. If FB_EMAIL + FB_PASSWORD env vars are set: types credentials and submits.
     Otherwise: waits 120 s for manual interaction (e.g. via VNC / X11 forward).
  6. After login detected: saves cookies → /app/state/fb_browser_session/cookies.json
  7. Runs health check to confirm the session is valid.

Usage:
  # Option A — automated (provide credentials via env):
  docker exec -e FB_EMAIL=... -e FB_PASSWORD=... autospare_backend \
      python3 /app/social/facebook_browser/fb_browser_login.py

  # Option B — interactive (X11 forwarding from your laptop to the server):
  # On your laptop: ssh -X user@161.97.158.177
  # Then in the container: docker exec -e DISPLAY=$DISPLAY autospare_backend \
  #     python3 /app/social/facebook_browser/fb_browser_login.py
  #
  # Option C — VNC (install x11vnc in container, connect on port 5900):
  # docker exec autospare_backend Xvfb :99 -screen 0 1280x800x24 &
  # docker exec autospare_backend x11vnc -display :99 -nopw -listen localhost -xkb &

Data Imported/Modified: /app/state/fb_browser_session/cookies.json
Data Sources: https://www.facebook.com/
Last Updated: 2026-08-10
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import subprocess
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [fb_login] %(message)s")
log = logging.getLogger("fb_login")

_STATE_DIR = pathlib.Path(os.getenv("STATE_DIR", "/app/state")) / "fb_browser_session"
_COOKIES_FILE = _STATE_DIR / "cookies.json"
_SCREENSHOT_DIR = _STATE_DIR
_LOGIN_URL = "https://mbasic.facebook.com/login"  # bare-HTML, no React/Bloks/CAPTCHA triggers
_LOGIN_URL_MOBILE = "https://m.facebook.com/login"
_LOGIN_URL_DESKTOP = "https://www.facebook.com/login"
_HOME_URL = "https://mbasic.facebook.com/"


def _start_xvfb(display: str = ":99") -> subprocess.Popen | None:
    """Start a virtual framebuffer if no display is set and Xvfb is available."""
    if os.environ.get("DISPLAY"):
        log.info("DISPLAY already set: %s — skipping Xvfb start", os.environ["DISPLAY"])
        return None

    try:
        proc = subprocess.Popen(
            ["Xvfb", display, "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.5)
        if proc.poll() is not None:
            log.error("Xvfb failed to start (exit code %s)", proc.returncode)
            return None
        os.environ["DISPLAY"] = display
        log.info("Xvfb started on %s (pid=%s)", display, proc.pid)
        return proc
    except FileNotFoundError:
        log.error("Xvfb not found — install it: apt-get install -y xvfb")
        return None


async def _screenshot(page, label: str) -> None:
    _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = str(_SCREENSHOT_DIR / f"{label}.png")
    await page.screenshot(path=path, full_page=False)
    log.info("Screenshot saved → %s", path)


def _prompt_credentials() -> tuple[str, str]:
    """Read credentials from terminal using getpass (no echo).

    Called only when FB_EMAIL / FB_PASSWORD are absent from the environment
    AND the script is connected to a real TTY (interactive shell).
    Password is never stored in memory beyond this function's scope — it is
    passed directly to the Playwright fill call and then deleted.
    """
    import getpass as _gp

    print("=" * 64)
    print("  Facebook login — enter credentials (input is hidden)")
    print("=" * 64)
    email = input("  Facebook email: ").strip()
    password = _gp.getpass("  Facebook password: ")
    return email, password


_RECAPTCHA_SITEKEY = "6LeyIlkaAAAAAE-EjcALU28lwxWPusUvGL3e0avS"


async def _try_capsolver(page, page_url: str) -> bool:
    """Solve reCAPTCHA Enterprise via CapSolver API and inject the token into the page.

    Requires CAPSOLVER_API_KEY env var. Returns True if token was injected and navigation
    away from the checkpoint was detected within 30s.

    Sitekey is the confirmed reCAPTCHA Enterprise key for Facebook's two_step_verification:
    6LeyIlkaAAAAAE-EjcALU28lwxWPusUvGL3e0avS

    To use: add CAPSOLVER_API_KEY=<your-key> to .env (get a key at capsolver.com).
    Cost: ~$0.80–3 per 1000 solves = fractions of a cent per login session.
    """
    api_key = os.environ.get("CAPSOLVER_API_KEY", "").strip()
    if not api_key:
        log.info("CAPSOLVER_API_KEY not set — skipping CapSolver solve")
        log.info("  To enable: add CAPSOLVER_API_KEY=<key> to .env and restart")
        return False

    try:
        import httpx
    except ImportError:
        log.warning("httpx not installed — cannot call CapSolver API")
        return False

    # Use the two_step_verification page URL (where the reCAPTCHA is embedded)
    # Clean to base URL without query params for the sitekey lookup
    base_url = page_url.split("?")[0].split("#")[0]
    log.info("CapSolver: submitting ReCaptchaV2EnterpriseTaskProxyless for %s", base_url[:80])

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            create_resp = await client.post(
                "https://api.capsolver.com/createTask",
                json={
                    "clientKey": api_key,
                    "task": {
                        "type": "ReCaptchaV2EnterpriseTaskProxyless",
                        "websiteURL": base_url,
                        "websiteKey": _RECAPTCHA_SITEKEY,
                    },
                },
            )
            create_data = create_resp.json()

        if create_data.get("errorId"):
            log.warning("CapSolver createTask error %s: %s",
                        create_data.get("errorId"), create_data.get("errorDescription"))
            return False

        task_id = create_data.get("taskId")
        if not task_id:
            log.warning("CapSolver: no taskId in response: %s", create_data)
            return False

        log.info("CapSolver task %s created — polling (up to 120s)…", task_id)

        token: str | None = None
        async with httpx.AsyncClient(timeout=30) as client:
            for _ in range(24):  # 24 × 5s = 120s
                await asyncio.sleep(5)
                result_resp = await client.post(
                    "https://api.capsolver.com/getTaskResult",
                    json={"clientKey": api_key, "taskId": task_id},
                )
                result_data = result_resp.json()
                status = result_data.get("status")
                if status == "ready":
                    token = result_data.get("solution", {}).get("gRecaptchaResponse", "")
                    log.info("CapSolver: token received (length=%d)", len(token))
                    break
                elif status == "failed":
                    log.warning("CapSolver task failed: %s", result_data.get("errorDescription"))
                    return False
                # status == "processing" → keep polling

        if not token:
            log.warning("CapSolver: timed out waiting for solution")
            return False

        # Inject the token into the page across all frames.
        # reCAPTCHA v2 Enterprise stores the response in a hidden textarea;
        # the callback then fires and Facebook auto-submits the verification form.
        injected = False
        for _frame in page.frames:
            try:
                result = await _frame.evaluate("""
                    (function(tok) {
                        // Set g-recaptcha-response textarea
                        const t = document.getElementById('g-recaptcha-response');
                        if (t) {
                            const setter = Object.getOwnPropertyDescriptor(
                                window.HTMLTextAreaElement.prototype, 'value').set;
                            setter.call(t, tok);
                            t.dispatchEvent(new Event('input',  {bubbles: true}));
                            t.dispatchEvent(new Event('change', {bubbles: true}));
                        }
                        // Fire any enterprise callback functions
                        try {
                            const cfg = window.___grecaptcha_cfg;
                            if (cfg && cfg.clients) {
                                Object.values(cfg.clients).forEach(function(c) {
                                    for (const k1 of Object.keys(c)) {
                                        for (const k2 of Object.keys(c[k1] || {})) {
                                            const obj = (c[k1] || {})[k2] || {};
                                            if (typeof obj.callback === 'function') {
                                                try { obj.callback(tok); } catch(e) {}
                                            }
                                        }
                                    }
                                });
                            }
                        } catch(e) {}
                        return !!t;
                    })
                """, token)
                if result:
                    log.info("CapSolver: token injected in frame %s", _frame.url[:60])
                    injected = True
                    break
            except Exception:
                continue

        if injected:
            log.info("CapSolver: token injected — waiting 15s for Facebook to process…")
            await asyncio.sleep(15)
            await _screenshot(page, "after_capsolver_inject")
            if "two_step" not in page.url.lower() and "checkpoint" not in page.url.lower():
                log.info("✅ CapSolver: CAPTCHA passed — navigated to %s", page.url[:80])
                return True
            else:
                log.warning("CapSolver: still on checkpoint after injection — page URL: %s", page.url[:80])
                return False
        else:
            log.warning("CapSolver: token obtained but could not inject into any frame")
            log.info("Frames: %s", [f.url[:60] for f in page.frames])
            return False

    except Exception as exc:
        log.warning("CapSolver exception: %s", exc)
        return False


async def login() -> bool:
    """Run the login flow. Returns True if session is authenticated at the end."""
    from playwright.async_api import async_playwright

    _STATE_DIR.mkdir(parents=True, exist_ok=True)

    fb_email = os.environ.get("FB_EMAIL", "")
    fb_password = os.environ.get("FB_PASSWORD", "")

    # If no env-var credentials: try interactive TTY prompt first, then
    # fall back to the 120s GUI-wait (for VNC / X11-forwarding sessions).
    if not (fb_email and fb_password):
        if sys.stdin.isatty():
            log.info("No FB_EMAIL/FB_PASSWORD env vars — prompting interactively (TTY detected)")
            fb_email, fb_password = _prompt_credentials()
        else:
            log.info("No credentials and no TTY — will wait 120s for GUI login")

    log.info("Launching headed Chromium (DISPLAY=%s)", os.environ.get("DISPLAY", "not set"))

    async with async_playwright() as p:
        # Headless mode: triggers reCAPTCHA Enterprise (checkbox) instead of Arkose Labs MatchKey
        # (which appears in headed/VNC mode). reCAPTCHA checkbox is clickable and sometimes passes.
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="he-IL",
            java_script_enabled=True,
            extra_http_headers={
                "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        # Apply stealth to context BEFORE creating any page — this is the correct API
        # for playwright-stealth v2.x (apply to context, not page).
        try:
            from playwright_stealth import Stealth
            await Stealth().apply_stealth_async(context)
            log.info("Stealth applied to browser context — automation fingerprints masked")
        except Exception as _se:
            log.warning("Stealth not applied: %s", _se)

        page = await context.new_page()

        log.info("Navigating to Facebook login page…")
        # networkidle ensures React has fully rendered the login form before we try to fill it.
        # domcontentloaded is too early — React components render after the initial HTML parse.
        await page.goto(_LOGIN_URL, wait_until="networkidle", timeout=45_000)
        await asyncio.sleep(2)
        await _screenshot(page, "login_screen")
        log.info("Login screenshot saved.")

        if fb_email and fb_password:
            log.info("Credentials provided — attempting automated login…")
            try:
                # Facebook's React login page does NOT always set id="email" on the input.
                # Use [name="email"] / [name="pass"] which are consistently present.
                # Wait explicitly for the email field to be attached and visible.
                email_selector = (
                    '[name="email"], '          # standard + React renders
                    '#email, '                  # classic desktop
                    'input[type="email"], '     # generic
                    'input[autocomplete="username"]'  # modern OAuth
                )
                log.info("Waiting for email field (selector: any of name/id/type/autocomplete)…")
                email_el = await page.wait_for_selector(email_selector, timeout=15_000, state="visible")
                await email_el.click()
                await asyncio.sleep(0.3)
                await email_el.fill(fb_email)
                log.info("Email filled.")
                await asyncio.sleep(0.5)

                pass_selector = '[name="pass"], #pass, input[type="password"]'
                pass_el = await page.wait_for_selector(pass_selector, timeout=10_000, state="visible")
                await pass_el.click()
                await asyncio.sleep(0.3)
                await pass_el.fill(fb_password)
                # Clear the in-memory reference immediately after use
                fb_password = None  # noqa: F841 (intentional clear)
                log.info("Password filled.")
                await asyncio.sleep(0.5)

                # Submit: Mobile Facebook (m.facebook.com) uses a React/Bloks UI with NO <form>
                # and NO <button type="submit">. The login button is a DIV with role="button".
                # Pressing Enter on the password field does NOT submit in this UI.
                # We must click the role="button" element with the "Log in" / "התחברות" text.
                log.info("Submitting login form…")
                submit_clicked = False
                for _btn_sel in (
                    # mbasic.facebook.com — classic HTML form submit button
                    'input[type="submit"][name="login"]',
                    'input[type="submit"]',
                    # Desktop React
                    '[data-testid="royal_login_button"]',
                    'button[type="submit"]',
                    '[name="login"]',
                    # Mobile Bloks UI: DIV with role="button"
                    '[role="button"]:has-text("Log in")',
                    '[role="button"]:has-text("Log In")',
                    '[role="button"]:has-text("התחברות")',
                ):
                    try:
                        _btn = await page.wait_for_selector(_btn_sel, timeout=3_000, state="visible")
                        await _btn.click()
                        submit_clicked = True
                        log.info("Submit button clicked (selector: %s)", _btn_sel)
                        break
                    except Exception:
                        continue
                if not submit_clicked:
                    log.info("No submit button found — falling back to Enter key on password field")
                    await pass_el.press("Enter")

                log.info("Submitted — waiting for navigation away from login page…")

                # Wait for URL to indicate we're past the login form:
                # - NOT on /login and NOT on bare m.facebook.com/ (which is still the mobile login page)
                # - AND NOT still showing the form (which appears at both /login and / when logged out)
                def _is_past_login(url: str) -> bool:
                    lower = url.lower()
                    if "/login" in lower:
                        return False
                    if "accounts.google" in lower:
                        return False
                    # Bare home pages (logged-out state) — still on login
                    if lower.rstrip("/") in (
                        "https://m.facebook.com",
                        "https://m.facebook.com/",
                        "https://www.facebook.com",
                        "https://www.facebook.com/",
                        "https://mbasic.facebook.com",
                        "https://mbasic.facebook.com/",
                    ):
                        return False
                    return True

                try:
                    await page.wait_for_url(_is_past_login, timeout=20_000)
                    await asyncio.sleep(2)
                    log.info("Navigated to: %s", page.url[:80])
                except Exception:
                    await asyncio.sleep(8)
                    log.warning("Still on login-like URL after wait: %s", page.url[:80])

                # Handle 2FA / checkpoint pages BEFORE saving the screenshot.
                # NOTE: /two_step_verification/ is Facebook's URL for BOTH reCAPTCHA Enterprise
                # (checkbox "אני לא רובוט") and real 2FA code prompts. We detect which one it is
                # by checking for a reCAPTCHA iframe on the page — if present, it's CAPTCHA, not 2FA.
                current_url = page.url.lower()
                if any(kw in current_url for kw in ("two_step", "checkpoint", "approvals", "login_approvals")):
                    await asyncio.sleep(2)
                    await _screenshot(page, "two_fa_page")
                    log.info("⚠️  Security checkpoint at: %s", page.url[:80])

                    # --- Detect reCAPTCHA vs real 2FA ---
                    page_html = await page.content()
                    is_captcha = "recaptcha" in page_html.lower() or "g-recaptcha" in page_html.lower()

                    if is_captcha:
                        # Check whether this is Arkose Labs MatchKey (most common from datacenter IPs)
                        # or classic reCAPTCHA Enterprise.
                        is_arkose = "arkose" in page_html.lower() or "matchkey" in page_html.lower() or "התחל" in page_html

                        # Check which challenge type: Arkose Labs vs reCAPTCHA Enterprise
                        # In headless mode we typically get reCAPTCHA Enterprise (easier to click);
                        # in headed/VNC mode we get Arkose Labs MatchKey (visual puzzle).
                        _rc_anchor_frame = None
                        for _f in page.frames:
                            if "recaptcha" in _f.url and "anchor" in _f.url:
                                _rc_anchor_frame = _f
                                log.info("Found reCAPTCHA anchor frame: %s…", _f.url[:80])
                                break

                        if _rc_anchor_frame:
                            # reCAPTCHA Enterprise — click the checkbox
                            log.info("reCAPTCHA Enterprise (checkbox) — attempting click via anchor frame…")
                            await asyncio.sleep(3)  # wait for iframe to fully render
                            try:
                                _cb = await _rc_anchor_frame.wait_for_selector(
                                    '#recaptcha-anchor, .recaptcha-checkbox-border',
                                    timeout=10_000, state="visible"
                                )
                                await _cb.click()
                                log.info("✅ reCAPTCHA checkbox clicked — waiting 8s for evaluation…")
                                await asyncio.sleep(8)
                                await _screenshot(page, "after_recaptcha_click")
                                _rc_url = page.url.lower()
                                if not any(kw in _rc_url for kw in ("two_step", "checkpoint")):
                                    log.info("✅ reCAPTCHA PASSED — now at: %s", page.url[:80])
                                else:
                                    # Check if a bframe appeared (image challenge)
                                    _bframe = next((f for f in page.frames if "bframe" in f.url), None)
                                    if _bframe:
                                        log.info("reCAPTCHA image challenge appeared — trying CapSolver…")
                                        _capsolver_ok = await _try_capsolver(page, page.url)
                                        if not _capsolver_ok:
                                            log.warning("CapSolver not available or failed — import cookies manually:")
                                            log.info("  python3 /app/maintenance/fb_cookie_import.py")
                                    else:
                                        log.warning("reCAPTCHA still on checkpoint — trying CapSolver…")
                                        _capsolver_ok = await _try_capsolver(page, page.url)
                                        if not _capsolver_ok:
                                            log.warning("CapSolver unavailable — import cookies manually:")
                                            log.info("  python3 /app/maintenance/fb_cookie_import.py")
                                    # Wait for navigation after any solver attempt
                                    for _w in range(36):
                                        await asyncio.sleep(5)
                                        if "two_step" not in page.url.lower() and "checkpoint" not in page.url.lower():
                                            log.info("✅ Challenge cleared — now at: %s", page.url[:80])
                                            break
                            except Exception as _rce:
                                log.warning("reCAPTCHA anchor click error: %s", _rce)
                                log.info("Trying CapSolver as fallback…")
                                _capsolver_ok = await _try_capsolver(page, page.url)
                                if not _capsolver_ok:
                                    log.warning("CapSolver unavailable — import cookies manually:")
                                    log.info("  python3 /app/maintenance/fb_cookie_import.py")
                                for _w in range(36):
                                    await asyncio.sleep(5)
                                    if "two_step" not in page.url.lower() and "checkpoint" not in page.url.lower():
                                        log.info("✅ Challenge cleared — now at: %s", page.url[:80])
                                        break

                        elif is_arkose:
                            log.info("Arkose Labs MatchKey challenge detected")
                            # Step 1: click the Start/התחל button — this transitions to reCAPTCHA checkbox
                            _arkose_started = False
                            for _start_sel in (
                                '[role="button"]:has-text("התחל")',
                                '[role="button"]:has-text("Start")',
                                'button:has-text("התחל")',
                                'button:has-text("Start")',
                            ):
                                try:
                                    _start_btn = await page.wait_for_selector(_start_sel, timeout=4_000, state="visible")
                                    await _start_btn.click()
                                    log.info("Clicked Start/התחל button for Arkose challenge")
                                    _arkose_started = True
                                    await asyncio.sleep(3)
                                    break
                                except Exception:
                                    continue
                            await _screenshot(page, "arkose_puzzle")

                            # Step 2: after Start, Facebook typically shows reCAPTCHA Enterprise
                            await asyncio.sleep(4)  # let any iframe load
                            await _screenshot(page, "arkose_after_start")
                            _post_arkose_html = await page.content()
                            log.info("After Arkose Start — page URL: %s", page.url[:80])

                            _rc_clicked = False
                            if "recaptcha" in _post_arkose_html.lower():
                                log.info("reCAPTCHA detected after Arkose — using page.frames to find anchor iframe…")
                                # Use page.frames directly (more reliable than frame_locator)
                                _rc_anchor_frame = None
                                for _f in page.frames:
                                    if "recaptcha" in _f.url and "anchor" in _f.url:
                                        _rc_anchor_frame = _f
                                        log.info("Found reCAPTCHA anchor frame: %s", _f.url[:80])
                                        break
                                if not _rc_anchor_frame:
                                    # Try any recaptcha frame
                                    for _f in page.frames:
                                        if "recaptcha" in _f.url or "google.com/recaptcha" in _f.url:
                                            _rc_anchor_frame = _f
                                            log.info("Found reCAPTCHA frame (fallback): %s", _f.url[:80])
                                            break

                                if _rc_anchor_frame:
                                    try:
                                        _cb = await _rc_anchor_frame.wait_for_selector(
                                            '#recaptcha-anchor, .recaptcha-checkbox, [role="checkbox"]',
                                            timeout=8_000, state="visible"
                                        )
                                        await _cb.click()
                                        log.info("✅ reCAPTCHA checkbox clicked via frame reference")
                                        await asyncio.sleep(8)
                                        await _screenshot(page, "after_recaptcha_click")
                                        _rc_clicked = True
                                        _rc_url = page.url.lower()
                                        if not any(kw in _rc_url for kw in ("two_step", "checkpoint")):
                                            log.info("✅ reCAPTCHA passed — now at: %s", page.url[:80])
                                        else:
                                            log.warning("reCAPTCHA still showing after click — waiting 180s for manual VNC solve")
                                            log.info("VNC: ssh -L 5900:127.0.0.1:5900 root@161.97.158.177 -p 63159")
                                    except Exception as _rce:
                                        log.warning("reCAPTCHA frame click failed: %s", _rce)
                                else:
                                    log.warning("No reCAPTCHA anchor frame found in page.frames: %s",
                                                [f.url[:60] for f in page.frames])

                            if not _rc_clicked:
                                log.info("Waiting 180s for manual VNC solve — ssh -L 5900:127.0.0.1:5900 root@161.97.158.177 -p 63159")
                            for _w in range(36):  # 180s total
                                await asyncio.sleep(5)
                                if "two_step" not in page.url.lower() and "checkpoint" not in page.url.lower():
                                    log.info("✅ Challenge cleared — now at: %s", page.url[:80])
                                    break
                        else:
                            # Classic reCAPTCHA Enterprise checkbox
                            log.info("reCAPTCHA Enterprise checkbox detected — attempting click…")
                            try:
                                captcha_frame = page.frame_locator(
                                    'iframe[src*="recaptcha"], iframe[src*="google.com/recaptcha"], '
                                    'iframe[title*="reCAPTCHA"]'
                                )
                                checkbox = captcha_frame.locator(
                                    '#recaptcha-anchor, .recaptcha-checkbox, [role="checkbox"]'
                                )
                                cnt = await checkbox.count()
                                log.info("reCAPTCHA checkbox elements found: %d", cnt)
                                if cnt > 0:
                                    await checkbox.first.click(timeout=8_000)
                                    log.info("Checkbox clicked — waiting 6s for evaluation…")
                                    await asyncio.sleep(6)
                                    await _screenshot(page, "after_recaptcha_click")
                                    new_url = page.url.lower()
                                    if not any(kw in new_url for kw in ("two_step", "recaptcha", "checkpoint")):
                                        log.info("✅ reCAPTCHA passed — now at: %s", page.url[:80])
                                    else:
                                        log.warning("reCAPTCHA still showing — waiting 120s for manual VNC solve")
                                        log.info("VNC: ssh -L 5900:127.0.0.1:5900 root@161.97.158.177 -p 63159")
                                        await asyncio.sleep(120)
                                else:
                                    log.warning("No reCAPTCHA checkbox found — waiting 120s for manual solve")
                                    log.info("VNC: ssh -L 5900:127.0.0.1:5900 root@161.97.158.177 -p 63159")
                                    await asyncio.sleep(120)
                            except Exception as _ce:
                                log.warning("reCAPTCHA click failed: %s — waiting 120s", _ce)
                                log.info("VNC: ssh -L 5900:127.0.0.1:5900 root@161.97.158.177 -p 63159")
                                await asyncio.sleep(120)

                    else:
                        # Real 2FA code prompt (SMS / authenticator app code)
                        log.info("2FA code prompt detected")
                        code_selector = (
                            '[name="approvals_code"], #approvals_code, '
                            'input[type="number"], input[inputmode="numeric"], '
                            'input[autocomplete="one-time-code"]'
                        )
                        if sys.stdin.isatty():
                            import getpass as _gp
                            print("\n" + "=" * 64)
                            print("  Facebook requires 2FA / approval code")
                            print("  Check your phone (SMS / authenticator app) for the code")
                            print("=" * 64)
                            code = _gp.getpass("  Enter the 6-digit code: ").strip()
                            if code:
                                try:
                                    code_el = await page.wait_for_selector(code_selector, timeout=10_000, state="visible")
                                    await code_el.fill(code)
                                    await asyncio.sleep(0.5)
                                    await code_el.press("Enter")
                                    log.info("2FA code submitted — waiting for navigation…")
                                    try:
                                        await page.wait_for_url(
                                            lambda url: "two_step" not in url and "checkpoint" not in url,
                                            timeout=15_000,
                                        )
                                        await asyncio.sleep(2)
                                        log.info("2FA navigation complete: %s", page.url[:80])
                                    except Exception:
                                        await asyncio.sleep(6)
                                        log.warning("Still on 2FA/checkpoint page: %s", page.url[:80])
                                except Exception as e2:
                                    log.error("2FA code entry failed: %s", e2)
                                    await asyncio.sleep(120)
                            else:
                                log.info("No code entered — waiting 120s for manual 2FA completion…")
                                await asyncio.sleep(120)
                        else:
                            # No TTY — poll for a code written to a file by the operator
                            _2fa_code_file = _STATE_DIR / "twofa_code.txt"
                            _2fa_code_file.unlink(missing_ok=True)
                            log.info(
                                "No TTY — write the 6-digit 2FA code to: %s  (polling for 3 min)",
                                _2fa_code_file,
                            )
                            _code_entered = False
                            for _tick in range(36):  # 36 × 5s = 3 minutes
                                await asyncio.sleep(5)
                                if _2fa_code_file.exists():
                                    _code = _2fa_code_file.read_text().strip()
                                    _2fa_code_file.unlink(missing_ok=True)
                                    if _code:
                                        log.info("2FA code read from file — submitting…")
                                        try:
                                            _el = await page.wait_for_selector(code_selector, timeout=10_000, state="visible")
                                            await _el.fill(_code)
                                            await asyncio.sleep(0.5)
                                            await _el.press("Enter")
                                            log.info("2FA code submitted")
                                            try:
                                                await page.wait_for_url(
                                                    lambda url: "two_step" not in url and "checkpoint" not in url,
                                                    timeout=15_000,
                                                )
                                                await asyncio.sleep(2)
                                                log.info("2FA navigation complete: %s", page.url[:80])
                                            except Exception:
                                                await asyncio.sleep(6)
                                                log.warning("Still on 2FA page after submit: %s", page.url[:80])
                                            _code_entered = True
                                            break
                                        except Exception as _e2:
                                            log.error("2FA fill failed: %s", _e2)
                                            break
                            if not _code_entered:
                                log.warning("2FA code not received in 3 min — session may be incomplete")

                # Only save a screenshot AFTER navigation away from the login form.
                # Taking it while still on the login page would capture the filled credentials.
                if "login" not in page.url.lower():
                    await _screenshot(page, "after_login")
                    log.info("After-login screenshot saved (navigated away from login form).")
                else:
                    log.warning("Did not navigate away from login — skipping after_login screenshot to avoid credential capture.")
            except Exception as exc:
                log.error("Automated login failed: %s", exc)
                await _screenshot(page, "login_error")
                log.info("Falling back to manual wait…")

        else:
            log.info(
                "\n"
                "================================================================\n"
                "  No credentials provided and no TTY available.\n"
                "  If you have a display (VNC / X11 forwarding), log in manually.\n"
                "  Waiting 120 seconds for you to complete login in the browser…\n"
                "================================================================"
            )
            await asyncio.sleep(120)

        # Health check — navigate to home and check for login form ABSENCE.
        # Use mobile URL since we logged in via m.facebook.com; desktop will still work
        # if cookies are set on .facebook.com domain.
        log.info("Running health check…")
        hc_url = "https://mbasic.facebook.com/home.php"
        await page.goto(hc_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3)
        content = await page.content()
        await _screenshot(page, "health_check_result")
        log.info("Health check URL after navigation: %s", page.url[:100])

        login_form_present = 'name="login"' in content or ('id="email"' in content and 'id="pass"' in content)
        cookie_names = [c["name"] for c in await context.cookies()]
        c_user_present = "c_user" in cookie_names
        xs_present = "xs" in cookie_names

        logged_in = not login_form_present and c_user_present and xs_present

        if logged_in:
            log.info("✅ Health check PASSED — session is authenticated (c_user+xs present, no login form)")
        elif login_form_present:
            log.warning("❌ Health check FAILED — login form visible (not authenticated)")
        else:
            log.warning("❌ Health check FAILED — c_user=%s xs=%s (cookies missing)",
                        "present" if c_user_present else "MISSING",
                        "present" if xs_present else "MISSING")

        cookies = await context.cookies()
        _COOKIES_FILE.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        log.info("Saved %d cookies → %s", len(cookies), _COOKIES_FILE)

        # Log cookie names (not values) for transparency
        names = [c["name"] for c in cookies]
        log.info("Cookie names: %s", names)
        log.info(
            "Session %s (c_user=%s, xs=%s)",
            "AUTHENTICATED" if logged_in else "NOT AUTHENTICATED",
            "present" if c_user_present else "MISSING",
            "present" if xs_present else "MISSING",
        )

        await browser.close()
        return logged_in


def main() -> None:
    xvfb_proc = None
    try:
        # Headless Chromium does NOT need a display. Only start/check Xvfb for headed mode.
        # (Currently we launch headless=True so the display block is a no-op, but kept for
        # when headed mode is re-enabled for VNC-based manual challenge solving.)
        if not os.environ.get("DISPLAY"):
            # Try :99 first (may already be running)
            os.environ["DISPLAY"] = ":99"
            xvfb_proc = _start_xvfb(":99")  # no-op if already running; sets DISPLAY

        success = asyncio.run(login())
        if success:
            log.info("✅ Facebook session established successfully.")
            log.info("The production pipeline is now ready for Group publishing.")
            sys.exit(0)
        else:
            log.error("❌ Facebook session NOT authenticated. Run this script again after login.")
            sys.exit(1)

    finally:
        if xvfb_proc:
            xvfb_proc.terminate()
            log.info("Xvfb stopped.")


if __name__ == "__main__":
    main()
