"""
Script: tests/test_order_shipping_e2e.py
Purpose: The REAL web-cart order endpoint (routes.orders.create_order) on a disposable database charges
         shipping ONCE (owner rule 2026-10-27): total = Σ(cost×1.45 + VAT) + one delivery fee per supplier,
         equal to the canonical customer price of the same offers. Before the fix the supplier shipping was
         inside the unit price (×1.45) AND a separate per-supplier fee was added.
Process: isolated DB only (tests/isolated_db_guard.py) — skips otherwise; no Stripe / external call.
Data Imported/Modified: one supplier, two parts, two offers, one user, one order in the disposable DBs.
Last Updated: 2026-10-27
"""
from __future__ import annotations

import os
import uuid

import pytest

import isolated_db_guard as guard

_REFUSAL = guard.url_refusal(os.getenv("DATABASE_PII_URL")) or guard.url_refusal(os.getenv("DATABASE_URL"))
pytestmark = pytest.mark.skipif(bool(_REFUSAL), reason=f"isolated DB required: {_REFUSAL}")

from routes.parts import _customer_price_fields          # noqa: E402


async def test_web_order_charges_shipping_once():
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import async_session_factory, pii_session_factory, User
    from routes.orders import create_order
    from routes.schemas import OrderCreate, OrderItemCreate

    tag = uuid.uuid4().hex[:8]
    brand, sup = uuid.uuid4(), uuid.uuid4()
    rows = [(uuid.uuid4(), uuid.uuid4(), 100.0, 35.0), (uuid.uuid4(), uuid.uuid4(), 50.0, 45.0)]
    async with async_session_factory() as cat:
        assert await guard.live_refusal(cat) is None
        await cat.execute(text("INSERT INTO car_brands (id, name) VALUES (:i, :n)"), {"i": brand, "n": f"e2e-ship-brand-{tag}"})
        await cat.execute(text("INSERT INTO suppliers (id, name, country, is_active) VALUES (:i, :n, 'IL', true)"),
                          {"i": sup, "n": f"e2e-ship-supplier-{tag}"})
        for part, offer, cost, ship in rows:
            await cat.execute(text("INSERT INTO parts_catalog (id, sku, name, manufacturer_id, part_type, is_active) "
                                   "VALUES (:i, :k, :n, :m, 'oem', true)"),
                              {"i": part, "k": f"e2e-ship-{tag}-{cost}", "n": f"e2e part {cost}", "m": brand})
            await cat.execute(text("INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_usd, price_ils, "
                                   "shipping_cost_ils, is_available) VALUES (:i, :s, :p, :k, 1, :c, :sh, true)"),
                              {"i": offer, "s": sup, "p": part, "k": f"e2e-ship-{tag}-{cost}", "c": cost, "sh": ship})
        await cat.commit()
    async with async_session_factory() as cat, pii_session_factory() as pii:
        assert await guard.live_refusal(pii) is None
        user = User(email=f"e2e-order-{tag}@example.com", full_name="E2E", role="customer", is_verified=True, is_active=True)
        pii.add(user)
        await pii.commit()
        data = OrderCreate(items=[OrderItemCreate(part_id=str(part), supplier_part_id=str(offer), quantity=1)
                                  for part, offer, _c, _s in rows],
                           shipping_address={"name": "E2E", "street": "Herzl 1", "city": "Haifa", "zip": "3100000",
                                             "phone": "0500000000", "country": "IL"})
        resp = await create_order(data, current_user=user, cat_db=cat, db=pii, redis=None)
        stored = (await pii.execute(text("SELECT subtotal, vat_amount, shipping_cost, total_amount FROM orders "
                                         "WHERE user_id = CAST(:u AS uuid)"), {"u": str(user.id)})).one()

    unit = [round(100 * 1.45, 2), round(50 * 1.45, 2)]
    vat = [round(u * 0.18, 2) for u in unit]
    expected_shipping = 45.0                                      # one fee per supplier: max(35, 45)
    expected_total = round(sum(unit) + sum(vat) + expected_shipping, 2)
    sub, vat_amt, ship_amt, total_amt = (float(x) for x in stored)
    assert sub == pytest.approx(sum(unit), abs=0.01)                 # no shipping inside the unit price
    assert vat_amt == pytest.approx(sum(vat), abs=0.01)
    assert ship_amt == expected_shipping                             # one fee for the one supplier
    assert total_amt == pytest.approx(expected_total, abs=0.01)
    flat = str(resp)
    # single-offer parity with the canonical customer price (search/compare)
    single = _customer_price_fields(100.0, 35.0, supplier_name="x", supplier_country="IL")["customer_total_ils"]
    assert single == round(unit[0] + vat[0] + 35.0, 2)
    # the old double-count (shipping inside ×1.45 + separate fee) must not be what was charged
    old_total = round((135 * 1.45) * 1.18 + (95 * 1.45) * 1.18 + 59, 2)
    assert abs(total_amt - old_total) > 1 and str(old_total) not in flat
