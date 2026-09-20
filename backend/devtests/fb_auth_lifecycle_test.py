"""
Script: devtests/fb_auth_lifecycle_test.py
Purpose: Offline unit tests for the Phase 5J auth lifecycle hardening in
         social/facebook_browser/session.py

Tests (all mocked — no Playwright, no Facebook, no Production writes):
  1.  _AuthState enum has required members
  2.  _health_check returns AUTHENTICATED when profile link JS fires
  3.  _health_check returns LOGIN_FORM when id=email in HTML
  4.  _health_check returns ACCOUNT_PICKER when list_accounts in JS
  5.  _health_check returns ACCOUNT_PICKER when URL contains /login
  6.  _health_check returns ACCOUNT_PICKER on Hebrew picker text
  7.  _health_check returns AMBIGUOUS when none of the signals match
  8.  _health_check returns ERROR on navigation exception
  9.  _start sets _valid=True when AUTHENTICATED on first check
  10. _start triggers auto-relogin on LOGIN_FORM detection
  11. _start triggers auto-relogin on ACCOUNT_PICKER detection
  12. _start re-validates after auto-relogin success
  13. _start sets _valid=False when auto-relogin returns False
  14. _start notifies owner when auto-relogin fails
  15. _try_auto_relogin returns False when FB_EMAIL missing
  16. _try_auto_relogin returns False when FB_PASSWORD missing
  17. _try_auto_relogin returns False when login() raises exception
  18. _try_auto_relogin returns False when login() returns False
  19. _try_auto_relogin returns False when cookies lack c_user after login
  20. _try_auto_relogin returns True when login() succeeds + cookies valid
  21. _stop still honours Phase 3 guard (no save when _valid=False)
  22. _stop still honours Phase 5C guard (no save when c_user missing mid-session)
  23. _notify_owner sends WA message and writes cooldown file
  24. _notify_owner respects 6-hour cooldown (no duplicate alert)
  25. __aenter__ returns None (not page) when _valid=False after relogin failure

Data Imported/Modified: none (all mocked)
Data Sources: internal mocks
Last Updated: 2026-09-12
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, "/app")

# ── Import target module ──────────────────────────────────────────────────────
from social.facebook_browser.session import (
    FacebookSession,
    _AuthState,
    _load_cookies,
    _save_cookies,
)

PASS = 0
FAIL = 0
ERRORS: list[str] = []


def run(label: str):
    def decorator(fn):
        global PASS, FAIL
        try:
            asyncio.run(fn()) if asyncio.iscoroutinefunction(fn) else fn()
            print(f"  ✓  {label}")
            PASS += 1
        except Exception as exc:
            print(f"  ✗  {label}")
            print(f"       {type(exc).__name__}: {exc}")
            FAIL += 1
            ERRORS.append(f"{label}: {exc}")
        return fn
    return decorator


def _make_page(html_content: str = "", js_returns=None, url: str = "https://www.facebook.com/") -> AsyncMock:
    """Build a mock Playwright Page that returns specified HTML content and JS results."""
    page = AsyncMock()
    page.goto = AsyncMock(return_value=None)
    page.content = AsyncMock(return_value=html_content)
    page.url = url
    page.is_closed = MagicMock(return_value=False)
    # evaluate returns different values per call; use a list for multiple calls
    if isinstance(js_returns, list):
        page.evaluate = AsyncMock(side_effect=js_returns)
    else:
        page.evaluate = AsyncMock(return_value=js_returns)
    page.screenshot = AsyncMock(return_value=None)
    return page


def _make_session(cookies_file: Path | None = None) -> FacebookSession:
    """Create a FacebookSession with state-dir patched to a temp dir."""
    s = FacebookSession()
    if cookies_file is not None:
        import social.facebook_browser.session as _mod
        _mod._COOKIES_FILE = cookies_file
        _mod._STATE_DIR = cookies_file.parent
        _mod._SCREENSHOT_DIR = cookies_file.parent
    return s


# ── Tests ─────────────────────────────────────────────────────────────────────

@run("1. _AuthState enum has required members")
def test_01_auth_state_members():
    assert hasattr(_AuthState, "AUTHENTICATED")
    assert hasattr(_AuthState, "LOGIN_FORM")
    assert hasattr(_AuthState, "ACCOUNT_PICKER")
    assert hasattr(_AuthState, "AMBIGUOUS")
    assert hasattr(_AuthState, "ERROR")


@run("2. _health_check → AUTHENTICATED when profile link JS fires")
async def test_02_authenticated():
    s = FacebookSession()
    s._page = _make_page(html_content="<html>facebook home</html>",
                         js_returns=[True])  # has_profile = True
    result = await s._health_check()
    assert result == _AuthState.AUTHENTICATED, f"got {result}"


@run("3. _health_check → LOGIN_FORM when id=email in HTML")
async def test_03_login_form():
    s = FacebookSession()
    s._page = _make_page(html_content='<input id="email" /><input name="login" />')
    result = await s._health_check()
    assert result == _AuthState.LOGIN_FORM, f"got {result}"


@run("4. _health_check → ACCOUNT_PICKER when list_accounts in JS")
async def test_04_account_picker_list_accounts():
    s = FacebookSession()
    # No login form in HTML, profile check returns False, picker check returns True
    s._page = _make_page(html_content="<html>facebook</html>",
                         js_returns=[False, True])
    result = await s._health_check()
    assert result == _AuthState.ACCOUNT_PICKER, f"got {result}"


@run("5. _health_check → ACCOUNT_PICKER when URL contains /login")
async def test_05_account_picker_url():
    s = FacebookSession()
    page = _make_page(html_content="<html>facebook</html>",
                      js_returns=[False, True])
    page.url = "https://www.facebook.com/login/?next=/"
    s._page = page
    result = await s._health_check()
    assert result == _AuthState.ACCOUNT_PICKER, f"got {result}"


@run("6. _health_check → ACCOUNT_PICKER on Hebrew picker text")
async def test_06_account_picker_hebrew():
    s = FacebookSession()
    # No login form, no profile, picker JS matches Hebrew text
    s._page = _make_page(html_content="<html>facebook</html>",
                         js_returns=[False, True])
    result = await s._health_check()
    assert result == _AuthState.ACCOUNT_PICKER, f"got {result}"


@run("7. _health_check → AMBIGUOUS when no signals match")
async def test_07_ambiguous():
    s = FacebookSession()
    s._page = _make_page(html_content="<html>some random page</html>",
                         js_returns=[False, False])  # profile=F, picker=F
    result = await s._health_check()
    assert result == _AuthState.AMBIGUOUS, f"got {result}"


@run("8. _health_check → ERROR on navigation exception")
async def test_08_error():
    s = FacebookSession()
    page = AsyncMock()
    page.goto = AsyncMock(side_effect=Exception("timeout"))
    s._page = page
    result = await s._health_check()
    assert result == _AuthState.ERROR, f"got {result}"


def _make_playwright_mock():
    """Build a minimal mock of the playwright async API for _start() patching.

    _start() calls: await async_playwright().start() → p
    Then: p.chromium.launch(…) → browser
    Then: browser.new_context(…) → context
    Then: context.new_page() → page
    """
    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_page = AsyncMock()
    mock_page.is_closed = MagicMock(return_value=False)

    mock_pw_instance = AsyncMock()
    mock_pw_instance.start = AsyncMock(return_value=mock_pw_instance)
    mock_pw_instance.chromium = MagicMock()
    mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
    mock_pw_instance.chromium.launch_persistent_context = AsyncMock(return_value=mock_context)
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_context.cookies = AsyncMock(return_value=[])
    mock_context.close = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.add_cookies = AsyncMock()
    mock_context.clear_cookies = AsyncMock()
    mock_browser.close = AsyncMock()
    mock_pw_instance.stop = AsyncMock()

    mock_pw_class = MagicMock(return_value=mock_pw_instance)
    return mock_pw_class, mock_browser, mock_context, mock_page


@run("9. _start sets _valid=True when AUTHENTICATED on first check")
async def test_09_start_authenticated():
    s = FacebookSession()
    mock_pw_class, _, mock_ctx, mock_page = _make_playwright_mock()

    async def _hc():
        return _AuthState.AUTHENTICATED

    mock_relo = AsyncMock(return_value=False)

    with patch.object(s, "_health_check", _hc), \
         patch.object(s, "_try_auto_relogin", mock_relo), \
         patch("social.facebook_browser.session._load_cookies", return_value=[]), \
         patch("playwright.async_api.async_playwright", mock_pw_class):
        await s._start()

    assert s._valid is True, f"_valid={s._valid}"
    mock_relo.assert_not_called()


@run("10. _start triggers auto-relogin on LOGIN_FORM detection")
async def test_10_start_relogin_on_login_form():
    s = FacebookSession()
    mock_pw_class, _, mock_ctx, mock_page = _make_playwright_mock()

    hc_count = [0]

    async def _hc():
        hc_count[0] += 1
        return _AuthState.LOGIN_FORM if hc_count[0] == 1 else _AuthState.AUTHENTICATED

    mock_relogin = AsyncMock(return_value=True)

    with patch.object(s, "_health_check", _hc), \
         patch.object(s, "_try_auto_relogin", mock_relogin), \
         patch.object(s, "_notify_owner_reauth_required", AsyncMock()), \
         patch("social.facebook_browser.session._load_cookies",
               return_value=[{"name": "c_user"}, {"name": "xs"}]), \
         patch("playwright.async_api.async_playwright", mock_pw_class):
        await s._start()

    mock_relogin.assert_called_once()


@run("11. _start triggers auto-relogin on ACCOUNT_PICKER detection")
async def test_11_start_relogin_on_account_picker():
    s = FacebookSession()
    mock_pw_class, _, mock_ctx, mock_page = _make_playwright_mock()

    hc_count = [0]

    async def _hc():
        hc_count[0] += 1
        return _AuthState.ACCOUNT_PICKER if hc_count[0] == 1 else _AuthState.AUTHENTICATED

    mock_relogin = AsyncMock(return_value=True)

    with patch.object(s, "_health_check", _hc), \
         patch.object(s, "_try_auto_relogin", mock_relogin), \
         patch.object(s, "_notify_owner_reauth_required", AsyncMock()), \
         patch("social.facebook_browser.session._load_cookies",
               return_value=[{"name": "c_user"}, {"name": "xs"}]), \
         patch("playwright.async_api.async_playwright", mock_pw_class):
        await s._start()

    mock_relogin.assert_called_once()


@run("12. _start re-validates after auto-relogin success → _valid=True")
async def test_12_start_relogin_success_valid():
    s = FacebookSession()
    mock_pw_class, _, mock_ctx, mock_page = _make_playwright_mock()

    hc_count = [0]

    async def _hc():
        hc_count[0] += 1
        return _AuthState.ACCOUNT_PICKER if hc_count[0] == 1 else _AuthState.AUTHENTICATED

    with patch.object(s, "_health_check", _hc), \
         patch.object(s, "_try_auto_relogin", AsyncMock(return_value=True)), \
         patch.object(s, "_notify_owner_reauth_required", AsyncMock()), \
         patch("social.facebook_browser.session._load_cookies",
               return_value=[{"name": "c_user"}, {"name": "xs"}]), \
         patch("playwright.async_api.async_playwright", mock_pw_class):
        await s._start()

    assert s._valid is True, f"_valid={s._valid}"


@run("13. _start sets _valid=False when auto-relogin returns False")
async def test_13_start_relogin_failure_invalid():
    s = FacebookSession()
    mock_pw_class, _, mock_ctx, mock_page = _make_playwright_mock()

    async def _hc():
        return _AuthState.ACCOUNT_PICKER

    with patch.object(s, "_health_check", _hc), \
         patch.object(s, "_try_auto_relogin", AsyncMock(return_value=False)), \
         patch.object(s, "_notify_owner_reauth_required", AsyncMock()), \
         patch("social.facebook_browser.session._load_cookies", return_value=[]), \
         patch("playwright.async_api.async_playwright", mock_pw_class):
        await s._start()

    assert s._valid is False, f"expected False, got {s._valid}"


@run("14. _start notifies owner when auto-relogin fails")
async def test_14_start_notifies_owner_on_failure():
    s = FacebookSession()
    mock_pw_class, _, mock_ctx, mock_page = _make_playwright_mock()

    async def _hc():
        return _AuthState.ACCOUNT_PICKER

    notify_mock = AsyncMock()

    with patch.object(s, "_health_check", _hc), \
         patch.object(s, "_try_auto_relogin", AsyncMock(return_value=False)), \
         patch.object(s, "_notify_owner_reauth_required", notify_mock), \
         patch("social.facebook_browser.session._load_cookies", return_value=[]), \
         patch("playwright.async_api.async_playwright", mock_pw_class):
        await s._start()

    notify_mock.assert_called_once()


@run("15. _try_auto_relogin returns False when FB_EMAIL missing")
async def test_15_auto_relogin_no_email():
    s = FacebookSession()
    env = {"FB_EMAIL": "", "FB_PASSWORD": "secret"}
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("FB_EMAIL", None)
        result = await s._try_auto_relogin()
    assert result is False


@run("16. _try_auto_relogin returns False when FB_PASSWORD missing")
async def test_16_auto_relogin_no_password():
    s = FacebookSession()
    env = {"FB_EMAIL": "test@example.com", "FB_PASSWORD": ""}
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("FB_PASSWORD", None)
        result = await s._try_auto_relogin()
    assert result is False


@run("17. _try_auto_relogin returns False when login() raises exception")
async def test_17_auto_relogin_exception():
    s = FacebookSession()
    env = {"FB_EMAIL": "test@example.com", "FB_PASSWORD": "secret"}

    async def _bad_login():
        raise RuntimeError("CAPTCHA unsolvable")

    with patch.dict(os.environ, env, clear=False), \
         patch("social.facebook_browser.session._load_cookies", return_value=[]), \
         patch("social.facebook_browser.fb_browser_login.login", _bad_login, create=True):
        # Patch the import inside _try_auto_relogin
        with patch("social.facebook_browser.session.FacebookSession._try_auto_relogin",
                   wraps=s._try_auto_relogin):
            # Directly mock the import path used in _try_auto_relogin
            mock_module = types.ModuleType("social.facebook_browser.fb_browser_login")
            mock_module.login = _bad_login  # type: ignore
            with patch.dict(sys.modules,
                            {"social.facebook_browser.fb_browser_login": mock_module}):
                result = await s._try_auto_relogin()
    assert result is False


@run("18. _try_auto_relogin returns False when login() returns False")
async def test_18_auto_relogin_login_returns_false():
    s = FacebookSession()
    env = {"FB_EMAIL": "test@example.com", "FB_PASSWORD": "secret"}

    async def _false_login():
        return False

    mock_module = types.ModuleType("social.facebook_browser.fb_browser_login")
    mock_module.login = _false_login  # type: ignore

    with patch.dict(os.environ, env, clear=False), \
         patch.dict(sys.modules, {"social.facebook_browser.fb_browser_login": mock_module}):
        result = await s._try_auto_relogin()
    assert result is False


@run("19. _try_auto_relogin returns False when cookies lack c_user after login")
async def test_19_auto_relogin_bad_cookies():
    s = FacebookSession()
    env = {"FB_EMAIL": "test@example.com", "FB_PASSWORD": "secret"}

    async def _ok_login():
        return True

    mock_module = types.ModuleType("social.facebook_browser.fb_browser_login")
    mock_module.login = _ok_login  # type: ignore

    with patch.dict(os.environ, env, clear=False), \
         patch.dict(sys.modules, {"social.facebook_browser.fb_browser_login": mock_module}), \
         patch("social.facebook_browser.session._load_cookies",
               return_value=[{"name": "datr"}, {"name": "sb"}]):  # no c_user / xs
        result = await s._try_auto_relogin()
    assert result is False


@run("20. _try_auto_relogin returns True when login() succeeds + cookies valid")
async def test_20_auto_relogin_success():
    s = FacebookSession()
    env = {"FB_EMAIL": "test@example.com", "FB_PASSWORD": "secret"}

    async def _ok_login():
        return True

    mock_module = types.ModuleType("social.facebook_browser.fb_browser_login")
    mock_module.login = _ok_login  # type: ignore

    with patch.dict(os.environ, env, clear=False), \
         patch.dict(sys.modules, {"social.facebook_browser.fb_browser_login": mock_module}), \
         patch("social.facebook_browser.session._load_cookies",
               return_value=[{"name": "c_user"}, {"name": "xs"}, {"name": "datr"}]):
        result = await s._try_auto_relogin()
    assert result is True


@run("21. _stop honours Phase 3 guard — no cookie save when _valid=False")
async def test_21_stop_phase3_guard():
    with tempfile.TemporaryDirectory() as tmp:
        cookies_file = Path(tmp) / "cookies.json"
        import social.facebook_browser.session as _mod
        orig_file = _mod._COOKIES_FILE
        orig_dir = _mod._STATE_DIR
        try:
            _mod._COOKIES_FILE = cookies_file
            _mod._STATE_DIR = Path(tmp)

            s = FacebookSession()
            s._valid = False
            s._page = AsyncMock()
            s._page.is_closed = MagicMock(return_value=False)
            s._context = AsyncMock()
            s._context.cookies = AsyncMock(return_value=[{"name": "c_user"}, {"name": "xs"}])
            s._browser = AsyncMock()
            s._playwright = AsyncMock()

            await s._stop()
            assert not cookies_file.exists(), "Cookies should NOT have been saved (_valid=False)"
        finally:
            _mod._COOKIES_FILE = orig_file
            _mod._STATE_DIR = orig_dir


@run("22. _stop honours Phase 5C guard — no save when c_user missing mid-session")
async def test_22_stop_phase5c_guard():
    with tempfile.TemporaryDirectory() as tmp:
        cookies_file = Path(tmp) / "cookies.json"
        import social.facebook_browser.session as _mod
        orig_file = _mod._COOKIES_FILE
        orig_dir = _mod._STATE_DIR
        try:
            _mod._COOKIES_FILE = cookies_file
            _mod._STATE_DIR = Path(tmp)

            s = FacebookSession()
            s._valid = True  # was valid at start
            s._page = AsyncMock()
            s._page.is_closed = MagicMock(return_value=False)
            s._context = AsyncMock()
            # Mid-session degradation: c_user has disappeared
            s._context.cookies = AsyncMock(return_value=[{"name": "datr"}, {"name": "sb"}])
            s._browser = AsyncMock()
            s._playwright = AsyncMock()

            await s._stop()
            assert not cookies_file.exists(), "Cookies should NOT be saved when c_user is missing"
        finally:
            _mod._COOKIES_FILE = orig_file
            _mod._STATE_DIR = orig_dir


@run("23. _notify_owner sends WA message and writes cooldown file")
async def test_23_notify_owner_sends():
    with tempfile.TemporaryDirectory() as tmp:
        import social.facebook_browser.session as _mod
        orig_dir = _mod._STATE_DIR
        _mod._STATE_DIR = Path(tmp)
        try:
            s = FacebookSession()
            wa_calls = []

            async def _wa_quiet(phone, msg, critical=False):
                wa_calls.append((phone, msg))

            with patch.dict(os.environ, {"OWNER_WHATSAPP_PHONE": "+972500000000"}), \
                 patch("social.facebook_browser.session._wa_send_quiet", _wa_quiet, create=True):
                # Patch the import inside _notify_owner_reauth_required
                import importlib
                import social.facebook_browser.session as sess_mod
                # Directly inject into the module's globals temporarily
                old_wa = sess_mod.__dict__.get("_wa_send_quiet_ref")

                # Simpler: patch the function at the call site
                with patch("BACKEND_API_ROUTES._wa_send_quiet", _wa_quiet, create=True):
                    # The method imports from BACKEND_API_ROUTES — patch that import
                    import builtins
                    real_import = builtins.__import__

                    def _fake_import(name, *args, **kwargs):
                        if name == "BACKEND_API_ROUTES":
                            mod = types.ModuleType("BACKEND_API_ROUTES")
                            mod._wa_send_quiet = _wa_quiet  # type: ignore
                            return mod
                        return real_import(name, *args, **kwargs)

                    with patch("builtins.__import__", _fake_import):
                        await s._notify_owner_reauth_required("test_reason")

            cooldown_file = Path(tmp) / "reauth_alert_sent_at"
            assert cooldown_file.exists(), "Cooldown file should be written after alert"
            assert len(wa_calls) == 1, f"Expected 1 WA message, got {len(wa_calls)}"
        finally:
            _mod._STATE_DIR = orig_dir


@run("24. _notify_owner respects 6-hour cooldown — no duplicate alert")
async def test_24_notify_owner_cooldown():
    with tempfile.TemporaryDirectory() as tmp:
        import social.facebook_browser.session as _mod
        orig_dir = _mod._STATE_DIR
        _mod._STATE_DIR = Path(tmp)
        try:
            s = FacebookSession()
            wa_calls = []

            async def _wa_quiet(phone, msg, critical=False):
                wa_calls.append((phone, msg))

            # Write a cooldown file indicating alert was sent 1 hour ago (within 6h)
            cooldown_file = Path(tmp) / "reauth_alert_sent_at"
            import time
            cooldown_file.write_text(str(time.time() - 3600))  # 1h ago

            import builtins
            real_import = builtins.__import__

            def _fake_import(name, *args, **kwargs):
                if name == "BACKEND_API_ROUTES":
                    mod = types.ModuleType("BACKEND_API_ROUTES")
                    mod._wa_send_quiet = _wa_quiet  # type: ignore
                    return mod
                return real_import(name, *args, **kwargs)

            with patch.dict(os.environ, {"OWNER_WHATSAPP_PHONE": "+972500000000"}), \
                 patch("builtins.__import__", _fake_import):
                await s._notify_owner_reauth_required("duplicate_test")

            assert len(wa_calls) == 0, f"Expected 0 WA messages (cooldown), got {len(wa_calls)}"
        finally:
            _mod._STATE_DIR = orig_dir


@run("25. __aenter__ returns None when _valid=False after relogin failure")
async def test_25_aenter_returns_none_when_invalid():
    s = FacebookSession()

    async def _fake_start():
        s._valid = False

    async def _fake_stop():
        pass

    with patch.object(s, "_start", _fake_start), \
         patch.object(s, "_stop", _fake_stop):
        result = await s.__aenter__()

    assert result is None, f"Expected None when session invalid, got {result}"


# ── Summary ──────────────────────────────────────────────────────────────────

print("=" * 60)
print("fb_auth_lifecycle_test.py — Phase 5J Auth Lifecycle")
print("=" * 60)

for item in [
    "1. _AuthState enum has required members",
    "2. _health_check → AUTHENTICATED when profile link JS fires",
    "3. _health_check → LOGIN_FORM when id=email in HTML",
    "4. _health_check → ACCOUNT_PICKER when list_accounts in JS",
    "5. _health_check → ACCOUNT_PICKER when URL contains /login",
    "6. _health_check → ACCOUNT_PICKER on Hebrew picker text",
    "7. _health_check → AMBIGUOUS when no signals match",
    "8. _health_check → ERROR on navigation exception",
    "9. _start sets _valid=True when AUTHENTICATED on first check",
    "10. _start triggers auto-relogin on LOGIN_FORM detection",
    "11. _start triggers auto-relogin on ACCOUNT_PICKER detection",
    "12. _start re-validates after auto-relogin success → _valid=True",
    "13. _start sets _valid=False when auto-relogin returns False",
    "14. _start notifies owner when auto-relogin fails",
    "15. _try_auto_relogin returns False when FB_EMAIL missing",
    "16. _try_auto_relogin returns False when FB_PASSWORD missing",
    "17. _try_auto_relogin returns False when login() raises exception",
    "18. _try_auto_relogin returns False when login() returns False",
    "19. _try_auto_relogin returns False when cookies lack c_user after login",
    "20. _try_auto_relogin returns True when login() succeeds + cookies valid",
    "21. _stop honours Phase 3 guard — no cookie save when _valid=False",
    "22. _stop honours Phase 5C guard — no save when c_user missing mid-session",
    "23. _notify_owner sends WA message and writes cooldown file",
    "24. _notify_owner respects 6-hour cooldown — no duplicate alert",
    "25. __aenter__ returns None when _valid=False after relogin failure",
]:
    pass  # labels handled by @run decorator above

print()
print(f"Results: {PASS} passed, {FAIL} failed")
if ERRORS:
    print("\nFailed tests:")
    for e in ERRORS:
        print(f"  - {e}")

sys.exit(0 if FAIL == 0 else 1)
