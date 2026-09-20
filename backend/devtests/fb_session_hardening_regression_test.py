"""
Facebook Session Hardening — Regression Tests (Phase 3)
Tests all 10 required regression cases for the three confirmed defects:

  Defect 1: _stop() overwrites cookies.json unconditionally (Cases 1-5)
  Defect 2: groups_scanned = len(approved) reports DB count not scan count (Cases 7-8)
  Defect 3: Auth failure indistinguishable from empty scan (Cases 6, 9)
  Case 10: Existing successful scan behavior unchanged

ALL tests are isolated:
  - No live Facebook browser automation
  - No Production cookies modified
  - No Production DB writes
  - No service restarts
  - Temp files / AsyncMock only
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

def _auth_cookies() -> list[dict]:
    return [{"name": "c_user", "value": "123456"}, {"name": "xs", "value": "AbcXyz"}]


def _bad_cookies() -> list[dict]:
    return [{"name": "consent_cookie", "value": "bad"}]


def _make_session(tmp: Path, *, valid: bool, browser_cookies: list[dict]):
    """Build a FacebookSession in known state without touching Playwright."""
    from social.facebook_browser import session as sess_mod  # type: ignore[import]

    s = sess_mod.FacebookSession()
    s._valid = valid
    fake_page = AsyncMock()
    fake_page.is_closed.return_value = False
    fake_context = AsyncMock()
    fake_context.cookies = AsyncMock(return_value=browser_cookies)
    fake_context.add_cookies = AsyncMock()
    s._page = fake_page
    s._context = fake_context
    s._browser = AsyncMock()
    s._playwright = AsyncMock()
    return s


# ── Case 1: Successful health check → cookies persisted ───────────────────────

print()
print("--- Case 1: Valid auth + successful health check → cookies persisted ---")


async def _case1():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cookies_file = tmp / "cookies.json"
        auth = _auth_cookies()
        cookies_file.write_text(json.dumps(auth))

        import social.facebook_browser.session as sess_mod  # type: ignore[import]

        with patch.multiple(sess_mod, _COOKIES_FILE=cookies_file, _STATE_DIR=tmp):
            s = _make_session(tmp, valid=True, browser_cookies=auth)
            await s._stop()

        saved = json.loads(cookies_file.read_text())
        if saved == auth:
            record("Case 1: valid auth + successful health check → cookies persisted", PASS,
                   "_stop() with _valid=True saves cookies correctly")
        else:
            record("Case 1: valid auth + successful health check → cookies persisted", FAIL,
                   f"expected {auth}, got {saved}")


run(_case1())

# ── Cases 2-5: Failed/ambiguous health check → cookies NOT overwritten ─────────

print()
print("--- Cases 2-5: Cookie overwrite protection on auth failure ---")


async def _case2():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cookies_file = tmp / "cookies.json"
        auth = _auth_cookies()
        cookies_file.write_text(json.dumps(auth))

        import social.facebook_browser.session as sess_mod  # type: ignore[import]

        with patch.multiple(sess_mod, _COOKIES_FILE=cookies_file, _STATE_DIR=tmp):
            s = _make_session(tmp, valid=False, browser_cookies=_bad_cookies())
            await s._stop()

        saved = json.loads(cookies_file.read_text())
        if saved == auth:
            record("Case 2: failed health check → existing auth cookies NOT overwritten", PASS,
                   "_stop() with _valid=False correctly skips cookie save")
        else:
            record("Case 2: failed health check → existing auth cookies NOT overwritten", FAIL,
                   f"BUG: cookies.json overwritten! Got {saved}")


run(_case2())


async def _case3():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cookies_file = tmp / "cookies.json"
        auth = _auth_cookies()
        cookies_file.write_text(json.dumps(auth))

        import social.facebook_browser.session as sess_mod  # type: ignore[import]

        with patch.multiple(sess_mod, _COOKIES_FILE=cookies_file, _STATE_DIR=tmp):
            # Simulate __aenter__ path when health check fails: _stop() called with _valid=False
            s = _make_session(tmp, valid=False, browser_cookies=_bad_cookies())
            await s._stop()  # exactly what __aenter__ does when not self._valid

        saved = json.loads(cookies_file.read_text())
        if saved == auth:
            record("Case 3: _stop() after failed _health_check() → no destructive persistence", PASS,
                   "_stop() with _valid=False leaves cookies.json untouched")
        else:
            record("Case 3: _stop() after failed _health_check() → no destructive persistence", FAIL,
                   f"BUG: _stop() overwrote cookies! Got {saved}")


run(_case3())


async def _case4():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cookies_file = tmp / "cookies.json"
        auth = _auth_cookies()
        cookies_file.write_text(json.dumps(auth))

        import social.facebook_browser.session as sess_mod  # type: ignore[import]

        with patch.multiple(sess_mod, _COOKIES_FILE=cookies_file, _STATE_DIR=tmp):
            s = _make_session(tmp, valid=False, browser_cookies=_bad_cookies())
            await s.__aexit__(None, None, None)  # __aexit__ calls _stop()

        saved = json.loads(cookies_file.read_text())
        if saved == auth:
            record("Case 4: __aexit__() after session failure → no destructive persistence", PASS,
                   "__aexit__ → _stop() with _valid=False leaves cookies.json untouched")
        else:
            record("Case 4: __aexit__() after session failure → no destructive persistence", FAIL,
                   f"BUG: __aexit__ overwrote cookies! Got {saved}")


run(_case4())


async def _case5():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cookies_file = tmp / "cookies.json"
        auth = _auth_cookies()
        cookies_file.write_text(json.dumps(auth))

        import social.facebook_browser.session as sess_mod  # type: ignore[import]

        with patch.multiple(sess_mod, _COOKIES_FILE=cookies_file, _STATE_DIR=tmp):
            s = _make_session(tmp, valid=False, browser_cookies=[])
            # context.cookies() raises to simulate browser crash during cleanup
            s._context.cookies = AsyncMock(side_effect=Exception("browser died"))
            await s._stop()

        saved = json.loads(cookies_file.read_text())
        if saved == auth:
            record("Case 5: browser exception during cleanup → no destructive persistence", PASS,
                   "Exception in _stop() cleanup path leaves cookies.json untouched")
        else:
            record("Case 5: browser exception during cleanup → no destructive persistence", FAIL,
                   f"BUG: cookies.json changed despite exception! Got {saved}")


run(_case5())

# ── Case 6: Consent/checkpoint page classified as auth failure ─────────────────

print()
print("--- Case 6: Consent/checkpoint classified as auth failure ---")


async def _case6():
    import social.facebook_browser.session as sess_mod  # type: ignore[import]

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        with patch.multiple(sess_mod, _COOKIES_FILE=tmp / "cookies.json", _STATE_DIR=tmp):
            s = sess_mod.FacebookSession()
            fake_page = AsyncMock()
            fake_page.goto = AsyncMock()
            # Consent page: no login form ('id="email"' absent) AND no profile link
            fake_page.content = AsyncMock(return_value=(
                "<html><body>"
                "<div>Cookies policy. Do you accept?</div>"
                "<button>Accept</button>"
                "</body></html>"
            ))
            fake_page.evaluate = AsyncMock(return_value=False)  # no profile link
            fake_page.screenshot = AsyncMock()
            s._page = fake_page

            result = await s._health_check()

    # Phase 5J: _health_check() now returns _AuthState enum, not bool.
    # Consent page → no login form, no profile, no picker signals → AMBIGUOUS.
    # AMBIGUOUS means auth failure (not AUTHENTICATED) — intent unchanged.
    from social.facebook_browser.session import _AuthState  # type: ignore[import]
    is_auth_failure = (result is False) or (result != _AuthState.AUTHENTICATED)
    if is_auth_failure:
        record("Case 6: consent/checkpoint page → _health_check() returns False (auth failure)", PASS,
               f"Ambiguous page correctly classified as auth failure ({result!r})")
    else:
        record("Case 6: consent/checkpoint page → _health_check() returns False (auth failure)", FAIL,
               f"BUG: consent page returned AUTHENTICATED instead of a failure state")


run(_case6())

# ── Case 7: Auth failure → zero groups attempted/fetched ──────────────────────

print()
print("--- Case 7: Auth failure → groups_attempted = 0 ---")


async def _case7():
    from social.facebook_browser.group_agent import GroupAgent  # type: ignore[import]

    agent = GroupAgent()
    approved = [
        {"id": "1", "group_url": "https://facebook.com/groups/a/", "group_name": "A"},
        {"id": "2", "group_url": "https://facebook.com/groups/b/", "group_name": "B"},
    ]

    with patch("social.facebook_browser.group_agent.FacebookSession") as MockSess:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=None)  # returns None = auth failed
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        MockSess.return_value = mock_ctx

        result = await agent.scan_groups(approved)

    if (result["session_failed"] is True and
            result["groups_attempted"] == 0 and
            result["groups_fetched"] == 0 and
            result["discoveries"] == [] and
            result["groups_selected"] == 2):
        record("Case 7: auth failure → groups_attempted=0, not DB count", PASS,
               f"session_failed=True, groups_selected={result['groups_selected']}, "
               f"groups_attempted={result['groups_attempted']}")
    else:
        record("Case 7: auth failure → groups_attempted=0, not DB count", FAIL,
               f"Got: {result}")


run(_case7())

# ── Case 8: groups_scanned reflects actual scanning, not DB count ──────────────

print()
print("--- Case 8: groups_scanned reflects actual scan, not DB selection ---")


async def _case8():
    from social.facebook_browser.group_agent import GroupAgent  # type: ignore[import]

    agent = GroupAgent()
    # 31 groups selected from DB (mimicking production scenario)
    approved = [
        {"id": str(i), "group_url": f"https://facebook.com/groups/{i}/", "group_name": f"G{i}"}
        for i in range(31)
    ]

    with patch("social.facebook_browser.group_agent.FacebookSession") as MockSess:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=None)  # auth failed
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        MockSess.return_value = mock_ctx

        result = await agent.scan_groups(approved)

    # On auth failure: groups_attempted must be 0, NOT 31
    if result["groups_attempted"] == 0 and result["groups_selected"] == 31:
        record("Case 8: groups_scanned = actual count, NOT DB selection count", PASS,
               "groups_selected=31 (DB count), groups_attempted=0 (auth failed before scanning)")
    else:
        record("Case 8: groups_scanned = actual count, NOT DB selection count", FAIL,
               f"BUG: groups_attempted={result['groups_attempted']} (expected 0), "
               f"groups_selected={result['groups_selected']}")


run(_case8())

# ── Case 9: Auth failure distinguishable from empty scan ──────────────────────

print()
print("--- Case 9: Auth failure distinguishable from empty successful scan ---")


async def _case9():
    from social import tools as t  # type: ignore[import]

    # Mock DB returning one approved group
    mock_row = MagicMock()
    mock_row.id = "uuid-test-1"
    mock_row.group_url = "https://facebook.com/groups/test/"
    mock_row.group_name = "Test Group"
    mock_row.status = "approved"
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [mock_row]
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    # GroupAgent is imported inside facebook_group_scan() from social.facebook_browser,
    # so we patch it at the source module, not as an attribute of social.tools.
    # ── Auth failure ───────────────────────────────────────────────────────────
    with patch("social.facebook_browser.GroupAgent") as MockAgent:
        mock_agent_inst = AsyncMock()
        mock_agent_inst.scan_groups = AsyncMock(return_value={
            "discoveries": [],
            "groups_selected": 1,
            "groups_attempted": 0,
            "groups_fetched": 0,
            "session_failed": True,
        })
        MockAgent.return_value = mock_agent_inst
        auth_fail_result = await t.facebook_group_scan(db=mock_db)

    # ── Empty successful scan ──────────────────────────────────────────────────
    with patch("social.facebook_browser.GroupAgent") as MockAgent2:
        mock_agent_inst2 = AsyncMock()
        mock_agent_inst2.scan_groups = AsyncMock(return_value={
            "discoveries": [],
            "groups_selected": 1,
            "groups_attempted": 1,
            "groups_fetched": 1,
            "session_failed": False,
        })
        MockAgent2.return_value = mock_agent_inst2
        empty_scan_result = await t.facebook_group_scan(db=mock_db)

    ok = (
        auth_fail_result.status == "error" and
        empty_scan_result.status == "success" and
        auth_fail_result.data.get("session_failed") is True and
        not empty_scan_result.data.get("session_failed")
    )

    if ok:
        record("Case 9: auth failure and empty scan DISTINGUISHABLE in ToolResult", PASS,
               f"auth_fail.status={auth_fail_result.status!r}, "
               f"empty_scan.status={empty_scan_result.status!r}")
    else:
        record("Case 9: auth failure and empty scan DISTINGUISHABLE in ToolResult", FAIL,
               f"auth_fail.status={auth_fail_result.status!r}, "
               f"empty_scan.status={empty_scan_result.status!r}")


run(_case9())

# ── Case 10: Existing successful scan behavior unchanged ──────────────────────

print()
print("--- Case 10: Existing successful scan behavior unchanged ---")


async def _case10():
    from social.facebook_browser.group_agent import GroupAgent  # type: ignore[import]

    agent = GroupAgent()
    approved = [
        {"id": "1", "group_url": "https://facebook.com/groups/cars/", "group_name": "Cars IL"},
    ]
    expected_disc = {
        "group_id": "1",
        "group_url": "https://facebook.com/groups/cars/",
        "group_name": "Cars IL",
        "post_url": "https://facebook.com/groups/cars/post/1",
        "post_text": "need oil filter toyota corolla",
        "author": "",
        "relevance_score": 0.5,
        "suggested_action": "comment",
    }

    with patch("social.facebook_browser.group_agent.FacebookSession") as MockSess:
        mock_page = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_page)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        MockSess.return_value = mock_ctx

        with patch.object(agent, "_scan_one_group", AsyncMock(return_value={
            "discoveries": [expected_disc],
            "telemetry": {"dom_candidates": 1, "text_valid": 1, "scored": 1,
                          "discoveries": 1, "near_misses": 0, "zero_score": 0},
        })):
            with patch("asyncio.sleep", AsyncMock()):
                result = await agent.scan_groups(approved)

    ok = (
        result["session_failed"] is False and
        result["groups_selected"] == 1 and
        result["groups_attempted"] == 1 and
        result["groups_fetched"] == 1 and
        len(result["discoveries"]) == 1 and
        result["discoveries"][0] == expected_disc
    )

    if ok:
        record("Case 10: existing successful scan behavior unchanged", PASS,
               f"session_failed=False, groups_fetched=1, discoveries={len(result['discoveries'])}")
    else:
        record("Case 10: existing successful scan behavior unchanged", FAIL,
               f"Got: {result}")


run(_case10())

# ── Additional: Phase 2 exact failure sequence verification ───────────────────

print()
print("--- Additional: Phase 2 failure sequence — auth cookies survive full sequence ---")


async def _phase2_sequence():
    """Verify the exact Phase 2 failure sequence cannot destroy last-known-good cookies."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cookies_file = tmp / "cookies.json"
        auth = _auth_cookies()
        cookies_file.write_text(json.dumps(auth))

        import social.facebook_browser.session as sess_mod  # type: ignore[import]

        with patch.multiple(sess_mod, _COOKIES_FILE=cookies_file, _STATE_DIR=tmp):
            # Step 1: Load cookies (simulated by read at start of _start)
            # Step 2: Health check becomes ambiguous → returns False → _valid = False
            # Step 3: __aenter__ calls _stop() with _valid=False
            s = _make_session(tmp, valid=False, browser_cookies=_bad_cookies())
            await s._stop()   # from __aenter__ when not self._valid

            # Step 4: __aexit__ also calls _stop()
            await s.__aexit__(None, None, None)

        saved = json.loads(cookies_file.read_text())
        if saved == auth:
            record("Phase 2 sequence: auth cookies survive full failure sequence", PASS,
                   "cookies.json unchanged after __aenter__→_stop + __aexit__→_stop with _valid=False")
        else:
            record("Phase 2 sequence: auth cookies survive full failure sequence", FAIL,
                   f"BUG: cookies.json was modified! Got {saved}")


run(_phase2_sequence())


async def _phase2_group_scan_cannot_produce_31_scanned():
    """Auth failure → facebook_group_scan cannot produce 'N groups scanned / no relevant posts'."""
    from social import tools as t  # type: ignore[import]

    # 31 groups in DB (production scenario)
    mock_rows = []
    for i in range(31):
        r = MagicMock()
        r.id = f"uuid-{i}"
        r.group_url = f"https://facebook.com/groups/{i}/"
        r.group_name = f"Group {i}"
        r.status = "approved"
        mock_rows.append(r)

    mock_result = MagicMock()
    mock_result.fetchall.return_value = mock_rows
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("social.facebook_browser.GroupAgent") as MockAgent:
        mock_agent_inst = AsyncMock()
        mock_agent_inst.scan_groups = AsyncMock(return_value={
            "discoveries": [],
            "groups_selected": 31,
            "groups_attempted": 0,
            "groups_fetched": 0,
            "session_failed": True,
        })
        MockAgent.return_value = mock_agent_inst
        result = await t.facebook_group_scan(db=mock_db)

    # Must NOT be "success" with groups_scanned=31 and empty discoveries
    is_failure = result.status == "error"
    no_false_positive = result.data.get("session_failed") is True

    if is_failure and no_false_positive:
        record("Phase 2 group scan: auth failure cannot produce '31 groups scanned / no relevant posts'",
               PASS,
               f"status='error', session_failed=True — callers can distinguish auth failure")
    else:
        record("Phase 2 group scan: auth failure cannot produce '31 groups scanned / no relevant posts'",
               FAIL,
               f"status={result.status!r}, data={result.data}")


run(_phase2_group_scan_cannot_produce_31_scanned())

# ── Summary ────────────────────────────────────────────────────────────────────

print()
print("=" * 60)
print("REGRESSION CASE SUMMARY")
print("=" * 60)
passed = sum(1 for r in results if r["status"] == PASS)
failed = sum(1 for r in results if r["status"] == FAIL)
skipped = sum(1 for r in results if r["status"] == SKIP)
print(f"Total: {len(results)}  |  PASS: {passed}  |  FAIL: {failed}  |  SKIP: {skipped}")
if failed:
    print()
    print("FAILURES:")
    for r in results:
        if r["status"] == FAIL:
            print(f"  [{r['name']}]")
            if r["detail"]:
                print(f"     {r['detail'][:400]}")

sys.exit(1 if failed else 0)
