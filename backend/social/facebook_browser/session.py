"""
Script: social/facebook_browser/session.py
Purpose: Persistent Playwright browser session for Facebook Group operations.
         Manages cookie persistence, session health-checks, automatic re-login,
         and human-like timing so the automated browser is not flagged as a bot.

Auth lifecycle (Phase 5J hardening):
  1. On start: cookies loaded → health-check.
  2. AUTHENTICATED → proceed normally.
  3. LOGIN_FORM / ACCOUNT_PICKER → auto-relogin via fb_browser_login.login()
     using FB_EMAIL + FB_PASSWORD already in .env.
  4. Auto-relogin success → cookies refreshed, session proceeds.
  5. Auto-relogin failure → WhatsApp owner, session blocked (_valid=False).
  6. Manual cookie import (maintenance/fb_cookie_import.py) remains as
     documented emergency fallback, not the normal lifecycle.

Persistent profile (2026-09-20 root fix): the browser runs on a REAL persistent
Chrome profile (_PROFILE_DIR, on the worker_state volume) instead of a fresh
cookies-only context. Facebook binds an authenticated session to the whole
browser identity (cookies + storage + fingerprint), not to cookies alone; replaying
cookies.json into a new context with a spoofed UA got the session rejected
("Continue as <name>" picker) while the profile that established it stayed
authenticated. cookies.json is now only a last-known-good backup / seed.

Phase 3 guard: _stop() only saves cookies when _valid=True at startup.
Phase 5C guard: _stop() only saves if live cookies still contain c_user + xs.

Data Imported/Modified: /app/state/fb_browser_session/cookies.json
Data Sources: https://www.facebook.com/
Last Updated: 2026-09-20 (persistent-profile session)
"""

from __future__ import annotations

import asyncio
import enum
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
# Persistent Chrome profile that holds the authenticated Facebook session. Defaults to
# the profile where the authenticated state was established (on the worker_state volume,
# so it survives container recreation).
_PROFILE_DIR = Path(os.getenv("FB_PROFILE_DIR", str(Path(os.getenv("STATE_DIR", "/app/state")) / "fb_native_test" / "profile")))
# One Chrome may own a profile at a time; serialize FacebookSession users in-process.
_PROFILE_LOCK = asyncio.Lock()
_HEALTH_URL = "https://www.facebook.com/"
_LOGIN_INDICATOR = "marketplace"  # kept for legacy compat — not used in _health_check


class _AuthState(enum.Enum):
    """Result of a health-check pass.  Drives the relogin decision in _start()."""
    AUTHENTICATED = "authenticated"       # Session is valid — profile link found
    LOGIN_FORM = "login_form"             # Classic login form (id=email) — definitely logged out
    ACCOUNT_PICKER = "account_picker"     # Modern multi-account selector — effectively logged out
    AMBIGUOUS = "ambiguous"               # Unknown page; treat as invalid
    ERROR = "error"                       # Navigation or JS exception


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
        self._holds_lock = False

    async def __aenter__(self):
        await _PROFILE_LOCK.acquire()
        self._holds_lock = True
        try:
            await self._start()
        except BaseException:
            await self._stop()
            self._release_lock()
            raise
        # Return None if unauthenticated — callers do `if not page: return error` checks.
        # This makes the guard pattern work: unauthenticated → None → caller sees it
        # immediately instead of receiving a truthy Page object that will fail later
        # inside the group with "post composer not found" (actually: login wall).
        if not self._valid:
            await self._stop()
            self._release_lock()
            return None
        return self._page

    async def __aexit__(self, *_):
        try:
            await self._stop()
        finally:
            self._release_lock()

    def _release_lock(self) -> None:
        if self._holds_lock:
            self._holds_lock = False
            _PROFILE_LOCK.release()

    async def _start(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        self._playwright = await async_playwright().start()
        _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        # Real persistent profile: no user_agent override (it must match the browser that
        # established the session). Chrome itself persists cookies/storage into the profile.
        self._context = await self._playwright.chromium.launch_persistent_context(
            str(_PROFILE_DIR),
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
            viewport={"width": 1280, "height": 800},
            locale="he-IL",
        )

        # The profile is the source of truth. Only seed from the cookies.json backup when
        # the profile carries no authenticated session -- never overwrite a live c_user/xs
        # with a possibly stale backup.
        try:
            profile_names = {c["name"] for c in await self._context.cookies()}
        except Exception:
            profile_names = set()
        cookies = _load_cookies()
        if "c_user" in profile_names and "xs" in profile_names:
            log.info("fb_browser: persistent profile already holds an authenticated session — not seeding cookies.json")
        elif cookies:
            await self._context.add_cookies(cookies)
            log.info("fb_browser: profile unauthenticated — seeded %d cookies from cookies.json backup", len(cookies))

        self._page = await self._context.new_page()
        auth_state = await self._health_check()
        self._valid = (auth_state == _AuthState.AUTHENTICATED)

        if not self._valid:
            log.warning("fb_browser: health-check → %s — attempting auto-relogin", auth_state.value)
            relogin_ok = await self._try_auto_relogin()
            if relogin_ok:
                # Reload fresh cookies into the existing context and re-validate
                fresh_cookies = _load_cookies()
                if fresh_cookies:
                    await self._context.clear_cookies()
                    await self._context.add_cookies(fresh_cookies)
                    log.info("fb_browser: reloaded %d cookies after auto-relogin", len(fresh_cookies))
                post_state = await self._health_check()
                self._valid = (post_state == _AuthState.AUTHENTICATED)
                if self._valid:
                    log.info("fb_browser: auto-relogin succeeded — session now authenticated")
                else:
                    log.error("fb_browser: auto-relogin completed but re-check shows %s", post_state.value)
                    await self._notify_owner_reauth_required("auto-relogin ran but session still invalid")
            else:
                log.error("fb_browser: auto-relogin failed — owner must intervene")
                await self._notify_owner_reauth_required(auth_state.value)

    async def _stop(self) -> None:
        # Phase 3 guard: only save when health check passed at startup (_valid=True).
        # Phase 5C guard: only save if the live cookies still contain c_user + xs.
        # Facebook can invalidate a session mid-scan without any Playwright error;
        # by teardown the browser may hold only 4 unauthenticated cookies even though
        # _valid=True (set once at startup, never re-checked). Without this guard,
        # _stop() would overwrite the on-disk cookies.json with degraded state.
        if self._valid and self._page and not self._page.is_closed():
            try:
                cookies = await self._context.cookies()
                cookie_names = {c["name"] for c in cookies}
                if "c_user" in cookie_names and "xs" in cookie_names:
                    _save_cookies(cookies)
                else:
                    log.warning(
                        "fb_browser: mid-session degradation detected "
                        "(c_user=%s xs=%s) — NOT overwriting cookies.json to "
                        "preserve last-known-good authenticated state",
                        "present" if "c_user" in cookie_names else "MISSING",
                        "present" if "xs" in cookie_names else "MISSING",
                    )
            except Exception:
                pass
        # Closing the persistent context flushes cookies/storage into the profile.
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._playwright:
            await self._playwright.stop()

    async def _health_check(self) -> _AuthState:
        """Navigate to FB home and classify the auth state precisely.

        States:
          AUTHENTICATED   — profile nav link found, OR cookie-presence
                            confirmation (c_user+xs in live context, no
                            login form) for IP-filtered environments.
          LOGIN_FORM      — classic login form (id=email / name=login) present.
          ACCOUNT_PICKER  — modern multi-account selector; no form, no profile.
          AMBIGUOUS       — none of the above matched; treat as invalid.
          ERROR           — navigation / JS exception.

        The ACCOUNT_PICKER state was previously mis-classified as AMBIGUOUS,
        causing the system to flag an expired session as "unknown" instead of
        triggering the auto-relogin path.  Both LOGIN_FORM and ACCOUNT_PICKER
        must drive the same outcome (auto-relogin).

        Phase 5K fix: added cookie-presence confirmation as fallback before
        AMBIGUOUS.  From this server IP Facebook omits the profile DOM element
        even on valid sessions; c_user+xs in the live browser context is a
        sufficient authenticated signal when no negative indicator is present.
        """
        try:
            await self._page.goto(_HEALTH_URL, wait_until="domcontentloaded", timeout=20_000)
            await _random_delay(1.0, 2.5)
            content = await self._page.content()

            # ── 1. Classic login form → definitely logged out ────────────────
            login_form_present = 'id="email"' in content or 'name="login"' in content
            if login_form_present:
                await _save_failure_screenshot(self._page, "session_health_login_form")
                log.warning("fb_browser: LOGIN_FORM detected — session not authenticated")
                return _AuthState.LOGIN_FORM

            # ── 2. JS: profile link → authenticated ──────────────────────────
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
                log.info("fb_browser: AUTHENTICATED — profile link found")
                return _AuthState.AUTHENTICATED

            # ── 3. Account-picker detection ──────────────────────────────────
            # The modern multi-account selector has no classic login form and no
            # profile link, but contains account-tile buttons or a "list_accounts"
            # form.  It appears when the device token is present but the session
            # token has expired — Facebook knows the device but not the user.
            try:
                is_picker = await self._page.evaluate("""() => {
                    // name="list_accounts" — the hidden form wrapping the account tiles
                    if (document.querySelector('[name="list_accounts"]')) return true;
                    // Continue-as button on the picker page
                    if (document.querySelector('[data-testid="qc_continue_button"]')) return true;
                    // Picker page URL is /login or /checkpoint — check without profile link
                    if (window.location.href.includes('/login') ||
                        window.location.href.includes('/checkpoint') ||
                        window.location.href.includes('/recover')) return true;
                    // Fallback: page contains the Hebrew/English "Log into Facebook" heading
                    // but no email field (the picker shows this heading on the chooser)
                    const text = document.body && document.body.innerText || '';
                    return (text.includes('היכנס') || text.includes('Log into Facebook') ||
                            text.includes('Choose an account')) &&
                           !document.getElementById('email');
                }""")
            except Exception:
                is_picker = False

            if is_picker:
                await _save_failure_screenshot(self._page, "session_health_account_picker")
                log.warning("fb_browser: ACCOUNT_PICKER detected — session expired, auto-relogin needed")
                return _AuthState.ACCOUNT_PICKER

            # ── 4. Cookie-presence confirmation (IP-block fallback) ──────────
            # From this server IP, Facebook serves a bot-aware page that omits
            # the profile DOM element even when the session is valid.  If the
            # browser context holds c_user + xs (Facebook accepted the cookies
            # and did NOT redirect to a login form), the session IS authenticated.
            # This is checked here — after all negative signals failed — so it
            # can never promote a login-form or picker page to AUTHENTICATED.
            try:
                live_cookies = await self._context.cookies()
                live_names = {c["name"] for c in live_cookies}
                if "c_user" in live_names and "xs" in live_names:
                    log.info(
                        "fb_browser: AUTHENTICATED (via cookie-presence confirmation) — "
                        "c_user + xs present in live browser context; no login form "
                        "detected. Profile DOM element absent due to server-side IP filter."
                    )
                    return _AuthState.AUTHENTICATED
            except Exception:
                pass

            # ── 5. Ambiguous fallback ────────────────────────────────────────
            await _save_failure_screenshot(self._page, "session_health_ambiguous")
            log.warning("fb_browser: AMBIGUOUS — no login form, no profile link, no picker signals, no c_user+xs")
            return _AuthState.AMBIGUOUS

        except Exception as exc:
            log.warning("fb_browser: health-check ERROR: %s", exc)
            return _AuthState.ERROR

    async def _try_auto_relogin(self) -> bool:
        """Call fb_browser_login.login() using FB_EMAIL + FB_PASSWORD from .env.

        Returns True if the login function reports success AND fresh cookies
        containing c_user + xs were written to disk.  The caller reloads
        those cookies into the existing Playwright context.

        Requires FB_EMAIL and FB_PASSWORD in the environment (.env).  If they
        are absent the relogin is skipped immediately (returns False).
        """
        fb_email = os.environ.get("FB_EMAIL", "").strip()
        fb_password = os.environ.get("FB_PASSWORD", "").strip()

        if not fb_email or not fb_password:
            log.error(
                "fb_browser: auto-relogin skipped — FB_EMAIL and/or FB_PASSWORD not set in .env"
            )
            return False

        log.info("fb_browser: starting auto-relogin via fb_browser_login.login() …")
        try:
            from social.facebook_browser.fb_browser_login import login as _fb_login
            success = await _fb_login()
        except Exception as exc:
            log.error("fb_browser: auto-relogin exception: %s", exc)
            return False

        if not success:
            log.error("fb_browser: fb_browser_login.login() returned False")
            return False

        # Verify the login wrote usable cookies
        fresh = _load_cookies()
        names = {c.get("name") for c in fresh}
        if "c_user" in names and "xs" in names:
            log.info("fb_browser: auto-relogin wrote %d cookies (c_user + xs present)", len(fresh))
            return True

        log.error(
            "fb_browser: auto-relogin reported success but cookies lack c_user/xs "
            "(names present: %s)", sorted(names)
        )
        return False

    async def _notify_owner_reauth_required(self, reason: str) -> None:
        """WhatsApp the owner that manual intervention is needed for Facebook auth.

        Called only when auto-relogin fails — not on every session expiry,
        because the auto-relogin path handles most expirations silently.
        Rate-limited by Redis with a 6-hour cooldown to avoid alert storms.
        """
        try:
            import os as _os
            from BACKEND_API_ROUTES import _wa_send_quiet  # type: ignore[import]

            owner_phone = _os.environ.get("OWNER_WHATSAPP_PHONE", "")
            if not owner_phone:
                log.warning("fb_browser: OWNER_WHATSAPP_PHONE not set — cannot send reauth alert")
                return

            # Deduplicate via simple file-based cooldown (6 h)
            _cooldown_file = _STATE_DIR / "reauth_alert_sent_at"
            _cooldown_s = 6 * 3600
            now = time.time()
            if _cooldown_file.exists():
                try:
                    sent_at = float(_cooldown_file.read_text())
                    if now - sent_at < _cooldown_s:
                        log.info(
                            "fb_browser: reauth alert suppressed (cooldown %dh, sent %.0fs ago)",
                            _cooldown_s // 3600, now - sent_at,
                        )
                        return
                except Exception:
                    pass

            msg = (
                "⚠️ *NOA — Facebook session expired*\n\n"
                f"Auto-relogin attempt failed ({reason}).\n"
                "נדרשת פעולה ידנית להחזרת session פייסבוק:\n"
                "```docker exec autospare_backend python3 "
                "/app/social/facebook_browser/fb_browser_login.py```\n"
                "או: ייבוא עוגיות ידני דרך `maintenance/fb_cookie_import.py`."
            )
            await _wa_send_quiet(owner_phone, msg, critical=False)
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            _cooldown_file.write_text(str(now))
            log.info("fb_browser: owner reauth alert sent (reason: %s)", reason)
        except Exception as exc:
            log.warning("fb_browser: failed to send reauth alert: %s", exc)

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
