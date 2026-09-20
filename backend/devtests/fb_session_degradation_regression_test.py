"""
Facebook Session Mid-Session Degradation — Regression Tests (Phase 5C)
Tests the Phase 5C fix: _stop() must NOT overwrite cookies.json when the live
browser cookies lack c_user and/or xs (mid-session Facebook invalidation).

Cases covered:
  Case A — startup health check fails (_valid=False): Phase 3 guard prevents save
  Case B — mid-session degradation (_valid=True, no c_user/xs): Phase 5C guard prevents save
  Case C — healthy full session (_valid=True, c_user+xs present): save proceeds normally

ALL tests are isolated:
  - No live Facebook browser automation
  - No Production cookies modified
  - No Production DB writes
  - No service restarts
  - Temp files / AsyncMock / MagicMock only
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, "/app")

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results = []


def record(name: str, status: str, detail: str = "") -> None:
    icon = "✅" if status == PASS else ("⚠️" if status == SKIP else "❌")
    print(f"  {icon}  [{status}] {name}" + (f"\n         {detail}" if detail else ""))
    results.append({"name": name, "status": status, "detail": detail})


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── helpers ────────────────────────────────────────────────────────────────────

def _full_auth_cookies() -> list[dict]:
    """7 cookies with c_user and xs — represents a healthy authenticated state."""
    return [
        {"name": "c_user", "value": "100003155460373"},
        {"name": "xs", "value": "16:XABCDEF:2:1786410166"},
        {"name": "datr", "value": "datr_value"},
        {"name": "sb", "value": "sb_value"},
        {"name": "fr", "value": "fr_value"},
        {"name": "wd", "value": "1280x800"},
        {"name": "locale", "value": "he_IL"},
    ]


def _degraded_cookies() -> list[dict]:
    """4 cookies WITHOUT c_user or xs — represents mid-session Facebook invalidation."""
    return [
        {"name": "datr", "value": "datr_value"},
        {"name": "sb", "value": "sb_value"},
        {"name": "wd", "value": "1280x800"},
        {"name": "fr", "value": "fr_value"},
    ]


def _partial_cookies_no_xs() -> list[dict]:
    """c_user present but xs missing — still degraded."""
    return [
        {"name": "c_user", "value": "100003155460373"},
        {"name": "datr", "value": "datr_value"},
    ]


def _partial_cookies_no_c_user() -> list[dict]:
    """xs present but c_user missing — still degraded."""
    return [
        {"name": "xs", "value": "16:XABCDEF:2:1786410166"},
        {"name": "datr", "value": "datr_value"},
    ]


def _build_mock_session(valid: bool, cookies: list[dict], page_closed: bool = False):
    """Build a FacebookSession with mocked internals, bypassing Playwright entirely."""
    from social.facebook_browser.session import FacebookSession

    session = FacebookSession.__new__(FacebookSession)
    session._valid = valid
    session._playwright = MagicMock()
    session._playwright.stop = AsyncMock()
    session._browser = MagicMock()
    session._browser.close = AsyncMock()

    session._page = MagicMock()
    session._page.is_closed = MagicMock(return_value=page_closed)

    session._context = MagicMock()
    session._context.cookies = AsyncMock(return_value=cookies)

    return session


# ── Test 1: Case A — Phase 3 guard (health check fails, _valid=False) ─────────

async def test_case_a_phase3_guard_blocks_save():
    """Case A: _valid=False (health check failed at startup) → _stop() must NOT save."""
    name = "Case A — Phase 3 guard: _valid=False → no save"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cookies_file = Path(tmpdir) / "fb_browser_session" / "cookies.json"

            with patch("social.facebook_browser.session._STATE_DIR",
                       Path(tmpdir) / "fb_browser_session"), \
                 patch("social.facebook_browser.session._COOKIES_FILE",
                       cookies_file):

                session = _build_mock_session(valid=False, cookies=_full_auth_cookies())
                await session._stop()

                if cookies_file.exists():
                    record(name, FAIL, "cookies.json was written despite _valid=False")
                else:
                    record(name, PASS)
    except Exception as exc:
        record(name, FAIL, str(exc))


# ── Test 2: Case B — Phase 5C guard (mid-session degradation) ─────────────────

async def test_case_b_degradation_guard_blocks_save():
    """Case B: _valid=True but c_user+xs missing → _stop() must NOT overwrite cookies."""
    name = "Case B — Phase 5C guard: degraded cookies → no overwrite"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cookies_file = Path(tmpdir) / "fb_browser_session" / "cookies.json"
            # Pre-seed with authenticated content (simulates last-known-good on disk)
            cookies_file.parent.mkdir(parents=True, exist_ok=True)
            good_data = _full_auth_cookies()
            cookies_file.write_text(json.dumps(good_data), encoding="utf-8")

            with patch("social.facebook_browser.session._STATE_DIR",
                       Path(tmpdir) / "fb_browser_session"), \
                 patch("social.facebook_browser.session._COOKIES_FILE",
                       cookies_file):

                session = _build_mock_session(valid=True, cookies=_degraded_cookies())
                await session._stop()

                saved = json.loads(cookies_file.read_text())
                saved_names = {c["name"] for c in saved}
                if "c_user" not in saved_names or "xs" not in saved_names:
                    record(name, FAIL,
                           "cookies.json was overwritten with degraded state — "
                           f"saved names: {saved_names}")
                else:
                    record(name, PASS, "last-known-good cookies preserved on disk")
    except Exception as exc:
        record(name, FAIL, str(exc))


# ── Test 3: Case C — healthy session saves normally ────────────────────────────

async def test_case_c_healthy_session_saves():
    """Case C: _valid=True, c_user+xs present → _stop() MUST save cookies."""
    name = "Case C — healthy session with c_user+xs → save proceeds normally"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cookies_file = Path(tmpdir) / "fb_browser_session" / "cookies.json"

            with patch("social.facebook_browser.session._STATE_DIR",
                       Path(tmpdir) / "fb_browser_session"), \
                 patch("social.facebook_browser.session._COOKIES_FILE",
                       cookies_file):

                session = _build_mock_session(valid=True, cookies=_full_auth_cookies())
                await session._stop()

                if not cookies_file.exists():
                    record(name, FAIL, "cookies.json was NOT written for healthy session")
                    return
                saved = json.loads(cookies_file.read_text())
                saved_names = {c["name"] for c in saved}
                if "c_user" in saved_names and "xs" in saved_names:
                    record(name, PASS, f"saved {len(saved)} cookies including c_user+xs")
                else:
                    record(name, FAIL, f"saved cookies missing auth keys: {saved_names}")
    except Exception as exc:
        record(name, FAIL, str(exc))


# ── Test 4: Partial degradation — c_user missing, xs present ──────────────────

async def test_partial_degradation_no_c_user_blocks_save():
    """Partial Case B: c_user missing, xs present → must NOT overwrite."""
    name = "Case B partial — c_user missing (xs present) → no overwrite"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cookies_file = Path(tmpdir) / "fb_browser_session" / "cookies.json"
            cookies_file.parent.mkdir(parents=True, exist_ok=True)
            cookies_file.write_text(json.dumps(_full_auth_cookies()), encoding="utf-8")

            with patch("social.facebook_browser.session._STATE_DIR",
                       Path(tmpdir) / "fb_browser_session"), \
                 patch("social.facebook_browser.session._COOKIES_FILE",
                       cookies_file):

                session = _build_mock_session(valid=True, cookies=_partial_cookies_no_c_user())
                await session._stop()

                saved = json.loads(cookies_file.read_text())
                saved_names = {c["name"] for c in saved}
                if "c_user" in saved_names:
                    record(name, PASS, "last-known-good preserved (c_user present in saved)")
                else:
                    record(name, FAIL, f"last-known-good was overwritten: {saved_names}")
    except Exception as exc:
        record(name, FAIL, str(exc))


# ── Test 5: Partial degradation — xs missing, c_user present ──────────────────

async def test_partial_degradation_no_xs_blocks_save():
    """Partial Case B: xs missing, c_user present → must NOT overwrite."""
    name = "Case B partial — xs missing (c_user present) → no overwrite"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cookies_file = Path(tmpdir) / "fb_browser_session" / "cookies.json"
            cookies_file.parent.mkdir(parents=True, exist_ok=True)
            cookies_file.write_text(json.dumps(_full_auth_cookies()), encoding="utf-8")

            with patch("social.facebook_browser.session._STATE_DIR",
                       Path(tmpdir) / "fb_browser_session"), \
                 patch("social.facebook_browser.session._COOKIES_FILE",
                       cookies_file):

                session = _build_mock_session(valid=True, cookies=_partial_cookies_no_xs())
                await session._stop()

                saved = json.loads(cookies_file.read_text())
                saved_names = {c["name"] for c in saved}
                if "xs" in saved_names:
                    record(name, PASS, "last-known-good preserved (xs present in saved)")
                else:
                    record(name, FAIL, f"last-known-good was overwritten: {saved_names}")
    except Exception as exc:
        record(name, FAIL, str(exc))


# ── Test 6: Page closed — _valid=True but page already closed ─────────────────

async def test_closed_page_skips_save():
    """When page is closed, _stop() must skip cookie save and not crash."""
    name = "Page closed — _valid=True, page.is_closed()=True → skip save, no crash"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cookies_file = Path(tmpdir) / "fb_browser_session" / "cookies.json"

            with patch("social.facebook_browser.session._STATE_DIR",
                       Path(tmpdir) / "fb_browser_session"), \
                 patch("social.facebook_browser.session._COOKIES_FILE",
                       cookies_file):

                session = _build_mock_session(
                    valid=True, cookies=_full_auth_cookies(), page_closed=True
                )
                await session._stop()  # must not raise
                session._context.close.assert_called_once()  # persistent context teardown flushes the profile
                session._playwright.stop.assert_called_once()
                record(name, PASS, "no crash; persistent context+playwright closed cleanly")
    except Exception as exc:
        record(name, FAIL, str(exc))


# ── Test 7: Double _stop() call — must not crash ──────────────────────────────

async def test_double_stop_no_crash():
    """Double _stop() (as happens in __aenter__ when _valid=False): must not raise."""
    name = "Double _stop() call — must not crash"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cookies_file = Path(tmpdir) / "fb_browser_session" / "cookies.json"

            with patch("social.facebook_browser.session._STATE_DIR",
                       Path(tmpdir) / "fb_browser_session"), \
                 patch("social.facebook_browser.session._COOKIES_FILE",
                       cookies_file):

                session = _build_mock_session(valid=False, cookies=[])
                await session._stop()
                await session._stop()  # second call — must not raise
                record(name, PASS)
    except Exception as exc:
        record(name, FAIL, str(exc))


# ── Test 8: Exception in context.cookies() — must survive without saving ──────

async def test_cookies_exception_survives():
    """If context.cookies() raises, _stop() must catch it and not crash."""
    name = "Exception in context.cookies() → _stop() survives without saving"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cookies_file = Path(tmpdir) / "fb_browser_session" / "cookies.json"

            with patch("social.facebook_browser.session._STATE_DIR",
                       Path(tmpdir) / "fb_browser_session"), \
                 patch("social.facebook_browser.session._COOKIES_FILE",
                       cookies_file):

                session = _build_mock_session(valid=True, cookies=[])
                session._context.cookies = AsyncMock(
                    side_effect=RuntimeError("Playwright context closed")
                )
                await session._stop()  # must not raise

                if cookies_file.exists():
                    record(name, FAIL, "cookies.json was written despite exception in cookies()")
                else:
                    record(name, PASS, "survived exception; nothing written")
    except Exception as exc:
        record(name, FAIL, str(exc))


# ── Test 9: Empty cookies list — must NOT save ────────────────────────────────

async def test_empty_cookies_blocks_save():
    """Empty cookie list → _stop() must not write cookies.json."""
    name = "Empty cookies list (_valid=True) → must NOT save"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cookies_file = Path(tmpdir) / "fb_browser_session" / "cookies.json"
            cookies_file.parent.mkdir(parents=True, exist_ok=True)
            cookies_file.write_text(json.dumps(_full_auth_cookies()), encoding="utf-8")

            with patch("social.facebook_browser.session._STATE_DIR",
                       Path(tmpdir) / "fb_browser_session"), \
                 patch("social.facebook_browser.session._COOKIES_FILE",
                       cookies_file):

                session = _build_mock_session(valid=True, cookies=[])
                await session._stop()

                saved = json.loads(cookies_file.read_text())
                saved_names = {c["name"] for c in saved}
                if "c_user" in saved_names and "xs" in saved_names:
                    record(name, PASS, "last-known-good preserved (empty cookies not saved)")
                else:
                    record(name, FAIL, f"empty cookies overwrote disk: {saved_names}")
    except Exception as exc:
        record(name, FAIL, str(exc))


# ── Test 10: Sep 12 incident re-play ─────────────────────────────────────────

async def test_sep12_incident_replay():
    """
    Replays the Sep 12 incident: session starts healthy, Facebook degrades mid-scan,
    _stop() receives 4 unauthenticated cookies. The fix must preserve last-known-good.
    """
    name = "Sep 12 incident replay — mid-scan FB invalidation → cookies.json preserved"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cookies_file = Path(tmpdir) / "fb_browser_session" / "cookies.json"
            cookies_file.parent.mkdir(parents=True, exist_ok=True)
            # Simulate last-known-good: 7 cookies on disk
            cookies_file.write_text(json.dumps(_full_auth_cookies()), encoding="utf-8")

            with patch("social.facebook_browser.session._STATE_DIR",
                       Path(tmpdir) / "fb_browser_session"), \
                 patch("social.facebook_browser.session._COOKIES_FILE",
                       cookies_file):

                # Simulate: health check passed at startup → _valid=True
                # But browser now holds only 4 unauthenticated cookies
                session = _build_mock_session(
                    valid=True, cookies=_degraded_cookies()  # 4 cookies: datr, sb, wd, fr
                )
                await session._stop()

                on_disk = json.loads(cookies_file.read_text())
                disk_names = {c["name"] for c in on_disk}
                if "c_user" in disk_names and "xs" in disk_names and len(on_disk) == 7:
                    record(name, PASS,
                           f"last-known-good preserved: {len(on_disk)} cookies on disk")
                else:
                    record(name, FAIL,
                           f"incident reproduced: disk now has {len(on_disk)} cookies "
                           f"{disk_names} — fix did NOT work")
    except Exception as exc:
        record(name, FAIL, str(exc))


# ── Runner ─────────────────────────────────────────────────────────────────────

def main():
    print()
    print("=" * 70)
    print("  Facebook Session Mid-Session Degradation — Regression Tests (Phase 5C)")
    print("=" * 70)
    print()

    tests = [
        test_case_a_phase3_guard_blocks_save,
        test_case_b_degradation_guard_blocks_save,
        test_case_c_healthy_session_saves,
        test_partial_degradation_no_c_user_blocks_save,
        test_partial_degradation_no_xs_blocks_save,
        test_closed_page_skips_save,
        test_double_stop_no_crash,
        test_cookies_exception_survives,
        test_empty_cookies_blocks_save,
        test_sep12_incident_replay,
    ]

    for test_fn in tests:
        run(test_fn())

    print()
    passed = sum(1 for r in results if r["status"] == PASS)
    failed = sum(1 for r in results if r["status"] == FAIL)
    skipped = sum(1 for r in results if r["status"] == SKIP)
    total = len(results)

    print("=" * 70)
    print(f"  Results: {passed}/{total} PASSED   {failed} FAILED   {skipped} SKIPPED")
    print("=" * 70)
    print()

    if failed > 0:
        print("FAILED TESTS:")
        for r in results:
            if r["status"] == FAIL:
                print(f"  ❌ {r['name']}")
                if r["detail"]:
                    print(f"     {r['detail']}")
        print()
        sys.exit(1)
    else:
        print("  ✅ ALL TESTS PASSED")
        print()
        sys.exit(0)


if __name__ == "__main__":
    main()
