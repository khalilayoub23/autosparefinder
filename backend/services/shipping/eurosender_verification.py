"""
Script: eurosender_verification.py
Purpose: The ONLY sanctioned entry points for read-only Eurosender Sandbox
         verification — route capability checks, quote lookups, and
         order-payload validation. Structurally incapable of creating a real
         shipment: this module does not import EurosenderAdapter.
         create_shipment, ORDER_CREATION_PATH, evaluate_and_create_shipment,
         or any fulfillment/order-service module, and never will —
         test_eurosender_verification.py enforces this with a structural
         source-inspection test plus an HTTP-intercept test that fails loudly
         if any request ever targets POST /v1/orders (the exact mutating
         path — /v1/orders/validate_creation is a different, non-mutating
         endpoint and IS used here).
Process:
  Built after the 2026-09-11 Phase 18 incident: a verification task called
  evaluate_and_create_shipment() (the real fulfillment orchestrator) instead
  of a read-only helper, causing 5 unintended live POST /v1/orders attempts.
  This module exists so no future verification task has to hand-roll adapter
  calls or guess which function is "safe" — everything exported here is
  provably non-mutating.
    verify_countries()        -> GET  /v1/countries
    verify_route_quote(...)   -> POST /v1/quotes
    verify_order_payload(...) -> POST /v1/orders/validate_creation
Data Imported/Modified: none — pure read/validate wrappers, no persistence.
Missing Data Delegation: n/a.
Last Updated: 2026-09-11
"""
from __future__ import annotations

from typing import Optional

from services.shipping.eurosender_adapter import EurosenderAdapter


async def verify_countries(adapter: Optional[EurosenderAdapter] = None) -> dict:
    """GET /v1/countries — no side effects, no order involved."""
    client = adapter or EurosenderAdapter()
    return await client.get_countries()


async def verify_route_quote(
    pickup_address: dict,
    delivery_address: dict,
    packages: list[dict],
    payment_method: str = "credit",
    service_type: Optional[str] = None,
    currency_code: str = "EUR",
    adapter: Optional[EurosenderAdapter] = None,
) -> dict:
    """POST /v1/quotes — a stateless price lookup. Never creates an order,
    never returns a persistent identifier (Eurosender has no quoteId)."""
    client = adapter or EurosenderAdapter()
    return await client.get_quote(
        pickup_address=pickup_address,
        delivery_address=delivery_address,
        packages=packages,
        payment_method=payment_method,
        service_type=service_type,
        currency_code=currency_code,
    )


async def verify_order_payload(
    pickup_address: dict,
    delivery_address: dict,
    packages: list[dict],
    service_type: str,
    payment_method: str,
    order_contact: dict,
    pickup_contact: dict,
    delivery_contact: dict,
    customer_internal_reference: str,
    label_format: str = "pdf",
    currency_code: str = "EUR",
    adapter: Optional[EurosenderAdapter] = None,
) -> dict:
    """POST /v1/orders/validate_creation — validates a would-be order
    WITHOUT creating one. This is a genuinely different endpoint from
    POST /v1/orders (real order creation), despite the similar path prefix —
    see EurosenderAdapter.ORDER_CREATION_PATH, which this module never
    references.
    """
    client = adapter or EurosenderAdapter()
    return await client.validate_creation(
        pickup_address=pickup_address,
        delivery_address=delivery_address,
        packages=packages,
        service_type=service_type,
        payment_method=payment_method,
        order_contact=order_contact,
        pickup_contact=pickup_contact,
        delivery_contact=delivery_contact,
        customer_internal_reference=customer_internal_reference,
        label_format=label_format,
        currency_code=currency_code,
    )
