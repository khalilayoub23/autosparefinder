"""
Script: eurosender_adapter.py
Purpose: Minimal adapter around the Eurosender integrators API, SANDBOX ONLY.
         Built from the OpenAPI bundle (integrators.eurosender.com/_bundle/apis/
         index.yaml) and public guides/webhooks docs validated in the 2026-09-08
         sandbox-contract resolution gate. Every field name and requirement below
         is taken from that verified contract — nothing here is invented.
Process:
  1. Every public method validates its inputs (required package fields, HS
     codes) and preflight-checks configuration (API key present, sandbox URL
     only) BEFORE entering any @retry_with_backoff-decorated call — that
     decorator treats an exception with no `.status_code` as retry-eligible,
     so a data/config error must never reach it or it turns into a slow,
     pointless retry loop (in tests and in production alike).
  2. No quoteId exists in the real API — get_quote() is a stateless price
     lookup; create_shipment() always re-submits full shipment/parcel data.
  3. customerInternalReference is accepted by create_shipment() and echoed
     back by get_order() — this is the ONLY reconciliation handle available
     for a timed-out POST (see eurosender_timeout.py).
Data Imported/Modified: none directly — callers persist orders.eurosender_*
  columns; see eurosender_order_service.py.
Data Sources: https://integrators.eurosender.com (OpenAPI bundle + guides)
Missing Data Delegation: EUROSENDER_API_KEY absent -> every method raises
  EurosenderNotConfigured immediately, before any network call or retry.
Last Updated: 2026-09-08
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

import httpx

from resilience import retry_with_backoff
from services.shipping import eurosender_config

logger = logging.getLogger(__name__)

_SECRET_HEADER_RE = re.compile(r"(x-api-key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+", re.I)
_REQUIRED_PACKAGE_FIELDS = {"parcelId", "quantity", "weight", "length", "width", "height", "content", "value"}

# The ONE real order-creation endpoint in this adapter. Referenced ONLY
# inside create_shipment() below — added 2026-09-11 after a verification
# script mistakenly called evaluate_and_create_shipment() (which reaches
# this endpoint) instead of a read-only helper, causing 5 unintended live
# Sandbox order-creation attempts. Grep for ORDER_CREATION_PATH to
# forensically enumerate every call site that can mutate a real order —
# there must only ever be one (create_shipment). See eurosender_verification.py
# for the sanctioned read-only alternative and
# test_eurosender_verification.py for the structural/HTTP-intercept proof
# that the read-only path can never reach this constant.
ORDER_CREATION_PATH = "/v1/orders"


def _scrub(text: str) -> str:
    """Redact an x-api-key value out of any string before it is logged/raised."""
    return _SECRET_HEADER_RE.sub(r"\1<redacted>", text or "")


class WarningSeverity(str, Enum):
    """Classification of a Eurosender response `warnings[]` entry — added
    2026-09-11 (Phase 18) after live Sandbox verification (Phase 17) showed
    quote responses can carry real warnings alongside an otherwise-successful
    2xx. These are observations, not universal API contract failures:
      ACCOUNT        — about the calling account's state (e.g. sandbox
                        balance/B2B confirmation), not the shipment payload.
                        Never treated as a shipment-blocking condition here.
      PAYLOAD        — about a specific field in what WE submitted
                        (parameterPath is set) — actionable by us.
      INFORMATIONAL  — anything else; observed but not actionable.
    """
    ACCOUNT = "account"
    PAYLOAD = "payload"
    INFORMATIONAL = "informational"


# Known warning codes observed live (2026-09-11, Phase 17) against the
# Eurosender Sandbox. Account-state warnings are sandbox-account-specific —
# they must never be hardcoded as a universal shipment/production failure
# rule (owner instruction, Phase 18 Phase 5).
_KNOWN_WARNING_SEVERITY: dict[str, WarningSeverity] = {
    "insufficient-payment-balance": WarningSeverity.ACCOUNT,
    "b2b-not-confirmed": WarningSeverity.ACCOUNT,
}


@dataclass(frozen=True)
class QuoteWarning:
    code: str
    message: str
    parameter_path: str
    severity: WarningSeverity


def parse_warnings(response: dict) -> list[QuoteWarning]:
    """Extract and classify the `warnings[]` array from a Eurosender quote/
    order response. Never raises on a missing/malformed field — an absent
    `warnings` key returns an empty list, not an error.
    """
    raw = (response or {}).get("warnings") or []
    result: list[QuoteWarning] = []
    for w in raw:
        if not isinstance(w, dict):
            continue
        code = str(w.get("code") or "")
        param_path = str(w.get("parameterPath") or "")
        severity = _KNOWN_WARNING_SEVERITY.get(code)
        if severity is None:
            severity = WarningSeverity.PAYLOAD if param_path else WarningSeverity.INFORMATIONAL
        result.append(QuoteWarning(
            code=code,
            message=str(w.get("message") or ""),
            parameter_path=param_path,
            severity=severity,
        ))
    return result


class EurosenderNotConfigured(RuntimeError):
    """Raised when EUROSENDER_API_KEY is absent. Never call the API without it."""


class EurosenderProductionBlocked(RuntimeError):
    """Raised if anything ever tries to point this adapter at the production API."""


def _raise_for_status_safe(resp: "httpx.Response") -> None:
    try:
        httpx.Response.raise_for_status(resp)
    except httpx.HTTPStatusError as exc:
        raise httpx.HTTPStatusError(_scrub(str(exc)), request=exc.request, response=exc.response) from None


def _validate_packages(packages: list[dict]) -> None:
    for pkg in packages:
        missing = _REQUIRED_PACKAGE_FIELDS - set(pkg.keys())
        if missing:
            raise ValueError(f"Package missing required Eurosender fields: {sorted(missing)}")


class EurosenderAdapter:
    """Sandbox-only Eurosender client. Never targets production."""

    def __init__(self, timeout_s: float = 20.0):
        self._timeout_s = timeout_s

    def _preflight(self) -> tuple[str, dict]:
        """Resolve (base_url, headers), raising immediately — and WITHOUT
        entering any retry-decorated method — if credentials are missing or
        the resolved URL is production. Called at the top of every public
        method, before @retry_with_backoff-wrapped network I/O.
        """
        url = eurosender_config.base_url()
        if url.startswith(eurosender_config.production_url()):
            raise EurosenderProductionBlocked(
                "This sandbox-only adapter refused a call to the Eurosender "
                "PRODUCTION API. Production execution is out of scope for this "
                "implementation — see the 2026-09-08 sandbox-implementation "
                "authorization."
            )
        key = eurosender_config.api_key()
        if not key:
            raise EurosenderNotConfigured(
                "EUROSENDER_API_KEY is not set — no sandbox credentials available. "
                "This adapter will not fabricate a request without real credentials."
            )
        return url, {"x-api-key": key, "Content-Type": "application/json"}

    @retry_with_backoff(max_retries=2, retry_on=(429, 503, 504), skip_on=(401, 403, 404))
    async def _do_request(self, method: str, url: str, headers: dict, json_body: Optional[dict]) -> dict:
        """The ONLY retry-decorated network call in this adapter for
        idempotent GET/read-like operations. Never call directly with
        side-effecting semantics — create_shipment() uses its own
        zero-retry variant below.
        """
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            resp = await client.request(method, url, headers=headers, json=json_body)
        _raise_for_status_safe(resp)
        return {} if not resp.content else resp.json()

    @retry_with_backoff(max_retries=0, skip_on=(400, 401, 403, 404, 409, 422))
    async def _do_request_no_retry(self, method: str, url: str, headers: dict, json_body: Optional[dict]) -> dict:
        """CRITICAL: max_retries=0. Used only for POST /v1/orders. An
        ambiguous network failure here must be handled by the caller's
        timeout state machine (eurosender_timeout.py), NEVER by blindly
        re-POSTing — a retry at this layer could create a second real
        shipment. See eurosender_timeout.py for the safe pattern.
        """
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            resp = await client.request(method, url, headers=headers, json=json_body)
        _raise_for_status_safe(resp)
        return {} if not resp.content else resp.json()

    # ------------------------------------------------------------------
    # GET /v1/countries — route capability check (Area 1 of the sandbox gate)
    # ------------------------------------------------------------------
    async def get_countries(self) -> dict:
        url, headers = self._preflight()
        return await self._do_request("GET", f"{url}/v1/countries", headers, None)

    # ------------------------------------------------------------------
    # POST /v1/quotes — stateless, no quoteId returned. Must be re-called
    # at order-creation time; never assume a checkout-time quote is valid
    # later (Area 2 / Area 7 of the sandbox gate).
    # ------------------------------------------------------------------
    async def get_quote(
        self,
        pickup_address: dict,
        delivery_address: dict,
        packages: list[dict],
        payment_method: str = "credit",
        service_type: Optional[str] = None,
        currency_code: str = "EUR",
    ) -> dict:
        """Every package dict MUST already contain the full required set:
        parcelId, quantity, weight, length, width, height, content, value.
        This method does not fill in any missing field — callers (dimension/
        weight/declared-value policy modules) are responsible for that.
        """
        _validate_packages(packages)
        url, headers = self._preflight()

        body: dict[str, Any] = {
            "shipment": {"pickupAddress": pickup_address, "deliveryAddress": delivery_address},
            "parcels": {"packages": packages},
            "paymentMethod": payment_method,
            "currencyCode": currency_code,
        }
        if service_type:
            body["serviceType"] = service_type
        return await self._do_request("POST", f"{url}/v1/quotes", headers, body)

    # ------------------------------------------------------------------
    # POST /v1/orders — always re-submits full data; customerInternalReference
    # is the ONLY handle for timeout reconciliation (Area 6 / Area 9).
    # ------------------------------------------------------------------
    async def create_shipment(
        self,
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
        courier_id: Optional[int] = None,
        comment: Optional[str] = None,
    ) -> dict:
        """Uses the ZERO-retry network path (_do_request_no_retry) — see its
        docstring. An ambiguous failure here must be surfaced to the caller's
        timeout state machine, never silently retried.
        """
        _validate_packages(packages)
        url, headers = self._preflight()

        body: dict[str, Any] = {
            "shipment": {
                "pickupAddress": pickup_address,
                "deliveryAddress": delivery_address,
                "pickupContact": pickup_contact,
                "deliveryContact": delivery_contact,
                "addOns": [],
            },
            "parcels": {"packages": packages},
            "serviceType": service_type,
            "paymentMethod": payment_method,
            "currencyCode": currency_code,
            "orderContact": order_contact,
            "labelFormat": label_format,
            "customerInternalReference": customer_internal_reference,
        }
        if courier_id is not None:
            body["courierId"] = courier_id
        if comment:
            body["comment"] = comment
        return await self._do_request_no_retry("POST", f"{url}{ORDER_CREATION_PATH}", headers, body)

    # ------------------------------------------------------------------
    # POST /v1/orders/validate_creation — confirmed live (2026-09-11, Phase 17)
    # to exist and validate the exact same payload shape as create_shipment()
    # WITHOUT creating a real order. Unlike create_shipment(), this uses the
    # RETRYABLE network path (_do_request): validation has no order-creation
    # side effect, so a transient 429/503/504 here cannot duplicate anything.
    # A 4xx (including 422 — Eurosender's normal "this would fail" response,
    # observed live) is a definite validation result, never retried, since
    # 422 is not in _do_request's retry_on set.
    # ------------------------------------------------------------------
    async def validate_creation(
        self,
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
        courier_id: Optional[int] = None,
        comment: Optional[str] = None,
    ) -> dict:
        """Never creates a real order — this method MUST NOT be pointed at
        /v1/orders. Returns the raw sanitized JSON body (2xx success shape,
        or the {status, violations[], detail, type, title} 422 shape observed
        live) for the caller to inspect.
        """
        _validate_packages(packages)
        url, headers = self._preflight()

        body: dict[str, Any] = {
            "shipment": {
                "pickupAddress": pickup_address,
                "deliveryAddress": delivery_address,
                "pickupContact": pickup_contact,
                "deliveryContact": delivery_contact,
                "addOns": [],
            },
            "parcels": {"packages": packages},
            "serviceType": service_type,
            "paymentMethod": payment_method,
            "currencyCode": currency_code,
            "orderContact": order_contact,
            "labelFormat": label_format,
            "customerInternalReference": customer_internal_reference,
        }
        if courier_id is not None:
            body["courierId"] = courier_id
        if comment:
            body["comment"] = comment
        return await self._do_request("POST", f"{url}/v1/orders/validate_creation", headers, body)

    # ------------------------------------------------------------------
    # GET /v1/orders/{orderCode} — used both for normal status reads AND for
    # timeout reconciliation via the echoed customerInternalReference.
    # ------------------------------------------------------------------
    async def get_order(self, order_code: str) -> dict:
        url, headers = self._preflight()
        return await self._do_request("GET", f"{url}/v1/orders/{order_code}", headers, None)

    async def get_tracking(self, order_code: str) -> dict:
        url, headers = self._preflight()
        return await self._do_request("GET", f"{url}/v1/orders/{order_code}/tracking", headers, None)

    async def get_label(self, order_code: str) -> dict:
        """Convenience wrapper — the label link is also present on the
        create_shipment() response and the order_label_ready webhook payload;
        this exists for the case a caller only has an orderCode.
        """
        order = await self.get_order(order_code)
        return {"labelLink": order.get("labelLink")}

    async def cancel_shipment(self, order_code: str) -> dict:
        """DELETE /v1/orders/{orderCode} — verified 2026-09-11 against the
        official OpenAPI spec after the previous implementation
        (POST .../cancel) returned a raw HTML 404 live against real order
        325493-26. Confirmed contract: DELETE method, no request body,
        204 No Content on success.
        """
        url, headers = self._preflight()
        return await self._do_request("DELETE", f"{url}/v1/orders/{order_code}", headers, None)

    # ------------------------------------------------------------------
    # POST /v1/proforma — required for Israel (non-EU) customs. HS codes,
    # declared value, and country of origin must come from
    # eurosender_hs_codes.py / eurosender_declared_value.py /
    # eurosender_proforma.py — never invented here.
    # ------------------------------------------------------------------
    async def create_proforma(
        self,
        order_code: str,
        items: list[dict],
        shipper: dict,
        receiver: dict,
        reason: str = "commercial",
        currency_code: str = "EUR",
    ) -> dict:
        """Body verified 2026-09-11 against the official OpenAPI spec
        (ProformaRequest schema) after a live Sandbox 400 on real order
        325493-26 ("Extra attributes are not allowed: unitValue,
        countryOfOrigin"). The request requires orderCode, shipper, receiver
        (each a full ProformaContactPersonRequest — see
        eurosender_proforma.build_proforma_contact()), items[], and reason
        (enum '1'..'6', default '2' = commercial). Each item dict must
        already carry: description, country, quantity, weight, value
        (integer), hsCode — see eurosender_proforma.build_proforma_items().
        """
        for item in items:
            if not item.get("hsCode"):
                raise ValueError(
                    f"Refusing to submit a proforma item without an HS code: {item!r}. "
                    "Route this item to manual_review instead of guessing a code."
                )
        url, headers = self._preflight()
        body = {
            "orderCode": order_code,
            "shipper": shipper,
            "receiver": receiver,
            "items": items,
            "reason": reason,
            "currency": currency_code,
        }
        return await self._do_request("POST", f"{url}/v1/proforma", headers, body)
