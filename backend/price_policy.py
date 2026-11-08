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
Last Updated: 2026-11-05 (supplier VAT price basis)
"""
from __future__ import annotations

import re
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


def local_supplier_sql(alias: str = "s") -> str:
    """SQL twin of is_local_supplier (alias = the suppliers table): a known country decides; no country -> the
    local supplier names. The ONE SQL form of the local/foreign test (cleanup agent and refresh_min_max_prices)."""
    keys = ", ".join("'" + k.replace("'", "''") + "'" for k in sorted(LOCAL_COUNTRY_KEYS))
    names = ", ".join("'" + n.replace("'", "''") + "'" for n in sorted(LOCAL_SUPPLIER_NAMES))
    return (f"(CASE WHEN BTRIM(COALESCE({alias}.country, '')) <> '' THEN LOWER(BTRIM({alias}.country)) IN ({keys}) "
            f"ELSE LOWER(BTRIM(COALESCE({alias}.name, ''))) IN ({names}) END)")


def vat_rate(supplier_name: Optional[str] = None, supplier_country: Optional[str] = None) -> float:
    """18% for a local supplier, 0% for a foreign one. Unknown supplier (no name AND no country) -> local."""
    if not (supplier_name or "").strip() and not (supplier_country or "").strip():
        return VAT_RATE
    return VAT_RATE if is_local_supplier(supplier_name, supplier_country) else 0.0


# ── supplier VAT price basis (2026-11-05, "no double VAT, no missing VAT") ─────────────────────────────────────
# suppliers.vat_price_basis says what the supplier's STORED offer cost (supplier_parts.price_ils) contains:
#   exclusive - the stored cost is ex-VAT; a LOCAL supplier gets 18% added once (the rule above, unchanged)
#   inclusive - the stored cost already contains 18% VAT; it is stripped to the ex-VAT cost first (round(c/1.18, 2),
#               the importers' own convention), so the customer VAT is added exactly once, never on top of VAT
#   unknown   - not verified: a LOCAL supplier's offer is NOT priced (unit_price -> None); never a silent default
# basis=None means "the caller does not supply it": the basis is then resolved from the supplier-basis REGISTRY
# (suppliers.vat_price_basis, loaded by load_vat_bases - BACKEND_API_ROUTES._vat_basis_registry_loop) by the supplier NAME, so every surface that
# passes a supplier name prices from the supplier's real basis with no per-call-site plumbing. Once the registry is
# loaded a LOCAL supplier that is not in it (or is 'unknown') is blocked - never silently priced as exclusive. Only
# while the registry has never been loaded (tests, a bare subprocess) does None keep the legacy exclusive reading.
# A call with NO supplier name (the catalog estimate = importer cost) is ex-VAT by definition: exclusive.
# A FOREIGN supplier is outside this rule: its rate is 0 whatever the basis.
VAT_BASES = ("inclusive", "exclusive", "unknown")
_BASIS_REGISTRY: Optional[Dict[str, str]] = None


def load_vat_bases(mapping: Optional[Dict[str, Optional[str]]]) -> int:
    """Replace the registry with {supplier name: basis}. A missing/blank basis is stored as 'unknown'. None unloads it."""
    global _BASIS_REGISTRY
    if mapping is None:
        _BASIS_REGISTRY = None
        return 0
    _BASIS_REGISTRY = {str(k).strip().lower(): (normalize_vat_basis(v) or "unknown") for k, v in mapping.items()}
    return len(_BASIS_REGISTRY)


def effective_vat_basis(supplier_name: Optional[str], supplier_country: Optional[str] = None,
                        vat_basis: Optional[str] = None) -> Optional[str]:
    """The basis a calculation must use: an explicit one wins; else the registry entry for the supplier name; else
    None (legacy exclusive, only while the registry is unloaded or no supplier is named)."""
    explicit = normalize_vat_basis(vat_basis)
    if explicit is not None:
        return explicit
    name = (supplier_name or "").strip().lower()
    if not name or _BASIS_REGISTRY is None:
        return None
    return _BASIS_REGISTRY.get(name, "unknown")


def normalize_vat_basis(basis: Optional[str]) -> Optional[str]:
    """'inclusive' | 'exclusive' | 'unknown'; None stays None (not supplied); anything else is 'unknown'."""
    if basis is None:
        return None
    b = str(basis).strip().lower()
    return b if b in VAT_BASES else "unknown"


def vat_basis_blocks(supplier_name: Optional[str] = None, supplier_country: Optional[str] = None,
                     vat_basis: Optional[str] = None) -> bool:
    """True when a LOCAL supplier's VAT basis is explicitly unknown: its price must not reach a customer."""
    if not (supplier_name or "").strip() and not (supplier_country or "").strip():
        local = True
    else:
        local = is_local_supplier(supplier_name, supplier_country)
    return local and effective_vat_basis(supplier_name, supplier_country, vat_basis) == "unknown"


def vat_basis_blocks_sql(alias: str = "s") -> str:
    """SQL twin of vat_basis_blocks (alias = the suppliers table): TRUE when a local supplier's basis is unknown OR unclassified (NULL) - same as the Python twin once the registry is loaded."""
    return (f"({local_supplier_sql(alias)} AND LOWER(BTRIM(COALESCE({alias}.vat_price_basis, ''))) NOT IN "
            f"('inclusive', 'exclusive'))")


def cost_ex_vat(cost_ils: Any, supplier_name: Optional[str] = None, supplier_country: Optional[str] = None,
                vat_basis: Optional[str] = None) -> Optional[float]:
    """The ex-VAT cost the margin is applied to. inclusive local -> round(cost/1.18, 2); exclusive / None / foreign
    -> the cost itself; blocked (local + unknown) or no cost -> None."""
    cost = _num(cost_ils)
    if cost <= 0 or vat_basis_blocks(supplier_name, supplier_country, vat_basis):
        return None
    local = vat_rate(supplier_name, supplier_country) > 0
    if local and effective_vat_basis(supplier_name, supplier_country, vat_basis) == "inclusive":
        return round(cost / (1 + VAT_RATE), 2)
    return cost


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
               supplier_country: Optional[str] = None, vat_basis: Optional[str] = None) -> Optional[UnitPrice]:
    cost = cost_ex_vat(cost_ils, supplier_name, supplier_country, vat_basis)
    if cost is None or cost <= 0:
        return None
    rate = vat_rate(supplier_name, supplier_country)
    net = round(cost * MARGIN, 2)
    vat = round(net * rate, 2)
    return UnitPrice(net=net, vat=vat, vat_rate=rate, total=round(net + vat, 2))


def customer_fields(cost_ils: Any, ship_ils: Any = 0, supplier_name: Optional[str] = None,
                    supplier_country: Optional[str] = None, vat_basis: Optional[str] = None) -> Dict[str, Optional[float]]:
    """The offer payload fields every surface returns: customer_price_ils (net), customer_vat_ils,
    customer_total_ils (net + VAT + the offer's delivery fee)."""
    p = unit_price(cost_ils, supplier_name, supplier_country, vat_basis)
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


# ── supplier-country hygiene (#128) ───────────────────────────────────────────────────────────────────────────────
# vat_rate() decides local/foreign from the supplier's country. An unusable country does not change that rule (it is a
# business decision, FIXES_TRACKER #128) but must never be SILENT: country_problem() is the one test for "this country is
# not evidence", and db_update_agent.validate_supplier_countries alerts the owner for every active supplier with priced
# offers whose country fails it.
PLACEHOLDER_COUNTRIES = frozenset({"unknown", "n/a", "na", "none", "null", "-", "?", "global", "worldwide", "other"})


def country_problem(supplier_country: Optional[str]) -> Optional[str]:
    """None when the country is usable evidence for the VAT rule (a 2-letter country code, or Israel by name);
    'missing' for NULL / empty; 'invalid' for a placeholder ('Unknown', 'Global', ...) or anything else."""
    c = (supplier_country or "").strip()
    if not c:
        return "missing"
    low = c.lower()
    if low in PLACEHOLDER_COUNTRIES:
        return "invalid"
    if low in LOCAL_COUNTRY_KEYS or re.fullmatch(r"[A-Za-z]{2}", c):
        return None
    return "invalid"


def country_problem_sql(alias: str = "s") -> str:
    """SQL twin of country_problem (alias = the suppliers table): TRUE when the country is missing or invalid."""
    keys = ", ".join("'" + k.replace("'", "''") + "'" for k in sorted(LOCAL_COUNTRY_KEYS))
    ph = ", ".join("'" + k.replace("'", "''") + "'" for k in sorted(PLACEHOLDER_COUNTRIES))
    c = f"BTRIM(COALESCE({alias}.country, ''))"
    return (f"(NOT ({c} <> '' AND LOWER({c}) NOT IN ({ph}) AND ({c} ~ '^[A-Za-z]{{2}}$' OR LOWER({c}) IN ({keys}))))")


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
