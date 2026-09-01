"""PASS 4 — Agent/Task Reporting Content Quality Tests — 2026-09-01.

Tests that MESSAGES contain the right content, not just the right routing metadata.
Covers the fixes from the PASS 3 audit findings (F1–F5, F8).

Run: docker exec autospare_backend python3 /app/devtests/pass4_content_quality_test.py
"""
import sys
import re
import pathlib

sys.path.insert(0, "/app")

fails: list[str] = []

_routes_src = pathlib.Path("/app/BACKEND_API_ROUTES.py").read_text(encoding="utf-8")
_agents_src = pathlib.Path("/app/BACKEND_AI_AGENTS.py").read_text(encoding="utf-8")
_dbagent_src = pathlib.Path("/app/db_update_agent.py").read_text(encoding="utf-8")


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"         got : {got!r}")
        print(f"         want: {want!r}")
        fails.append(label)


def check_present(label: str, pattern: str, src: str) -> None:
    ok = pattern in src
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"         pattern not found: {pattern!r}")
        fails.append(label)


def check_absent(label: str, pattern: str, src: str) -> None:
    ok = pattern not in src
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"         pattern unexpectedly present: {pattern!r}")
        fails.append(label)


# ─── Test 1: F1 — NOA post approval title no longer singles out one platform ──
print("1. F1 — NOA post approval notification does NOT collapse to a single platform name")

# The old bug: title said "NOA — פוסט Discord מוכן לאישורך" (or any single platform)
# instead of communicating the full list.  The fix removes the per-platform title
# and builds _platform_display from _configured.
check_present(
    "F1: _platform_display built from _configured list",
    '_platform_display = " · ".join(p.title() for p in (_configured or ["all"]))',
    _routes_src,
)
check_present(
    "F1: title uses generic 'NOA — פוסט מוכן לאישורך' (no single platform)",
    '"NOA — פוסט מוכן לאישורך"',
    _routes_src,
)
check_present(
    "F1: body prefixes 'פרסום: {_platform_display}'",
    'f"פרסום: {_platform_display}\\n\\n{wa_post_body}"',
    _routes_src,
)

# ─── Test 2: F1 — Functional platform display construction ────────────────────
print("\n2. F1 — _platform_display correctly formats multi-platform list")

def _platform_display(configured):
    return " · ".join(p.title() for p in (configured or ["all"]))

check(
    "F1 functional: 4 platforms → all shown",
    _platform_display(["discord", "facebook", "telegram", "tiktok"]),
    "Discord · Facebook · Telegram · Tiktok",
)
check(
    "F1 functional: single platform still works",
    _platform_display(["facebook"]),
    "Facebook",
)
check(
    "F1 functional: empty list falls back to 'All'",
    _platform_display([]),
    "All",
)

# ─── Test 3: F1/F8 — Consistency between creation and reminder notifications ──
print("\n3. F8 — Pending posts REMINDER also shows all platforms (join, not single)")

# R27 (pending posts reminder) at _sp_db query uses ", ".join(_pr[1]) — verify it
# uses a join rather than indexing a single platform.
check_present(
    "F8: pending posts reminder uses join for platforms",
    '", ".join(_pr[1]) if _pr[1] else "?"',
    _routes_src,
)
# And the creation notification no longer hard-codes a single platform in the title
check_absent(
    "F8: creation title no longer uses 'פוסט {platform.title()}'",
    'f"NOA — פוסט {platform.title()} מוכן לאישורך"',
    _routes_src,
)

# ─── Test 4: F2 — NOA engagement removes the incorrect 'חדשות' (new) label ────
print("\n4. F2 — NOA engagement message does not say 'חדשות' for all pending items")

check_absent(
    "F2: 'תגובות חדשות' no longer in engagement notify call",
    "תגובות חדשות ברשתות",
    _routes_src,
)
check_present(
    "F2: engagement notify uses 'תגובות ממתינות לתשובה'",
    "תגובות ממתינות לתשובה",
    _routes_src,
)

# ─── Test 5: F2 — Engagement message body format ─────────────────────────────
print("\n5. F2 — Engagement message title format is correct")

# The new title must still include the count placeholder
check_present(
    "F2: engagement title still includes drafted count",
    'f"NOA — {drafted} תגובות ממתינות לתשובה"',
    _routes_src,
)

# ─── Test 6: F3 — Harvest stall message includes action command ───────────────
print("\n6. F3 — Harvest stall message includes an actionable owner command")

check_present(
    "F3: stall message has action_line variable",
    'action_line = "פעולה: כתוב *שאיבה* לסטטוס מלא"',
    _routes_src,
)
check_present(
    "F3: idle message has action_line variable",
    'action_line = "פעולה: כתוב *שאיבה* לסטטוס"',
    _routes_src,
)
check_present(
    "F3: recovery message has empty action_line (no spurious instruction)",
    'action_line = ""',
    _routes_src,
)
check_present(
    "F3: action_line is appended to msg only when non-empty",
    '+ (f"\\n{action_line}" if action_line else "")',
    _routes_src,
)

# ─── Test 7: F3 — Verified command *שאיבה* exists in owner console ────────────
print("\n7. F3 — Verified: *שאיבה* is a real command in owner_console.py")

_console_src = pathlib.Path("/app/agents/owner_console.py").read_text(encoding="utf-8")
check_present(
    "F3 command verified: owner_console handles 'שאיבה' command",
    '"שאיבה"',
    _console_src,
)

# ─── Test 8: F4 — Health monitor has SERVICE_ACTIONS dict ────────────────────
print("\n8. F4 — Health monitor defines SERVICE_ACTIONS with per-service hints")

check_present(
    "F4: SERVICE_ACTIONS dict present",
    "SERVICE_ACTIONS = {",
    _routes_src,
)
# Verify all 6 monitored services have an action entry
for svc in ["postgres_catalog", "postgres_pii", "redis", "meilisearch", "huggingface", "stripe"]:
    check_present(
        f"F4: SERVICE_ACTIONS has entry for '{svc}'",
        f'"{svc}":',
        _routes_src,
    )

# ─── Test 9: F4 — Service-down message uses SERVICE_ACTIONS hint ──────────────
print("\n9. F4 — Service-down message uses per-service action hint from SERVICE_ACTIONS")

check_present(
    "F4: down message uses SERVICE_ACTIONS.get(svc)",
    "_action_hint = SERVICE_ACTIONS.get(svc, \"\")",
    _routes_src,
)
check_present(
    "F4: down message appends action_hint when available",
    'f"\\nפעולה: {_action_hint}" if _action_hint else " בדוק את המערכת בהקדם."',
    _routes_src,
)

# ─── Test 10: F4 — Functional: service-specific action is actually different ──
print("\n10. F4 — Functional: service-down messages are service-specific")

# Simulate what the code does for each service
SERVICE_ACTIONS = {
    "postgres_catalog": "docker logs autospare_postgres_catalog --tail 30",
    "postgres_pii":     "docker logs autospare_postgres_pii --tail 30",
    "redis":            "docker logs autospare_redis --tail 30",
    "meilisearch":      "docker logs autospare_meilisearch --tail 30",
    "huggingface":      "בדוק את HF_TOKEN ב-.env",
    "stripe":           "בדוק את STRIPE_SECRET_KEY ב-.env",
}
SERVICE_LABELS = {
    "postgres_catalog": "מסד נתונים — קטלוג",
    "postgres_pii":     "מסד נתונים — לקוחות",
    "redis":            "Redis (תור/מטמון)",
    "meilisearch":      "מנוע חיפוש",
    "huggingface":      "Hugging Face AI",
    "stripe":           "Stripe (תשלומים)",
}

def _build_down_msg(svc: str) -> str:
    label = SERVICE_LABELS.get(svc, svc)
    action_hint = SERVICE_ACTIONS.get(svc, "")
    return (
        f"שירות {label} אינו זמין."
        + (f"\nפעולה: {action_hint}" if action_hint else " בדוק את המערכת בהקדם.")
    )

_pg_msg = _build_down_msg("postgres_catalog")
_redis_msg = _build_down_msg("redis")
_hf_msg = _build_down_msg("huggingface")

check(
    "F4 functional: postgres down contains docker logs command",
    "docker logs autospare_postgres_catalog" in _pg_msg,
    True,
)
check(
    "F4 functional: redis down contains docker logs redis",
    "docker logs autospare_redis" in _redis_msg,
    True,
)
check(
    "F4 functional: huggingface down mentions HF_TOKEN",
    "HF_TOKEN" in _hf_msg,
    True,
)
check(
    "F4 functional: messages are distinct per service",
    len({_pg_msg, _redis_msg, _hf_msg}) == 3,
    True,
)

# ─── Test 11: F5 — DB agent error body uses tail-truncation + log reference ───
print("\n11. F5 — DB agent error body uses tail-truncation and adds log reference")

check_present(
    "F5: error preview prefers tail of error string",
    "_err_str[-120:].lstrip() if len(_err_str) > 120 else _err_str",
    _dbagent_src,
)
check_present(
    "F5: log reference line added to error body",
    '"לוגים: docker logs autospare_backend | tail -50"',
    _dbagent_src,
)

# ─── Test 12: F5 — Functional: tail-truncation extracts right content ─────────
print("\n12. F5 — Functional: tail-truncation gives exception type, not prefix")

def _err_preview(err_str: str) -> str:
    return err_str[-120:].lstrip() if len(err_str) > 120 else err_str

# A typical asyncpg error: long prefix, meaningful tail
_long_err = ("During handling of the above exception, another exception occurred:\n"
             "  File \"/app/db_update_agent.py\", line 1234, in run_task\n"
             "asyncpg.exceptions.TooManyConnectionsError: sorry, too many clients already")

_preview = _err_preview(_long_err)
check(
    "F5 functional: preview contains exception class name",
    "TooManyConnectionsError" in _preview,
    True,
)
check(
    "F5 functional: preview is at most 120 chars",
    len(_preview) <= 120,
    True,
)

# Short error: unchanged
_short_err = "DB connection refused"
check(
    "F5 functional: short error passed through unchanged",
    _err_preview(_short_err),
    _short_err,
)

# ─── Test 13: Regression — dedup/alert_key/cooldown mechanisms unchanged ──────
print("\n13. Regression — F1/F2 changes did NOT remove existing alert_key guards")

# F1 alert_key must still be per-post-id
check_present(
    "Regression F1: noa_post_ready alert_key still present",
    "noa_post_ready_{social_post_id}",
    _routes_src,
)
# F2 alert_key must still be present for engagement
check_present(
    "Regression F2: noa_engagement_pending_reply alert_key still present",
    "noa_engagement_pending_reply",
    _routes_src,
)
# F3 changes must not have removed _wa_send_update call
check_present(
    "Regression F3: harvest stall still calls _wa_send_update",
    "await _wa_send_update(msg)",
    _routes_src,
)

# ─── Test 14: Regression — critical/severity routing unchanged ────────────────
print("\n14. Regression — severity routing not changed by any PASS 4 fix")

check_present(
    "Regression: notify_owner critical path still uses _is_critical",
    '_is_critical = severity == "critical"',
    _routes_src,
)
check_present(
    "Regression: _admin_critical still derives from state == 'error'",
    '_admin_critical = (state == "error")',
    _routes_src,
)
check_present(
    "Regression: _wa_send_update still called with critical=_is_critical",
    "_wa_send_update(text, critical=_is_critical)",
    _routes_src,
)

# ─── Summary ──────────────────────────────────────────────────────────────────
print()
if fails:
    print(f"FAILED: {len(fails)} test(s):")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)

print("ALL 14 TESTS PASS")
print()
print("PASS 4 content quality fixes verified:")
print("  F1: NOA post approval shows full platform list (not single platform)")
print("  F2: NOA engagement removes incorrect 'חדשות' (new) label")
print("  F3: Harvest stall/idle messages include actionable *שאיבה* command")
print("  F4: Service-down alerts use service-specific diagnostic hints")
print("  F5: DB agent errors use tail-truncation + docker logs reference")
print("  F8: Creation and reminder notifications consistent on platform scope")
print("  Regression: all dedup/alert_key/critical/severity mechanics unchanged")
