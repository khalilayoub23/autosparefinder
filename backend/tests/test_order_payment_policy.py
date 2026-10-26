"""#111 regression: in production the platform never spends on a supplier for an order that was not really paid.

2026-10-25: the stuck-order agent re-detected four April TEST orders every day (Stripe test sessions, manual test
markers, @example.com customers), attempted a REAL supplier payment (Stripe Issuing, blocked only because it is off
in live mode) and told the owner "the customer payment was received". These tests pin the rule
(order_payment_policy) and its two enforcement points: routes.utils.trigger_supplier_fulfillment (the money path) and
the stuck-order agent's queries. Database tests run on an isolated DB only (tests/isolated_db_guard.py).
"""
import asyncio
import os
import uuid

import pytest

import isolated_db_guard as g
import order_payment_policy as pol

PROD = {"ENVIRONMENT": "production"}
SANDBOX = {"ENVIRONMENT": "sandbox"}


@pytest.mark.parametrize("email,test", [
    ("pa1@example.com", True), ("x@EXAMPLE.ORG", True), ("y@example.net", True), ("q@shop.test", True),
    ("q@x.invalid", True), ("a@box.localhost", True), ("b@site.example", True), ("svc@autospare.internal", True),
    ("someone@hotmail.com", False), ("ops@autosparefinder.com", False), ("a@example.com.co.il", False),
    ("", False), (None, False), ("not-an-address", False),
])
def test_reserved_test_email(email, test):
    assert pol.is_reserved_test_email(email) is test


@pytest.mark.parametrize("status,provider,refs,live", [
    ("paid", "stripe", ("cs_live_a1",), True),
    ("paid", "stripe", ("cs_live_a1:5f0c…",), True),                 # composite reference of the checkout flow
    ("paid", "stripe", (None, "cs_live_a1"), True),                   # in provider_transaction_id only
    ("paid", "stripe", ("cs_test_a1",), False),                        # Stripe TEST mode
    ("paid", "stripe", ("manual-pi_3TNP",), False),                    # manual test marker
    ("paid", "simulated", ("SIM-56881E39",), False),                   # simulated payment
    ("pending", "stripe", ("cs_live_a1",), False),                     # live session never paid
    ("refunded", "stripe", ("cs_live_a1",), False),
    ("paid", "stripe", (None, None), False),
])
def test_live_paid_payment(status, provider, refs, live):
    assert pol.is_live_paid_payment(status, provider, *refs) is live


def test_enforced_only_in_production():
    assert pol.production_enforced(PROD) and pol.production_enforced({"ENVIRONMENT": " Production "})
    assert not pol.production_enforced(SANDBOX) and not pol.production_enforced({})


REFUSAL = g.static_refusal()
TAG = "zz111" + uuid.uuid4().hex[:8]


async def _seed(c):
    """Four orders: A test customer + test payment, B real customer + live payment, C real customer + test payment
    (anomaly), D test customer + live payment."""
    users = {}
    for key, email in (("t", f"{TAG}@example.com"), ("r", f"{TAG}@realshop-qa.co.il")):
        users[key] = await c.fetchval(
            "INSERT INTO users (id, email, full_name) VALUES (gen_random_uuid(), $1, $2) RETURNING id", email, TAG)
    orders = {}
    for key, user, ref in (("A", "t", "cs_test_" + TAG), ("B", "r", "cs_live_" + TAG + ":x"),
                           ("C", "r", "cs_test_" + TAG + "c"), ("D", "t", "cs_live_" + TAG + "d")):
        oid = await c.fetchval("""INSERT INTO orders (id, user_id, status, subtotal, vat_amount, total_amount, order_number,
                                    created_at, updated_at)
                                  VALUES (gen_random_uuid(), $1, 'paid', 100, 18, 118, $2, now() - interval '2 days',
                                          now() - interval '2 days') RETURNING id""", users[user], f"{TAG}-{key}")
        await c.execute("""INSERT INTO payments (id, order_id, user_id, amount, amount_ils, currency, status, provider,
                                                 payment_intent_id, provider_transaction_id)
                           VALUES (gen_random_uuid(), $1, $2, 118, 118, 'ILS', 'paid', 'stripe', $3, $3)""",
                        oid, users[user], ref)
        orders[key] = oid
    return users, orders


async def _cleanup(c):
    await c.execute(f"DELETE FROM notifications WHERE user_id IN (SELECT id FROM users WHERE full_name = '{TAG}')")
    await c.execute(f"DELETE FROM payments WHERE order_id IN (SELECT id FROM orders WHERE order_number LIKE '{TAG}-%')")
    await c.execute(f"DELETE FROM supplier_payments WHERE order_id IN (SELECT id FROM orders WHERE order_number LIKE '{TAG}-%')")
    await c.execute(f"DELETE FROM orders WHERE order_number LIKE '{TAG}-%'")
    await c.execute(f"DELETE FROM users WHERE full_name = '{TAG}'")


@pytest.mark.skipif(REFUSAL is not None, reason=f"isolated DB only: {REFUSAL}")
def test_rule_and_both_enforcement_points_on_a_real_database(monkeypatch, capsys):
    import asyncpg
    from sqlalchemy import select
    import BACKEND_DATABASE_MODELS as m

    dsn = os.environ["DATABASE_PII_URL"].replace("postgresql+asyncpg://", "postgresql://")

    async def run():
        c = await asyncpg.connect(dsn)
        try:
            why = await g.live_refusal(_Adapter(c))
            if why:
                pytest.fail(f"REFUSED before any write: {why}")
            await _cleanup(c)
            users, orders = await _seed(c)
            await m.pii_engine.dispose(close=False)
            async with m.pii_session_factory() as db:
                rows = {k: (await db.execute(select(m.Order).where(m.Order.id == v))).scalar_one() for k, v in orders.items()}
                # the rule, per order, in production and in a sandbox
                verdicts = {k: await pol.supplier_spend_refusal(db, o, PROD) for k, o in rows.items()}
                assert verdicts["B"] is None
                assert "test address" in verdicts["A"] and "test address" in verdicts["D"]
                assert "no live-mode paid payment" in verdicts["C"]
                for o in rows.values():
                    assert await pol.supplier_spend_refusal(db, o, SANDBOX) is None
                # the SAME rule as SQL (stuck-order agent)
                live = set((await db.execute(pol.live_paid_order_ids())).scalars().all())
                testers = set((await db.execute(pol.test_customer_user_ids())).scalars().all())
                assert {orders["B"], orders["D"]} <= live and not {orders["A"], orders["C"]} & live
                assert users["t"] in testers and users["r"] not in testers
                agent_view = set((await db.execute(select(m.Order.id).where(
                    m.Order.id.in_(orders.values()), m.Order.id.in_(pol.live_paid_order_ids()),
                    ~m.Order.user_id.in_(pol.test_customer_user_ids())))).scalars().all())
                assert agent_view == {orders["B"]}
            # the money path refuses A (test order) in production before ANY supplier work: its status and its
            # notifications stay untouched (without the gate, an order with no items is set to 'processing' and the
            # admins are notified), and no supplier payment row is created
            from routes.utils import trigger_supplier_fulfillment     # imported in the sandbox environment;
            monkeypatch.setenv("ENVIRONMENT", "production")             # the policy reads ENVIRONMENT at call time
            async with m.pii_session_factory() as db:
                a = (await db.execute(select(m.Order).where(m.Order.id == orders["A"]))).scalar_one()
                await trigger_supplier_fulfillment([a], db)
                await db.commit()
            assert await c.fetchval("SELECT status FROM orders WHERE id = $1", orders["A"]) == "paid"
            assert await c.fetchval("SELECT count(*) FROM supplier_payments WHERE order_id = $1", orders["A"]) == 0
            assert "supplier spend refused" in capsys.readouterr().out
        finally:
            await _cleanup(c)
            await c.close()
            await m.pii_engine.dispose()

    asyncio.run(run())


class _Adapter:
    def __init__(self, conn):
        self.conn = conn

    async def execute(self, stmt, params=None):
        value = await self.conn.fetchval(str(stmt))

        class R:
            def scalar(_self):
                return value
        return R()
