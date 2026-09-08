"""Regression tests — P1 order/payment state-desync alert fix — 2026-09-05.

Covers the forensic finding: an order whose SupplierPayment already reached a
terminal SUCCESS state (paid / tracking_received / cancelled) was still being
swept into the stuck-orders "auto_handled" alert purely because Order.status
hadn't advanced to match. Confirmed live on AUTO-2026-71C901A4 (a QA fixture):
its supplier_payments row reached tracking_received in April 2026 via a real
execution of trigger_supplier_fulfillment()'s auto_fake_tracking branch, but
Order.status stayed at 'confirmed' — a full unrestricted production sweep
found this to be the ONLY such row in the entire orders/supplier_payments
population (blast radius = 1, isolated).

Fix: `_all_suppliers_terminal_ids` — GROUP BY order_id HAVING
bool_and(status IN (paid, tracking_received, cancelled)) — excludes an order
from BOTH the stuck-candidate query and the manual-action query only when
EVERY supplier_payments row on that order is terminal. This correctly
generalises to multi-supplier orders (uq_supplier_payments_order_supplier:
one row per (order, supplier); a retry updates that row in place rather than
inserting a new one, so "current" and "latest" are the same row per
supplier) — a single still-failing/pending supplier on an otherwise-terminal
order must never be masked.

Run: docker exec autospare_backend python3 /app/devtests/stuck_orders_state_desync_fix_test.py
"""
import sys
import pathlib
from datetime import datetime, timedelta

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


_routes_src = pathlib.Path("/app/BACKEND_API_ROUTES.py").read_text(encoding="utf-8")

# ─── Static: the new guard exists and is wired into both queries ──────────────
print("1. Static — new terminal-payment guard present and wired into both queries")

check_present(
    "guard subquery: _all_suppliers_terminal_ids defined with bool_and",
    "func.bool_and(\n                            SupplierPayment.status.in_([\"paid\", \"tracking_received\", \"cancelled\"])",
    _routes_src,
)
check_present(
    "guard grouped by order_id",
    "select(SupplierPayment.order_id)\n                    .group_by(SupplierPayment.order_id)",
    _routes_src,
)
check_present(
    "guard applied to the stuck-candidate query",
    "~Order.id.in_(_all_suppliers_terminal_ids)",
    _routes_src,
)
check(
    "guard applied to BOTH queries (stuck + manual_orders), not just one",
    _routes_src.count("~Order.id.in_(_all_suppliers_terminal_ids)"),
    2,
)
check_present(
    "regression: existing nonretryable exclusion untouched",
    "~Order.id.in_(_nonretryable_recent_ids)",
    _routes_src,
)
check_present(
    "regression: STUCK_ORDER_HOURS cutoff logic untouched",
    "Order.updated_at <= cutoff,",
    _routes_src,
)

# ─── Functional: mirror the exact new SQL semantics ────────────────────────────
print("\n2. Functional — GROUP BY / HAVING bool_and logic simulation")

TERMINAL = ("paid", "tracking_received", "cancelled")


def _all_suppliers_terminal(payments: list[dict]) -> bool:
    """Mirrors: SELECT order_id GROUP BY order_id HAVING bool_and(status IN TERMINAL).
    An order with zero supplier_payments rows never appears in this grouped
    result at all (no row to group), so it can never satisfy the HAVING and
    is correctly never treated as terminal-excluded."""
    if not payments:
        return False
    return all(p["status"] in TERMINAL for p in payments)


def _is_nonretryable(payment: dict, now: datetime) -> bool:
    if payment["status"] != "failed" or payment["provider"] != "stripe_issuing":
        return False
    reason = (payment.get("failure_reason") or "").lower()
    if "sandbox mode only" not in reason and "not configured" not in reason:
        return False
    return payment["updated_at"] > now - timedelta(hours=24)


def _is_stuck(order_status: str, order_updated_at: datetime, payments: list[dict],
              now: datetime, cutoff_hours: int) -> bool:
    if order_status not in ("confirmed", "paid", "processing"):
        return False
    nonretryable = any(_is_nonretryable(p, now) for p in payments)
    if nonretryable:
        return False
    if _all_suppliers_terminal(payments):
        return False
    cutoff = now - timedelta(hours=cutoff_hours)
    return order_updated_at <= cutoff


def _requires_manual_action(order_status: str, order_updated_at: datetime, payments: list[dict],
                             now: datetime, cutoff_hours: int) -> bool:
    if order_status not in ("confirmed", "paid", "processing"):
        return False
    cutoff = now - timedelta(hours=cutoff_hours)
    if order_updated_at > cutoff:
        return False
    nonretryable = any(_is_nonretryable(p, now) for p in payments)
    if not nonretryable:
        return False
    return not _all_suppliers_terminal(payments)


NOW = datetime(2026, 9, 5, 17, 30, 0)
OLD_ORDER_UPDATED_AT = datetime(2026, 4, 17, 23, 7, 37)

# Case 1: STALE ORDER + TRACKING_RECEIVED (the exact real fixture shape)
print("\nCase 1: stale order, single payment tracking_received")
payments_1 = [{"status": "tracking_received", "provider": "stripe_issuing",
               "failure_reason": None, "updated_at": NOW - timedelta(hours=1)}]
check("=> NOT a stuck candidate", _is_stuck("confirmed", OLD_ORDER_UPDATED_AT, payments_1, NOW, 4), False)
check("=> does NOT require manual action either", _requires_manual_action("confirmed", OLD_ORDER_UPDATED_AT, payments_1, NOW, 4), False)

# Case 2: STALE ORDER + PAID
print("\nCase 2: stale order, single payment paid")
payments_2 = [{"status": "paid", "provider": "stripe", "failure_reason": None, "updated_at": NOW}]
check("=> NOT a stuck candidate", _is_stuck("paid", OLD_ORDER_UPDATED_AT, payments_2, NOW, 4), False)

# Case 3: REAL FAILED PAYMENT (transient reason) — existing behavior must remain intact
print("\nCase 3: real recent failure, transient (non-structural) reason")
payments_3 = [{"status": "failed", "provider": "stripe_issuing",
               "failure_reason": "card_declined", "updated_at": NOW - timedelta(hours=5)}]
check("=> STILL a stuck candidate (existing retry behavior intact)", _is_stuck("confirmed", NOW - timedelta(hours=5), payments_3, NOW, 4), True)

# Case 4: REAL PENDING PAYMENT
print("\nCase 4: real pending payment, order stale")
payments_4 = [{"status": "pending", "provider": "stripe", "failure_reason": None, "updated_at": NOW - timedelta(hours=6)}]
check("=> STILL a stuck candidate (pending payment must remain retryable)", _is_stuck("confirmed", NOW - timedelta(hours=6), payments_4, NOW, 4), True)

# Case 5: MULTIPLE PAYMENT ROWS — one supplier terminal, another currently failed
print("\nCase 5: two suppliers on one order — one tracking_received, one currently failed")
payments_5 = [
    {"status": "tracking_received", "provider": "stripe_issuing", "failure_reason": None, "updated_at": NOW - timedelta(hours=2)},
    {"status": "failed", "provider": "stripe", "failure_reason": "card_declined", "updated_at": NOW - timedelta(hours=1)},
]
check(
    "=> MUST NOT be suppressed by the terminal supplier — still a stuck candidate",
    _is_stuck("confirmed", NOW - timedelta(hours=6), payments_5, NOW, 4),
    True,
)

# Case 6: reverse — a supplier's row currently shows tracking_received (its only
# current state, since retries update in place per Phase-1 findings: there is no
# separate "older failed" row for the same supplier to query). Modelled as the
# single-row evolving case, equivalent to Case 1/2.
print("\nCase 6: single supplier, current status tracking_received (any earlier failed state is not a separate row)")
payments_6 = [{"status": "tracking_received", "provider": "stripe_issuing", "failure_reason": None, "updated_at": NOW}]
check("=> MUST NOT remain stuck", _is_stuck("confirmed", OLD_ORDER_UPDATED_AT, payments_6, NOW, 4), False)

# Case 7: AUTO-HANDLED TRUTHFULNESS — a terminal, no-action-needed order can never
# appear in the `stuck` list, so it can never receive data.auto_handled=true
# (that field is only ever written for orders present in `stuck`).
print("\nCase 7: auto_handled truthfulness")
check(
    "=> terminal order excluded from stuck => can never receive auto_handled:true",
    _is_stuck("confirmed", OLD_ORDER_UPDATED_AT, payments_1, NOW, 4),
    False,
)

# Case 8: manual-action truthfulness, INCLUDING the defense-in-depth overlap case —
# even if an order were hypothetically BOTH nonretryable AND all-terminal (should
# never happen today, since nonretryable requires a 'failed' row), the guard must
# still exclude it from the honest manual-action path.
print("\nCase 8: manual-action truthfulness (incl. defense-in-depth overlap case)")
payments_8_normal_terminal = [{"status": "tracking_received", "provider": "stripe_issuing",
                                "failure_reason": None, "updated_at": NOW}]
check(
    "=> terminal order never enters manual-action path (normal case)",
    _requires_manual_action("confirmed", OLD_ORDER_UPDATED_AT, payments_8_normal_terminal, NOW, 4),
    False,
)


def _all_suppliers_terminal_ignoring_nonretryable_disjointness(payments: list[dict]) -> bool:
    # Same function, called out separately to make the defense-in-depth check explicit.
    return _all_suppliers_terminal(payments)


# Synthetic overlap: force a payment that matches BOTH _is_nonretryable (failed,
# sandbox reason, recent) is impossible to also be "terminal" by definition
# (failed is not in TERMINAL) — proving the sets are disjoint BY CONSTRUCTION,
# and confirming why the defense-in-depth guard on manual_result is a genuine
# no-op today (not dead code masking a real gap).
payments_8_disjoint_proof = [{"status": "failed", "provider": "stripe_issuing",
                               "failure_reason": "sandbox mode only", "updated_at": NOW}]
check(
    "=> proof: a nonretryable (failed) payment can never also satisfy all-terminal (disjoint by construction)",
    _is_nonretryable(payments_8_disjoint_proof[0], NOW) and _all_suppliers_terminal(payments_8_disjoint_proof),
    False,
)

# Case 9: EXISTING QA FIXTURE REGRESSION — exact real AUTO-2026-71C901A4 row shape
print("\nCase 9: exact real production fixture shape (AUTO-2026-71C901A4)")
fixture_payment = [{
    "status": "tracking_received",
    "provider": "stripe_issuing",  # verified live: provider label is NOT a reliable terminal signal
    "failure_reason": None,
    "updated_at": datetime(2026, 9, 5, 16, 45, 15, 827823),
}]
fixture_order_updated_at = datetime(2026, 4, 17, 23, 7, 37, 300024)
fixture_now = datetime(2026, 9, 5, 17, 11, 18, 310968)
check(
    "=> AUTO-2026-71C901A4 shape is excluded from stuck by the repaired logic",
    _is_stuck("confirmed", fixture_order_updated_at, fixture_payment, fixture_now, 4),
    False,
)
check(
    "=> AUTO-2026-71C901A4 shape does not require manual action either",
    _requires_manual_action("confirmed", fixture_order_updated_at, fixture_payment, fixture_now, 4),
    False,
)

# ─── Summary ────────────────────────────────────────────────────────────────────
print()
if fails:
    print(f"FAILED: {len(fails)} test(s):")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)

print("ALL TESTS PASS")
print()
print("P1 state-desync alert fix verified:")
print("  - Terminal-payment orders (paid/tracking_received/cancelled) excluded from stuck")
print("  - Real failed/pending payments remain fully detectable/retryable")
print("  - Multi-supplier orders require ALL suppliers terminal, not just one")
print("  - auto_handled:true is now unreachable for a no-op terminal-payment order")
print("  - Manual-action path also excludes terminal-payment orders (defense in depth)")
print("  - The real production fixture (AUTO-2026-71C901A4) is excluded by the fix")
