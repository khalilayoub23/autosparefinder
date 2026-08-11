"""
Script: social/facebook_browser/session.py
Purpose: Persistent Playwright browser session for Facebook Group operations.
         Manages cookie persistence, session health-checks, and human-like
         timing so the automated browser is not flagged as a bot.

Process:
  1. On first run: browser opens, waits for manual login confirmation from owner.
  2. Cookies are saved to /app/state/fb_browser_session/cookies.json.
  3. On subsequent runs: cookies are loaded; a lightweight health-check
     (fetch the FB home page, confirm we're logged in) validates the session.
  4. If the health-check fails: session is invalidated, owner is notified via
     WhatsApp to log in again.
  5. All page interactions use human-like random delays (0.8–3.5s).

Data Imported/Modified: /app/state/fb_browser_session/cookies.json
Data Sources: https://www.facebook.com/
Last Updated: 2026-08-06
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("fb_browser.session")

_STATE_DIR = Path(os.getenv("STATE_DIR", "/app/state")) / "fb_browser_session"
_COOKIES_FILE = _STATE_DIR / "cookies.json"
_SCREENSHOT_DIR = Path(os.getenv("STATE_DIR", "/app/state")) / "logs" / "fb_browser_failures"
_HEALTH_URL = "https://www.facebook.com/"
# "marketplace" appears even on the public (logged-out) homepage — unreliable.
# Instead: when logged OUT the home page contains the login form (#email / #loginbutton).
# When logged IN those elements are absent and the news-feed compositor is present.
# Use the ABSENCE of the login form as the primary signal; fall back to a JS DOM check.
_LOGIN_INDICATOR = "marketplace"  # kept for legacy compat — not used in _health_check


async def _random_delay(min_s: float = 0.8, max_s: float = 3.5) -> None:
    """Human-like pause between actions."""
    await asyncio.sleep(random.uniform(min_s, max_s))


def _save_cookies(cookies: list[dict]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _COOKIES_FILE.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
    log.info("fb_browser: saved %d cookies", len(cookies))


def _load_cookies() -> list[dict]:
    if _COOKIES_FILE.exists():
        try:
            return json.loads(_COOKIES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


_MAX_FAILURE_SCREENSHOTS = 50


async def _save_failure_screenshot(page: Any, label: str) -> None:
    try:
        _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        # Rotate: keep at most _MAX_FAILURE_SCREENSHOTS files on disk
        existing = sorted(_SCREENSHOT_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime)
        while len(existing) >= _MAX_FAILURE_SCREENSHOTS:
            try:
                existing.pop(0).unlink(missing_ok=True)
            except Exception:
                break
        ts = int(time.time())
        path = str(_SCREENSHOT_DIR / f"{label}_{ts}.png")
        # full_page=False: avoids capturing potentially large pages with sensitive DOM
        await page.screenshot(path=path, full_page=False)
        log.info("fb_browser: failure screenshot saved → %s", path)
    except Exception as exc:
        log.debug("fb_browser: screenshot failed: %s", exc)


class FacebookSession:
    """Manages a persistent, cookie-backed Playwright Facebook session.

    Usage:
        session = FacebookSession()
        async with session as page:
            await page.goto("https://www.facebook.com/groups/...")
    """

    def __init__(self) -> None:
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._valid = False

    async def __aenter__(self):
        await self._start()
        # Return None if unauthenticated — callers do `if not page: return error` checks.
        # This makes the guard pattern work: unauthenticated → None → caller sees it
        # immediately instead of receiving a truthy Page object that will fail later
        # inside the group with "post composer not found" (actually: login wall).
        if not self._valid:
            await self._stop()
            return None
        return self._page

    async def __aexit__(self, *_):
        await self._stop()

    async def _start(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        cookies = _load_cookies()
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="he-IL",
        )

        if cookies:
            await self._context.add_cookies(cookies)
            log.info("fb_browser: loaded %d saved cookies", len(cookies))

        self._page = await self._context.new_page()
        self._valid = await self._health_check()
        if not self._valid:
            log.warning("fb_browser: session health-check failed — cookies may be stale")

    async def _stop(self) -> None:
        if self._page and not self._page.is_closed():
            try:
                cookies = await self._context.cookies()
                _save_cookies(cookies)
            except Exception:
                pass
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _health_check(self) -> bool:
        """Navigate to FB home and verify the session is AUTHENTICATED.

        Reliable signal: the Facebook login form (id="email" / name="login") is
        present ONLY when the user is NOT logged in.  The old "marketplace" check
        was a false positive — that text appears on the public home page too.
        Secondary DOM check via JS: profile/avatar link only exists when logged in.
        """
        try:
            await self._page.goto(_HEALTH_URL, wait_until="domcontentloaded", timeout=20_000)
            await _random_delay(1.0, 2.5)
            content = await self._page.content()

            # Login form present ⟹ NOT logged in
            login_form_present = 'id="email"' in content or 'name="login"' in content

            if login_form_present:
                await _save_failure_screenshot(self._page, "session_health_fail")
                log.warning("fb_browser: login form detected — session not authenticated")
                return False

            # JS cross-check: profile nav link only exists when authenticated
            try:
                has_profile = await self._page.evaluate("""() => {
                    return !!(
                        document.querySelector('[aria-label="Your profile"]') ||
                        document.querySelector('a[href*="/me/"]') ||
                        document.querySelector('[data-testid="blue_bar_profile_link"]')
                    );
                }""")
            except Exception:
                has_profile = False

            if has_profile:
                log.info("fb_browser: session healthy (authenticated — profile link found)")
                return True

            # Fallback: no login form AND no profile link — ambiguous but likely a
            # redirect/error page; treat as invalid and let the caller retry.
            await _save_failure_screenshot(self._page, "session_health_ambiguous")
            log.warning("fb_browser: health-check ambiguous (no login form, no profile link)")
            return False

        except Exception as exc:
            log.warning("fb_browser: health-check error: %s", exc)
            return False

    @property
    def is_valid(self) -> bool:
        return self._valid

    async def navigate(self, url: str, wait_until: str = "domcontentloaded",
                       timeout: int = 30_000) -> str:
        """Navigate to a URL and return the page text content."""
        await self._page.goto(url, wait_until=wait_until, timeout=timeout)
        await _random_delay()
        return await self._page.content()

    async def type_and_submit(
        self,
        selector: str,
        text: str,
        submit_selector: str | None = None,
    ) -> None:
        """Type text into a field and optionally click a submit button."""
        elem = await self._page.wait_for_selector(selector, timeout=10_000)
        await elem.click()
        await _random_delay(0.5, 1.5)
        # Type character by character with random cadence (human-like)
        for char in text:
            await self._page.keyboard.type(char)
            await asyncio.sleep(random.uniform(0.05, 0.15))
        if submit_selector:
            await _random_delay(0.8, 2.0)
            submit = await self._page.wait_for_selector(submit_selector, timeout=8_000)
            await submit.click()
            await _random_delay(1.5, 3.5)
