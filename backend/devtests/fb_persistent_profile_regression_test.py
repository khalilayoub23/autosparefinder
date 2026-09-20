"""
Facebook persistent-profile session — regression tests (2026-09-20).

Incident: the production scanner replayed cookies.json into a FRESH ephemeral Chromium
context (spoofed UA). Facebook rejected that replay ("Continue as <name>" picker) while the
persistent Chrome profile that established the session stayed authenticated for 11+ hours,
same IP, headless. Fix: FacebookSession runs on a persistent profile (_PROFILE_DIR).

Checks (unit, mocked Playwright; production cookies/profile never touched):
  1. _start uses launch_persistent_context on _PROFILE_DIR, never chromium.launch/new_context
  2. no user_agent override (must match the browser that established the session)
  3. profile already holds c_user+xs -> cookies.json backup is NOT injected over it
  4. profile unauthenticated -> cookies.json backup IS seeded
  5. _stop closes the persistent context (flushes profile) and playwright
  6. lock is released on the invalid-session path and on exit (no deadlock)
  7. two concurrent `async with FacebookSession()` are serialized
  8. cookies.json c_user+xs guard still intact (degraded live cookies never overwrite)
Live (optional; skipped when the native profile is absent):
  9. a throwaway COPY of the real profile authenticates through FacebookSession with a
     deliberately stale cookies.json present -> AUTHENTICATED, stale backup ignored.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, "/app")
import social.facebook_browser.session as sm  # noqa: E402

results: list[tuple[str, bool, str]] = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail and not ok else ""))


def _pw(profile_cookie_names: list[str]):
    ctx = AsyncMock()
    ctx.cookies = AsyncMock(return_value=[{"name": n, "value": "x"} for n in profile_cookie_names])
    ctx.add_cookies = AsyncMock()
    ctx.clear_cookies = AsyncMock()
    ctx.close = AsyncMock()
    page = AsyncMock()
    page.is_closed = MagicMock(return_value=False)
    ctx.new_page = AsyncMock(return_value=page)
    pw = AsyncMock()
    pw.chromium = MagicMock()
    pw.chromium.launch = AsyncMock()
    pw.chromium.launch_persistent_context = AsyncMock(return_value=ctx)
    pw.stop = AsyncMock()
    cls = MagicMock()
    cls.return_value.start = AsyncMock(return_value=pw)
    return cls, pw, ctx


async def _start_with(profile_names, backup_cookies, auth=sm._AuthState.AUTHENTICATED):
    cls, pw, ctx = _pw(profile_names)
    s = sm.FacebookSession()
    with patch("playwright.async_api.async_playwright", cls), \
         patch.object(sm, "_load_cookies", return_value=backup_cookies), \
         patch.object(sm.FacebookSession, "_health_check", AsyncMock(return_value=auth)), \
         patch.object(sm, "_PROFILE_DIR", Path(tempfile.mkdtemp())):
        await s._start()
    return s, pw, ctx


async def main() -> None:
    backup = [{"name": "c_user", "value": "stale"}, {"name": "xs", "value": "stale"}]

    s, pw, ctx = await _start_with(["c_user", "xs", "datr"], backup)
    rec("1. _start uses launch_persistent_context (not launch/new_context)",
        pw.chromium.launch_persistent_context.await_count == 1 and pw.chromium.launch.await_count == 0)
    kw = pw.chromium.launch_persistent_context.await_args.kwargs
    rec("2. no user_agent override on the persistent profile", "user_agent" not in kw, str(list(kw)))
    rec("3. authenticated profile -> stale cookies.json NOT injected", ctx.add_cookies.await_count == 0)
    rec("3b. session valid from the profile", s._valid is True)

    s, pw, ctx = await _start_with(["datr", "sb"], backup)
    rec("4. unauthenticated profile -> cookies.json backup seeded", ctx.add_cookies.await_count == 1)

    s, pw, ctx = await _start_with(["c_user", "xs"], backup)
    await s._stop()
    rec("5. _stop closes persistent context + playwright",
        ctx.close.await_count == 1 and pw.stop.await_count == 1)

    # 6. lock released on invalid path and on exit
    cls, pw, ctx = _pw([])
    with patch("playwright.async_api.async_playwright", cls), \
         patch.object(sm, "_load_cookies", return_value=[]), \
         patch.object(sm.FacebookSession, "_health_check", AsyncMock(return_value=sm._AuthState.LOGIN_FORM)), \
         patch.object(sm.FacebookSession, "_try_auto_relogin", AsyncMock(return_value=False)), \
         patch.object(sm.FacebookSession, "_notify_owner_reauth_required", AsyncMock()), \
         patch.object(sm, "_PROFILE_DIR", Path(tempfile.mkdtemp())):
        async with sm.FacebookSession() as page:
            invalid_none = page is None
    rec("6a. invalid session yields None and releases the lock", invalid_none and not sm._PROFILE_LOCK.locked())

    order: list[str] = []

    async def _worker(tag: str):
        async with sm.FacebookSession():
            order.append(f"{tag}-in")
            await asyncio.sleep(0.05)
            order.append(f"{tag}-out")

    def _fresh_cls():
        c, _pw_i, _ctx = _pw(["c_user", "xs"])
        return c

    with patch("playwright.async_api.async_playwright", side_effect=lambda: _fresh_cls()()), \
         patch.object(sm, "_load_cookies", return_value=[]), \
         patch.object(sm.FacebookSession, "_health_check", AsyncMock(return_value=sm._AuthState.AUTHENTICATED)), \
         patch.object(sm, "_PROFILE_DIR", Path(tempfile.mkdtemp())):
        await asyncio.wait_for(asyncio.gather(_worker("a"), _worker("b")), 10)
    rec("7. concurrent sessions are serialized (no interleave)",
        order in (["a-in", "a-out", "b-in", "b-out"], ["b-in", "b-out", "a-in", "a-out"]), str(order))
    rec("6b. lock free after normal exit", not sm._PROFILE_LOCK.locked())

    # 8. degraded live cookies must not overwrite last-known-good
    with tempfile.TemporaryDirectory() as td:
        cf = Path(td) / "cookies.json"
        good = [{"name": "c_user", "value": "g"}, {"name": "xs", "value": "g"}]
        cf.write_text(json.dumps(good))
        s = sm.FacebookSession()
        s._valid = True
        s._page = MagicMock(); s._page.is_closed = MagicMock(return_value=False)
        s._context = AsyncMock(); s._context.cookies = AsyncMock(return_value=[{"name": "datr", "value": "d"}])
        s._playwright = AsyncMock()
        with patch.object(sm, "_COOKIES_FILE", cf), patch.object(sm, "_STATE_DIR", Path(td)):
            await s._stop()
        rec("8. degraded cookies never overwrite cookies.json", json.loads(cf.read_text()) == good)

    # 9. live: throwaway copy of the real authenticated profile + stale cookies.json backup
    src = Path("/app/state/fb_native_test/profile")
    if not (src / "Default" / "Cookies").exists():
        print("  ⚠️ 9. live profile test SKIPPED (native profile absent)")
    else:
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "p").mkdir()
            shutil.copytree(src / "Default", tmp / "p" / "Default", ignore=shutil.ignore_patterns("Cache", "Code Cache", "Service Worker"))
            shutil.copy2(src / "Local State", tmp / "p" / "Local State")
            stale = tmp / "cookies.json"
            stale.write_text(json.dumps([{"name": "c_user", "value": "stale", "domain": ".facebook.com", "path": "/"},
                                         {"name": "xs", "value": "stale", "domain": ".facebook.com", "path": "/"}]))
            with patch.object(sm, "_PROFILE_DIR", tmp / "p"), patch.object(sm, "_COOKIES_FILE", stale), \
                 patch.object(sm, "_STATE_DIR", tmp), patch.object(sm, "_SCREENSHOT_DIR", tmp / "shots"):
                async with sm.FacebookSession() as page:
                    ok = page is not None
            rec("9. live copy of real profile authenticates through FacebookSession (stale backup ignored)", ok)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in results if not r[1]]
    print(f"\nResults: {len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


asyncio.run(main())
