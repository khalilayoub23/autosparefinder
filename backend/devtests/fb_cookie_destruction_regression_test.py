"""
Regression test: Phase 5J cookie destruction incident.

During the Phase 5J controlled activation, fb_browser_login.login() overwrote
last-known-good cookies.json with 6 degraded checkpoint cookies (no c_user/xs)
because line 728 wrote cookies unconditionally regardless of authentication result.

ROOT FIX: cookies are now written ONLY when logged_in=True (c_user AND xs present).
A failed or partial login preserves the existing cookies.json byte-for-byte.

This file proves:
  - OLD behavior: unconditional write → test WOULD FAIL (cookies destroyed)
  - NEW behavior: conditional write → test PASSES (cookies preserved)

Tests are fully offline (no real Facebook login, no Playwright, no network).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, "/app")

import social.facebook_browser.fb_browser_login as login_mod

PASS = "✅  [PASS]"
FAIL = "❌  [FAIL]"
_fail_count = 0


def record(label: str, status: str, detail: str = "") -> None:
    global _fail_count
    if status == FAIL:
        _fail_count += 1
    print(f"{status}  {label}")
    if detail:
        print(f"         {detail}")


# ── Cookie fixtures ───────────────────────────────────────────────────────────

GOOD_COOKIES = [
    {"name": "c_user", "value": "REDACTED", "domain": ".facebook.com", "path": "/"},
    {"name": "xs", "value": "REDACTED", "domain": ".facebook.com", "path": "/"},
    {"name": "datr", "value": "REDACTED", "domain": ".facebook.com", "path": "/"},
    {"name": "sb", "value": "REDACTED", "domain": ".facebook.com", "path": "/"},
    {"name": "fr", "value": "REDACTED", "domain": ".facebook.com", "path": "/"},
]

CHECKPOINT_COOKIES = [
    {"name": "checkpoint", "value": "REDACTED", "domain": ".facebook.com", "path": "/"},
    {"name": "datr", "value": "REDACTED", "domain": ".facebook.com", "path": "/"},
    {"name": "fr", "value": "REDACTED", "domain": ".facebook.com", "path": "/"},
    {"name": "locale", "value": "REDACTED", "domain": ".facebook.com", "path": "/"},
]

CUSER_ONLY_COOKIES = [
    {"name": "c_user", "value": "REDACTED", "domain": ".facebook.com", "path": "/"},
    {"name": "datr", "value": "REDACTED", "domain": ".facebook.com", "path": "/"},
]

XS_ONLY_COOKIES = [
    {"name": "xs", "value": "REDACTED", "domain": ".facebook.com", "path": "/"},
    {"name": "datr", "value": "REDACTED", "domain": ".facebook.com", "path": "/"},
]

EMPTY_COOKIES: list = []


# ── Playwright mock builder ───────────────────────────────────────────────────

def _make_pw_mock(
    browser_cookies: list[dict],
    page_content: str = "<html><body></body></html>",
    page_url: str = "https://www.facebook.com/home.php",
) -> MagicMock:
    """Build a minimal Playwright mock that satisfies login()'s call sites."""
    mock_elem = AsyncMock()
    mock_elem.fill = AsyncMock()
    mock_elem.press = AsyncMock()
    mock_elem.click = AsyncMock()

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.content = AsyncMock(return_value=page_content)
    mock_page.url = page_url
    mock_page.wait_for_selector = AsyncMock(return_value=mock_elem)
    mock_page.wait_for_url = AsyncMock()
    mock_page.screenshot = AsyncMock()
    mock_page.keyboard = AsyncMock()
    mock_page.keyboard.press = AsyncMock()
    mock_page.frames = []
    mock_page.evaluate = AsyncMock(return_value=False)
    mock_page.query_selector = AsyncMock(return_value=None)

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    # Return same cookies on every call so health-check reads match the final state
    mock_context.cookies = AsyncMock(return_value=browser_cookies)
    mock_context.add_cookies = AsyncMock()

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw_instance = AsyncMock()
    mock_pw_instance.chromium = MagicMock()
    mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
    mock_pw_instance.__aenter__ = AsyncMock(return_value=mock_pw_instance)
    mock_pw_instance.__aexit__ = AsyncMock(return_value=False)

    mock_pw_class = MagicMock(return_value=mock_pw_instance)
    return mock_pw_class


async def _run_login(tmp_cookies_file: Path, browser_cookies: list[dict]) -> bool | Exception:
    """Execute login() with mocked Playwright, redirecting persistence to tmp_cookies_file."""
    pw_mock = _make_pw_mock(browser_cookies)
    env = {"FB_EMAIL": "test@example.com", "FB_PASSWORD": "testpassword"}
    with patch("playwright.async_api.async_playwright", pw_mock), \
         patch.object(login_mod, "_COOKIES_FILE", tmp_cookies_file), \
         patch.object(login_mod, "_STATE_DIR", tmp_cookies_file.parent), \
         patch.dict(os.environ, env, clear=False):
        try:
            return await login_mod.login()
        except Exception as exc:
            return exc


def _write_good_cookies(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(GOOD_COOKIES, indent=2), encoding="utf-8")


def _read_cookies(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _cookie_names(cookies: list[dict]) -> set[str]:
    return {c["name"] for c in cookies}


# ── Test cases ────────────────────────────────────────────────────────────────

def run_test_1_checkpoint_does_not_overwrite():
    """Phase 5J exact incident: checkpoint cookies must NOT overwrite c_user+xs."""
    with tempfile.TemporaryDirectory() as tmp:
        cookies_file = Path(tmp) / "cookies.json"
        _write_good_cookies(cookies_file)

        result = asyncio.run(_run_login(cookies_file, CHECKPOINT_COOKIES))

        after = _read_cookies(cookies_file)
        names_after = _cookie_names(after)
        login_result = result if isinstance(result, bool) else False

        if login_result is False and "c_user" in names_after and "xs" in names_after and "checkpoint" not in names_after:
            record(
                "1. Checkpoint cookies do NOT overwrite last-known-good (Phase 5J incident)",
                PASS,
                f"login=False, c_user preserved, xs preserved, checkpoint NOT written",
            )
        else:
            record(
                "1. Checkpoint cookies do NOT overwrite last-known-good (Phase 5J incident)",
                FAIL,
                f"login={login_result}, c_user={'c_user' in names_after}, xs={'xs' in names_after}, "
                f"checkpoint={'checkpoint' in names_after}, names={sorted(names_after)}",
            )


def run_test_2_successful_login_writes_cookies():
    """Successful login (c_user + xs present) MUST write new cookies."""
    with tempfile.TemporaryDirectory() as tmp:
        cookies_file = Path(tmp) / "cookies.json"
        # Start with empty file (no pre-existing state)

        result = asyncio.run(_run_login(cookies_file, GOOD_COOKIES))

        if not isinstance(result, bool):
            record("2. Successful login writes cookies to disk", FAIL, f"login() raised: {result}")
            return

        after = _read_cookies(cookies_file)
        names_after = _cookie_names(after)

        if result is True and "c_user" in names_after and "xs" in names_after:
            record(
                "2. Successful login writes cookies to disk",
                PASS,
                f"login=True, c_user={('c_user' in names_after)}, xs={('xs' in names_after)}, "
                f"count={len(after)}",
            )
        else:
            record(
                "2. Successful login writes cookies to disk",
                FAIL,
                f"login={result}, names={sorted(names_after)}",
            )


def run_test_3_cuser_only_does_not_overwrite():
    """c_user present but xs missing → xs required for valid session → no write."""
    with tempfile.TemporaryDirectory() as tmp:
        cookies_file = Path(tmp) / "cookies.json"
        _write_good_cookies(cookies_file)

        result = asyncio.run(_run_login(cookies_file, CUSER_ONLY_COOKIES))

        after = _read_cookies(cookies_file)
        names_after = _cookie_names(after)
        login_result = result if isinstance(result, bool) else False

        if login_result is False and "c_user" in names_after and "xs" in names_after:
            record(
                "3. c_user-only (xs missing) preserves existing cookies.json",
                PASS,
                f"login=False, original c_user+xs still present",
            )
        else:
            record(
                "3. c_user-only (xs missing) preserves existing cookies.json",
                FAIL,
                f"login={login_result}, xs={'xs' in names_after}, c_user={'c_user' in names_after}",
            )


def run_test_4_xs_only_does_not_overwrite():
    """xs present but c_user missing → c_user required → no write."""
    with tempfile.TemporaryDirectory() as tmp:
        cookies_file = Path(tmp) / "cookies.json"
        _write_good_cookies(cookies_file)

        result = asyncio.run(_run_login(cookies_file, XS_ONLY_COOKIES))

        after = _read_cookies(cookies_file)
        names_after = _cookie_names(after)
        login_result = result if isinstance(result, bool) else False

        if login_result is False and "c_user" in names_after and "xs" in names_after:
            record(
                "4. xs-only (c_user missing) preserves existing cookies.json",
                PASS,
                f"login=False, original c_user+xs still present",
            )
        else:
            record(
                "4. xs-only (c_user missing) preserves existing cookies.json",
                FAIL,
                f"login={login_result}, xs={'xs' in names_after}, c_user={'c_user' in names_after}",
            )


def run_test_5_empty_cookies_does_not_overwrite():
    """Empty cookie set from browser → no write."""
    with tempfile.TemporaryDirectory() as tmp:
        cookies_file = Path(tmp) / "cookies.json"
        _write_good_cookies(cookies_file)

        result = asyncio.run(_run_login(cookies_file, EMPTY_COOKIES))

        after = _read_cookies(cookies_file)
        names_after = _cookie_names(after)
        login_result = result if isinstance(result, bool) else False

        if login_result is False and "c_user" in names_after and "xs" in names_after:
            record(
                "5. Empty browser cookie set preserves existing cookies.json",
                PASS,
                f"login=False, original c_user+xs still present",
            )
        else:
            record(
                "5. Empty browser cookie set preserves existing cookies.json",
                FAIL,
                f"login={login_result}, names={sorted(names_after)}",
            )


def run_test_6_exception_before_write_preserves_cookies():
    """login() raising an exception before the write must leave cookies.json intact."""
    with tempfile.TemporaryDirectory() as tmp:
        cookies_file = Path(tmp) / "cookies.json"
        _write_good_cookies(cookies_file)

        # Make playwright raise immediately — simulates network failure before login
        pw_mock = MagicMock()
        pw_mock.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("Playwright failed"))
        pw_mock.return_value.__aexit__ = AsyncMock(return_value=False)

        env = {"FB_EMAIL": "test@example.com", "FB_PASSWORD": "testpassword"}
        with patch("playwright.async_api.async_playwright", pw_mock), \
             patch.object(login_mod, "_COOKIES_FILE", cookies_file), \
             patch.object(login_mod, "_STATE_DIR", cookies_file.parent), \
             patch.dict(os.environ, env, clear=False):
            try:
                asyncio.run(login_mod.login())
                raised = False
            except Exception:
                raised = True

        after = _read_cookies(cookies_file)
        names_after = _cookie_names(after)

        if "c_user" in names_after and "xs" in names_after:
            record(
                "6. Exception before write preserves existing cookies.json",
                PASS,
                f"exception raised={raised}, c_user+xs preserved",
            )
        else:
            record(
                "6. Exception before write preserves existing cookies.json",
                FAIL,
                f"c_user={'c_user' in names_after}, xs={'xs' in names_after}",
            )


def run_test_7_login_form_visible_does_not_overwrite():
    """login form still visible after 'login' → not authenticated → no write."""
    with tempfile.TemporaryDirectory() as tmp:
        cookies_file = Path(tmp) / "cookies.json"
        _write_good_cookies(cookies_file)

        # Page content still shows login form (name="login") even though checkpoint
        # cookie is present — this can happen on some redirect paths
        pw_mock = _make_pw_mock(
            CHECKPOINT_COOKIES,
            page_content='<html><body><form name="login"><input id="email"/></form></body></html>',
            page_url="https://www.facebook.com/login/",
        )

        env = {"FB_EMAIL": "test@example.com", "FB_PASSWORD": "testpassword"}
        with patch("playwright.async_api.async_playwright", pw_mock), \
             patch.object(login_mod, "_COOKIES_FILE", cookies_file), \
             patch.object(login_mod, "_STATE_DIR", cookies_file.parent), \
             patch.dict(os.environ, env, clear=False):
            try:
                result = asyncio.run(login_mod.login())
            except Exception:
                result = False

        after = _read_cookies(cookies_file)
        names_after = _cookie_names(after)
        login_result = result if isinstance(result, bool) else False

        if login_result is False and "c_user" in names_after and "xs" in names_after:
            record(
                "7. Login form still visible → preserves existing cookies.json",
                PASS,
                f"login=False, original c_user+xs preserved",
            )
        else:
            record(
                "7. Login form still visible → preserves existing cookies.json",
                FAIL,
                f"login={login_result}, c_user={'c_user' in names_after}, xs={'xs' in names_after}",
            )


def run_test_8_no_cookies_file_pre_existing():
    """When no cookies.json exists before login, a failed login must NOT create one."""
    with tempfile.TemporaryDirectory() as tmp:
        cookies_file = Path(tmp) / "cookies.json"
        # Do NOT pre-write the file

        result = asyncio.run(_run_login(cookies_file, CHECKPOINT_COOKIES))

        login_result = result if isinstance(result, bool) else False
        file_created = cookies_file.exists()

        if login_result is False and not file_created:
            record(
                "8. Failed login with no pre-existing cookies.json — file NOT created",
                PASS,
                f"login=False, cookies.json not created",
            )
        elif login_result is True:
            record(
                "8. Failed login with no pre-existing cookies.json — file NOT created",
                FAIL,
                f"login() unexpectedly returned True with checkpoint cookies",
            )
        else:
            record(
                "8. Failed login with no pre-existing cookies.json — file NOT created",
                FAIL,
                f"login=False but cookies.json was CREATED — partial cookies written",
            )


def run_test_9_return_value_preserved():
    """login() return value contract unchanged: True=authenticated, False=not."""
    with tempfile.TemporaryDirectory() as tmp:
        cookies_file = Path(tmp) / "cookies.json"

        # Success path
        result_success = asyncio.run(_run_login(cookies_file, GOOD_COOKIES))
        # Failure path (need a new temp file so cookies exist for failure test)
        cookies_file2 = Path(tmp) / "cookies2.json"
        _write_good_cookies(cookies_file2)
        result_fail = asyncio.run(_run_login(cookies_file2, CHECKPOINT_COOKIES))

        if result_success is True and result_fail is False:
            record(
                "9. Return value contract: True=authenticated, False=not",
                PASS,
                f"success→True, failure→False",
            )
        else:
            record(
                "9. Return value contract: True=authenticated, False=not",
                FAIL,
                f"success={result_success!r}, fail={result_fail!r}",
            )


def run_test_10_old_behavior_would_have_failed():
    """
    Document that the OLD unconditional write WOULD have overwritten cookies.

    This test simulates the old behavior by directly calling the write that
    was previously unconditional, and shows that it destroys the good state.
    This is a documentation test — it SHOULD pass (demonstrating the old
    code was wrong) but does not directly test the current implementation.
    """
    with tempfile.TemporaryDirectory() as tmp:
        cookies_file = Path(tmp) / "cookies.json"
        _write_good_cookies(cookies_file)

        pre_names = _cookie_names(_read_cookies(cookies_file))

        # Simulate OLD behavior: write checkpoint cookies unconditionally
        cookies_file.write_text(json.dumps(CHECKPOINT_COOKIES, indent=2), encoding="utf-8")

        after_names = _cookie_names(_read_cookies(cookies_file))

        # OLD behavior: c_user and xs are LOST; checkpoint is present
        old_behavior_destroyed = "c_user" not in after_names and "xs" not in after_names and "checkpoint" in after_names

        if old_behavior_destroyed:
            record(
                "10. [DOC] Old unconditional write WOULD have destroyed c_user+xs",
                PASS,
                f"Confirmed: pre={sorted(pre_names)} → post={sorted(after_names)} (old bug reproduced)",
            )
        else:
            record(
                "10. [DOC] Old unconditional write WOULD have destroyed c_user+xs",
                FAIL,
                f"Unexpected: pre={sorted(pre_names)} → post={sorted(after_names)}",
            )


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("=" * 70)
    print("fb_cookie_destruction_regression_test.py — Phase 5J Cookie Fix")
    print("=" * 70)
    print()

    run_test_1_checkpoint_does_not_overwrite()
    run_test_2_successful_login_writes_cookies()
    run_test_3_cuser_only_does_not_overwrite()
    run_test_4_xs_only_does_not_overwrite()
    run_test_5_empty_cookies_does_not_overwrite()
    run_test_6_exception_before_write_preserves_cookies()
    run_test_7_login_form_visible_does_not_overwrite()
    run_test_8_no_cookies_file_pre_existing()
    run_test_9_return_value_preserved()
    run_test_10_old_behavior_would_have_failed()

    print()
    print("=" * 70)
    print("fb_cookie_destruction_regression_test.py — Phase 5J Cookie Fix")
    print("=" * 70)
    print()
    print(f"Results: {10 - _fail_count} passed, {_fail_count} failed")
    print()


if __name__ == "__main__":
    main()
    sys.exit(0 if _fail_count == 0 else 1)
