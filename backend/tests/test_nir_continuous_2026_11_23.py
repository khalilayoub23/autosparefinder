"""NIR 2026-11-23: no send-time window on supplier e-mail; contact discovery rules; registered office is not a pickup."""
import asyncio, inspect, re
from datetime import datetime, timezone

import supplier_outreach as out
from services import nir_contact_discovery as ncd


def test_outreach_send_path_has_no_time_window():
    src = inspect.getsource(out)
    for needle in ("_notify_window_open", "_wa_send_quiet", ".hour", "quiet", "ZoneInfo", "Jerusalem", "local_schedule"):
        assert needle not in src, needle


def test_plan_prepares_at_any_hour(monkeypatch):
    import supplier_qualification as Q
    class S:
        rows = []
        async def for_supplier(self, sid): return []
        async def insert(self, row): self.rows.append(row); return True
    q = Q.ensure_schema(Q.new_record("s1", datetime.now(timezone.utc)), datetime.now(timezone.utc))
    Q.set_requirement(q, "contacts", Q.VERIFIED, evidence_kind="official_policy_page", source="https://x.com/c", note="n",
                      details={"contacts": [{"address": "info@x.com", "status": Q.VERIFIED, "source": "https://x.com/c"}]},
                      now=datetime.now(timezone.utc))
    for hour in (1, 3, 6, 8, 22, 23):                       # outside 09:00-21:00 Israel and UTC alike
        now = datetime(2026, 11, 24, hour, 0, tzinfo=timezone.utc)
        s = S()
        p = asyncio.run(out.plan(s, "s1", q, now=now, purposes=["israel_shipping"]))
        assert p["action"] == "prepared", (hour, p)


def test_pick_contacts_generic_same_domain_only():
    html = "info@acme.com privacy@acme.com john.smith@acme.com sales@other.com parts@shop.acme.com careers@acme.com"
    assert ncd.pick_contacts(html, "acme.com") == ["info@acme.com", "parts@shop.acme.com"]


def test_registered_office_never_read_as_pickup():
    from services.shipping.eurosender_fulfillment import _extract_pickup_from_shipping_info as f
    si = {"registered_office": {"street": "The Heritage Building, Stourbridge Road", "zip": "WV15 6AP", "country": "GB"}}
    assert f(si) == (None, None)


def test_ebay_relisting_has_no_env_switch_and_guard_is_code_level():
    """EBAY_REFRESH_ALLOW_RELISTING is read by no code or compose file; the guard is the identity check itself."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    hits = [str(p) for p in root.rglob("*.py") if "tests" not in p.parts and "EBAY_REFRESH_ALLOW_RELISTING" in p.read_text(errors="ignore")]
    assert hits == []
    from services import ebay_listing_identity as eli, ebay_price_sync as eps
    assert hasattr(eli, "check_refresh") and "_guard.check_refresh" in inspect.getsource(eps)


def test_software_vendor_names_are_skipped():
    assert ncd._NON_SUPPLIER_NAME.search("inventory source | #1 dropshipping automation & supplier integration software")
    assert not ncd._NON_SUPPLIER_NAME.search("Mann Filter")


def test_robot_or_older_mail_is_not_a_reply():
    from datetime import timedelta
    from email_agent.supplier_routing import _plausible_reply
    sent = datetime(2026, 11, 19, 23, 45, tzinfo=timezone.utc)
    o = {"sent_at": sent}
    assert not _plausible_reply({"sender_email": "donotreply@sngbarratt.com", "received_at": sent + timedelta(days=1)}, o)
    assert not _plausible_reply({"sender_email": "simon@sngbarratt.com", "received_at": sent - timedelta(days=60)}, o)
    assert _plausible_reply({"sender_email": "simon@sngbarratt.com", "received_at": sent + timedelta(hours=3)}, o)
