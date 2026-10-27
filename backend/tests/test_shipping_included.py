"""
Script: tests/test_shipping_included.py
Purpose: Shipping is part of every offer's price and is charged ONCE (owner rule 2026-10-27).
         total = cost×1.45 + conditional VAT + the offer's known delivery fee.
Incident: the web-cart order endpoint put the supplier's shipping INTO the unit price (×1.45) AND added a
         separate per-supplier fee; the chat search showed shipping twice the same way (915,293 of 1,519,166
         available offers carry supplier shipping). The cart page added VAT again on a VAT-inclusive price and
         fell back to an invented ₪91. Agents told customers shipping "appears at checkout".
Process: pure-logic tests — every customer price path must equal the canonical price for the same offer.
Data Imported/Modified: none.
Last Updated: 2026-10-27
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import BACKEND_AI_AGENTS as A
import policy_guard as pg
import reply_language as rl
from routes.orders import _order_line_pricing
from routes.parts import _customer_price_fields

IL = ("AutoParts Pro IL", "IL")
FOREIGN = ("car-parts.ie", "IE")
RATE = 3.7


def sp(price_ils=100.0, ship_ils=35.0, price_usd=None, ship_usd=None):
    return SimpleNamespace(price_ils=price_ils, price_usd=price_usd, shipping_cost_ils=ship_ils, shipping_cost_usd=ship_usd)


def sup(kind):
    return SimpleNamespace(name=kind[0], country=kind[1], id=kind[0])


def canonical_total(cost, ship, kind):
    return _customer_price_fields(cost, ship, supplier_name=kind[0], supplier_country=kind[1])["customer_total_ils"]


# ── 1. every pricing path = canonical (shipping once, never marked up) ──────────

@pytest.mark.parametrize("kind", [IL, FOREIGN], ids=["IL", "foreign"])
@pytest.mark.parametrize("cost,ship", [(100.0, 35.0), (358.0, 0.0), (1234.5, 89.9)])
def test_chat_price_helper_counts_shipping_once(kind, cost, ship):
    p = A.BaseAgent().calculate_customer_price_from_ils(cost, ship, customer_shipping=ship,
                                                         supplier_name=kind[0], supplier_country=kind[1],
                                                         local_vat_only=True)
    assert p["price_no_vat"] == round(cost * 1.45, 2)            # shipping NOT in the margin base
    assert p["shipping"] == ship
    assert p["total"] == pytest.approx(canonical_total(cost, ship, kind), abs=0.01)


def test_helper_without_customer_shipping_uses_supplier_shipping_once():
    p = A.BaseAgent().calculate_customer_price_from_ils(100.0, 35.0, supplier_name="x", supplier_country="IE",
                                                         local_vat_only=True)
    assert p["price_no_vat"] == 145.0 and p["shipping"] == 35.0 and p["total"] == 180.0


@pytest.mark.parametrize("kind", [IL, FOREIGN], ids=["IL", "foreign"])
@pytest.mark.parametrize("offer", [sp(100.0, 35.0), sp(358.0, 0.0), sp(0, None, price_usd=40.0, ship_usd=12.0)],
                         ids=["ils+ship", "ils-noship", "usd+usdship"])
def test_order_line_equals_canonical_and_whatsapp_checkout(kind, offer):
    unit, vat, fee = _order_line_pricing(offer, sup(kind), RATE)
    cost = float(offer.price_ils or 0) or float(offer.price_usd) * RATE
    assert unit == round(cost * 1.45, 2)                          # was (cost + shipping) × 1.45
    expected_fee = A.resolve_customer_shipping_fee(offer.shipping_cost_ils, offer.shipping_cost_usd, RATE, kind[0], kind[1])
    assert fee == round(expected_fee, 2)
    assert unit + vat + fee == pytest.approx(canonical_total(cost, fee, kind), abs=0.01)


def test_order_line_without_supplier_shipping_uses_the_supplier_profile_fee():
    unit, vat, fee = _order_line_pricing(sp(100.0, None), sup(IL), RATE)
    assert fee == A.get_supplier_shipping(*IL)


def test_whatsapp_checkout_uses_the_same_rule():
    src = Path(A.__file__).with_name("routes").joinpath("payments.py").read_text(encoding="utf-8")
    i = src.index("async def create_whatsapp_checkout(")
    body = src[i:i + 9000]
    assert "unit_price = round(cost_ils * 1.45, 2)" in body and "resolve_customer_shipping_fee(" in body


def test_no_pricing_path_puts_shipping_into_the_margin_base():
    root = Path(A.__file__).parent
    for f in ("BACKEND_AI_AGENTS.py", "routes/orders.py", "routes/parts.py", "routes/brands.py", "routes/payments.py"):
        code = (root / f).read_text(encoding="utf-8")
        assert "cost_ils + ship_ils" not in code, f
        assert "price_usd or 0) + float(sp.shipping_cost_usd" not in code, f
        assert "price_usd or 0) + float(sp_row.shipping_cost_usd" not in code, f
    assert "get_supplier_shipping as _get_ship2" not in (root / "routes/orders.py").read_text(encoding="utf-8")


# ── 2. cart API: VAT-inclusive price + one delivery fee per supplier ───────────

async def test_cart_response_delivery_fee_once_per_supplier(monkeypatch):
    import routes.cart as cart
    supA, supB = SimpleNamespace(name="AutoParts Pro IL", country="IL"), SimpleNamespace(name="car-parts.ie", country="IE")
    rows = {
        "a1": (SimpleNamespace(id="a1", price_ils=100.0, price_usd=None, shipping_cost_ils=30.0, shipping_cost_usd=None,
                               supplier_id="A", part_type=None, stock_quantity=5), SimpleNamespace(id="p1", name="Pads", part_type="oem"), supA),
        "a2": (SimpleNamespace(id="a2", price_ils=50.0, price_usd=None, shipping_cost_ils=45.0, shipping_cost_usd=None,
                               supplier_id="A", part_type=None, stock_quantity=5), SimpleNamespace(id="p2", name="Disc", part_type="oem"), supA),
        "b1": (SimpleNamespace(id="b1", price_ils=80.0, price_usd=None, shipping_cost_ils=20.0, shipping_cost_usd=None,
                               supplier_id="B", part_type=None, stock_quantity=5), SimpleNamespace(id="p3", name="Filter", part_type="aftermarket"), supB),
    }

    class _Row(tuple):
        @property
        def SupplierPart(self):
            return self[0]

    class _DB:
        async def execute(self, *_a, **_k):
            return [_Row(v) for v in rows.values()]

    async def _rate(_db):
        return RATE

    async def _thumbs(_db, _ids):
        return {}
    monkeypatch.setattr(cart, "get_usd_to_ils_rate", _rate)
    monkeypatch.setattr(cart, "_customer_thumbnail_map", _thumbs)
    items = [SimpleNamespace(id=k, part_id=v[1].id, supplier_part_id=k, quantity=1) for k, v in rows.items()]
    out = await cart._cart_to_response(items, _DB())
    by = {r["supplierPartId"]: r for r in out}
    assert by["a1"]["price"] == round(100 * 1.45 * 1.18, 2) and by["b1"]["price"] == round(80 * 1.45, 2)   # canonical, VAT incl.
    assert all(r["vatIncluded"] for r in out)
    assert (by["a1"]["deliveryFee"], by["a2"]["deliveryFee"], by["b1"]["deliveryFee"]) == (30.0, 45.0, 20.0)
    assert sum(r["shippingCost"] for r in out) == 45.0 + 20.0      # once per supplier (max), like the order


# ── 3. frontend cart math (the shared helper, run in Node) ────────────────────

FRONT = Path(A.__file__).resolve().parent.parent / "frontend" / "src" / "utils" / "cartTotals.js"


@pytest.mark.skipif(not shutil.which("node") or not FRONT.exists(), reason="node or frontend source not available")
def test_frontend_cart_totals_match_the_charge(tmp_path):
    items = [
        {"price": round(100 * 1.45 * 1.18, 2), "quantity": 2, "isIlSupplier": True, "supplierId": "A", "deliveryFee": 30},
        {"price": round(50 * 1.45 * 1.18, 2), "quantity": 1, "isIlSupplier": True, "supplierId": "A", "deliveryFee": 45},
        {"price": round(80 * 1.45, 2), "quantity": 1, "isIlSupplier": False, "supplierId": "B", "deliveryFee": 20},
        {"price": round(10 * 1.45, 2), "quantity": 1, "isIlSupplier": False, "supplierId": "C", "deliveryFee": 0},
    ]
    script = tmp_path / "t.mjs"
    script.write_text(f"import {{ cartTotals }} from '{FRONT.as_posix()}';\n"
                      f"console.log(JSON.stringify(cartTotals({json.dumps(items)})));\n", encoding="utf-8")
    got = json.loads(subprocess.check_output(["node", str(script)], text=True))
    parts = sum(i["price"] * i["quantity"] for i in items)
    assert got["subtotal"] == pytest.approx(parts, abs=0.01)                       # VAT already inside
    il_lines = sum(i["price"] * i["quantity"] for i in items if i["isIlSupplier"])
    assert got["vat"] == pytest.approx(il_lines - il_lines / 1.18, abs=0.01)       # "of which VAT", not added
    assert got["shipping"] == 45 + 20 + 0                                          # once per supplier; no ₪91
    assert got["total"] == pytest.approx(parts + 65, abs=0.01)
    empty = json.loads(subprocess.check_output(["node", "-e", f"import('{FRONT.as_posix()}').then(m=>console.log(JSON.stringify(m.cartTotals([]))))"], text=True))
    assert empty["shipping"] == 0 and empty["total"] == 0


def test_frontend_has_no_invented_shipping_fallback():
    src = FRONT.parent.parent
    for f in ("stores/cartStore.js", "pages/Cart.jsx"):
        p = src / f
        if p.exists():
            code = p.read_text(encoding="utf-8")
            assert "|| 91" not in code and "* 0.18" not in code, f


# ── 4. what the agents say ───────────────────────────────────────────────────

def test_shipping_policy_says_included_never_at_checkout():
    pol = A.TELEGRAM_BOT_POLICY
    assert "ALREADY INCLUDED in every part's total price" in pol and "charged ONCE" in pol
    assert "shown at checkout" not in pol and "appear at checkout" not in pol
    for cls in A.AGENT_MAP.values():
        assert "appears at checkout" not in cls.system_prompt and "₪29" not in cls.system_prompt


@pytest.mark.parametrize("text", [
    A._shipping_fact_he({}), A._delivery_fact_he({}),
    *[v["delivery_unknown"] for v in A._SALES_RESULT_LABELS.values()],
])
def test_fixed_texts_never_defer_shipping_to_checkout(text):
    for bad in ("בקופה", "at checkout", "عند الدفع"):
        assert bad not in text


@pytest.mark.parametrize("reply,bad", [
    ("تكلفة الشحن إلى حيفا تختلف حسب المورد وتظهر عند إتمام الطلب في صفحة الدفع.", True),   # live 2026-10-27
    ("Shipping costs depend on the supplier, and the exact amount will appear at checkout.", True),
    ("עלות המשלוח לחיפה מוצגת רק בזמן ההזמנה בקופה.", True),
    ("The delivery fee is added later, after payment.", True),
    ("Parts ship from the supplier after payment.", False),
    ("Delivery across Israel is included in each part's total price.", False),
    ("המשלוח כלול במחיר הכולל של כל חלק.", False),
    ("التوصيل مشمول في السعر الإجمالي لكل قطعة.", False),
])
def test_guard_flags_deferred_shipping_cost(reply, bad):
    kinds = {v.kind for v in pg.validate_customer_reply(reply, [])}
    assert ("shipping_deferred" in kinds) is bad


class _Fake:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    async def __call__(self, prompt, system="", **kw):
        self.calls.append(prompt)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


@pytest.mark.parametrize("lang,bad,good", [
    ("en", "Shipping costs vary and the exact amount will appear at checkout.",
           "Delivery across Israel is already included in each part's total price. Which part do you need?"),
    ("ar", "تكلفة الشحن تظهر عند إتمام الطلب في صفحة الدفع.",
           "التوصيل مشمول في السعر الإجمالي لكل قطعة. ما القطعة التي تحتاجها؟"),
    ("he", "עלות המשלוח תוצג בקופה.",
           "המשלוח כלול במחיר הכולל של כל חלק. איזה חלק אתה צריך?"),
])
async def test_agent_reply_deferring_shipping_is_corrected(monkeypatch, lang, bad, good):
    f = _Fake([bad, good])
    monkeypatch.setattr(A, "hf_text", f)
    monkeypatch.setattr(A, "hf_text_fast", f)
    monkeypatch.setenv("CEREBRAS_API_KEY", "k")
    q = {"en": "How much is shipping to Haifa?", "ar": "كم تكلفة الشحن إلى حيفا؟", "he": "כמה עולה משלוח לחיפה?"}[lang]
    out = await A.SalesAgent().think([{"role": "user", "content": q}], source="whatsapp")
    assert out == good and len(f.calls) == 2 and "already included" in f.calls[1]
