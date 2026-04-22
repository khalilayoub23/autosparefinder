from types import SimpleNamespace
from uuid import uuid4
import pytest

from routes.orders import _build_existing_order_fingerprint
from routes.payments import _allow_simulated_payments, _build_tracking_url_from_number
from routes.utils import (
    trigger_supplier_refund,
    _normalize_supplier_spend_provider,
    _resolve_supplier_spend_provider,
    _convert_ils_to_minor_units,
    _extract_issuing_decline_reason,
    _compute_test_topup_amount_minor,
    _build_topup_source_candidates,
)


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeAsyncDB:
    def __init__(self, supplier_payments):
        self._supplier_payments = supplier_payments

    async def execute(self, _query):
        return _FakeScalarResult(self._supplier_payments)


def test_allow_simulated_payments_flag_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ALLOW_SIMULATED_PAYMENTS", raising=False)
    assert _allow_simulated_payments() is False


def test_allow_simulated_payments_flag_enabled(monkeypatch):
    monkeypatch.setenv("ALLOW_SIMULATED_PAYMENTS", "true")
    assert _allow_simulated_payments() is True


def test_normalize_supplier_spend_provider_defaults_to_payments():
    assert _normalize_supplier_spend_provider(None) == "payments"
    assert _normalize_supplier_spend_provider("invalid") == "payments"


def test_normalize_supplier_spend_provider_accepts_issuing():
    assert _normalize_supplier_spend_provider("issuing") == "issuing"


def test_resolve_supplier_spend_provider_prefers_supplier_credentials(monkeypatch):
    monkeypatch.setenv("SUPPLIER_SPEND_PROVIDER", "payments")
    creds = {"supplier_spend_provider": "issuing"}
    assert _resolve_supplier_spend_provider(creds) == "issuing"


def test_resolve_supplier_spend_provider_defaults_to_issuing(monkeypatch):
    monkeypatch.delenv("SUPPLIER_SPEND_PROVIDER", raising=False)
    assert _resolve_supplier_spend_provider(None) == "issuing"


def test_resolve_supplier_spend_provider_uses_env_when_set(monkeypatch):
    monkeypatch.setenv("SUPPLIER_SPEND_PROVIDER", "payments")
    assert _resolve_supplier_spend_provider(None) == "payments"


def test_convert_ils_to_minor_units_ils():
    minor, ccy, major = _convert_ils_to_minor_units(1152.61, "ils", 3.65)
    assert minor == 115261
    assert ccy == "ils"
    assert major == 1152.61


def test_convert_ils_to_minor_units_usd():
    minor, ccy, major = _convert_ils_to_minor_units(365.0, "usd", 3.65)
    assert minor == 10000
    assert ccy == "usd"
    assert major == 100.0


def test_extract_issuing_decline_reason_from_request_history():
    auth = {
        "request_history": [
            {"reason": "insufficient_funds"}
        ]
    }
    assert _extract_issuing_decline_reason(auth) == "insufficient_funds"


def test_extract_issuing_decline_reason_empty_when_missing():
    assert _extract_issuing_decline_reason({}) == ""


def test_compute_test_topup_amount_minor_applies_buffer_with_cap():
    assert _compute_test_topup_amount_minor(10000, 5000, 12000) == 12000


def test_compute_test_topup_amount_minor_without_cap_hit():
    assert _compute_test_topup_amount_minor(10000, 5000, 50000) == 15000


def test_build_topup_source_candidates_uses_default_fallback():
    assert _build_topup_source_candidates(None) == ["btok_us_verified", "tok_visa_debit"]


def test_build_topup_source_candidates_deduplicates():
    assert _build_topup_source_candidates("btok_us_verified") == ["btok_us_verified", "tok_visa_debit"]


def test_build_topup_source_candidates_prefers_configured_then_fallback():
    assert _build_topup_source_candidates("tok_custom") == ["tok_custom", "btok_us_verified", "tok_visa_debit"]


def test_build_tracking_url_prefers_explicit_url():
    url = _build_tracking_url_from_number("1Z999AA10123456784", "https://carrier.example/track/123")
    assert url == "https://carrier.example/track/123"


def test_build_tracking_url_infers_ups_from_tracking_number():
    url = _build_tracking_url_from_number("1Z999AA10123456784", None)
    assert "ups.com/track" in url


def test_build_existing_order_fingerprint_is_stable_for_same_payload():
    user_id = uuid4()
    order_a = SimpleNamespace(user_id=user_id, shipping_address={"street": "Herzl 55", "city": "Acre"})
    order_b = SimpleNamespace(user_id=user_id, shipping_address={"street": "  Herzl   55 ", "city": "acre"})
    sig = {str(uuid4()): 2, str(uuid4()): 1}

    fp_a = _build_existing_order_fingerprint(order_a, sig)
    fp_b = _build_existing_order_fingerprint(order_b, dict(sig))

    assert fp_a == fp_b


def test_build_existing_order_fingerprint_changes_when_items_change():
    user_id = uuid4()
    order = SimpleNamespace(user_id=user_id, shipping_address={"street": "Herzl 55", "city": "Acre"})
    key_1 = str(uuid4())
    key_2 = str(uuid4())

    fp_a = _build_existing_order_fingerprint(order, {key_1: 1})
    fp_b = _build_existing_order_fingerprint(order, {key_1: 1, key_2: 1})

    assert fp_a != fp_b


@pytest.mark.asyncio
async def test_trigger_supplier_refund_marks_simulated_refund(monkeypatch):
    monkeypatch.setenv("ALLOW_SIMULATED_SUPPLIER_PAYMENTS", "1")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)

    order = SimpleNamespace(id=uuid4(), order_number="ORD-1001", total_amount=200.0)
    supplier_payment = SimpleNamespace(
        id=uuid4(),
        supplier_name="Fake Supplier",
        status="paid",
        amount_ils=120.0,
        metadata_json={},
        provider="simulated",
        provider_payment_id="SIM-SP-ABC123",
        failure_reason=None,
    )
    db = _FakeAsyncDB([supplier_payment])

    summary = await trigger_supplier_refund(
        order=order,
        db=db,
        reason="customer_cancelled",
        customer_refund_amount_ils=50.0,
    )

    assert summary["processed"] == 1
    assert summary["refunded"] == 1
    assert summary["failed"] == 0
    assert supplier_payment.status == "cancelled"
    assert supplier_payment.metadata_json["supplier_refund_status"] == "simulated"
    assert supplier_payment.metadata_json["supplier_refund_amount_ils"] == 30.0


@pytest.mark.asyncio
async def test_trigger_supplier_refund_skips_unpaid_supplier_payment():
    order = SimpleNamespace(id=uuid4(), order_number="ORD-2001", total_amount=150.0)
    supplier_payment = SimpleNamespace(
        id=uuid4(),
        supplier_name="Supplier B",
        status="pending",
        amount_ils=80.0,
        metadata_json={},
        provider="stripe",
        provider_payment_id="pi_123",
        failure_reason=None,
    )
    db = _FakeAsyncDB([supplier_payment])

    summary = await trigger_supplier_refund(
        order=order,
        db=db,
        reason="manual_refund",
        customer_refund_amount_ils=80.0,
    )

    assert summary["processed"] == 1
    assert summary["refunded"] == 0
    assert summary["skipped"] == 1
    assert supplier_payment.status == "pending"


@pytest.mark.asyncio
async def test_trigger_supplier_refund_marks_issuing_as_manual_required(monkeypatch):
    monkeypatch.setenv("ALLOW_SIMULATED_SUPPLIER_PAYMENTS", "0")

    order = SimpleNamespace(id=uuid4(), order_number="ORD-3001", total_amount=200.0)
    supplier_payment = SimpleNamespace(
        id=uuid4(),
        supplier_name="Supplier Issuing",
        status="paid",
        amount_ils=120.0,
        metadata_json={"spend_provider": "issuing"},
        provider="stripe_issuing",
        provider_payment_id="iauth_123",
        failure_reason=None,
    )
    db = _FakeAsyncDB([supplier_payment])

    summary = await trigger_supplier_refund(
        order=order,
        db=db,
        reason="customer_cancelled",
        customer_refund_amount_ils=100.0,
    )

    assert summary["processed"] == 1
    assert summary["refunded"] == 0
    assert summary["failed"] == 1
    assert supplier_payment.metadata_json["supplier_refund_status"] == "manual_required"
