"""
Script: price_policy.py
Purpose: THE customer price policy of AutoSpareFinder - the ONE place that defines how a customer price is
         calculated. Every surface that shows, quotes, checks or charges a customer price (website search /
         compare / cart / orders / Stripe checkout and its live re-check / WhatsApp-Telegram-web chat / NOA posts /
         partner API / brands pages) calls these functions; none may re-implement the margin, the VAT rule,
         the rounding or the delivery rule (guard: tests/test_price_policy_unified.py).
Process (owner policy, CLAUDE.md "Pricing & VAT"):
  cost        = the supplier OFFER's cost in ILS (price_ils, else price_usd x live USD->ILS rate)
  net         = round(cost x MARGIN, 2)                       MARGIN = 1.45 (uniform 45%, no exceptions)
  vat         = round(net x vat_rate, 2)                      18% ONLY for a LOCAL (Israeli) supplier, 0% for a
                                                              foreign one; an UNKNOWN supplier counts as local
                                                              (VAT is never silently dropped)
  unit_total  = net + vat                                     = the customer unit price shown everywhere
  line        = net x qty, vat x qty
  delivery    = the offer's known delivery fee (resolve_customer_shipping_fee), charged ONCE per supplier per
                order (the highest fee among that supplier's lines), never inside the margin base
  order total = sum(lines) + sum(delivery per supplier)
  No live offer (catalog-only part): the estimate uses the catalog's IMPORTER cost with the same rule as a
  local supplier (importer_price_ils; else max_price_ils / 1.18, the IL consumer reference incl. VAT; else
  online_price_ils as a FOREIGN cost). `base_price` is NEVER a customer price (it is an internal column).
  A raw cost (supplier_parts.price_ils, parts_catalog.min_price_ils / importer_price_ils / online_price_ils /
  base_price) is never shown as, or stands in for, a customer price.
Data Imported/Modified: none (pure functions).
Last Updated: 2026-10-29
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

MARGIN = 1.45
VAT_RATE = 0.18
LOCAL_SUPPLIER_NAMES = frozenset({"autoparts pro il"})
LOCAL_COUNTRY_KEYS = frozenset({"il", "israel", "ישראל"})


def _num(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if f == f else 0.0      # NaN -> 0


def is_local_supplier(supplier_name: Optional[str] = None, supplier_country: Optional[str] = None) -> bool:
    country = (supplier_country or "").strip().lower()
    if country:
        return country in LOCAL_COUNTRY_KEYS
    return (supplier_name or "").strip().lower() in LOCAL_SUPPLIER_NAMES


def vat_rate(supplier_name: Optional[str] = None, supplier_country: Optional[str] = None) -> float:
    """18% for a local supplier, 0% for a foreign one. Unknown supplier (no name AND no country) -> local."""
    if not (supplier_name or "").strip() and not (supplier_country or "").strip():
        return VAT_RATE
    return VAT_RATE if is_local_supplier(supplier_name, supplier_country) else 0.0


def offer_cost_ils(price_ils: Any, price_usd: Any = None, usd_to_ils: Any = None) -> float:
    """The supplier offer's cost in ILS: price_ils when set, else price_usd x the live rate. 0 = no usable cost."""
    c = _num(price_ils)
    if c > 0:
        return c
    u, r = _num(price_usd), _num(usd_to_ils)
    return u * r if u > 0 and r > 0 else 0.0


@dataclass(frozen=True)
class UnitPrice:
    net: float          # before VAT (cost x 1.45)
    vat: float          # VAT on net (0 for a foreign supplier)
    vat_rate: float
    total: float        # net + vat = THE customer unit price

    def line(self, quantity: int) -> Tuple[float, float, float]:
        q = max(int(quantity or 0), 0)
        net, vat = round(self.net * q, 2), round(self.vat * q, 2)
        return net, vat, round(net + vat, 2)


def unit_price(cost_ils: Any, supplier_name: Optional[str] = None,
               supplier_country: Optional[str] = None) -> Optional[UnitPrice]:
    cost = _num(cost_ils)
    if cost <= 0:
        return None
    rate = vat_rate(supplier_name, supplier_country)
    net = round(cost * MARGIN, 2)
    vat = round(net * rate, 2)
    return UnitPrice(net=net, vat=vat, vat_rate=rate, total=round(net + vat, 2))


def customer_fields(cost_ils: Any, ship_ils: Any = 0, supplier_name: Optional[str] = None,
                    supplier_country: Optional[str] = None) -> Dict[str, Optional[float]]:
    """The offer payload fields every surface returns: customer_price_ils (net), customer_vat_ils,
    customer_total_ils (net + VAT + the offer's delivery fee)."""
    p = unit_price(cost_ils, supplier_name, supplier_country)
    if p is None:
        return {"customer_price_ils": None, "customer_vat_ils": None, "customer_total_ils": None}
    return {"customer_price_ils": p.net, "customer_vat_ils": p.vat,
            "customer_total_ils": round(p.total + max(_num(ship_ils), 0.0), 2)}


def delivery_fee(supplier_shipping_ils: Any = None, supplier_shipping_usd: Any = None, usd_to_ils: Any = None,
                 supplier_name: Optional[str] = None, supplier_country: Optional[str] = None) -> float:
    """The offer's customer delivery fee - the ONE resolver (supplier shipping, else the supplier profile)."""
    from BACKEND_AI_AGENTS import resolve_customer_shipping_fee
    return round(_num(resolve_customer_shipping_fee(
        supplier_shipping_ils=supplier_shipping_ils, supplier_shipping_usd=supplier_shipping_usd,
        usd_to_ils_rate=usd_to_ils, supplier_name=supplier_name, supplier_country=supplier_country)), 2)


def order_totals(lines: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    """lines: {"unit": UnitPrice, "quantity": int, "supplier_key": str, "delivery_fee": float}.
    Delivery is charged once per supplier (the highest fee among that supplier's lines)."""
    subtotal = vat = 0.0
    per_supplier: Dict[str, float] = {}
    for ln in lines:
        n, v, _t = ln["unit"].line(ln["quantity"])
        subtotal += n
        vat += v
        k = str(ln.get("supplier_key") or "")
        per_supplier[k] = max(per_supplier.get(k, 0.0), _num(ln.get("delivery_fee")))
    shipping = round(sum(per_supplier.values()), 2)
    subtotal, vat = round(subtotal, 2), round(vat, 2)
    return {"subtotal": subtotal, "vat": vat, "shipping": shipping, "total": round(subtotal + vat + shipping, 2)}


def agorot(amount_ils: Any) -> int:
    """ILS -> agorot for a payment provider: ROUNDED (int(x*100) truncated ₪29.99 to 2998)."""
    return int(round(_num(amount_ils) * 100))


# ── which offer sets a part's price (the SAME on every channel) ────────────────────────────────────────────
# A customer-visible offer: available, active supplier, not an internal/test supplier, with a listing URL (the
# website search's own predicate since 2026-09-21). Among them the PRICE-BASIS offer is the one whose product
# class is compatible with the part (offer_classification.class_rank_sql), then the lowest cost.
# Aliases: sp = supplier_parts, s = suppliers.
ORDERABLE_OFFER_SQL = ("sp.is_available = TRUE AND s.is_active = TRUE "
                       "AND s.name NOT IN ('Official Manufacturer Sites', 'Sandbox Supplier QA')")
# the offer that sets a part's ADVERTISED price must also be listed (search hides URL-less price-list offers); an
# offer the customer CHOSE (compare page lists every orderable offer) is re-priced with ORDERABLE_OFFER_SQL.
VISIBLE_OFFER_SQL = ORDERABLE_OFFER_SQL + " AND NULLIF(BTRIM(sp.supplier_url), '') IS NOT NULL"


def offer_cost_sql(rate_param: str = ":usd_to_ils_rate") -> str:
    return f"COALESCE(sp.price_ils, sp.price_usd * {rate_param})"


def price_basis_order_sql(part_type_sql: str, rate_param: str = ":usd_to_ils_rate") -> str:
    """ORDER BY fragment: class-compatible offers first, then the cheapest cost."""
    from offer_classification import class_rank_sql
    return f"{class_rank_sql('sp.part_type', part_type_sql)}, {offer_cost_sql(rate_param)} ASC"


def catalog_estimate(importer_price_ils: Any = None, max_price_ils: Any = None,
                     online_price_ils: Any = None) -> Optional[Tuple[float, Optional[str]]]:
    """(cost_ils, supplier_country) for a part with NO live offer, or None. The importer cost is a LOCAL cost;
    the consumer reference max_price_ils includes 18% VAT; online_price_ils is a foreign buy price.
    base_price is deliberately not an input."""
    imp = _num(importer_price_ils)
    if imp > 0:
        return imp, "il"
    mx = _num(max_price_ils)
    if mx > 0:
        return round(mx / (1 + VAT_RATE), 2), "il"
    onl = _num(online_price_ils)
    if onl > 0:
        return onl, "foreign"
    return None


def catalog_estimate_price(importer_price_ils: Any = None, max_price_ils: Any = None,
                           online_price_ils: Any = None) -> Optional[UnitPrice]:
    est = catalog_estimate(importer_price_ils, max_price_ils, online_price_ils)
    if est is None:
        return None
    cost, country = est
    return unit_price(cost, supplier_country=country)
