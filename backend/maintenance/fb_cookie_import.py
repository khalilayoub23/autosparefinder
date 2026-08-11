"""
Script: maintenance/fb_cookie_import.py
Purpose: Import Facebook session cookies from a real browser into the platform's
         Playwright cookies.json — bypassing the CAPTCHA entirely by reusing an
         already-authenticated browser session.

Process:
  1. User logs into Facebook normally in their own browser.
  2. Opens DevTools → Application → Cookies → https://www.facebook.com
  3. Copies the values of: c_user, xs, datr, sb, fr
  4. Runs this script with those values (or lets it prompt).
  5. Script writes cookies.json and runs a health check.

Usage:
  # Interactive (prompts for each value):
  docker exec -it autospare_backend python3 /app/maintenance/fb_cookie_import.py

  # Batch via env vars (non-interactive):
  docker exec -e FB_C_USER=... -e FB_XS=... -e FB_DATR=... -e FB_SB=... \
      autospare_backend python3 /app/maintenance/fb_cookie_import.py

  # Show cookies.json status without overwriting:
  docker exec autospare_backend python3 /app/maintenance/fb_cookie_import.py --check

Data Imported/Modified: /app/state/fb_browser_session/cookies.json
Data Sources: User's browser DevTools (Application > Cookies > facebook.com)
Last Updated: 2026-08-10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import pathlib
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [fb_cookie_import] %(message)s")
log = logging.getLogger("fb_cookie_import")

_STATE_DIR = pathlib.Path(os.getenv("STATE_DIR", "/app/state")) / "fb_browser_session"
_COOKIES_FILE = _STATE_DIR / "cookies.json"

# All five critical Facebook session cookies and their Playwright attributes.
# domain MUST be ".facebook.com" (dot-prefix = works for all subdomains).
# sameSite, secure, httpOnly must match what Facebook sets or Playwright will reject.
_COOKIE_SPECS = [
    {"name": "c_user",  "domain": ".facebook.com", "httpOnly": False, "secure": True,  "sameSite": "Lax",  "path": "/"},
    {"name": "xs",      "domain": ".facebook.com", "httpOnly": True,  "secure": True,  "sameSite": "Strict", "path": "/"},
    {"name": "datr",    "domain": ".facebook.com", "httpOnly": True,  "secure": True,  "sameSite": "None", "path": "/"},
    {"name": "sb",      "domain": ".facebook.com", "httpOnly": True,  "secure": True,  "sameSite": "None", "path": "/"},
    {"name": "fr",      "domain": ".facebook.com", "httpOnly": False, "secure": True,  "sameSite": "None", "path": "/"},
]


def _read_env_cookies() -> dict[str, str]:
    """Read cookie values from environment variables (non-interactive mode)."""
    return {
        "c_user": os.environ.get("FB_C_USER", ""),
        "xs":     os.environ.get("FB_XS",     ""),
        "datr":   os.environ.get("FB_DATR",   ""),
        "sb":     os.environ.get("FB_SB",     ""),
        "fr":     os.environ.get("FB_FR",     ""),
    }


def _prompt_cookies() -> dict[str, str]:
    """Interactively prompt for each cookie value (no echo for xs — it's session-sensitive)."""
    import getpass as _gp

    print()
    print("=" * 68)
    print("  Facebook Cookie Import")
    print()
    print("  Steps:")
    print("  1. Open facebook.com in your browser (must be logged in)")
    print("  2. Open DevTools (F12) → Application → Storage → Cookies")
    print("  3. Select https://www.facebook.com in the left pane")
    print("  4. Find each cookie by name and copy its VALUE column")
    print("  5. Paste each value below (xs input is hidden for security)")
    print("=" * 68)
    print()

    values: dict[str, str] = {}
    for name in ("c_user", "xs", "datr", "sb", "fr"):
        if name == "xs":
            # xs is session-sensitive — hide input
            val = _gp.getpass(f"  {name} (hidden): ").strip()
        else:
            val = input(f"  {name}: ").strip()
        values[name] = val

    print()
    return values


def _build_cookies(values: dict[str, str]) -> list[dict]:
    """Build a Playwright-compatible cookies list from the raw values."""
    # Expiry: 365 days from now (Facebook cookies typically last 1+ year)
    expires = int(time.time()) + 365 * 24 * 3600
    cookies = []
    for spec in _COOKIE_SPECS:
        name = spec["name"]
        val = values.get(name, "").strip()
        if not val:
            continue
        cookie = {**spec, "value": val, "expires": expires}
        cookies.append(cookie)
    return cookies


async def _health_check(cookies: list[dict]) -> bool:
    """Load cookies into a headless browser and verify the session is authenticated."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.error("playwright not installed")
        return False

    log.info("Running health check with imported cookies…")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        try:
            await page.goto("https://mbasic.facebook.com/home.php",
                            wait_until="domcontentloaded", timeout=25_000)
            await asyncio.sleep(3)
            content = await page.content()
            final_url = page.url

            login_form = 'name="login"' in content or (
                'id="email"' in content and 'id="pass"' in content
            )
            live_cookies = await context.cookies()
            live_names = [c["name"] for c in live_cookies]
            has_c_user = "c_user" in live_names
            has_xs     = "xs" in live_names

            log.info("Health check URL: %s", final_url[:100])
            log.info("Cookie names after navigation: %s", live_names)

            if login_form:
                log.warning("❌ Login form detected — cookies are invalid or expired")
                await browser.close()
                return False

            if has_c_user and has_xs:
                log.info("✅ Health check PASSED — c_user + xs present, no login form")
                # Re-save the live cookies (Facebook may refresh some values on navigation)
                _STATE_DIR.mkdir(parents=True, exist_ok=True)
                _COOKIES_FILE.write_text(json.dumps(live_cookies, indent=2), encoding="utf-8")
                log.info("Saved %d live cookies → %s", len(live_cookies), _COOKIES_FILE)
                await browser.close()
                return True

            log.warning("❌ c_user=%s xs=%s — session not authenticated",
                        "present" if has_c_user else "MISSING",
                        "present" if has_xs else "MISSING")
            await browser.close()
            return False

        except Exception as exc:
            log.error("Health check error: %s", exc)
            await browser.close()
            return False


def _check_existing() -> None:
    """Print the current cookies.json status (names only, never values)."""
    if not _COOKIES_FILE.exists():
        log.info("No cookies.json found at %s", _COOKIES_FILE)
        return
    try:
        cookies = json.loads(_COOKIES_FILE.read_text())
        names = [c["name"] for c in cookies]
        has_c_user = "c_user" in names
        has_xs = "xs" in names
        log.info("Existing cookies.json — %d cookies: %s", len(cookies), names)
        log.info("Session status: c_user=%s xs=%s → %s",
                 "present" if has_c_user else "MISSING",
                 "present" if has_xs else "MISSING",
                 "AUTHENTICATED" if (has_c_user and has_xs) else "NOT AUTHENTICATED")
    except Exception as exc:
        log.error("Failed to read cookies.json: %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Facebook cookies from a real browser")
    parser.add_argument("--check", action="store_true", help="Only show current cookies.json status")
    args = parser.parse_args()

    if args.check:
        _check_existing()
        return

    # Read cookie values
    env_values = _read_env_cookies()
    has_env = all(env_values.get(n) for n in ("c_user", "xs"))

    if has_env:
        log.info("Reading cookies from environment variables (FB_C_USER, FB_XS, FB_DATR, FB_SB, FB_FR)")
        values = env_values
    elif sys.stdin.isatty():
        values = _prompt_cookies()
    else:
        log.error("No cookie values provided and no TTY for interactive input.")
        log.error("Set FB_C_USER + FB_XS (+ optionally FB_DATR FB_SB FB_FR) in environment,")
        log.error("or run with -it flag for interactive prompt.")
        sys.exit(1)

    # Validate minimum required cookies
    missing = [n for n in ("c_user", "xs") if not values.get(n)]
    if missing:
        log.error("Required cookies missing: %s — cannot build a valid session", missing)
        sys.exit(1)

    # Build and save cookie list
    cookies = _build_cookies(values)
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _COOKIES_FILE.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
    log.info("Saved %d cookies → %s (names: %s)",
             len(cookies), _COOKIES_FILE, [c["name"] for c in cookies])

    # Health check
    ok = asyncio.run(_health_check(cookies))
    if ok:
        log.info("✅ Facebook session is AUTHENTICATED and ready for Group publishing.")
        sys.exit(0)
    else:
        log.error("❌ Session health check failed — cookies may be stale or incorrect.")
        log.error("Try again with fresh cookie values from DevTools.")
        sys.exit(1)


if __name__ == "__main__":
    main()
