"""One logical order + one Stripe session per chat request — the REAL routes.payments.create_whatsapp_checkout
against the sandbox catalog + PII DBs, Stripe replaced by tests/checkout_fixture.FakeStripe (FIXES_TRACKER #77).

Each case checks the PERSISTED state (orders / order_items / payments rows) and the Stripe sessions created,
not return values alone. Crash boundaries are produced the way they happen in production: the awaiting task is
cancelled (process death) while Stripe's call is running in its executor thread, which then completes anyway.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import isolated_db_guard  # noqa: E402
from checkout_fixture import FakeStripe, cleanup, create_fixture, order_state, patch_stripe  # noqa: E402

pytestmark = pytest.mark.skipif(isolated_db_guard.static_refusal() is not None,
                                reason=f"isolated DB only: {isolated_db_guard.static_refusal()}")
ANON = "00000000-0000-0000-0000-000000000001"


def _run(scenario):
    """scenario(call, fake, part) → coroutine. Returns (scenario result, persisted orders, fake)."""
    out = {}

    async def main():
        import routes.payments as pay
        from BACKEND_DATABASE_MODELS import pii_engine
        assert pii_engine.url.host == "sandbox_postgres", f"REFUSED: PII host {pii_engine.url.host!r}"
        mp = pytest.MonkeyPatch()
        fake = FakeStripe()
        patch_stripe(mp, fake)
        part = await create_fixture()
        try:
            def call(ref, qty=1):
                return pay.create_whatsapp_checkout(user_id=ANON, part_id=part, quantity=qty, shipping_address={},
                                                    source="whatsapp", idempotency_ref=ref)
            out["result"] = await scenario(call, fake, part)
            out["orders"] = await order_state(part)
            out["fake"] = fake
        finally:
            mp.undo()
            await cleanup(part)
            import BACKEND_DATABASE_MODELS as _m
            await _m.pii_engine.dispose()
            await _m.engine.dispose()
    asyncio.run(main())
    o = out["orders"]
    print(f"EVIDENCE-CHECKOUT orders={len(o)} order_numbers={[x['order_number'] for x in o]} "
          f"stripe_sessions={len(out['fake'].sessions)} stripe_create_calls={len(out['fake'].create_calls)}")
    return out["result"], o, out["fake"]


def _rid():
    import uuid
    return f"wa:e2e-chk-{uuid.uuid4().hex[:8]}"


def test_P1_retry_after_success_reuses_the_same_order_and_session():
    rid = _rid()

    async def sc(call, fake, part):
        return part, [await call(rid), await call(rid), await call(rid)]
    (part, res), orders, fake = _run(sc)
    assert all(r["ok"] for r in res) and len({r["checkout_url"] for r in res}) == 1
    assert res[1]["reused"] and res[2]["reused"]
    assert len(orders) == 1 and orders[0]["status"] == "pending_payment" and len(fake.sessions) == 1
    import routes.payments as pay
    assert orders[0]["order_id"] == str(pay.chat_checkout_order_id(rid, part, 1))     # the deterministic id


def test_P1b_order_id_is_deterministic_per_request_part_quantity():
    import routes.payments as pay
    a = pay.chat_checkout_order_id("wa:x", "p1", 1)
    assert a == pay.chat_checkout_order_id("wa:x", "p1", 1)
    assert a != pay.chat_checkout_order_id("wa:y", "p1", 1) != pay.chat_checkout_order_id("wa:x", "p1", 2)


def test_P2_failure_before_order_creation_then_retry_one_order():
    rid = _rid()

    async def sc(call, fake, part):
        fake.fail_before = 1                                    # Stripe unreachable: nothing created anywhere
        first = await call(rid)
        return first, await call(rid)
    (first, second), orders, fake = _run(sc)
    assert not first["ok"] and second["ok"]
    assert len(orders) == 1 and len(fake.sessions) == 1


def test_P3_crash_while_stripe_creates_the_session_before_it_is_persisted():
    rid = _rid()

    async def sc(call, fake, part):
        fake.slow_s = 1.0
        t = asyncio.ensure_future(call(rid))
        await asyncio.sleep(0.4)                                # order flushed, Stripe call in flight
        t.cancel()                                              # the process dies: the DB transaction rolls back
        try:
            await t
        except BaseException:
            pass
        await asyncio.sleep(1.0)                                # the Stripe thread finishes: the session EXISTS
        mid = await order_state(part)
        fake.slow_s = 0.0
        return mid, await call(rid)                              # recovery re-run
    (mid, again), orders, fake = _run(sc)
    assert mid == []                                             # nothing local survived the crash
    assert again["ok"] and len(orders) == 1
    assert len(fake.sessions) == 1                               # the replayed key returned the SAME session
    assert orders[0]["session"] in fake.sessions


def test_P4_ambiguous_stripe_response_session_created_but_error_returned():
    rid = _rid()

    async def sc(call, fake, part):
        fake.raise_after = 1
        first = await call(rid)                                  # error to us, but Stripe made the session
        return first, await call(rid)
    (first, second), orders, fake = _run(sc)
    assert not first["ok"] and "Stripe error" in first["error"]
    assert second["ok"] and len(orders) == 1 and len(fake.sessions) == 1


def test_P5_persisted_but_link_never_delivered_retry_returns_the_same_link():
    rid = _rid()

    async def sc(call, fake, part):
        first = await call(rid)                                   # committed; pretend delivery never happened
        return first, await call(rid)
    (first, second), orders, fake = _run(sc)
    assert first["checkout_url"] == second["checkout_url"] and second.get("reused")
    assert len(orders) == 1 and len(fake.create_calls) == 1       # the retry did not even call Stripe create


def test_P6_concurrent_attempts_for_one_request_one_order_one_session():
    rid = _rid()

    async def sc(call, fake, part):
        fake.slow_s = 0.5
        return await asyncio.gather(*[call(rid) for _ in range(4)])
    res, orders, fake = _run(sc)
    assert all(r["ok"] for r in res), res
    assert len({r["checkout_url"] for r in res}) == 1
    assert len(orders) == 1 and len(fake.sessions) == 1


def test_P7_existing_session_completed_or_expired_never_a_second_order():
    rid = _rid()

    async def sc(call, fake, part):
        first = await call(rid)
        for s in fake.sessions.values():
            s.status = "complete"
        return first, await call(rid)
    (first, second), orders, fake = _run(sc)
    assert first["ok"] and not second["ok"] and second["error"] == "checkout_session_complete"
    assert len(orders) == 1 and len(fake.sessions) == 1


def test_P8_reconcile_failure_is_an_error_not_a_new_order():
    rid = _rid()

    async def sc(call, fake, part):
        first = await call(rid)
        fake.retrieve_fails = True
        return first, await call(rid)
    (first, second), orders, fake = _run(sc)
    assert not second["ok"] and second["error"].startswith("checkout_reconcile_failed")
    assert len(orders) == 1


def test_P9_different_requests_are_different_orders_and_no_ref_is_unchanged():
    async def sc(call, fake, part):
        return [await call(_rid()), await call(_rid()), await call(None), await call(None)]
    res, orders, fake = _run(sc)
    assert all(r["ok"] for r in res) and len(orders) == 4 and len(fake.sessions) == 4


def test_P10_quantity_is_part_of_the_logical_checkout():
    rid = _rid()

    async def sc(call, fake, part):
        return [await call(rid, 1), await call(rid, 2), await call(rid, 1)]
    res, orders, fake = _run(sc)
    assert len(orders) == 2 and res[0]["checkout_url"] == res[2]["checkout_url"]
