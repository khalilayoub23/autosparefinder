"""
Script: eurosender_sandbox_probe_test.py
Purpose: Phase 17 of the Eurosender sandbox-implementation authorization —
         attempt ONLY harmless, read-only sandbox API validation:
           GET /v1/countries
           POST /v1/quotes (synthetic test payload, no order created)
         Never creates a shipment, never calls production, never uses a real
         customer/supplier address.
Process:
  Run: docker exec autospare_backend python3 /app/devtests/eurosender_sandbox_probe_test.py
  If EUROSENDER_API_KEY is not set, this script reports BLOCKED and exits
  without attempting any network call — it does NOT fabricate a response or
  simulate what the API "would" return.
Data Imported/Modified: none.
Data Sources: https://sandbox-api.eurosender.com (only if credentials exist).
Missing Data Delegation: no credentials -> BLOCKED, printed verbatim, exit 0
  (this is an expected/documented outcome, not a script failure).
Last Updated: 2026-09-08
"""
import asyncio
import json
import sys

from services.shipping import eurosender_config
from services.shipping.eurosender_adapter import (
    EurosenderAdapter,
    EurosenderNotConfigured,
    EurosenderProductionBlocked,
)

SYNTHETIC_PACKAGE = {
    "parcelId": "SANDBOX-PROBE-1",
    "quantity": 1,
    "weight": 1.0,
    "length": 20,
    "width": 15,
    "height": 10,
    "content": "sandbox contract test — synthetic payload, not a real shipment",
    "value": 10,
}
SYNTHETIC_PICKUP = {"country": "IE", "zip": "D01", "city": "Dublin", "street": "Sandbox Test Street 1"}
SYNTHETIC_DELIVERY = {"country": "IL", "zip": "6100000", "city": "Tel Aviv", "street": "Sandbox Test Street 2"}


async def main() -> int:
    print("=" * 70)
    print("EUROSENDER SANDBOX CONTRACT PROBE — Phase 17")
    print("=" * 70)
    print(f"EUROSENDER_SANDBOX (must be True): {eurosender_config.sandbox_mode()}")
    print(f"Resolved base URL: {eurosender_config.base_url()}")

    if not eurosender_config.api_key():
        print()
        print("RESULT: BLOCKED")
        print("Reason: EUROSENDER_API_KEY is not set in this environment.")
        print("No network call was attempted — this script does not fabricate")
        print("or simulate a sandbox API response in the absence of real")
        print("credentials (per the sandbox-implementation authorization).")
        print()
        print("To unblock: obtain a Eurosender sandbox account at")
        print("integrators.eurosender.com and set EUROSENDER_API_KEY in .env,")
        print("then re-run this probe.")
        return 0

    adapter = EurosenderAdapter()
    results = {}

    print()
    print("--- GET /v1/countries ---")
    try:
        countries = await adapter.get_countries()
        results["countries"] = countries
        print(json.dumps(countries, indent=2)[:2000])
    except EurosenderProductionBlocked as exc:
        print(f"BLOCKED (refused production URL): {exc}")
        return 0
    except Exception as exc:
        print(f"FAILED: {exc}")
        results["countries_error"] = str(exc)

    print()
    print("--- POST /v1/quotes (synthetic payload) ---")
    try:
        quote = await adapter.get_quote(
            pickup_address=SYNTHETIC_PICKUP,
            delivery_address=SYNTHETIC_DELIVERY,
            packages=[SYNTHETIC_PACKAGE],
        )
        results["quote"] = quote
        print(json.dumps(quote, indent=2)[:2000])
    except EurosenderProductionBlocked as exc:
        print(f"BLOCKED (refused production URL): {exc}")
        return 0
    except Exception as exc:
        print(f"FAILED: {exc}")
        results["quote_error"] = str(exc)

    print()
    print("RESULT: SANDBOX PROBE COMPLETE — see output above for exact API responses.")
    print("No shipment was created. No order-creation endpoint was called.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
