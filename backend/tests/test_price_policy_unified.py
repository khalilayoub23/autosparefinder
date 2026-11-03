"""
Script: tests/test_price_policy_unified.py
Purpose: The customer price policy is ONE rule (backend/price_policy.py) and every price surface returns the same
         number for the same offer (owner, 2026-10-29: "every agent or customer channel use the same policy and get
         unified price"). Three layers:
           1. the policy itself (margin, conditional VAT, unknown supplier, rounding, lines, delivery once per
              supplier, agorot, catalog estimate never from base_price);
           2. equivalence: website search/compare fields, cart/wishlist/watch unit price, chat + brands pages
              (agent functions, ILS and USD), order lines (create_order), the pre-charge re-price, all = policy;
           3. guards: no customer-facing module carries its own 1.45 / 1.18 / 0.18 number; no customer payload
              carries a raw cost or base_price; the website never computes a price.
Process: pure functions + fakes (no network, no DB).
Data Imported/Modified: none.
Last Updated: 2026-10-29
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import price_policy as pp

BACKEND = Path(__file__).resolve().parent.parent
RATE = 3.71

# local / foreign / USD-priced / unknown supplier / a cost with awkward rounding
OFFERS = [
    {"id": "local", "price_ils": 100.0, "price_usd": None, "ship_ils": 29.0, "ship_usd": None,
     "name": "AutoParts Pro IL", "country": "IL"},
    {"id": "foreign", "price_ils": 243.17, "price_usd": None, "ship_ils": 58.5, "ship_usd": None,
     "name": "car-parts.ie", "country": "IE"},
    {"id": "usd", "price_ils": None, "price_usd": 19.99, "ship_ils": None, "ship_usd": 12.0,
     "name": "eBay", "country": "US"},
    {"id": "unknown", "price_ils": 77.77, "price_usd": None, "ship_ils": 0.0, "ship_usd": None,
     "name": None, "country": None},
    {"id": "rounding", "price_ils": 33.333, "price_usd": None, "ship_ils": 0.01, "ship_usd": None,
     "name": "x", "country": "israel"},
]


def _expected(o):
    cost = pp.offer_cost_ils(o["price_ils"], o["price_usd"], RATE)
    return cost, pp.unit_price(cost, o["name"], o["country"]), pp.delivery_fee(
        o["ship_ils"], o["ship_usd"], RATE, o["name"], o["country"])


# ── 1. the policy ─────────────────────────────────────────────────────────────────────────────────────────
def test_policy_numbers():
    u = pp.unit_price(100, "AutoParts Pro IL", "IL")
    assert (u.net, u.vat, u.total, u.vat_rate) == (145.0, 26.1, 171.1, 0.18)
    f = pp.unit_price(100, "car-parts.ie", "IE")
    assert (f.net, f.vat, f.total, f.vat_rate) == (145.0, 0.0, 145.0, 0.0)
    assert pp.unit_price(100).vat_rate == 0.18                         # unknown supplier = local (VAT never dropped)
    assert pp.unit_price(0) is None and pp.unit_price(None) is None and pp.unit_price("x") is None
    assert pp.offer_cost_ils(None, 10, 3.7) == 37.0 and pp.offer_cost_ils(5, 10, 3.7) == 5.0
    assert pp.agorot(29.99) == 2999 and pp.agorot(0.1 + 0.2) == 30     # rounded, never truncated
    r = pp.unit_price(33.333, None, "IL")             # 48.33285 -> 48.33 ; 48.33 x 0.18 = 8.6994 -> 8.70
    assert (r.net, r.vat, r.total) == (48.33, 8.7, 57.03) and r.line(3) == (144.99, 26.1, 171.09)


def test_order_totals_charge_delivery_once_per_supplier():
    a, b = pp.unit_price(100, "x", "IL"), pp.unit_price(50, "y", "IE")
    t = pp.order_totals([
        {"unit": a, "quantity": 2, "supplier_key": "S1", "delivery_fee": 29.0},
        {"unit": a, "quantity": 1, "supplier_key": "S1", "delivery_fee": 25.0},   # same supplier: one fee (highest)
        {"unit": b, "quantity": 1, "supplier_key": "S2", "delivery_fee": 40.0},
    ])
    assert t == {"subtotal": 507.5, "vat": 78.3, "shipping": 69.0, "total": 654.8}


def test_catalog_estimate_uses_importer_cost_never_base_price():
    assert pp.catalog_estimate(100, 999, 50) == (100.0, "il")
    assert pp.catalog_estimate(None, 118, None) == (100.0, "il")          # consumer reference incl. VAT
    assert pp.catalog_estimate(None, None, 80) == (80.0, "foreign")
    assert pp.catalog_estimate(None, None, None) is None
    assert pp.catalog_estimate_price(100).total == 171.1
    assert "base_price" not in pp.catalog_estimate.__code__.co_varnames


# ── 2. every surface = the policy ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("o", OFFERS, ids=[o["id"] for o in OFFERS])
def test_website_search_and_cart_fields_equal_policy(o):
    from routes.parts import _customer_price_fields, _customer_unit_price
    cost, u, fee = _expected(o)
    assert _customer_price_fields(cost, fee, o["name"], o["country"]) == {
        "customer_price_ils": u.net, "customer_vat_ils": u.vat, "customer_total_ils": round(u.total + fee, 2)}
    assert _customer_unit_price(cost, o["name"], o["country"]) == u.total


@pytest.mark.parametrize("o", OFFERS, ids=[o["id"] for o in OFFERS])
def test_chat_and_brands_agent_prices_equal_policy(o):
    import BACKEND_AI_AGENTS as A
    cost, u, fee = _expected(o)
    agent = A.BaseAgent.__new__(A.BaseAgent)
    p = agent.calculate_customer_price_from_ils(cost, o["ship_ils"] or 0, customer_shipping=fee,
                                                supplier_name=o["name"], supplier_country=o["country"])
    assert (p["price_no_vat"], p["vat"], p["total"]) == (u.net, u.vat, round(u.total + fee, 2))
    if o["price_usd"]:
        q = agent.calculate_customer_price(o["price_usd"], o["ship_usd"] or 0, customer_shipping=fee,
                                           usd_to_ils_rate=RATE, supplier_name=o["name"],
                                           supplier_country=o["country"])
        assert (q["price_no_vat"], q["vat"], q["total"]) == (u.net, u.vat, round(u.total + fee, 2))


def test_agent_vat_rule_is_the_policy_rule():
    import BACKEND_AI_AGENTS as A
    for name, country in (("AutoParts Pro IL", None), ("x", "IL"), ("x", "IE"), (None, None), ("", "")):
        assert A.get_supplier_vat_rate(name, country) == pp.vat_rate(name, country)
    assert A.PROFIT_MARGIN == pp.MARGIN and A.VAT_RATE == pp.VAT_RATE


@pytest.mark.parametrize("o", OFFERS, ids=[o["id"] for o in OFFERS])
def test_order_line_equals_policy(o):
    from routes.orders import _order_line_pricing
    cost, u, fee = _expected(o)
    sp = SimpleNamespace(price_ils=o["price_ils"], price_usd=o["price_usd"],
                         shipping_cost_ils=o["ship_ils"], shipping_cost_usd=o["ship_usd"])
    assert _order_line_pricing(sp, SimpleNamespace(name=o["name"], country=o["country"]), RATE) == (u.net, u.vat, fee)


class _FakeCat:
    """Answers the re-price query with the offer of each item's supplier_part_id."""

    def __init__(self, offers):
        self.offers = offers

    async def execute(self, _sql, params):
        o = self.offers[params.get("spid") or params.get("pid")]
        row = (o["supplier_id"], o["price_ils"], o["price_usd"], o["ship_ils"], o["ship_usd"], o["name"], o["country"])
        return SimpleNamespace(fetchone=lambda: row)


def test_pre_charge_reprice_matches_create_order_and_is_idempotent():
    from routes.payments import _live_reprice_order
    offers = {"spA": {**OFFERS[0], "supplier_id": "S1"}, "spB": {**OFFERS[1], "supplier_id": "S2"},
              "spC": {**OFFERS[0], "supplier_id": "S1", "price_ils": 40.0, "ship_ils": 10.0}}
    items = [SimpleNamespace(part_id="p1", supplier_part_id="spA", quantity=2, unit_price=0, vat_amount=0, total_price=0),
             SimpleNamespace(part_id="p2", supplier_part_id="spB", quantity=1, unit_price=0, vat_amount=0, total_price=0),
             SimpleNamespace(part_id="p3", supplier_part_id="spC", quantity=1, unit_price=0, vat_amount=0, total_price=0)]
    order = SimpleNamespace(subtotal=0, vat_amount=0, shipping_cost=0, total_amount=0, discount_amount=0)
    assert asyncio.run(_live_reprice_order(_FakeCat(offers), order, items, RATE)) is True
    lines = []
    for it in items:
        o = offers[it.supplier_part_id]
        _c, u, fee = _expected(o)
        assert (it.unit_price, it.vat_amount, it.total_price) == (u.net, u.vat, u.line(it.quantity)[2])
        lines.append({"unit": u, "quantity": it.quantity, "supplier_key": o["supplier_id"], "delivery_fee": fee})
    t = pp.order_totals(lines)
    assert (order.subtotal, order.vat_amount, order.shipping_cost, order.total_amount) == (
        t["subtotal"], t["vat"], t["shipping"], t["total"])
    assert t["shipping"] == round(29.0 + 58.5, 2)                       # S1 once (highest of 29 / 10) + S2
    assert asyncio.run(_live_reprice_order(_FakeCat(offers), order, items, RATE)) is False   # stable: no 409 loop


def test_search_price_range_is_customer_prices_or_estimate():
    from routes.parts import _apply_customer_price_range
    d = {}
    _apply_customer_price_range(d, [{"customer_price_ils": 145.0, "customer_vat_ils": 26.1},
                                    {"customer_price_ils": 200.0, "customer_vat_ils": 0.0}], 1, 2, 3)
    assert (d["cheapest_price_ils"], d["most_expensive_ils"], d["customer_estimate_ils"], d["is_estimate"]) == (
        171.1, 200.0, None, False)
    e = {}
    _apply_customer_price_range(e, [], 100, None, None)
    assert (e["cheapest_price_ils"], e["customer_estimate_ils"], e["is_estimate"]) == (None, 171.1, True)


# ── 3. guards ─────────────────────────────────────────────────────────────────────────────────────────────
CUSTOMER_MODULES = (sorted((BACKEND / "routes").glob("*.py")) + sorted((BACKEND / "services").rglob("*.py"))
                    + sorted((BACKEND / "social").glob("*.py")) + sorted((BACKEND / "agents").glob("*.py"))
                    + [BACKEND / n for n in ("BACKEND_AI_AGENTS.py", "BACKEND_API_ROUTES.py", "procurement.py",
                                             "supplier_ordering.py", "warranty_policy.py")])


def test_no_customer_module_has_its_own_margin_or_vat_number():
    bad = []
    for f in CUSTOMER_MODULES:
        if not f.exists():
            continue
        for n in ast.walk(ast.parse(f.read_text())):
            if isinstance(n, ast.Constant) and isinstance(n.value, float) and n.value in (1.45, 1.18, 0.18):
                bad.append(f"{f.relative_to(BACKEND)}:{n.lineno} {n.value}")
    assert not bad, f"price numbers outside price_policy.py: {bad}"


def test_no_customer_payload_carries_a_raw_cost_or_base_price():
    src = (BACKEND / "routes" / "parts.py").read_text()
    for key in ('"base_price":', '"min_price_ils":', '"max_price_ils":', '"importer_price_ils":', '"online_price_ils":'):
        assert key not in src, key
    agents = (BACKEND / "BACKEND_AI_AGENTS.py").read_text()
    assert '"base_price": bp' not in agents
    assert "round(bp + shipping, 2)" not in agents and "bp / (1 + VAT_RATE)" not in agents


def test_customer_offer_selection_is_one_rule():
    """website search, wishlist/watch, chat, brands pages, NOA and the pre-charge re-price pick the SAME offer."""
    for rel in ("routes/parts.py", "BACKEND_AI_AGENTS.py", "routes/brands.py", "BACKEND_API_ROUTES.py",
                "routes/payments.py"):
        src = (BACKEND / rel).read_text()
        assert "VISIBLE_OFFER_SQL" in src and "price_basis_order_sql" in src, rel
    assert "ORDER BY sp.part_id, sp.is_available DESC, s.priority ASC" not in (BACKEND / "routes/brands.py").read_text()
    pay = (BACKEND / "routes/payments.py").read_text()
    # an offer the customer CHOSE is re-priced if still orderable (the compare page lists unlisted offers too)
    assert 'sp.id = CAST(:spid AS uuid) AND {_pp.ORDERABLE_OFFER_SQL}' in pay
    assert pp.VISIBLE_OFFER_SQL.startswith(pp.ORDERABLE_OFFER_SQL) and "supplier_url" in pp.VISIBLE_OFFER_SQL
    assert 'suppliers.sort(key=lambda o: (o.get("customer_total_ils") is None' in (BACKEND / "routes/parts.py").read_text()


def test_website_never_computes_a_price():
    f = BACKEND.parent / "frontend" / "src" / "pages" / "Parts.jsx"
    if not f.exists():
        pytest.skip("frontend/ is not available in this environment (host-side test)")
    src = f.read_text()
    assert "* 1.45" not in src and "* 0.18" not in src
    assert "part.min_price_ils" not in src and "part.base_price" not in src
