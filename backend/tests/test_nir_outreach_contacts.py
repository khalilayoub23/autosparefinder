"""NIR outreach: a placeholder / truncated address is never a contact; every candidate is considered (2026-11-19)."""
import inspect

import pytest

import supplier_outreach as so
import supplier_qualification as q


@pytest.mark.parametrize("addr", ["info@yourwebsite.com", "name@example.com", "a@example.co.uk", "management@autoworks.co.i", "x@domain.com", "", None, "no-at-sign"])
def test_placeholder_or_broken_addresses_are_not_contacts(addr):
    assert not q.valid_email(addr)


@pytest.mark.parametrize("addr", ["sales@exoracing.co.uk", "ceo@1aauto.com", "info@motorstate.com"])
def test_real_looking_addresses_pass(addr):
    assert q.valid_email(addr)


def test_verified_contacts_drops_a_placeholder_even_when_marked_verified():
    rec = q.new_record("sup-1")
    rec["requirements"]["contacts"]["details"] = {"contacts": [{"address": "info@yourwebsite.com", "status": q.VERIFIED, "source": "https://x"},
                                                                {"address": "sales@real-shop.com", "status": q.VERIFIED, "source": "https://x"}]}
    assert [c["address"] for c in q.verified_contacts(rec)] == ["sales@real-shop.com"]


def test_candidate_query_is_not_capped_at_50():
    assert inspect.signature(so._candidates).parameters["limit"].default >= 200
