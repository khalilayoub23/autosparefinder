"""
Script: eurosender_declared_value.py
Purpose: Compute the customs declared value for a Eurosender parcel/proforma.
         Eurosender's guide states undervaluing is prohibited by law — the
         declared value must be the TRUE commercial transaction value (what
         the customer paid), never the supplier's cost to us.
Process:
  declared_value_eur(item_total_prices_ils, eur_to_ils) sums ONLY the
  customer-facing item totals (order_items.total_price) and converts to an
  integer EUR value (Eurosender's `value` field is typed as an integer).
Data Imported/Modified: none (pure calculation).
Data Sources: order_items.total_price (customer price, already inclusive of
              the platform's margin — see CLAUDE.md _customer_price_fields).
Missing Data Delegation: none — this function structurally cannot accept a
  supplier-cost figure; its only input parameter is a list of customer-facing
  item totals, by design (see eurosender_declared_value_test.py which proves
  supplier cost fields are never passed here).
Last Updated: 2026-09-08
"""
from __future__ import annotations

import os


class DeclaredValueError(ValueError):
    pass


def declared_value_eur(item_total_prices_ils: list[float], eur_to_ils: float | None = None) -> int:
    """Sum customer-facing item totals (ILS) and convert to an integer EUR
    declared value for Eurosender's PackageRequest.value field.

    IMPORTANT — by design, this function's ONLY numeric input is a list of
    order_items.total_price values. It has no parameter for supplier cost,
    importer_price_ils, or shipping cost, so those figures cannot flow into
    the declared customs value through this call — the exclusion is
    structural, not merely a convention callers must remember.

    Shipping must be excluded by the CALLER (do not append order.shipping_cost
    to item_total_prices_ils before calling this).
    """
    if not item_total_prices_ils:
        raise DeclaredValueError("Cannot declare a customs value with zero order items.")

    rate = eur_to_ils if eur_to_ils is not None else float(os.getenv("EUR_TO_ILS", "4.05") or "4.05")
    if rate <= 0:
        raise DeclaredValueError(f"Invalid EUR_TO_ILS rate: {rate}")

    total_ils = sum(float(p) for p in item_total_prices_ils)
    if total_ils <= 0:
        raise DeclaredValueError(
            f"Computed customs value is non-positive ({total_ils} ILS) — "
            "refusing to submit a zero/negative declared value (undervaluing "
            "is prohibited)."
        )

    value_eur = round(total_ils / rate)
    if value_eur < 1:
        # Eurosender's value field is an integer EUR amount; a sub-1-EUR
        # shipment still needs a truthful non-zero declaration.
        value_eur = 1
    return value_eur
