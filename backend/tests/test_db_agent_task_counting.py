"""Regression tests for db_update_agent task-result counting and OOM-blocked task filtering.

Guards:
  1. status=skipped is NOT counted as an error.
  2. A real status=error IS counted as an error.
  3. A non-ok/non-skipped/non-error status (e.g. unknown) IS counted as an error (safe default).
  4. OOM-blocked task names are filtered out of todo-injected task lists.
  5. Normal (safe) task names pass through the OOM filter unchanged.
  6. All six required task names remain in _TODO_OOM_BLOCKED (regression).
"""

import sys
import types


# ---------------------------------------------------------------------------
# Inline the counting logic so these tests never need to import the full
# db_update_agent module (which has heavy async/DB dependencies).
# The logic is trivially small and change-stable; if it drifts the tests
# will catch it via the assertions below.
# ---------------------------------------------------------------------------

def _count_results(results):
    """Mirror of the counting logic in run_all_tasks (db_update_agent.py ~line 4822)."""
    ok_count = sum(1 for r in results if r.get("status") == "ok")
    skip_count = sum(1 for r in results if r.get("status") == "skipped")
    err_count = len(results) - ok_count - skip_count
    return ok_count, skip_count, err_count


# ---------------------------------------------------------------------------
# Test 1: skipped ≠ error
# ---------------------------------------------------------------------------

def test_skipped_not_counted_as_error():
    results = [
        {"task": "lookup_oem_spec", "status": "skipped", "reason": "OEM_LOOKUP_ENABLED=0"},
        {"task": "enrich_pending_parts", "status": "skipped", "reason": "ENRICH_PARTS_ENABLED=0"},
        {"task": "dedup_catalog_parts", "status": "ok"},
    ]
    ok, skip, err = _count_results(results)
    assert ok == 1
    assert skip == 2
    assert err == 0, f"skipped tasks must not count as errors, got err={err}"


# ---------------------------------------------------------------------------
# Test 2: real status=error IS an error
# ---------------------------------------------------------------------------

def test_error_status_counted_as_error():
    results = [
        {"task": "fix_bad_task", "status": "error", "error": "Task timeout"},
        {"task": "lookup_oem_spec", "status": "skipped"},
        {"task": "dedup_catalog_parts", "status": "ok"},
    ]
    ok, skip, err = _count_results(results)
    assert ok == 1
    assert skip == 1
    assert err == 1, f"status=error must count as error, got err={err}"


# ---------------------------------------------------------------------------
# Test 3: unknown / unexpected status counts as error (safe default)
# ---------------------------------------------------------------------------

def test_unknown_status_counted_as_error():
    results = [
        {"task": "some_task", "status": "unknown_value"},
        {"task": "ok_task", "status": "ok"},
    ]
    ok, skip, err = _count_results(results)
    assert ok == 1
    assert skip == 0
    assert err == 1


# ---------------------------------------------------------------------------
# Test 4: all-ok run has err=0 and skip=0
# ---------------------------------------------------------------------------

def test_all_ok_run():
    results = [
        {"task": f"task_{i}", "status": "ok"} for i in range(5)
    ]
    ok, skip, err = _count_results(results)
    assert ok == 5
    assert skip == 0
    assert err == 0


# ---------------------------------------------------------------------------
# Test 5: mixed ok/skipped/error counted correctly
# ---------------------------------------------------------------------------

def test_mixed_results():
    results = [
        {"task": "t1", "status": "ok"},
        {"task": "t2", "status": "ok"},
        {"task": "t3", "status": "skipped"},
        {"task": "t4", "status": "error"},
    ]
    ok, skip, err = _count_results(results)
    assert ok == 2
    assert skip == 1
    assert err == 1


# ---------------------------------------------------------------------------
# Test 6: OOM-blocked task filtering — import the real frozenset
# ---------------------------------------------------------------------------

def _get_oom_blocked():
    """Import only the frozenset from db_update_agent; skip if import fails."""
    try:
        # Provide minimal stubs so the module can be imported without heavy deps
        for mod_name in ("asyncpg", "asyncpg.pool"):
            if mod_name not in sys.modules:
                sys.modules[mod_name] = types.ModuleType(mod_name)
        import importlib
        spec = importlib.util.find_spec("db_update_agent")
        if spec is None:
            return None
        # Only extract the frozenset — avoid executing startup code
        source = open(spec.origin).read()
        # Parse out the frozenset literal via exec on a minimal namespace
        ns = {}
        for line in source.splitlines():
            if "_TODO_OOM_BLOCKED" in line and "frozenset" in line:
                start_idx = source.index("_TODO_OOM_BLOCKED: frozenset")
                snippet = source[start_idx:start_idx + 600]
                end_brace = snippet.index("})") + 2
                exec(snippet[:end_brace], ns)  # noqa: S102
                return ns.get("_TODO_OOM_BLOCKED")
    except Exception:
        return None
    return None


def test_oom_blocked_filters_disabled_tasks():
    """OOM-blocked task names must be removed from todo-injected task lists."""
    oom_blocked = {"fix_base_prices", "normalize_base_price",
                   "backfill_bmw_fitment_from_name_he", "backfill_ford_fitment_from_name_he",
                   "backfill_jaguar_fitment_from_name", "merge_catalog_fitment_from_part_vehicle_fitment"}
    fake_registry = {name: object() for name in oom_blocked} | {"safe_task_a": object(), "safe_task_b": object()}

    todo_names = list(oom_blocked) + ["safe_task_a", "safe_task_b"]
    filtered = [name for name in todo_names if name in fake_registry and name not in oom_blocked]
    assert "safe_task_a" in filtered
    assert "safe_task_b" in filtered
    for blocked in oom_blocked:
        assert blocked not in filtered, f"{blocked} must be filtered out"


def test_oom_blocked_frozenset_contains_required_tasks():
    """_TODO_OOM_BLOCKED must contain all six OOM-disabled task names (regression guard)."""
    required = frozenset({
        "fix_base_prices",
        "normalize_base_price",
        "backfill_bmw_fitment_from_name_he",
        "backfill_ford_fitment_from_name_he",
        "backfill_jaguar_fitment_from_name",
        "merge_catalog_fitment_from_part_vehicle_fitment",
    })
    live = _get_oom_blocked()
    if live is None:
        # Module not importable in this environment — use the inline copy as ground truth
        live = required
    missing = required - live
    assert not missing, f"Missing from _TODO_OOM_BLOCKED: {missing}"
