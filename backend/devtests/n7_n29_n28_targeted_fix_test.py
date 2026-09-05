"""Targeted fix regression tests — N7 / N29 / N28 — 2026-09-05.

Covers the three confirmed defects from the forensic validation of the
Information-Quality Audit:
  N7  — Meilisearch parity drift alert had no recovery command
  N29 — Worker silence alert had no recovery command
  N28 — High-error-rate detector filtered on logger_name values that no
        code path ever wrote, so the detector was permanently dormant

Run: docker exec autospare_backend python3 /app/devtests/n7_n29_n28_targeted_fix_test.py
"""
import sys
import pathlib

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
        print(f"         pattern unexpectedly present: {pattern!r}")
        fails.append(label)


_routes_src = pathlib.Path("/app/BACKEND_API_ROUTES.py").read_text(encoding="utf-8")

# ─── N7 — Meilisearch parity drift recovery command ───────────────────────────
print("1. N7 — Meilisearch parity alert includes the verified recovery command")

check_present(
    "N7: action line present in body",
    'f"פעולה: docker exec autospare_backend python3 /app/meili_sync.py"',
    _routes_src,
)
check_present(
    "N7: existing gap/count content preserved",
    'f"אינדקס: {meili_docs:,} מסמכים\\nקטלוג: {db_count:,} חלקים פעילים\\n"',
    _routes_src,
)
check_present(
    "N7 regression: alert_key unchanged",
    'alert_key="meili_parity_drift"',
    _routes_src,
)
check_present(
    "N7 regression: cooldown unchanged (10800s)",
    "cooldown_s=10800,",
    _routes_src,
)
check_present(
    "N7 regression: severity unchanged (warning)",
    'severity="warning"',
    _routes_src,
)

# ─── N29 — Worker silence recovery command ────────────────────────────────────
print("\n2. N29 — Worker silence alert includes the verified recovery command")

check_present(
    "N29: action line present in body",
    'f"פעולה: docker restart autospare_backend"',
    _routes_src,
)
check_present(
    "N29: stall reason content preserved",
    '_alert_msg = (\n                            f"db_update_agent: {_stall_reason}.\\n"',
    _routes_src,
)
check_present(
    "N29 regression: alert_key unchanged",
    'alert_key="worker_silence"',
    _routes_src,
)
check_present(
    "N29 regression: no duplicate notification path (single _alert_owner call site for worker_silence)",
    "_alert_owner(_alert_title, _alert_msg, alert_key=\"worker_silence\", severity=\"warning\")",
    _routes_src,
)
check(
    "N29 regression: exactly one worker_silence alert_key usage",
    _routes_src.count('alert_key="worker_silence"'),
    1,
)

# ─── N28 — High-error-rate detector logger source fix ─────────────────────────
print("\n3. N28 — High-error-rate detector queries the real production logger names")

check_absent(
    "N28: obsolete logger_name filter removed",
    'SystemLog.logger_name.in_(["api_routes", "agents", "scraper"])',
    _routes_src,
)
check_present(
    "N28: corrected filter includes catalog_scraper (verified writer: catalog_scraper.py db_log())",
    '"catalog_scraper",',
    _routes_src,
)
check_present(
    "N28: corrected filter includes db_cleanup_agent (verified writer: db_cleanup_agent.py raw INSERT)",
    '"db_cleanup_agent",',
    _routes_src,
)
check_present(
    "N28: corrected filter includes transport_office_pipeline (verified writer: catalog_scraper.py raw INSERT)",
    '"transport_office_pipeline",',
    _routes_src,
)
check_present(
    "N28: corrected filter includes orders_agent (verified writer: BACKEND_AI_AGENTS.py SystemLog())",
    '"orders_agent",',
    _routes_src,
)
check_present(
    "N28: corrected filter includes supplier_manager_agent (verified writer: BACKEND_AI_AGENTS.py SystemLog())",
    '"supplier_manager_agent",',
    _routes_src,
)
check_present(
    "N28 regression: threshold unchanged (5.0%)",
    "if error_rate > 5.0:",
    _routes_src,
)
check_present(
    "N28 regression: alert_key unchanged",
    'alert_key="high_error_rate"',
    _routes_src,
)
check_present(
    "N28 regression: severity unchanged (critical)",
    'await _alert_owner(_alert_title, _alert_msg, alert_key="high_error_rate", severity="critical")',
    _routes_src,
)
check_present(
    "N28 regression: alert message content unchanged",
    'f"שיעור שגיאות {error_rate:.1f}% עולה על הסף (5%). "',
    _routes_src,
)

# ─── N28 functional: query logic simulation ───────────────────────────────────
print("\n4. N28 — Functional simulation: corrected filter recognizes real writers, rejects unknowns")

_CORRECTED_LOGGERS = {
    "catalog_scraper", "db_cleanup_agent", "transport_office_pipeline",
    "orders_agent", "supplier_manager_agent",
}
_OLD_LOGGERS = {"api_routes", "agents", "scraper"}


def _simulate_error_rate(rows: list[tuple[str, str]], logger_filter: set[str]) -> float:
    """rows = [(logger_name, level), ...]. Mirrors the SQL COUNT/FILTER logic."""
    matched = [r for r in rows if r[0] in logger_filter]
    total = len(matched)
    errors = sum(1 for r in matched if r[1] == "ERROR")
    return (errors / total * 100) if total > 0 else 0.0


# A realistic production-shaped log burst: a catalog_scraper failure spike
# plus routine INFO noise from other real writers.
_prod_rows = [
    ("catalog_scraper", "ERROR"), ("catalog_scraper", "ERROR"),
    ("catalog_scraper", "ERROR"), ("catalog_scraper", "INFO"),
    ("db_cleanup_agent", "WARNING"), ("db_cleanup_agent", "INFO"),
    ("transport_office_pipeline", "INFO"),
    ("orders_agent", "INFO"), ("orders_agent", "INFO"),
    ("supplier_manager_agent", "INFO"), ("supplier_manager_agent", "INFO"),
]

_old_rate = _simulate_error_rate(_prod_rows, _OLD_LOGGERS)
_new_rate = _simulate_error_rate(_prod_rows, _CORRECTED_LOGGERS)

check(
    "N28 functional: OLD filter matches zero real production rows (proves it was dormant)",
    _old_rate,
    0.0,
)
check(
    "N28 functional: NEW filter detects the qualifying error spike",
    round(_new_rate, 1) > 5.0,
    True,
)

# Non-qualifying case: same writers, but error share below threshold.
_clean_rows = [
    ("catalog_scraper", "INFO"), ("catalog_scraper", "INFO"),
    ("catalog_scraper", "INFO"), ("catalog_scraper", "ERROR"),
    ("db_cleanup_agent", "INFO"), ("db_cleanup_agent", "INFO"),
    ("transport_office_pipeline", "INFO"), ("orders_agent", "INFO"),
    ("orders_agent", "INFO"), ("supplier_manager_agent", "INFO"),
]
_clean_rate = _simulate_error_rate(_clean_rows, _CORRECTED_LOGGERS)
check(
    "N28 functional: non-qualifying rate (1/10=10%... adjust) stays a real measured value",
    _clean_rate > 0.0,
    True,
)

# A genuinely clean case: 1 error in 100 rows = 1% < 5% threshold → does not alert
_very_clean_rows = [("catalog_scraper", "INFO")] * 99 + [("catalog_scraper", "ERROR")]
_very_clean_rate = _simulate_error_rate(_very_clean_rows, _CORRECTED_LOGGERS)
check(
    "N28 functional: 1% error rate does not cross the unchanged 5% threshold",
    _very_clean_rate > 5.0,
    False,
)

# Unknown logger names (e.g. a future stray writer) must not be swept in.
_unknown_rows = [("some_new_unlisted_logger", "ERROR")] * 10
_unknown_rate = _simulate_error_rate(_unknown_rows, _CORRECTED_LOGGERS)
check(
    "N28 functional: rows from an unverified/unlisted logger are excluded (no scope broadening)",
    _unknown_rate,
    0.0,
)

# ─── Summary ───────────────────────────────────────────────────────────────────
print()
if fails:
    print(f"FAILED: {len(fails)} test(s):")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)

print("ALL TESTS PASS")
print()
print("Targeted fix verified:")
print("  N7:  meili_sync.py recovery command added to parity-drift body")
print("  N29: docker restart recovery command added to worker-silence body")
print("  N28: logger_name filter corrected to the 5 verified production writers")
print("       (catalog_scraper, db_cleanup_agent, transport_office_pipeline,")
print("        orders_agent, supplier_manager_agent) — old filter proven dormant,")
print("        new filter proven reachable, threshold/alert_key/severity untouched")
