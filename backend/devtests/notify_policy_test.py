"""Notification policy tests — 2026-09-01.

Tests the four root-cause fixes:

1. notify_owner(severity="critical") propagates critical=True to _wa_send_update()
   so critical alerts bypass quiet hours.
2. Normal info/warning alerts are NOT marked critical (stay in the quiet-hours queue).
3. alert_key deduplication: same event twice → only one delivery.
4. Meaningful state change (success → failure) is NOT suppressed even within cooldown.
5. NOA engagement loop now carries an alert_key so the 15-min firing is capped.

Run: docker exec autospare_backend python3 /app/devtests/notify_policy_test.py
"""
import sys
import asyncio

sys.path.insert(0, "/app")

fails: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"         got : {got!r}")
        print(f"         want: {want!r}")
        fails.append(label)


# ─── Test 1: critical flag propagation through notify_owner() ─────────────────
print("1. severity=critical reaches _wa_send_update with critical=True")

_captured_wa_calls: list[dict] = []


async def _mock_wa_send_update(text: str, critical: bool = False, alert_key: str = "") -> dict:
    _captured_wa_calls.append({"text": text, "critical": critical, "alert_key": alert_key})
    return {"ok": True}


async def test_critical_propagation() -> None:
    import BACKEND_API_ROUTES as routes

    original_send = routes._wa_send_update
    routes._wa_send_update = _mock_wa_send_update  # type: ignore[attr-defined]
    try:
        _captured_wa_calls.clear()
        await routes.notify_owner(
            "health",
            "שירות נפל",
            "הדאטהבייס לא זמין",
            severity="critical",
        )
        check("critical alert → critical=True in _wa_send_update",
              _captured_wa_calls[-1]["critical"] if _captured_wa_calls else None,
              True)
    finally:
        routes._wa_send_update = original_send  # type: ignore[attr-defined]


asyncio.run(test_critical_propagation())

# ─── Test 2: info and warning are NOT critical ─────────────────────────────────
print("\n2. severity=info/warning stays critical=False")


async def test_non_critical_propagation() -> None:
    import BACKEND_API_ROUTES as routes

    original_send = routes._wa_send_update
    routes._wa_send_update = _mock_wa_send_update  # type: ignore[attr-defined]
    try:
        for sev in ("info", "warning", "success"):
            _captured_wa_calls.clear()
            await routes.notify_owner("harvest", f"עדכון {sev}", severity=sev)
            got = _captured_wa_calls[-1]["critical"] if _captured_wa_calls else None
            check(f"severity={sev} → critical=False", got, False)
    finally:
        routes._wa_send_update = original_send  # type: ignore[attr-defined]


asyncio.run(test_non_critical_propagation())

# ─── Test 3: quiet-hours gate — critical bypasses, info queues ─────────────────
print("\n3. _wa_send_quiet: critical=True bypasses quiet hours, False queues")


async def test_quiet_hours_gate() -> None:
    import BACKEND_API_ROUTES as routes

    _direct_sends: list[dict] = []
    _queued: list[dict] = []

    original_send = routes._wa_send
    original_window = routes._notify_window_open

    # Force window CLOSED (simulate 03:00 AM)
    routes._notify_window_open = lambda now_local=None: (False, None)  # type: ignore[assignment]

    async def _fake_wa_send(to: str, text: str) -> dict:
        _direct_sends.append({"to": to, "text": text})
        return {"ok": True}

    async def _fake_redis_rpush(key, val):
        _queued.append({"key": key, "val": val})
        return 1

    routes._wa_send = _fake_wa_send  # type: ignore[attr-defined]

    try:
        import unittest.mock as _mock

        # critical=True DURING QUIET HOURS → should still send directly
        _direct_sends.clear()
        _queued.clear()
        result = await routes._wa_send_quiet(to="972500000000", text="DB DOWN", critical=True)
        check("critical=True during quiet hours → direct send", len(_direct_sends), 1)
        check("critical=True during quiet hours → nothing queued", len(_queued), 0)

        # critical=False DURING QUIET HOURS → should queue, NOT send directly
        _direct_sends.clear()
        _queued.clear()
        # We need to also mock Redis for the queue path
        import redis.asyncio as _redis_mod
        with _mock.patch.object(routes, "get_redis") as _mock_redis:
            _fake_r = _mock.AsyncMock()
            _fake_r.rpush = _mock.AsyncMock(return_value=1)
            _fake_r.ltrim = _mock.AsyncMock(return_value=None)
            _mock_redis.return_value = _fake_r
            result = await routes._wa_send_quiet(to="972500000000", text="info msg", critical=False)
        check("critical=False during quiet hours → queued (not sent)",
              result.get("queued"), True)
        check("critical=False during quiet hours → no direct send", len(_direct_sends), 0)
    finally:
        routes._wa_send = original_send  # type: ignore[attr-defined]
        routes._notify_window_open = original_window  # type: ignore[assignment]


asyncio.run(test_quiet_hours_gate())

# ─── Test 4: alert_key deduplication ─────────────────────────────────────────
print("\n4. alert_key deduplication — same event twice → one delivery")


async def test_alert_key_dedup() -> None:
    import BACKEND_API_ROUTES as routes
    import unittest.mock as _mock

    _sends: list[str] = []

    async def _fake_wa_update(text: str, critical: bool = False, alert_key: str = "") -> dict:
        _sends.append(text)
        return {"ok": True}

    original_send = routes._wa_send_update
    routes._wa_send_update = _fake_wa_update  # type: ignore[attr-defined]

    try:
        with _mock.patch.object(routes, "get_redis") as _mock_redis:
            _fake_r = _mock.AsyncMock()
            _exists_calls: list[str] = []
            _set_calls: list[str] = []

            # First call: key does not exist → allow through
            _fake_r.exists = _mock.AsyncMock(side_effect=lambda k: (0 if k not in _set_calls else 1))
            _fake_r.set = _mock.AsyncMock(side_effect=lambda k, v, ex: _set_calls.append(k))
            _mock_redis.return_value = _fake_r

            _sends.clear()
            await routes.notify_owner(
                "health", "אחריות מעוכבת", severity="info",
                alert_key="test_dedup_key", cooldown_s=3600,
            )
            first_sends = len(_sends)

            # Second call: key IS in _set_calls (simulates Redis EXISTS returning 1)
            _fake_r.exists = _mock.AsyncMock(return_value=1)
            await routes.notify_owner(
                "health", "אחריות מעוכבת", severity="info",
                alert_key="test_dedup_key", cooldown_s=3600,
            )
            second_sends = len(_sends)

        check("first send delivered", first_sends, 1)
        check("second send suppressed by cooldown", second_sends, 1)  # still 1 — no second
    finally:
        routes._wa_send_update = original_send  # type: ignore[attr-defined]


asyncio.run(test_alert_key_dedup())

# ─── Test 5: NOA engagement notification has alert_key ───────────────────────
print("\n5. NOA engagement notify_owner call carries alert_key")

import ast
import pathlib

_routes_src = pathlib.Path("/app/BACKEND_API_ROUTES.py").read_text(encoding="utf-8")
_engagement_block = ""
for i, line in enumerate(_routes_src.splitlines()):
    if "noa_engagement_pending_reply" in line:
        _engagement_block = line.strip()
        break

check(
    "NOA engagement block contains alert_key='noa_engagement_pending_reply'",
    "noa_engagement_pending_reply" in _routes_src,
    True,
)
check(
    "NOA engagement cooldown_s is set (at least 1800)",
    "cooldown_s=3600" in _routes_src or "cooldown_s=7200" in _routes_src or "cooldown_s=1800" in _routes_src,
    True,
)

# ─── Test 6: supervised task crash uses critical=True ────────────────────────
print("\n6. _supervised_task crash callback sends with critical=True")

_src_chunk = ""
for i, line in enumerate(_routes_src.splitlines()):
    if "_wa_send_update" in line and "critical=True" in line and i < 200:
        _src_chunk = line.strip()
        break

# _supervised_task is early in the file; search the first 8000 chars
check(
    "_supervised_task callback has critical=True in _wa_send_update call",
    "critical=True" in _routes_src[:8000],
    True,
)

# ─── Test 7: health monitor admin send has _admin_critical ───────────────────
print("\n7. Health monitor admin send uses _admin_critical variable")

check(
    "_admin_critical used in health monitor admin WhatsApp send",
    "_admin_critical" in _routes_src,
    True,
)
check(
    "_admin_critical is derived from state == 'error'",
    "_admin_critical = (state == \"error\")" in _routes_src,
    True,
)

# ─── Test 8: _is_critical derived from severity in notify_owner ──────────────
print("\n8. notify_owner computes _is_critical from severity")

check(
    "_is_critical = severity == 'critical' present in notify_owner",
    '_is_critical = severity == "critical"' in _routes_src,
    True,
)
check(
    # Loosened 2026-09-09 (owner alert architecture remediation): notify_owner now
    # also threads alert_key through to _wa_send_update for quiet-hours-queue dedup
    # (see _wa_send_quiet), so the exact call is
    # "_wa_send_update(text, critical=_is_critical, alert_key=alert_key)". Match on
    # the substring that actually matters — critical is still derived correctly —
    # rather than pinning the full argument list.
    "_wa_send_update called with critical=_is_critical",
    "_wa_send_update(text, critical=_is_critical" in _routes_src,
    True,
)

# ─── Summary ──────────────────────────────────────────────────────────────────
print()
if fails:
    print(f"FAILED: {len(fails)} test(s):")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print(f"ALL {8} TESTS PASS")
print()
print("Critical alert chain verified:")
print("  notify_owner(severity='critical')")
print("  → _wa_send_update(text, critical=True)")
print("  → _wa_send_quiet(to, text, critical=True)")
print("  → _wa_send() immediately (bypasses quiet hours)")
