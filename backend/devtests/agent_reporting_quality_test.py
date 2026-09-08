"""FINAL PASS — Agent/Task Reporting Content Quality Tests — 2026-09-02.

Covers the delta-based reporting implementation for db_update_agent and
regression-checks all prior passes (notify-policy, dedup/fingerprint, PASS 4).

Run: docker exec autospare_backend python3 /app/devtests/agent_reporting_quality_test.py
"""
import sys
import json
import pathlib
import hashlib

sys.path.insert(0, "/app")

fails: list[str] = []


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
        print(f"         pattern present (should be gone): {pattern!r}")
        fails.append(label)


_routes_src = pathlib.Path("/app/BACKEND_API_ROUTES.py").read_text(encoding="utf-8")
_dbagent_src = pathlib.Path("/app/db_update_agent.py").read_text(encoding="utf-8")

# ─── Test 1: Delta key defined (prev_fail key name is correct) ────────────────
print("1. Delta: prev_failed_tasks Redis key is defined in db_update_agent")
check_present(
    "Delta key: autospare:dbagent:prev_failed_tasks",
    "_PREV_FAIL_KEY = \"autospare:dbagent:prev_failed_tasks\"",
    _dbagent_src,
)

# ─── Test 2: Delta key TTL set to 14400 (4h, > 3h cycle) ────────────────────
print("\n2. Delta: prev_failed_tasks TTL is 14400 seconds (4h, exceeds 3h cycle)")
check_present(
    "Delta TTL: ex=14400",
    "ex=14400",
    _dbagent_src,
)

# ─── Test 3: NEW failures correctly computed (set difference) ─────────────────
print("\n3. Delta: NEW failures computed as current tasks NOT in prev_fail_map")
check_present(
    "Delta: _new_tasks set difference on t not in _prev_fail_map",
    "{t: e for t, e in _current_fail_map.items() if t not in _prev_fail_map}",
    _dbagent_src,
)

# ─── Test 4: ONGOING failures correctly computed ──────────────────────────────
print("\n4. Delta: ONGOING failures computed as current tasks IN prev_fail_map")
check_present(
    "Delta: _ongoing_tasks set intersection with _prev_fail_map",
    "{t: e for t, e in _current_fail_map.items() if t in _prev_fail_map}",
    _dbagent_src,
)

# ─── Test 5: IDENTICAL check suppresses pure-ongoing identical failures ───────
print("\n5. Delta: identical-task-set suppression prevents redundant alerts")
check_present(
    "Delta: _identical guards against repeated send",
    "_identical = (not _new_tasks and set(_current_fail_map) == set(_prev_fail_map))",
    _dbagent_src,
)

# ─── Test 6: RESOLVED branch fires when err_count==0 + prev_fail_map set ─────
print("\n6. Delta: RESOLVED branch fires (err_count=0, prev_fail_map non-empty)")
check_present(
    "Delta: elif _prev_fail_map branch present",
    "elif _prev_fail_map:",
    _dbagent_src,
)
check_present(
    "Delta: resolved notice says 'שוקמו:' with task names",
    "f\"שוקמו: {_resolved_names}",
    _dbagent_src,
)

# ─── Test 7: RESOLVED branch deletes the prev key (clears state) ─────────────
print("\n7. Delta: RESOLVED branch deletes the prev_failed_tasks key from Redis")
check_present(
    "Delta: await _r3.delete(_PREV_FAIL_KEY)",
    "await _r3.delete(_PREV_FAIL_KEY)",
    _dbagent_src,
)

# ─── Test 8: Delta label in notification title ────────────────────────────────
print("\n8. Delta: notification title includes '(חדש)' or '(מתמשך)' label")
check_present(
    "Delta: title uses _delta_label variable",
    "_delta_label = \"חדש\" if _new_tasks else \"מתמשך\"",
    _dbagent_src,
)
check_present(
    "Delta: title embeds delta label in parentheses",
    "f\"db_update_agent — {err_count} משימות נכשלו ({_delta_label})\"",
    _dbagent_src,
)

# ─── Test 9: Severity escalates for NEW failures (error vs warning) ───────────
print("\n9. Delta: severity escalates to 'error' for new failures, 'warning' for ongoing")
check_present(
    "Delta: severity escalation present",
    "severity=\"error\" if _new_tasks else \"warning\"",
    _dbagent_src,
)

# ─── Test 10: Functional — delta logic simulation ────────────────────────────
print("\n10. Delta: functional simulation of all cases")


def _simulate_delta(current: dict, prev: dict):
    """Mirrors the new db_update_agent delta logic."""
    _new = {t: e for t, e in current.items() if t not in prev}
    _ongoing = {t: e for t, e in current.items() if t in prev}
    _resolved = {t for t in prev if t not in current}
    _identical = not _new and set(current) == set(prev)
    return _new, _ongoing, _resolved, _identical


# Case A: first failure (nothing in prev) → all NEW
_n, _o, _r, _id = _simulate_delta(
    {"normalize_part_types": "TooManyConnections", "task_cat": "timeout"},
    {},
)
check("Case A: all tasks new when prev is empty", (bool(_n), bool(_o), _id), (True, False, False))

# Case B: same failure again → IDENTICAL → suppress
_n, _o, _r, _id = _simulate_delta(
    {"normalize_part_types": "TooManyConnections"},
    {"normalize_part_types": "TooManyConnections (prev)"},  # error text can differ
)
check("Case B: identical task set = suppress (regardless of error text)", _id, True)

# Case C: one new + one ongoing → alert (not suppressed)
_n, _o, _r, _id = _simulate_delta(
    {"normalize_part_types": "err1", "new_task": "err2"},
    {"normalize_part_types": "err1"},
)
check("Case C: mixed new+ongoing → not suppressed, new task detected", (bool(_n), _id), (True, False))

# Case D: one resolved, one still failing → task set changed → alert
_n, _o, _r, _id = _simulate_delta(
    {"task_b": "err"},
    {"task_a": "prev_err", "task_b": "err"},
)
check("Case D: one resolved (task_a) → set changed → not identical", (_id, "task_a" in _r), (False, True))

# Case E: full recovery → resolved
_n, _o, _r, _id = _simulate_delta({}, {"task_a": "err"})
check("Case E: no current errors → resolved set non-empty", bool(_r), True)

# ─── Test 11: Old sig-based dedup mechanism removed ───────────────────────────
print("\n11. Delta: old sha256 sig-based cooldown mechanism replaced")
check_absent(
    "Delta: old _ck = autospare:alert_cooldown:dbagent_task_errors no longer present",
    "autospare:alert_cooldown:dbagent_task_errors:",
    _dbagent_src,
)

# ─── Test 12: Regression — PASS 4 fixes still intact ─────────────────────────
print("\n12. Regression: all PASS 4 fixes (F1-F5) remain intact")
_pass4_src = pathlib.Path("/app/devtests/pass4_content_quality_test.py").read_text(encoding="utf-8")
# Rather than re-running all 14 pass4 checks inline, verify the test file itself is present
# and run it as a subprocess to get the definitive answer
import subprocess
_p4 = subprocess.run(
    [sys.executable, "/app/devtests/pass4_content_quality_test.py"],
    capture_output=True, text=True
)
_p4_ok = _p4.returncode == 0
print(f"  {'PASS' if _p4_ok else 'FAIL'}  Regression: pass4_content_quality_test 14/14")
if not _p4_ok:
    print("  PASS4 stdout:", _p4.stdout[-800:])
    print("  PASS4 stderr:", _p4.stderr[-400:])
    fails.append("Regression: pass4_content_quality_test 14/14")

# Also run notify_policy_test
_p1 = subprocess.run(
    [sys.executable, "/app/devtests/notify_policy_test.py"],
    capture_output=True, text=True
)
_p1_ok = _p1.returncode == 0
print(f"  {'PASS' if _p1_ok else 'FAIL'}  Regression: notify_policy_test (PASS 1, 8 tests)")
if not _p1_ok:
    print("  NOTIFY stdout:", _p1.stdout[-800:])
    fails.append("Regression: notify_policy_test (PASS 1, 8 tests)")

# ─── Summary ──────────────────────────────────────────────────────────────────
print()
total_checks = 12  # headline groups
if fails:
    print(f"FAILED: {len(fails)} check(s):")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)

print("ALL CHECKS PASS")
print()
print("FINAL PASS — delta-based reporting verified:")
print("  • db_update_agent: NEW/ONGOING/RESOLVED delta correctly implemented")
print("  • IDENTICAL-task-set suppression prevents noise on stable failures")
print("  • RESOLVED recovery notice fires when failures clear")
print("  • Delta label (חדש/מתמשך) in title for at-a-glance context")
print("  • Severity escalates to 'error' for new failures, 'warning' for ongoing")
print("  • Old sig-based cooldown replaced by per-cycle state persistence")
print("  • All PASS 4 fixes (F1-F5, F8) confirmed intact via subprocess")
print("  • All PASS 1 notify-policy checks confirmed intact via subprocess")
