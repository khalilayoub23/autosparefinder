"""
Script: eurosender_proforma.py
Purpose: Builds the ProformaRequest body for a Eurosender shipment to Israel
         (non-EU destination -> Eurosender marks the order "Awaiting customs
         documentation" and requires a proforma before it proceeds).

         CONTRACT VERIFIED 2026-09-11 against the official OpenAPI spec
         (integrators.eurosender.com/_bundle/apis/index.yaml, ProformaRequest/
         ProformaItemRequest/ProformaContactPersonRequest schemas) after a
         live Sandbox submission on real order 325493-26 returned HTTP 400
         "Extra attributes are not allowed (unitValue, countryOfOrigin are
         unknown)" — the pre-2026-09-11 version of this module used invented
         field names never validated against the real API. Corrected fields,
         confirmed by the official schema:
           item.value        (integer, NOT "unitValue")
           item.country      (string,  NOT "countryOfOrigin")
           item.weight       (number,  REQUIRED — was missing entirely)
         The request body also requires top-level `shipper`, `receiver`
         (each a full ProformaContactPersonRequest) and `reason` (enum:
         '1' gift / '2' commercial / '3' personal_use / '4' repair /
         '5' return_after_repair / '6' sample) — none of which the prior
         implementation sent at all.
Process:
  build_proforma_items() takes the SAME per-category HS codes and per-item
  customer-facing prices already resolved by eurosender_hs_codes.py /
  eurosender_declared_value.py during shipment creation — it never re-derives
  or invents a classification/value independently.
  build_proforma_contact() reuses the SAME pickup/delivery contact+address
  dicts already used for create_shipment() — companyName/vat/eoriNumber are
  honestly null when we don't have that data (the schema allows null for
  those three; inventing a VAT/EORI number would be worse than omitting it).
Data Imported/Modified: none (pure builders) — submission happens via
  EurosenderAdapter.create_proforma(), called by eurosender_fulfillment.py
  only when the order-creation response indicated "Awaiting customs
  documentation".
Missing Data Delegation: an item whose category has no resolved HS code, no
  weight, or no country-of-origin is never included — routed to
  ProformaBuildError, never a guessed value.
Last Updated: 2026-09-11
"""
from __future__ import annotations


class ProformaBuildError(ValueError):
    pass


# Eurosender's proforma reason enum. The official OpenAPI spec renders this
# oddly as `enum: [- '1': gift, - '2': commercial, ...]` (numeric key mapped
# to a string label) — ambiguous whether the API wants the key or the label.
# LIVE-VERIFIED 2026-09-11 against real order 325493-26: "2" was rejected
# ("The value you selected is not a valid choice"); "commercial" (the label)
# was accepted (the only remaining violation on that request was unrelated —
# synthetic placeholder phone numbers). "commercial" is the correct value
# for a real paid e-commerce sale — a factual description of the
# transaction type, not a guess.
REASON_COMMERCIAL = "commercial"


def build_proforma_items(
    items_with_categories: list[dict],
    hs_by_category: dict[str, str],
    eur_to_ils: float,
) -> list[dict]:
    """items_with_categories: [{"category": str, "quantity": int, "content": str,
                                 "total_price_ils": float, "weight_kg": float,
                                 "country_of_origin": str}, ...]

    Returns Eurosender ProformaItemRequest dicts with the VERIFIED field
    names: description, country, quantity, weight, value (integer), hsCode.

    Raises ProformaBuildError if any item's category has no HS code, no
    weight, or no country of origin — hard stops, never guessed (owner has
    not authorized a universal HS fallback, and no country of manufacture
    is invented for a part we don't have that data for).
    """
    proforma_items = []
    for item in items_with_categories:
        category = item.get("category") or ""
        hs_code = hs_by_category.get(category)
        if not hs_code:
            raise ProformaBuildError(
                f"No HS code resolved for category '{category}' — refusing to "
                "build a proforma item without a real classification."
            )
        country_of_origin = item.get("country_of_origin")
        if not country_of_origin:
            raise ProformaBuildError(
                f"No country of origin provided for category '{category}' — "
                "refusing to guess a customs country-of-origin value."
            )
        weight_kg = item.get("weight_kg")
        if weight_kg is None or weight_kg <= 0:
            raise ProformaBuildError(
                f"No valid weight provided for category '{category}' proforma item."
            )
        qty = max(int(item.get("quantity") or 1), 1)
        total_price_ils = float(item.get("total_price_ils") or 0)
        unit_value_eur = round((total_price_ils / eur_to_ils) / qty) if qty else 0
        if unit_value_eur <= 0:
            raise ProformaBuildError(
                f"Non-positive unit value for category '{category}' — refusing "
                "to declare a zero/negative customs value."
            )
        proforma_items.append({
            "description": item.get("content") or category,
            "country": country_of_origin,
            "quantity": qty,
            "weight": round(float(weight_kg), 3),
            "value": int(unit_value_eur),
            "hsCode": hs_code,
        })
    return proforma_items


def build_proforma_contact(contact: dict, address: dict) -> dict:
    """Builds a verified ProformaContactPersonRequest from the SAME
    pickup/delivery contact+address dicts already used for
    create_shipment() — no new data is invented. companyName/vat/eoriNumber
    are honestly null when unavailable (the schema allows null for these
    three specifically)."""
    return {
        "contactPerson": contact.get("name") or "",
        "companyName": None,
        "phone": contact.get("phone") or "",
        "email": contact.get("email") or "",
        "street": address.get("street") or "",
        "zip": address.get("zip") or "",
        "city": address.get("city") or "",
        "country": address.get("country") or "",
        "vat": None,
        "eoriNumber": None,
    }
