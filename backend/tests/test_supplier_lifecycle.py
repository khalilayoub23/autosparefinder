"""Supplier lifecycle across NIR, BOAZ and the Email Agent (owner task 2026-10-27).

Everything here is offline: in-memory stores, fake Gmail clients, stub supplier connectors. No real email,
no supplier order, no customer charge, no supplier payment. External boundaries are SIMULATED.
"""
import base64
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_identity
import boaz_tasks as bt
import procurement as pr
import supplier_authorization as au
import supplier_outreach as so
import supplier_qualification as q
from email_agent import supplier_routing as sr
from email_agent.gmail_client import (GmailError, OwnerApproval, SendProhibited, SupplierInquiryAuthorization,
                                      assert_allowed, GmailClient)
from email_agent.store import InMemoryStore

UTC = timezone.utc
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
BACKEND = Path(__file__).resolve().parent.parent


# ═══ helpers ════════════════════════════════════════════════════════════════════════════════════════
def verified_record(sid="s1", contact="sales@parts.example.com", now=NOW):
    rec = q.new_record(sid, now)
    for k in q.REQUIREMENTS:
        q.set_requirement(rec, k, q.VERIFIED, evidence_kind="official_documentation",
                          source=f"https://parts.example.com/{k}", note=f"{k} documented", now=now,
                          details={"data_timestamp": now.isoformat()} if k == "availability" else {})
    rec["requirements"]["contacts"]["details"] = {"contacts": [
        {"address": contact, "kind": "sales", "status": q.VERIFIED, "source": "https://parts.example.com/contact"}]}
    return rec


def gap_record(sid="s1", contact="sales@parts.example.com"):
    rec = q.new_record(sid, NOW)
    q.set_requirement(rec, "contacts", q.VERIFIED, evidence_kind="official_policy_page",
                      source="https://parts.example.com/contact", note="contact page lists sales address", now=NOW,
                      details={"contacts": [{"address": contact, "status": q.VERIFIED,
                                             "source": "https://parts.example.com/contact"}]})
    return rec


class FakeGmail:
    """Drafts + sends in memory. Records every call; send only through the supplier-inquiry method."""

    def __init__(self, tamper=False, fail_send=False):
        self.drafts, self.sent, self.calls, self.tamper, self.fail_send = {}, [], [], tamper, fail_send

    def create_new_draft(self, raw, thread_id=None):
        did = f"d{len(self.drafts) + 1}"
        self.drafts[did] = (raw, thread_id)
        self.calls.append(("create", did))
        return {"id": did}

    def get_draft(self, did):
        import email
        raw, tid = self.drafts[did]
        msg = email.message_from_bytes(base64.urlsafe_b64decode(raw))
        body = msg.get_payload(decode=True).decode()
        to = "attacker@evil.example" if self.tamper else msg["To"]
        return {"id": did, "message": {"id": "m-" + did, "threadId": tid or "t-" + did, "labelIds": ["DRAFT"],
                "payload": {"mimeType": "text/plain", "headers": [{"name": "To", "value": to},
                            {"name": "From", "value": msg["From"]}, {"name": "Subject", "value": msg["Subject"]}],
                            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}}}}

    def send_supplier_inquiry(self, auth):
        assert isinstance(auth, SupplierInquiryAuthorization)
        self.calls.append(("send", auth.draft_id))
        if self.fail_send:
            raise GmailError("transient", "timeout")
        self.sent.append(auth.draft_id)
        return {"id": "sent-" + auth.draft_id, "threadId": "thr-" + auth.draft_id, "labelIds": ["SENT"]}


@pytest.fixture
def outreach_on(monkeypatch):
    monkeypatch.setenv("NIR_OUTREACH_ENABLED", "1")


async def standing_grant(store):
    return await au.grant(store, domain="supplier_message", scope="routine_inquiry", entity_type="policy",
                          entity_id="*", approved_by="owner", source="test", standing=True)


# ═══ 1. authorization boundaries ═══════════════════════════════════════════════════════════════════
async def test_each_domain_is_independent():
    st = au.InMemoryAuthStore()
    await standing_grant(st)
    await au.grant(st, domain="supplier_activation", scope="activate", entity_type="supplier", entity_id="s1",
                   approved_by="owner", source="test", terms={"qualification": "integration_ready"})
    # supplier messaging + activation do NOT authorize a purchase order or a payment
    for dom, scope in (("purchase_order", "create_po"), ("supplier_payment", "pay_supplier"),
                       ("commercial_commitment", "accept_terms")):
        with pytest.raises(au.AuthorizationError):
            await au.require(st, dom, "supplier", "s1", scope=scope, terms={"x": 1})
    # a messaging permission is not an activation permission for another supplier
    with pytest.raises(au.AuthorizationError):
        await au.require(st, "supplier_activation", "supplier", "s2", scope="activate", terms={"qualification": "x"})


async def test_standing_grants_only_for_routine_inquiries_and_only_from_the_owner():
    st = au.InMemoryAuthStore()
    for dom, scope in (("purchase_order", "create_po"), ("supplier_payment", "pay_supplier"),
                       ("supplier_activation", "activate")):
        with pytest.raises(ValueError):
            await au.grant(st, domain=dom, scope=scope, entity_type="policy", entity_id="*", approved_by="o",
                           source="test", standing=True, terms={"a": 1})
    for src in ("email", "agent", "model", "task"):
        with pytest.raises(ValueError):
            await au.grant(st, domain="supplier_message", scope="routine_inquiry", entity_type="policy",
                           entity_id="*", approved_by="o", source=src, standing=True)


async def test_terms_bound_one_shot_and_expiry():
    st = au.InMemoryAuthStore()
    terms = {"order": "A", "qty": 1, "cost": "10.00"}
    a = await au.grant(st, domain="purchase_order", scope="create_po", entity_type="procurement", entity_id="p1",
                       approved_by="o", source="test", terms=terms, now=NOW)
    with pytest.raises(au.AuthorizationError):        # changed quantity = different terms
        await au.require(st, "purchase_order", "procurement", "p1", scope="create_po",
                         terms={**terms, "qty": 2}, now=NOW)
    with pytest.raises(au.AuthorizationError):        # approval for p1 cannot authorize p2
        await au.require(st, "purchase_order", "procurement", "p2", scope="create_po", terms=terms, now=NOW)
    got = await au.require(st, "purchase_order", "procurement", "p1", scope="create_po", terms=terms, now=NOW)
    assert got.id == a.id
    await au.consume(st, got, "used", now=NOW)
    with pytest.raises(au.AuthorizationError):        # one-shot: cannot be replayed
        await au.require(st, "purchase_order", "procurement", "p1", scope="create_po", terms=terms, now=NOW)
    b = await au.grant(st, domain="supplier_payment", scope="pay_supplier", entity_type="procurement",
                       entity_id="p1", approved_by="o", source="test", terms=terms, now=NOW)
    with pytest.raises(au.AuthorizationError):        # expired (48 h default)
        await au.require(st, "supplier_payment", "procurement", "p1", scope="pay_supplier", terms=terms,
                         now=NOW + timedelta(hours=49))
    await au.revoke(st, b.id, "test")
    with pytest.raises(au.AuthorizationError):
        await au.require(st, "supplier_payment", "procurement", "p1", scope="pay_supplier", terms=terms, now=NOW)


# ═══ 2. BOAZ task ownership + execution ════════════════════════════════════════════════════════════
def test_canonical_identity_and_alias_lookup():
    assert agent_identity.canonical("pricing_agent") == "BOAZ" == agent_identity.canonical("supplier_manager_agent")
    assert {"BOAZ", "pricing_agent"} <= agent_identity.aliases("BOAZ")
    assert "NIR" not in agent_identity.aliases("BOAZ")
    assert agent_identity.aliases("rex") == frozenset({"rex"})            # unknown agents match exactly
    src = (BACKEND / "agent_todo_utils.py").read_text()
    assert "assigned_to_agent = ANY(CAST(:agent_names AS varchar[]))" in src
    assert 'get_active_agent_todos(db, "BOAZ")' in (BACKEND / "BACKEND_AI_AGENTS.py").read_text()


def _todo(owner, kind=None, category="general", **art):
    return {"id": str(uuid.uuid4()), "assigned_to_agent": owner, "status": "not_started", "title": "t",
            "category": category, "artifacts": {**({"kind": kind} if kind else {}), **art}, "metrics": {}}


async def test_boaz_claims_only_its_own_tasks_and_never_fakes_completion():
    todos = [_todo("BOAZ", "bulk_scan"), _todo("pricing_agent"), _todo("NIR", "bulk_scan"), _todo("LIOR")]
    repo = bt.InMemoryTaskRepo(todos)
    alerts = []

    async def notify(*a, **k):
        alerts.append(k.get("alert_key"))
        return {"ok": True}

    async def bulk():
        return {"status": "no_candidates", "created": 0}

    out = await bt.run_once(repo, {"bulk_scan": bulk}, notify=notify)
    assert out == {"claimed": 2, "completed": 1, "escalated": 1, "retry": 0, "failed": 0}
    assert todos[0]["status"] == "completed" and todos[0]["metrics"]["boaz"]["result"]["status"] == "no_candidates"
    assert todos[1]["status"] == "in_progress" and "no automated BOAZ handler" in todos[1]["progress_notes"]
    assert todos[2]["status"] == "not_started" and todos[3]["status"] == "not_started"   # NIR / LIOR untouched
    assert len(alerts) == 1
    again = await bt.run_once(repo, {"bulk_scan": bulk}, notify=notify)
    assert again["claimed"] == 0                                                         # no re-execution


async def test_boaz_failure_retries_then_escalates_never_completes():
    todos = [_todo("BOAZ", "bulk_scan")]
    repo = bt.InMemoryTaskRepo(todos)

    async def boom():
        return {"status": "error", "error": "db down"}

    for i in range(bt.MAX_ATTEMPTS):
        await bt.run_once(repo, {"bulk_scan": boom})
    assert todos[0]["status"] == "in_progress" and todos[0]["metrics"]["boaz"]["attempts"] == bt.MAX_ATTEMPTS
    assert "escalated" in todos[0]["progress_notes"]


async def test_boaz_email_task_links_supplier_and_escalates():
    store = InMemoryStore()
    rid = str(uuid.uuid4())
    store.rows["m1"] = {"id": rid, "gmail_message_id": "m1", "classification": "supplier", "created_at": NOW,
                        "context": {"supplier": {"status": "resolved", "supplier_id": "sup-123456"}}}
    linked = []

    async def link(sid, entry):
        linked.append(sid)

    todos = [_todo("BOAZ", category="email", email_ref=rid[:8])]
    out = await bt.run_once(bt.InMemoryTaskRepo(todos), {"email_store": store, "link_supplier_mail": link})
    assert linked == ["sup-123456"] and out["escalated"] == 1
    assert "owner's decision" in todos[0]["progress_notes"]


async def test_bulk_deal_detector_writes_to_the_pii_database(monkeypatch):
    import BACKEND_AI_AGENTS as agents
    import BACKEND_DATABASE_MODELS as models
    added = []

    class _Res:
        def __init__(self, rows):
            self._r = rows

        def all(self):
            return self._r

        def scalars(self):
            return SimpleNamespace(all=lambda: [])

    class _CatDB:
        async def execute(self, *a, **k):
            return _Res([(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 70.0, 60, 100.0)])

        def add(self, obj):                       # the old bug: writing ApprovalQueue into the catalog session
            raise AssertionError("ApprovalQueue added to the CATALOG session")

    class _PiiDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **k):
            return _Res([])

        def add(self, obj):
            added.append(obj)

        async def commit(self):
            pass

    monkeypatch.setattr(models, "pii_session_factory", lambda: _PiiDB())
    out = await agents.SupplierManagerAgent().detect_bulk_opportunities(_CatDB())
    assert out["created"] == 1 and type(added[0]).__name__ == "ApprovalQueue"


# ═══ 3. NIR qualification ══════════════════════════════════════════════════════════════════════════
def test_all_verified_is_integration_ready():
    c = q.classify(verified_record(), NOW)
    assert c["classification"] == q.INTEGRATION_READY and c["needs_owner_approval_to_activate"]


def test_no_api_is_a_separate_category():
    rec = verified_record()
    q.set_requirement(rec, "api", q.FAILED, evidence_kind="official_documentation", source="https://x/faq",
                      note="FAQ: no API offered", now=NOW)
    c = q.classify(rec, NOW)
    assert c["classification"] == q.NO_API and not c["integration_ready"]


def test_unverified_or_failed_israel_shipping_cannot_pass():
    rec = verified_record()
    rec["requirements"]["israel_shipping"].update(status=q.UNVERIFIED)
    assert q.classify(rec, NOW)["classification"] == q.NEEDS_INVESTIGATION
    q.set_requirement(rec, "israel_shipping", q.FAILED, evidence_kind="official_policy_page",
                      source="https://x/shipping", note="ships to EU only", now=NOW)
    assert q.classify(rec, NOW)["classification"] == q.NOT_QUALIFIED


def test_stale_inventory_is_not_current():
    rec = verified_record()
    rec["requirements"]["availability"]["details"]["data_timestamp"] = (NOW - timedelta(days=45)).isoformat()
    c = q.classify(rec, NOW)
    assert c["statuses"]["availability"] == q.UNVERIFIED and "stale" in c["notes"]["availability"]


def test_claims_and_keywords_never_verify_and_contradictions_surface():
    rec = q.from_sourcing_signals("s1", {"ships_il": True, "has_api": True, "http_ok": True}, "parts.example.com")
    assert all(r["status"] == q.UNVERIFIED for r in rec["requirements"].values())
    with pytest.raises(ValueError):
        q.set_requirement(rec, "api", q.VERIFIED, evidence_kind="supplier_claim", source="email", note="says so")
    with pytest.raises(ValueError):
        q.set_requirement(rec, "api", q.VERIFIED, evidence_kind="api_test", source=None, note="no source")
    q.set_requirement(rec, "israel_shipping", q.FAILED, evidence_kind="official_policy_page",
                      source="https://x/ship", note="no IL", now=NOW)
    r = q.record_supplier_claim(rec, "israel_shipping", "we ship to Israel", value=True, source_ref="email:abcd")
    assert r["contradiction"] and rec["requirements"]["israel_shipping"]["status"] == q.FAILED
    q.record_supplier_claim(rec, "catalog", "full catalog via FTP", value=True, source_ref="email:abcd")
    assert rec["requirements"]["catalog"]["status"] == q.UNVERIFIED
    assert rec["requirements"]["catalog"]["evidence_kind"] == "supplier_claim"
    for k, r in rec["requirements"].items():
        assert r["observed_at"] and r["note"]                        # every status has a time + explanation


def test_missing_contact_flagged_and_unverified_contacts_unusable():
    rec = q.new_record("s1", NOW)
    rec["requirements"]["contacts"]["details"] = {"contacts": [{"address": "guess@x.com", "status": q.UNVERIFIED}]}
    assert q.verified_contacts(rec) == [] and "contacts" in q.classify(rec, NOW)["unverified"]


# ═══ 4. NIR outreach (authorized, non-binding) ═════════════════════════════════════════════════════
def test_scope_template_bound_and_commitments_blocked():
    r = so.render_inquiry(["israel_shipping", "api_info"])
    so.check_scope(r["purposes"], r["body"])
    assert "does not constitute an order" in r["body"]
    with pytest.raises(so.OutreachRefused):
        so.check_scope(r["purposes"], r["body"] + "\nWe accept your terms and will place an order.")
    with pytest.raises(so.OutreachRefused):
        so.render_inquiry(["place_order"])
    for p in so.PURPOSES:                                            # no template itself carries a commitment
        so.check_scope([p], so.render_inquiry([p])["body"])
    for bad in ("We accept the offer", "purchase order 55", "exclusive deal", "our password is x"):
        assert any(pat.search(bad) for pat in so.COMMITMENT_PATTERNS)


async def test_no_verified_contact_no_outreach():
    st = so.InMemoryOutreachStore()
    assert (await so.plan(st, "s1", q.new_record("s1")))["reason"] == "no verified contact"
    assert st.rows == []


async def test_outreach_disabled_by_default(monkeypatch):
    monkeypatch.delenv("NIR_OUTREACH_ENABLED", raising=False)
    st, auth, gm = so.InMemoryOutreachStore(), au.InMemoryAuthStore(), FakeGmail()
    await standing_grant(auth)
    p = await so.plan(st, "s1", gap_record())
    r = await so.send_outreach(st, auth, gm, p, sender="biz@example.com")
    assert r["sent"] is False and gm.calls == []


async def test_outreach_needs_the_owner_permission(outreach_on):
    st, auth, gm = so.InMemoryOutreachStore(), au.InMemoryAuthStore(), FakeGmail()
    p = await so.plan(st, "s1", gap_record())
    r = await so.send_outreach(st, auth, gm, p, sender="biz@example.com")
    assert r["sent"] is False and "no approval" in r["reason"] and gm.calls == []
    assert st.rows[0]["state"] == "CANCELLED"


async def test_outreach_sends_once_records_and_dedupes(outreach_on):
    st, auth, gm = so.InMemoryOutreachStore(), au.InMemoryAuthStore(), FakeGmail()
    await standing_grant(auth)
    p = await so.plan(st, "s1", gap_record(), now=NOW)
    assert "israel_shipping" in p["purposes"] and "collaboration_interest" in p["purposes"]
    r = await so.send_outreach(st, auth, gm, p, sender="biz@example.com")
    row = st.rows[0]
    assert r["sent"] and gm.sent == ["d1"] and row["state"] == "AWAITING_REPLY" and row["authorization_id"]
    assert (await so.plan(st, "s1", gap_record(), now=NOW + timedelta(days=1)))["action"] == "skip"
    row["sent_at"] = NOW
    p2 = await so.plan(st, "s1", gap_record(), now=NOW + timedelta(days=8))
    assert p2["action"] == "prepared" and p2["followup"] and p2["outreach"]["gmail_thread_id"] == row["gmail_thread_id"]


async def test_followups_are_capped(outreach_on):
    st = so.InMemoryOutreachStore()
    for i in range(so.MAX_ATTEMPTS):
        st.rows.append({"id": str(uuid.uuid4()), "supplier_id": "s1", "state": "AWAITING_REPLY", "purposes": ["api_info"],
                        "recipient": "sales@parts.example.com", "idempotency_key": f"k{i}", "created_at": NOW,
                        "sent_at": NOW - timedelta(days=30)})
    assert "maximum" in (await so.plan(st, "s1", gap_record(), now=NOW))["reason"]


async def test_tampered_draft_is_not_sent(outreach_on):
    st, auth, gm = so.InMemoryOutreachStore(), au.InMemoryAuthStore(), FakeGmail(tamper=True)
    await standing_grant(auth)
    p = await so.plan(st, "s1", gap_record())
    r = await so.send_outreach(st, auth, gm, p, sender="biz@example.com")
    assert not r["sent"] and gm.sent == [] and st.rows[0]["state"] == "FAILED"


async def test_send_error_is_failed_not_retried(outreach_on):
    st, auth, gm = so.InMemoryOutreachStore(), au.InMemoryAuthStore(), FakeGmail(fail_send=True)
    await standing_grant(auth)
    p = await so.plan(st, "s1", gap_record())
    r = await so.send_outreach(st, auth, gm, p, sender="biz@example.com")
    assert not r["sent"] and st.rows[0]["state"] == "FAILED" and [c for c in gm.calls if c[0] == "send"] == [("send", "d1")]


async def test_store_refuses_sent_without_authorization():
    st = so.InMemoryOutreachStore()
    st.rows.append({"id": "o1", "supplier_id": "s1", "authorization_id": None})
    with pytest.raises(ValueError):
        await st.update("o1", sent_at=NOW)


def test_gmail_send_paths_are_separate():
    # general send: blocked without the owner approval; the inquiry path only reaches drafts/send
    for path in ("/drafts/send", "/messages/send"):
        with pytest.raises(SendProhibited):
            assert_allowed("POST", path)
    assert_allowed("POST", "/drafts/send", supplier_inquiry=True)
    for path in ("/messages/send", "/messages/x/modify", "/drafts"):
        with pytest.raises(SendProhibited):
            assert_allowed("POST", path, supplier_inquiry=True)
    with pytest.raises(SendProhibited):
        assert_allowed("POST", "/drafts/send", owner_approved=True, supplier_inquiry=True)
    client = GmailClient.__new__(GmailClient)
    with pytest.raises(SendProhibited):
        client.send_supplier_inquiry(OwnerApproval("r", "d", "o", 1.0))        # an owner approval is not an inquiry auth
    with pytest.raises(SendProhibited):
        client.send_supplier_inquiry(SupplierInquiryAuthorization("o", "d", "", "x@y.z", "h"))


# ═══ 5. Gmail routing: supplier replies -> NIR, Spam/Junk ══════════════════════════════════════════
def _email_row(store, *, thread, sender, classification="unknown", folder="inbox"):
    rid = str(uuid.uuid4())
    mid = "m" + rid[:6]
    store.rows[mid] = {"id": rid, "gmail_message_id": mid, "gmail_thread_id": thread, "sender_email": sender,
                       "status": "processed", "classification": classification, "created_at": NOW,
                       "task_ids": [], "source_folder": folder, "supplier_routed_at": None, "supplier_route": None,
                       "requires_human": False}
    return store.rows[mid]


class _Todos:
    def __init__(self):
        self.todos = []

    async def create(self, agent, title, description, priority, artifacts):
        self.todos.append({"agent": agent, "artifacts": artifacts})
        return f"task-{len(self.todos)}"


async def _sent_inquiry(ost, thread="thr-1", recipient="sales@parts.example.com", sid="s1"):
    row = {"id": str(uuid.uuid4()), "supplier_id": sid, "recipient": recipient, "purposes": ["api_info"],
           "state": "AWAITING_REPLY", "idempotency_key": str(uuid.uuid4()), "created_at": NOW,
           "gmail_thread_id": thread, "authorization_id": "a1", "sent_at": NOW}
    ost.rows.append(row)
    return row


async def test_reply_in_outreach_thread_reaches_nir_with_references():
    store, ost, todos = InMemoryStore(), so.InMemoryOutreachStore(), _Todos()
    o = await _sent_inquiry(ost)
    e = _email_row(store, thread="thr-1", sender="sales@parts.example.com", folder="spam")
    out = await sr.route_supplier_replies(store, ost, todos)
    assert out["to_nir"] == 1 and todos.todos[0]["agent"] == "NIR"
    art = todos.todos[0]["artifacts"]
    assert art["outreach_id"] == o["id"] and art["source_folder"] == "spam" and art["gmail_thread_id"] == "thr-1"
    assert o["state"] == "REPLY_RECEIVED" and o["reply_message_id"] == e["gmail_message_id"]
    assert e["supplier_route"]["owner"] == "NIR" and e["task_ids"] == ["task-1"]
    again = await sr.route_supplier_replies(store, ost, todos)            # repeated pass: nothing new
    assert sum(again.values()) == 0 and len(todos.todos) == 1


async def test_ambiguous_supplier_replies_become_exceptions():
    store, ost, todos = InMemoryStore(), so.InMemoryOutreachStore(), _Todos()
    await _sent_inquiry(ost, thread="thr-1")
    e1 = _email_row(store, thread="thr-1", sender="someone@other.example")           # wrong domain in thread
    await _sent_inquiry(ost, thread="thr-2", recipient="info@multi.example", sid="s2")
    await _sent_inquiry(ost, thread="thr-3", recipient="sales@multi.example", sid="s3")
    e2 = _email_row(store, thread="new-thread", sender="x@multi.example")            # two open inquiries
    out = await sr.route_supplier_replies(store, ost, todos)
    assert out["exceptions"] == 2 and todos.todos == []
    assert e1["requires_human"] and e2["requires_human"]


async def test_operational_supplier_mail_goes_to_boaz_unrelated_mail_unchanged():
    store, ost, todos = InMemoryStore(), so.InMemoryOutreachStore(), _Todos()
    a = _email_row(store, thread="t9", sender="notice@aliexpress.com", classification="aliexpress")
    b = _email_row(store, thread="t8", sender="news@shop.example", classification="newsletter_marketing")
    out = await sr.route_supplier_replies(store, ost, todos)
    assert out == {"to_nir": 0, "to_boaz": 1, "exceptions": 0, "other": 1}
    assert a["supplier_route"]["owner"] == "BOAZ" and b["supplier_route"] == {"owner": None}


async def test_owner_alert_marks_spam_and_nir_routing():
    from email_agent import owner_flow as of
    row = {"id": str(uuid.uuid4()), "classification": "unknown", "sender_email": "s@p.example", "subject": "re",
           "source_folder": "spam", "supplier_route": {"owner": "NIR"}, "task_ids": ["t1"]}
    assert of.is_important(row)
    title, body = of.format_alert(row, "NIR")
    assert "ספאם" in body and "NIR" in body


async def test_spam_pass_reads_only_relevant_messages(monkeypatch):
    from email_agent import agent as ag
    from email_agent.config import load_config

    class _Client:
        granted_scopes = {"https://www.googleapis.com/auth/gmail.readonly"}

        def __init__(self):
            self.metadata = []

        def profile(self):
            return {"emailAddress": "biz@example.com"}

        def list_message_ids(self, query, n, include_spam=False):
            if "in:spam" in query:
                assert include_spam is True
                return [{"id": "sp1", "threadId": "ts1"}, {"id": "sp2", "threadId": "ts2"}]
            return []

        def get_message_metadata(self, mid):
            self.metadata.append(mid)
            frm = "sales@parts.example.com" if mid == "sp1" else "prize@lottery.example"
            return {"id": mid, "payload": {"headers": [{"name": "From", "value": frm}]}}

    class _Source:
        async def find_suppliers_by_domain(self, dom):
            return [{"id": "s1"}] if dom == "parts.example.com" else []

    processed = []

    async def fake_process(cfg, client, store, source, mid, tid, mode, cache=None):
        processed.append(mid)
        await store.save_result(mid, {"classification": "supplier"})
        return {}

    monkeypatch.setattr(ag, "process_message", fake_process)
    monkeypatch.setattr(ag._control, "read_control", lambda *a, **k: {"running": True, "reason": "t"})
    cfg = load_config({"EMAIL_AGENT_ENABLED": "1", "GMAIL_OAUTH_CLIENT_ID": "x", "GMAIL_OAUTH_CLIENT_SECRET": "y",
                       "GMAIL_OAUTH_REFRESH_TOKEN": "z", "EMAIL_AGENT_MAILBOX": "biz@example.com",
                       "EMAIL_AGENT_SCAN_SPAM": "1"})
    store, client = InMemoryStore(), _Client()
    s = await ag.process_once(cfg, client, store, _Source())
    assert processed == ["sp1"] and s["spam"]["relevant"] == 1 and s["spam"]["ignored"] == 1
    assert store.rows["sp1"]["source_folder"] == "spam" and "sp2" not in store.rows   # unrelated spam not stored
    assert "gmail.modify" in s["spam"]["unspam"]
    s2 = await ag.process_once(cfg, client, store, _Source())
    assert processed == ["sp1"] and s2["spam"]["relevant"] == 0                     # repeat scan: no duplicate


async def test_spam_scan_failure_is_visible(monkeypatch):
    from email_agent import agent as ag
    from email_agent.config import load_config

    class _Client:
        granted_scopes = {"https://www.googleapis.com/auth/gmail.readonly"}

        def profile(self):
            return {"emailAddress": "biz@example.com"}

        def list_message_ids(self, query, n, include_spam=False):
            if include_spam:
                raise GmailError("scope", "insufficient permissions")
            return []

    monkeypatch.setattr(ag._control, "read_control", lambda *a, **k: {"running": True, "reason": "t"})
    cfg = load_config({"EMAIL_AGENT_ENABLED": "1", "GMAIL_OAUTH_CLIENT_ID": "x", "GMAIL_OAUTH_CLIENT_SECRET": "y",
                       "GMAIL_OAUTH_REFRESH_TOKEN": "z", "EMAIL_AGENT_MAILBOX": "biz@example.com",
                       "EMAIL_AGENT_SCAN_SPAM": "1"})
    s = await ag.process_once(cfg, _Client(), InMemoryStore(), SimpleNamespace())
    assert s["spam"]["error"].startswith("scope")


# ═══ 6. procurement ════════════════════════════════════════════════════════════════════════════════
ITEMS = [{"supplier_part_id": "sp-1", "part_id": "p-1", "quantity": 1, "unit_cost": "100.00", "currency": "ILS"}]
SHIP = {"cost": "20.00", "currency": "ILS"}


class _Connector:
    def __init__(self, result=None, exc=None):
        self.calls, self.result, self.exc = 0, result, exc

    async def place_order(self, row):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result


async def _prepared(store, order="o1", supplier="s1", items=ITEMS):
    r = await pr.prepare(store, order_id=order, order_number="AUTO-1", supplier_id=supplier, items=items,
                         shipping=SHIP, checks={"supplier_active": True})
    return r["procurement"]


async def test_customer_payment_alone_prepares_only():
    store, auth = pr.InMemoryProcurementStore(), au.InMemoryAuthStore()
    row = await _prepared(store)
    assert row["state"] == "AWAITING_PO_AUTHORIZATION"
    again = await pr.prepare(store, order_id="o1", order_number="AUTO-1", supplier_id="s1", items=ITEMS,
                             shipping=SHIP, checks={})
    assert again["created"] is False and len(store.rows) == 1                       # duplicate webhook: idempotent
    terms = pr.material_terms("o1", "s1", ITEMS, SHIP)
    assert "not authorized" in await pr.payment_refusal(store, auth, "o1", {"s1": terms})
    conn = _Connector(SimpleNamespace(success=True, order_id="X"))
    r = await pr.submit_po(store, auth, row["id"], conn, terms)
    assert not r["submitted"] and conn.calls == 0                                   # no PO approval -> no call


async def test_full_authorized_path_and_one_shot_payment():
    store, auth = pr.InMemoryProcurementStore(), au.InMemoryAuthStore()
    row = await _prepared(store)
    terms = pr.material_terms("o1", "s1", ITEMS, SHIP)
    with pytest.raises(pr.ProcurementError):                                        # payment before submission
        await pr.authorize_payment(store, auth, row["id"], approver="owner", source="test")
    await pr.authorize_po(store, auth, row["id"], approver="owner", source="test")
    conn = _Connector(SimpleNamespace(success=True, order_id="SUP-77"))
    assert (await pr.submit_po(store, auth, row["id"], conn, terms))["submitted"]
    again = await pr.submit_po(store, auth, row["id"], conn, terms)
    assert not again["submitted"] and conn.calls == 1                               # never resubmitted
    assert "not authorized" in await pr.payment_refusal(store, auth, "o1", {"s1": terms})
    await pr.authorize_payment(store, auth, row["id"], approver="owner", source="test")
    assert await pr.payment_refusal(store, auth, "o1", {"s1": terms}) is None
    assert await pr.payment_refusal(store, auth, "o1", {"s1": terms}) is not None   # consumed: no double pay
    states = [e["to_state"] for e in store.events]
    assert states == ["AWAITING_PO_AUTHORIZATION", "PO_AUTHORIZED", "SUBMITTED", "PAYMENT_AUTHORIZED", "PAYMENT_AUTHORIZED"]


async def test_changed_terms_require_reapproval():
    store, auth = pr.InMemoryProcurementStore(), au.InMemoryAuthStore()
    row = await _prepared(store)
    await pr.authorize_po(store, auth, row["id"], approver="owner", source="test")
    changed = pr.material_terms("o1", "s1", [{**ITEMS[0], "unit_cost": "130.00"}], SHIP)
    conn = _Connector(SimpleNamespace(success=True, order_id="X"))
    r = await pr.submit_po(store, auth, row["id"], conn, changed)
    assert not r["submitted"] and conn.calls == 0 and store.rows[row["id"]]["state"] == "AWAITING_PO_AUTHORIZATION"


async def test_approval_for_one_order_cannot_authorize_another():
    store, auth = pr.InMemoryProcurementStore(), au.InMemoryAuthStore()
    a = await _prepared(store, order="oA")
    b = await _prepared(store, order="oB")
    await pr.authorize_po(store, auth, a["id"], approver="owner", source="test")
    conn = _Connector(SimpleNamespace(success=True, order_id="X"))
    r = await pr.submit_po(store, auth, b["id"], conn, pr.material_terms("oB", "s1", ITEMS, SHIP))
    assert not r["submitted"] and conn.calls == 0


async def test_placeholder_connector_and_ambiguous_results():
    from services.suppliers.base_supplier import BaseSupplier
    store, auth = pr.InMemoryProcurementStore(), au.InMemoryAuthStore()
    row = await _prepared(store)
    terms = pr.material_terms("o1", "s1", ITEMS, SHIP)
    await pr.authorize_po(store, auth, row["id"], approver="owner", source="test")
    stub = SimpleNamespace(place_order=lambda r: BaseSupplier.place_order(SimpleNamespace(name="stub"), "x", {}))  # the real stub
    r = await pr.submit_po(store, auth, row["id"], stub, terms)
    assert not r["submitted"] and store.rows[row["id"]]["state"] == "SUBMISSION_FAILED"
    assert "not yet implemented" in store.rows[row["id"]]["last_error"]
    # ambiguous (timeout) -> EXCEPTION, never retried automatically
    row2 = await _prepared(store, order="o2")
    t2 = pr.material_terms("o2", "s1", ITEMS, SHIP)
    await pr.authorize_po(store, auth, row2["id"], approver="owner", source="test")
    conn = _Connector(exc=TimeoutError())
    assert not (await pr.submit_po(store, auth, row2["id"], conn, t2))["submitted"]
    assert store.rows[row2["id"]]["state"] == "EXCEPTION"
    assert "reconcile" in (await pr.submit_po(store, auth, row2["id"], conn, t2))["reason"] and conn.calls == 1


async def test_manual_submission_needs_po_approval_and_evidence():
    store, auth = pr.InMemoryProcurementStore(), au.InMemoryAuthStore()
    row = await _prepared(store)
    with pytest.raises(au.AuthorizationError):
        await pr.record_manual_submission(store, auth, row["id"], "SUP-1", approver="owner")
    await pr.authorize_po(store, auth, row["id"], approver="owner", source="test")
    with pytest.raises(pr.ProcurementError):
        await pr.record_manual_submission(store, auth, row["id"], " ", approver="owner")
    await pr.record_manual_submission(store, auth, row["id"], "SUP-1", approver="owner")
    assert store.rows[row["id"]]["state"] == "SUBMITTED"


async def test_store_constraints_mirror_the_database():
    store = pr.InMemoryProcurementStore()
    row = await _prepared(store)
    with pytest.raises(ValueError):
        await store.update(row["id"], state="SUBMITTED")
    with pytest.raises(ValueError):
        await store.update(row["id"], state="SUPPLIER_PAID", po_authorization_id="a")


def test_gate_bypass_never_in_production():
    assert pr.gate_bypassed({"ENVIRONMENT": "production", "PROCUREMENT_GATE_SANDBOX_BYPASS": "1"}) is False
    assert pr.gate_bypassed({"ENVIRONMENT": "sandbox"}) is False
    assert pr.gate_bypassed({"ENVIRONMENT": "sandbox", "PROCUREMENT_GATE_SANDBOX_BYPASS": "1"}) is True


def test_fulfillment_calls_the_gate_before_any_supplier_spend():
    src = (BACKEND / "routes" / "utils.py").read_text()
    body = src[src.index("async def trigger_supplier_fulfillment"):]
    gate = body.index("_proc.gate_paid_order(order_db, order_items")
    assert gate < body.index("supplier_part_ids = [oi.supplier_part_id") < body.index("supplier_payment = SupplierPayment(")
    assert "_proc_refusal = f\"procurement gate error" in body                        # fails closed


async def test_gate_paid_order_prepares_notifies_once_and_refuses(monkeypatch):
    store, auth = pr.InMemoryProcurementStore(), au.InMemoryAuthStore()
    sent = []

    async def notify(*a, **k):
        sent.append(k["alert_key"])
        return {"ok": True}

    async def terms_for(order, items):
        return {"s1": {"items": ITEMS, "shipping": SHIP, "checks": {}, "terms": pr.material_terms("o1", "s1", ITEMS, SHIP)}}

    monkeypatch.setattr(pr, "current_terms_for_order", terms_for)
    monkeypatch.delenv("PROCUREMENT_GATE_SANDBOX_BYPASS", raising=False)
    order = SimpleNamespace(id="o1", order_number="AUTO-1")
    for _ in range(3):                                                              # repeated webhooks / stuck-order agent
        assert await pr.gate_paid_order(order, [], store=store, auth_store=auth, notify=notify)
    assert len(store.rows) == 1 and sent == ["procurement_o1"]


# ═══ 7. NIR onboarding no longer relabels live suppliers ══════════════════════════════════════════
class _Conn:
    def __init__(self, existing=None, candidates=None):
        self.existing, self.candidates, self.sql = existing, candidates or [], []

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        return self.existing

    async def fetch(self, sql, *a):
        return self.candidates

    async def execute(self, sql, *a):
        self.sql.append((sql, a))


async def test_existing_supplier_status_is_never_overwritten():
    from services import supplier_sourcing as ss
    conn = _Conn(existing={"id": "sup-1", "name": "RockAuto", "website": "https://rockauto.com", "is_active": True})
    r = await ss.onboard_seller(conn, name="RockAuto", website="https://www.rockauto.com", signals={"ships_il": True})
    upd = [s for s in conn.sql if isinstance(s, tuple)][0]
    assert "nir_rediscovery" in upd[0] and '"status"' not in upd[1][1] and '"source"' not in upd[1][1]
    assert r["already_active"] is True


async def test_approve_and_reject_need_exactly_one_candidate():
    from services import supplier_sourcing as ss
    two = [{"id": "aaaa1", "name": "A", "is_active": False, "qualification": None},
           {"id": "aaaa2", "name": "B", "is_active": False, "qualification": None}]
    assert (await ss.approve_supplier("aaaa", _Conn(candidates=two), auth_store=au.InMemoryAuthStore()))["error"] == "ambiguous"
    assert (await ss.approve_supplier("a", _Conn(candidates=two)))["error"] == "short"
    assert (await ss.reject_supplier("aaaa", _Conn(candidates=two)))["error"] == "ambiguous"
    one = [two[0]]
    auth = au.InMemoryAuthStore()
    conn = _Conn(candidates=one)
    r = await ss.approve_supplier("aaaa1", conn, auth_store=auth)
    assert r["ok"] and auth.rows[0].domain == "supplier_activation" and auth.rows[0].consumed_at is not None
    # activation grants nothing else
    with pytest.raises(au.AuthorizationError):
        await au.require(auth, "supplier_message", "supplier", "aaaa1", scope="routine_inquiry")


# ═══ 8. migration 0042 ═════════════════════════════════════════════════════════════════════════════
def test_migration_0042_shape():
    import importlib.util
    spec = importlib.util.spec_from_file_location("m0042", BACKEND / "alembic_pii" / "versions" / "0042_supplier_lifecycle.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    sql = " ".join(m.UPGRADE_SQL)
    assert m.down_revision == "0041_email_agent_owner_flow"
    for c in ("supplier_outreach_sent_requires_authorization", "procurement_submitted_requires_po_authorization",
              "procurement_paid_requires_payment_authorization", "idempotency_key    varchar(128) NOT NULL UNIQUE"):
        assert c in sql
    assert "UPDATE email_agent_messages SET supplier_routed_at = created_at" in sql      # no re-routing of history


# ═══ 9. cross-agent end-to-end (SIMULATED: fake Gmail, fake supplier, no payment provider) ═════════
async def test_end_to_end_supplier_lifecycle(monkeypatch, outreach_on):
    from services import supplier_sourcing as ss
    auth, ost, store, todos = au.InMemoryAuthStore(), so.InMemoryOutreachStore(), InMemoryStore(), _Todos()
    gm = FakeGmail()
    # 1. NIR discovers a supplier with missing information (homepage keywords only -> all UNVERIFIED)
    rec = q.from_sourcing_signals("sup-e2e1", {"ships_il": True, "has_api": True, "http_ok": True}, "parts.example.com")
    assert q.classify(rec, NOW)["classification"] == q.NEEDS_INVESTIGATION
    q.set_requirement(rec, "contacts", q.VERIFIED, evidence_kind="official_policy_page",
                      source="https://parts.example.com/contact", note="official contact page", now=NOW,
                      details={"contacts": [{"address": "sales@parts.example.com", "status": q.VERIFIED,
                                             "source": "https://parts.example.com/contact"}]})
    # 2. authorized, non-binding inquiry through the permitted path
    await standing_grant(auth)
    p = await so.plan(ost, "sup-e2e1", rec, now=NOW)
    sent = await so.send_outreach(ost, auth, gm, p, sender="biz@example.com")
    assert sent["sent"] and len(gm.sent) == 1
    o = ost.rows[0]
    # 3-4. a simulated supplier reply lands (in Spam) in the inquiry's thread -> routed to NIR with references
    e = _email_row(store, thread=o["gmail_thread_id"], sender="sales@parts.example.com", folder="spam")
    assert (await sr.route_supplier_replies(store, ost, todos))["to_nir"] == 1
    assert todos.todos[0]["agent"] == "NIR" and o["state"] == "REPLY_RECEIVED"
    # 5. NIR records the supplier's claims (still UNVERIFIED), then verifies each with evidence
    for k in q.REQUIREMENTS:
        if k != "contacts":
            q.record_supplier_claim(rec, k, f"supplier says {k} ok", value=True, source_ref=f"email:{e['id'][:8]}")
    assert q.classify(rec, NOW)["classification"] == q.NEEDS_INVESTIGATION         # claims are not evidence
    for k in ("api", "israel_shipping", "availability", "catalog", "business"):
        q.set_requirement(rec, k, q.VERIFIED, evidence_kind="api_test" if k == "api" else "official_documentation",
                          source=f"https://parts.example.com/docs/{k}", note=f"{k} checked", now=NOW,
                          details={"data_timestamp": NOW.isoformat()} if k == "availability" else {})
    assert q.classify(rec, NOW)["classification"] == q.INTEGRATION_READY
    await so.record_review(ost, o["id"], "QUALIFIED")
    # 6-7. owner activation boundary (exactly one candidate, recorded authorization)
    with pytest.raises(au.AuthorizationError):
        await au.require(auth, "supplier_activation", "supplier", "sup-e2e1", scope="activate",
                         terms={"qualification": q.INTEGRATION_READY})
    conn = _Conn(candidates=[{"id": "sup-e2e1", "name": "Parts Example", "is_active": False, "qualification": rec}])
    act = await ss.approve_supplier("sup-e2e1", conn, auth_store=auth)
    assert act["ok"] and act["qualification"] == q.INTEGRATION_READY
    # BOAZ receives the handoff and records onboarding prerequisites
    handoff = [_todo("BOAZ", "supplier_handoff", supplier_id="sup-e2e1")]
    onboard = {}

    async def status(sid):
        return {"exists": True, "is_active": True, "priced_offers": 0, "qualification_unverified": []}

    async def record(sid, entry):
        onboard[sid] = entry

    out = await bt.run_once(bt.InMemoryTaskRepo(handoff), {"supplier_status": status, "record_onboarding": record})
    assert out["escalated"] == 1 and "price connector" in onboard["sup-e2e1"]["missing"][0]
    # 8. a simulated customer payment creates procurement preparation ONLY
    pstore = pr.InMemoryProcurementStore()
    terms = pr.material_terms("order-e2e", "sup-e2e1", ITEMS, SHIP)

    async def terms_for(order, items):
        return {"sup-e2e1": {"items": ITEMS, "shipping": SHIP, "checks": {"supplier_active": True}, "terms": terms}}

    monkeypatch.setattr(pr, "current_terms_for_order", terms_for)
    order = SimpleNamespace(id="order-e2e", order_number="AUTO-E2E")
    refusal = await pr.gate_paid_order(order, [], store=pstore, auth_store=auth)
    assert refusal and "not authorized" in refusal
    row = next(iter(pstore.rows.values()))
    # 9-12. PO prepared -> separate PO approval -> mocked supplier -> recorded result
    connector = _Connector(SimpleNamespace(success=True, order_id="SUP-E2E-1"))
    assert not (await pr.submit_po(pstore, auth, row["id"], connector, terms))["submitted"]   # no approval yet
    await pr.authorize_po(pstore, auth, row["id"], approver="owner", source="test")
    assert (await pr.submit_po(pstore, auth, row["id"], connector, terms))["submitted"]
    # 13. replay: duplicate payment webhook, retried task, repeated submission -> no new effects
    assert await pr.gate_paid_order(order, [], store=pstore, auth_store=auth)                  # still no payment auth
    assert not (await pr.submit_po(pstore, auth, row["id"], connector, terms))["submitted"]
    assert connector.calls == 1 and len(pstore.rows) == 1
    await pr.authorize_payment(pstore, auth, row["id"], approver="owner", source="test")
    assert await pr.gate_paid_order(order, [], store=pstore, auth_store=auth) is None          # released once
    assert await pr.gate_paid_order(order, [], store=pstore, auth_store=auth)                  # not twice
    # 14. audit trail: every transition and every authorization recorded
    moves = [e["to_state"] for e in pstore.events if e["from_state"] != e["to_state"]]
    assert moves == ["AWAITING_PO_AUTHORIZATION", "PO_AUTHORIZED", "SUBMITTED", "PAYMENT_AUTHORIZED"]
    assert any(e["detail"].get("refused") for e in pstore.events)                             # refusal audited too
    assert any(e["detail"].get("payment_authorization_consumed") for e in pstore.events)
    domains = sorted({a.domain for a in auth.rows})
    assert domains == ["purchase_order", "supplier_activation", "supplier_message", "supplier_payment"]
    assert all(a.approved_by and a.source for a in auth.rows)
    assert [c[0] for c in gm.calls] == ["create", "send"]                                     # one email, total


# ═══ 10. owner console: approvals only from exact owner commands ═══════════════════════════════════
async def test_supplier_console_commands():
    from agents import supplier_console as sc
    store, auth = pr.InMemoryProcurementStore(), au.InMemoryAuthStore()
    row = await _prepared(store)
    import supplier_ordering as so_

    async def manual(_sid):
        return so_.ManualConnector()

    kw = {"procurement_store": store, "auth_store": auth, "connector_factory": manual}
    for text in ("שלום", "סטטוס", "תאשר את הרכש בבקשה", "approve everything"):
        assert await sc.handle(text, **kw) is None                     # free text never approves anything
    assert "AWAITING_PO_AUTHORIZATION" in await sc.handle("רכש", **kw)
    assert "לא ניתן לאשר תשלום" in await sc.handle(f"אשרתשלוםספק {row['id'][:8]}", **kw)
    po = await sc.handle(f"אשררכש {row['id'][:8]}", **kw)
    assert "✅" in po and "manual ordering" in po
    assert [a.domain for a in auth.rows] == ["purchase_order"]
    assert "✅" in await sc.handle(f"הוזמןידנית {row['id'][:8]} SUP-123", **kw)
    assert "✅" in await sc.handle(f"אשרתשלוםספק {row['id'][:8]}", **kw)
    assert [a.domain for a in auth.rows] == ["purchase_order", "supplier_payment"]
    assert "✅" in await sc.handle("אשרפניותספקים", **kw)
    assert any(a.standing and a.domain == "supplier_message" for a in auth.rows)
    assert "בוטלה" in await sc.handle("בטלפניותספקים", **kw)
    with pytest.raises(au.AuthorizationError):
        await au.require(auth, "supplier_message", "supplier", "s9", scope="routine_inquiry")


def test_owner_console_routes_supplier_commands_after_email_and_help_lists_them():
    src = (BACKEND / "agents" / "owner_console.py").read_text()
    assert src.index("_email_console.handle(msg)") < src.index("_supplier_console.handle(msg)")
    assert "*אשררכש [מזהה]*" in src and "*אשרפניותספקים*" in src
