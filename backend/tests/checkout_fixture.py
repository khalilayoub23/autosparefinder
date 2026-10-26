"""Sandbox checkout fixture for the payment-idempotency E2E tests (FIXES_TRACKER #77).

Creates a priced, available test part in the SANDBOX catalog (car brand + supplier + part + offer, all tagged
"zz-chk-") and removes it — plus every order / item / payment written for it in the sandbox PII DB — afterwards.
Stripe is replaced by FakeStripe, which implements Stripe's idempotency-key semantics (same key → same session,
recorded per key) and can be told to be slow, to fail before creating, or to create and then raise (an ambiguous
response). No real Stripe call, charge or session can happen: the secret key is a fake and both SDK entry points
used by create_whatsapp_checkout (Session.create / Session.retrieve) are replaced.
"""
import threading
import time
import uuid

TAG = "zz-chk-"


class FakeSession:
    def __init__(self, sid):
        self.id = sid
        self.url = f"https://checkout.example.test/c/{sid}"
        self.status = "open"


class FakeStripe:
    """Stripe Checkout Session API with idempotency: a replayed idempotency_key returns the original session."""

    def __init__(self):
        self.lock = threading.Lock()
        self.by_key, self.sessions, self.create_calls = {}, {}, []
        self.slow_s = 0.0                 # Session.create takes this long (runs in an executor thread)
        self.fail_before = 0              # the next N creates raise BEFORE creating anything
        self.raise_after = 0              # the next N creates create the session, then raise (ambiguous response)
        self.retrieve_fails = False

    def create(self, **kw):
        key = kw.get("idempotency_key")
        self.create_calls.append(key)
        if self.slow_s:
            time.sleep(self.slow_s)
        with self.lock:
            if self.fail_before:
                self.fail_before -= 1
                raise ConnectionError("stripe unreachable (nothing created)")
            if key in self.by_key:
                s = self.by_key[key]          # idempotent replay: the same session, not a new one
            else:
                s = FakeSession(f"cs_test_{uuid.uuid4().hex[:12]}")
                self.by_key[key] = s
                self.sessions[s.id] = s
            if self.raise_after:
                self.raise_after -= 1
                raise TimeoutError("stripe response lost (session WAS created)")
            return s

    def retrieve(self, sid, **kw):
        if self.retrieve_fails:
            raise ConnectionError("stripe unreachable")
        return self.sessions[sid]


def patch_stripe(mp, fake):
    """Replace the Stripe SDK entry points and the key resolution used by create_whatsapp_checkout."""
    import stripe
    import routes.payments as pay
    mp.setattr(stripe.checkout.Session, "create", staticmethod(lambda **kw: fake.create(**kw)))
    mp.setattr(stripe.checkout.Session, "retrieve", staticmethod(lambda sid, **kw: fake.retrieve(sid, **kw)))
    mp.setattr(pay, "resolve_stripe_secret_key", lambda: ("sk_test_sandbox_fake", "test"))
    mp.setattr(pay, "_is_valid_stripe_secret_key", lambda k: k == "sk_test_sandbox_fake")


async def create_fixture():
    """A priced, available part in the sandbox catalog. Returns its part id (str)."""
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import async_session_factory, engine
    assert engine.url.host == "sandbox_postgres", f"REFUSED: catalog host {engine.url.host!r}"
    s = uuid.uuid4().hex[:8]
    brand, sup, part, offer = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with async_session_factory() as db:
        await db.execute(text("INSERT INTO car_brands (id, name) VALUES (:i, :n)"), {"i": brand, "n": f"{TAG}brand-{s}"})
        await db.execute(text("INSERT INTO suppliers (id, name, country, is_active) VALUES (:i, :n, 'IL', true)"),
                         {"i": sup, "n": f"{TAG}supplier-{s}"})
        await db.execute(text("INSERT INTO parts_catalog (id, sku, name, manufacturer_id, part_type, is_active) "
                              "VALUES (:i, :k, :n, :m, 'oem', true)"),
                         {"i": part, "k": f"{TAG}sku-{s}", "n": f"{TAG}part {s}", "m": brand})
        await db.execute(text("INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_usd, price_ils, "
                              "is_available) VALUES (:i, :s, :p, :k, 30, 110, true)"),
                         {"i": offer, "s": sup, "p": part, "k": f"{TAG}sku-{s}"})
        await db.commit()
    return str(part)


async def order_state(part_id):
    """Every order / payment the sandbox PII DB holds for the fixture part."""
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import pii_session_factory
    async with pii_session_factory() as db:
        rows = (await db.execute(text(
            "SELECT o.id, o.order_number, o.status, p.payment_intent_id FROM orders o "
            "JOIN order_items i ON i.order_id = o.id LEFT JOIN payments p ON p.order_id = o.id "
            "WHERE i.part_id = CAST(:p AS uuid) ORDER BY o.created_at"), {"p": part_id})).fetchall()
    return [{"order_id": str(r[0]), "order_number": r[1], "status": r[2], "session": r[3]} for r in rows]


async def cleanup(part_id):
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import async_session_factory, pii_session_factory
    async with pii_session_factory() as db:
        ids = [r[0] for r in (await db.execute(text(
            "SELECT DISTINCT order_id FROM order_items WHERE part_id = CAST(:p AS uuid)"), {"p": part_id})).fetchall()]
        if ids:
            for t in ("payments", "order_items"):
                await db.execute(text(f"DELETE FROM {t} WHERE order_id = ANY(:ids)"), {"ids": ids})
            await db.execute(text("DELETE FROM orders WHERE id = ANY(:ids)"), {"ids": ids})
        await db.commit()
    async with async_session_factory() as db:
        await db.execute(text("DELETE FROM supplier_parts WHERE part_id = CAST(:p AS uuid)"), {"p": part_id})
        await db.execute(text("DELETE FROM parts_catalog WHERE id = CAST(:p AS uuid)"), {"p": part_id})
        await db.execute(text("DELETE FROM suppliers WHERE name LIKE :t"), {"t": f"{TAG}%"})
        await db.execute(text("DELETE FROM car_brands WHERE name LIKE :t"), {"t": f"{TAG}%"})
        await db.commit()
