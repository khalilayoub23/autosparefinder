"""Email Agent foundation (email_agent/) - Gmail read, normalization, classification, context
resolution, response policy, draft-only boundary, idempotency, post-conditions, redaction.

Everything runs against an in-process fake Gmail transport and a fake context source: no
network, no real mailbox, no real email. The one test that touches Postgres
(test_pgstore_sql_against_temp_table) is opt-in (EMAIL_AGENT_PG_TEST=1) and uses a session-local
TEMP table, so it leaves nothing behind.
"""
import base64
import importlib.util
import json
import logging
import os
import urllib.parse
from email import message_from_bytes
from pathlib import Path

import pytest

from email_agent import agent as agent_mod
from email_agent import classifier, drafts, policy
from email_agent.config import CONFIGURED, DISABLED, UNCONFIGURED, EmailAgentConfig, load_config
from email_agent.context import ResolvedContext, candidate_tokens, resolve_context, verify_context
from email_agent.gmail_client import (ALLOWED_ENDPOINTS, API_BASE, TOKEN_URL, GmailClient, GmailError,
                                      SendProhibited, assert_allowed)
from email_agent.normalize import MalformedMessage, html_to_text, normalize_message, normalize_thread
from email_agent.redaction import redact, register_secrets
from email_agent.senders import is_authenticated, org_domain, sender_kind
from email_agent.store import InMemoryStore

MAILBOX = "autosparefinder2024@gmail.com"
READ = "https://www.googleapis.com/auth/gmail.readonly"
COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
FAKE_REFRESH = "1//fake-refresh-token-value-000"
FAKE_SECRET = "GOCSPX-fake-client-secret-000"
FAKE_ACCESS = "ya29.fake-access-token-000"


def cfg(**kw):
    base = dict(enabled=True, client_id="cid.apps.googleusercontent.com", client_secret=FAKE_SECRET,
                refresh_token=FAKE_REFRESH, mailbox=MAILBOX, gmail_drafts=False, max_attempts=3)
    base.update(kw)
    return EmailAgentConfig(**base)


def b64(s):
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode().rstrip("=")


def gmsg(mid, tid, frm, subject, text=None, html=None, labels=("INBOX",), extra_headers=None,
         attachments=(), ts=1_700_000_000_000, auth=True, to=MAILBOX):
    dom = frm.rsplit("@", 1)[-1].rstrip(">")
    headers = [{"name": "From", "value": frm}, {"name": "To", "value": to},
               {"name": "Subject", "value": subject}, {"name": "Message-ID", "value": f"<{mid}@mail.test>"}]
    if auth:
        headers.append({"name": "Authentication-Results",
                        "value": f"mx.google.com; dkim=pass header.i=@{dom}; dmarc=pass (p=NONE) header.from={dom}"})
    for k, v in (extra_headers or {}).items():
        headers.append({"name": k, "value": v})
    parts = []
    if text is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": b64(text), "size": len(text)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": b64(html), "size": len(html)}})
    body_part = {"mimeType": "multipart/alternative", "parts": parts}
    att_parts = [{"mimeType": mt, "filename": fn, "body": {"attachmentId": f"att-{i}", "size": size}}
                 for i, (fn, mt, size) in enumerate(attachments)]
    payload = ({"mimeType": "multipart/mixed", "headers": headers, "parts": [body_part] + att_parts}
               if att_parts else {**body_part, "headers": headers})
    return {"id": mid, "threadId": tid, "labelIds": list(labels), "internalDate": str(ts),
            "snippet": (text or "")[:80], "payload": payload}


class FakeGmail:
    """Stands in for both oauth2.googleapis.com/token and the Gmail REST API."""

    def __init__(self, messages=(), scopes=f"{READ} {COMPOSE}", token_status=200, token_error="invalid_grant",
                 mailbox=MAILBOX):
        self.messages = list(messages)
        self.scopes, self.token_status, self.token_error, self.mailbox = scopes, token_status, token_error, mailbox
        self.calls, self.drafts, self.fail = [], {}, {}
        self.raw_override = {}

    def paths(self, method=None):
        return [p for m, p in self.calls if method in (None, m)]

    def __call__(self, method, url, headers, body):
        if url == TOKEN_URL:
            self.calls.append((method, "TOKEN"))
            if self.token_status != 200:
                return self.token_status, json.dumps({"error": self.token_error}).encode()
            return 200, json.dumps({"access_token": FAKE_ACCESS, "expires_in": 3600, "scope": self.scopes}).encode()
        assert url.startswith(API_BASE)
        path = url[len(API_BASE):].split("?")[0]
        self.calls.append((method, path))
        assert headers["Authorization"] == f"Bearer {FAKE_ACCESS}"
        queue = self.fail.get(path)
        if queue:
            return queue.pop(0), b'{"error": {"status": "UNAVAILABLE", "message": "backend error"}}'
        if path in self.raw_override:
            return 200, self.raw_override[path]
        if path == "/profile":
            return 200, json.dumps({"emailAddress": self.mailbox, "messagesTotal": len(self.messages)}).encode()
        if path == "/messages":
            listed = [{"id": m["id"], "threadId": m["threadId"]} for m in self.messages if "INBOX" in m["labelIds"]]
            return 200, json.dumps({"messages": listed}).encode()
        if path.startswith("/threads/"):
            tid = path.split("/")[-1]
            return 200, json.dumps({"id": tid, "messages": [m for m in self.messages if m["threadId"] == tid]}).encode()
        if path == "/drafts" and method == "POST":
            req = json.loads(body)["message"]
            mime = message_from_bytes(base64.urlsafe_b64decode(req["raw"]))
            did = f"draft-{len(self.drafts) + 1}"
            text = mime.get_payload(decode=True).decode("utf-8")
            self.drafts[did] = {"id": did, "message": {
                "id": f"dm-{did}", "threadId": req["threadId"], "labelIds": ["DRAFT"],
                "payload": {"mimeType": "text/plain",
                            "headers": [{"name": k, "value": str(v)} for k, v in mime.items()],
                            "body": {"data": b64(text), "size": len(text)}}}}
            return 200, json.dumps({"id": did, "message": {"id": f"dm-{did}", "threadId": req["threadId"]}}).encode()
        if path.startswith("/drafts/"):
            d = self.drafts.get(path.split("/")[-1])
            return (200, json.dumps(d).encode()) if d else (404, b'{"error": {"status": "NOT_FOUND"}}')
        return 404, b'{"error": {"status": "NOT_FOUND"}}'


class FakeSource:
    def __init__(self, users=(), orders=(), suppliers=()):
        self.users, self.orders, self.suppliers = list(users), list(orders), list(suppliers)

    async def find_user_by_email(self, email):
        return [u for u in self.users if u["email"].lower() == email.lower()]

    async def find_orders_by_tokens(self, tokens):
        keys = ("order_number", "tracking_number", "eurosender_order_code", "supplier_order_id")
        return [o for o in self.orders if any(o.get(k) and o[k] in tokens for k in keys)]

    async def find_suppliers_by_domain(self, domain):
        return [s for s in self.suppliers if domain in (s.get("website") or "")]

    async def get_user(self, user_id):
        return next((u for u in self.users if u["id"] == user_id), None)

    async def get_order(self, order_id):
        return next((o for o in self.orders if o["id"] == order_id), None)

    async def get_supplier(self, supplier_id):
        return next((s for s in self.suppliers if s["id"] == supplier_id), None)


USER = {"id": "u-1", "email": "dana@gmail.com", "full_name": "Dana Levi"}
ORDER = {"id": "o-1", "order_number": "AUTO-2026-1A2B3C4D", "user_id": "u-1", "status": "shipped",
         "tracking_number": "TRK998877", "tracking_url": "https://www.eurosender.com/track/TRK998877",
         "shipping_provider": "eurosender", "eurosender_order_code": "408540-26", "eurosender_status": "created"}
SUPPLIER = {"id": "s-1", "name": "Parts GmbH", "website": "https://www.partsgmbh.de/shop", "is_active": True}


def client_for(fake, **kw):
    return GmailClient(cfg(**kw), transport=fake, sleep=lambda s: None)


async def run(fake, source=None, store=None, **cfg_kw):
    c = cfg(**cfg_kw)
    store = store or InMemoryStore()
    summary = await agent_mod.process_once(c, GmailClient(c, transport=fake, sleep=lambda s: None), store,
                                           source or FakeSource())
    return summary, store


# ── 1. configuration missing ────────────────────────────────────────────────────
def test_config_states_disabled_unconfigured_configured():
    assert load_config({}).state == DISABLED
    c = load_config({"EMAIL_AGENT_ENABLED": "1", "GMAIL_OAUTH_CLIENT_ID": "x"})
    assert c.state == UNCONFIGURED
    assert c.missing() == ["GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN"]
    full = load_config({"EMAIL_AGENT_ENABLED": "1", "GMAIL_OAUTH_CLIENT_ID": "x",
                        "GMAIL_OAUTH_CLIENT_SECRET": FAKE_SECRET, "GMAIL_OAUTH_REFRESH_TOKEN": FAKE_REFRESH})
    assert full.state == CONFIGURED and full.public()["send_enabled"] is False
    assert FAKE_SECRET not in repr(full) and FAKE_REFRESH not in repr(full)
    assert FAKE_SECRET not in json.dumps(full.public()) and FAKE_REFRESH not in json.dumps(full.public())


async def test_unconfigured_cycle_is_controlled_and_makes_no_call():
    fake = FakeGmail()
    summary, store = await run(fake, refresh_token="")
    assert summary["connection"] == {"ok": False, "state": UNCONFIGURED, "missing": ["GMAIL_OAUTH_REFRESH_TOKEN"]}
    assert fake.calls == [] and store.rows == {}


async def test_loop_idles_without_crashing_when_unconfigured(monkeypatch):
    from email_agent import loop as loop_mod
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(loop_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setenv("EMAIL_AGENT_ENABLED", "1")
    for k in ("GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(KeyboardInterrupt):
        await loop_mod.email_agent_loop()
    assert sleeps[1] >= 3600 and loop_mod.last_cycle == {"state": UNCONFIGURED}


# ── 2. OAuth / token failure ────────────────────────────────────────────────────
async def test_token_failure_is_reported_not_raised():
    fake = FakeGmail([gmsg("m1", "t1", "dana@gmail.com", "hi", "where is my order")], token_status=400)
    summary, store = await run(fake)
    assert summary["connection"]["ok"] is False and summary["connection"]["error_kind"] == "auth"
    assert summary["processed"] == 0 and store.rows == {}
    assert FAKE_REFRESH not in json.dumps(summary) and FAKE_SECRET not in json.dumps(summary)


def test_token_endpoint_5xx_is_transient():
    with pytest.raises(GmailError) as e:
        client_for(FakeGmail(token_status=503)).profile()
    assert e.value.kind == "transient" and e.value.retryable


async def test_wrong_account_guard_blocks_processing():
    fake = FakeGmail([gmsg("m1", "t1", "dana@gmail.com", "hi", "order?")], mailbox="someone.else@gmail.com")
    summary, store = await run(fake)
    assert summary["connection"]["error_kind"] == "wrong_account" and store.rows == {}
    assert "/messages" not in fake.paths()


async def test_read_scope_missing_blocks_processing():
    summary, _ = await run(FakeGmail([], scopes="https://www.googleapis.com/auth/youtube.force-ssl"))
    assert summary["connection"]["ok"] is False and summary["connection"]["error_kind"] == "scope"


# ── 3 / 4. normalization, multipart text + html ─────────────────────────────────
def test_normalization_preserves_ids_and_headers():
    raw = gmsg("m1", "t9", '"Dana Levi" <Dana@Gmail.com>', "=?utf-8?b?16nXnNeV150=?=", "שלום, איפה ההזמנה?",
               extra_headers={"Reply-To": "dana.reply@gmail.com", "Cc": "a@x.com, b@y.com"})
    e = normalize_message(raw, MAILBOX)
    assert (e.message_id, e.thread_id) == ("m1", "t9")
    assert e.sender_email == "dana@gmail.com" and e.sender_name == "Dana Levi"
    assert e.subject == "שלום" and e.rfc822_message_id == "<m1@mail.test>"
    assert e.reply_address == "dana.reply@gmail.com" and e.cc == ["a@x.com", "b@y.com"]
    assert e.to == [MAILBOX] and e.date is not None and e.date.tzinfo is not None
    assert e.is_inbound and e.body_source == "text/plain" and "איפה ההזמנה" in e.body_text


def test_multipart_prefers_plain_and_falls_back_to_sanitized_html():
    both = normalize_message(gmsg("m1", "t1", "a@b.com", "s", text="plain body", html="<p>html body</p>"))
    assert both.body_text == "plain body" and both.body_source == "text/plain"
    html = "<html><head><style>p{color:red}</style></head><body><script>alert('x')</script>" \
           "<p>Hello <b>Dana</b></p><div>Order AUTO-2026-1A2B3C4D</div></body></html>"
    only_html = normalize_message(gmsg("m2", "t1", "a@b.com", "s", html=html))
    assert only_html.body_source == "text/html"
    assert "Hello Dana" in only_html.body_text and "AUTO-2026-1A2B3C4D" in only_html.body_text
    assert "alert" not in only_html.body_text and "color:red" not in only_html.body_text
    assert html_to_text("<p>a</p><p>b</p>") == "a\nb"


def test_quoted_reply_text_is_separated_from_new_text():
    body = "Thanks, any update?\n\nOn Mon, 1 Jan 2026 at 10:00, Shop <x@y.com> wrote:\n> we offer a refund policy"
    e = normalize_message(gmsg("m1", "t1", "a@b.com", "Re: order", text=body))
    assert e.new_text == "Thanks, any update?" and "refund" in e.body_text


# ── 5. thread preservation ──────────────────────────────────────────────────────
def test_thread_is_ordered_and_marks_our_own_replies_outbound():
    raw = {"id": "t1", "messages": [
        gmsg("m2", "t1", MAILBOX, "Re: q", "our reply", labels=("SENT",), ts=2000),
        gmsg("m1", "t1", "dana@gmail.com", "q", "question", ts=1000),
        {"id": "broken"},
        gmsg("m3", "t1", "dana@gmail.com", "Re: q", "follow up", ts=3000)]}
    thread = normalize_thread(raw, MAILBOX)
    assert [m.message_id for m in thread] == ["m1", "m2", "m3"]
    assert [m.is_inbound for m in thread] == [True, False, True]
    assert {m.thread_id for m in thread} == {"t1"}


async def test_already_answered_thread_gets_no_new_draft():
    fake = FakeGmail([gmsg("m1", "t1", "dana@gmail.com", "Order question", "where is my order?", ts=1000),
                      gmsg("m2", "t1", MAILBOX, "Re: Order question", "answered", labels=("SENT",), ts=2000)])
    summary, store = await run(fake, FakeSource(users=[USER]))
    assert store.rows["m1"]["recommended_action"] == "log_only" and store.rows["m1"]["draft_body"] is None


# ── 6 / 19. idempotency, retry without duplicate processing ─────────────────────
async def test_same_message_is_processed_exactly_once():
    fake = FakeGmail([gmsg("m1", "t1", "dana@gmail.com", "Order AUTO-2026-1A2B3C4D", "where is my order?")])
    src, store = FakeSource(users=[USER], orders=[ORDER]), InMemoryStore()
    first, _ = await run(fake, src, store, gmail_drafts=True)
    second, _ = await run(fake, src, store, gmail_drafts=True)
    assert (first["new"], first["processed"], first["drafts"]) == (1, 1, 1)
    assert (second["new"], second["skipped"], second["processed"]) == (0, 1, 0)
    assert len(store.rows) == 1 and len(fake.drafts) == 1 and fake.paths("POST").count("/drafts") == 1


async def test_transient_failure_retries_then_succeeds_once():
    fake = FakeGmail([gmsg("m1", "t1", "dana@gmail.com", "part", "do you have a brake part in stock?")])
    fake.fail["/threads/t1"] = [503, 503, 503]   # exhausts the client's bounded GET retries
    store = InMemoryStore()
    first, _ = await run(fake, store=store)
    assert first["failed"] == 1 and store.rows["m1"]["status"] == "retry" and store.rows["m1"]["attempts"] == 1
    second, _ = await run(fake, store=store)
    assert second["retried"] == 1 and second["processed"] == 1
    assert store.rows["m1"]["status"] == "processed" and store.rows["m1"]["attempts"] == 2
    third, _ = await run(fake, store=store)
    assert third["skipped"] == 1 and len(store.rows) == 1


async def test_retries_are_bounded_then_terminal():
    fake = FakeGmail([gmsg("m1", "t1", "dana@gmail.com", "x", "y")])
    fake.fail["/threads/t1"] = [503] * 50
    store = InMemoryStore()
    for _ in range(5):
        await run(fake, store=store)
    assert store.rows["m1"]["status"] == "failed" and store.rows["m1"]["attempts"] == 3


async def test_interrupted_draft_attempt_never_creates_a_second_draft():
    fake = FakeGmail([gmsg("m1", "t1", "dana@gmail.com", "Order AUTO-2026-1A2B3C4D", "where is my order?")])
    fake.fail["/drafts"] = [503]                 # POST is not auto-retried; outcome unknown
    src, store = FakeSource(users=[USER], orders=[ORDER]), InMemoryStore()
    first, _ = await run(fake, src, store, gmail_drafts=True)
    assert first["failed"] == 1 and store.rows["m1"]["status"] == "retry"
    assert store.rows["m1"]["draft_attempted_at"] and fake.paths("POST").count("/drafts") == 1
    second, _ = await run(fake, src, store, gmail_drafts=True)
    assert second["processed"] == 1 and fake.paths("POST").count("/drafts") == 1 and fake.drafts == {}
    assert store.rows["m1"]["requires_human"] is True and store.rows["m1"]["gmail_draft_id"] is None


async def test_auth_failure_mid_cycle_releases_claim_without_burning_attempts():
    fake = FakeGmail([gmsg("m1", "t1", "dana@gmail.com", "x", "y")])
    fake.fail["/threads/t1"] = [403]
    summary, store = await run(fake)
    assert summary["aborted"] == "scope" and store.rows["m1"] == {**store.rows["m1"], "status": "retry", "attempts": 0}


# ── 7 / 8. classification categories, unknown ───────────────────────────────────
CASES = [
    ("customer_inquiry", "dana@gmail.com", "שאלה", "יש לכם רפידות בלם לטויוטה קורולה? מה המחיר?", {}, ()),
    ("order_shipping", "dana@gmail.com", "Hi", "Where is my order? I have no tracking yet.", {}, ()),
    ("supplier", "sales@partsgmbh.de", "Stock list", "Attached our updated catalogue.", {}, ()),
    ("shipping_eurosender", "noreply@eurosender.com", "Your shipment", "Order 408540-26 was picked up.", {}, ()),
    ("ebay", "ebay@ebay.com", "Your eBay item", "Your item has been listed.", {}, ()),
    ("aliexpress", "transaction@notice.aliexpress.com", "Order update", "Your package left the warehouse.", {}, ()),
    ("payment_billing", "dana@gmail.com", "חשבונית", "לא קיבלתי חשבונית על התשלום", {}, ()),
    ("account_security", "dana@gmail.com", "help", "I forgot my password and cannot log in", {}, ()),
    ("complaint_dispute", "dana@gmail.com", "Complaint", "This is unacceptable, I will contact my lawyer", {}, ()),
    ("refund_cancellation", "dana@gmail.com", "ביטול", "אני רוצה לבטל את ההזמנה ולקבל החזר כספי", {}, ()),
    ("automated_notification", "no-reply@someservice.io", "Report", "Your weekly report is ready.",
     {"Auto-Submitted": "auto-generated"}, ()),
    ("newsletter_marketing", "news@bigshop.com", "50% off!", "Deals of the week",
     {"List-Unsubscribe": "<mailto:u@bigshop.com>"}, ()),
    ("spam_irrelevant", "win@lottery.biz", "You won", "Claim your prize", {}, ("INBOX", "SPAM")),
    ("unknown", "someone@gmail.com", "hello", "lorem ipsum dolor sit amet", {}, ()),
]


@pytest.mark.parametrize("expected,frm,subject,text,headers,labels", CASES, ids=[c[0] for c in CASES])
async def test_classification_categories(expected, frm, subject, text, headers, labels):
    raw = gmsg("m1", "t1", frm, subject, text, extra_headers=headers, labels=labels or ("INBOX",))
    e = normalize_message(raw, MAILBOX)
    src = FakeSource(users=[USER], orders=[ORDER], suppliers=[SUPPLIER])
    ctx = await resolve_context(e, [e], src)
    c = classifier.classify(e, [e], ctx)
    d = policy.decide(c, ctx)
    c.recommended_action, c.requires_human = d.recommended_action, d.requires_human
    assert c.classification == expected, c.reason
    assert set(classifier.CATEGORIES) >= {expected} and 0.0 <= c.confidence <= 1.0 and c.reason
    assert (c.message_id, c.thread_id, c.sender) == ("m1", "t1", e.sender_email)
    assert classifier.validate_classification(c, ctx) == []
    assert set(c.to_dict()) >= {"classification", "confidence", "reason", "thread_id", "message_id", "sender",
                                "references", "recommended_action", "requires_human"}


async def test_body_outweighs_a_misleading_subject():
    e = normalize_message(gmsg("m1", "t1", "dana@gmail.com", "Quick question about a part",
                               "Actually I want a refund, please cancel everything."))
    ctx = await resolve_context(e, [e], FakeSource(users=[USER]))
    assert classifier.classify(e, [e], ctx).classification == "refund_cancellation"


async def test_short_followup_uses_thread_context():
    first = normalize_message(gmsg("m1", "t1", "dana@gmail.com", "Hi", "Where is my order?", ts=1000))
    follow = normalize_message(gmsg("m2", "t1", "dana@gmail.com", "Re: Hi", "???", ts=2000))
    ctx = await resolve_context(follow, [first, follow], FakeSource(users=[USER]))
    c = classifier.classify(follow, [first, follow], ctx)
    assert c.classification == "order_shipping" and "earlier messages" in c.reason


async def test_spoofed_platform_sender_is_not_trusted():
    e = normalize_message(gmsg("m1", "t1", "noreply@eurosender.com", "Shipment", "Order 408540-26 refund", auth=False))
    ctx = await resolve_context(e, [e], FakeSource(orders=[ORDER]))
    c = classifier.classify(e, [e], ctx)
    assert c.classification == "unknown" and "unauthenticated_sender" in c.risk_flags
    assert ctx.order["status"] == "sender_mismatch" and c.references == {}
    assert policy.decide(c, ctx).requires_human is True


def test_validator_rejects_fabricated_identifiers_and_bad_schema():
    ctx = ResolvedContext()
    c = classifier.Classification("order_shipping", 0.8, "r", "t1", "m1", "a@b.com",
                                  references={"order_number": "AUTO-2026-FAKE0000"})
    assert any("not a DB-verified" in p for p in classifier.validate_classification(c, ctx))
    bad = classifier.Classification("made_up", 1.7, "", "t1", "m1", "a@b.com", recommended_action="send_now")
    problems = classifier.validate_classification(bad, ctx)
    assert len(problems) == 4


# ── 9 / 10 / 11. context resolution ─────────────────────────────────────────────
async def test_unresolved_customer_and_order_stay_unresolved():
    e = normalize_message(gmsg("m1", "t1", "stranger@gmail.com", "Order", "Where is order AUTO-2026-ZZZZ9999?"))
    ctx = await resolve_context(e, [e], FakeSource(users=[USER], orders=[ORDER]))
    assert ctx.customer["status"] == "unresolved" and ctx.order["status"] == "unresolved"
    assert ctx.shipment["status"] == "unresolved" and ctx.references() == {}
    c = classifier.classify(e, [e], ctx)
    d = policy.decide(c, ctx)
    assert d.recommended_action == "draft_request_order_number"
    body = drafts.build_draft(d.recommended_action, e, ctx).body
    assert "AUTO-2026" not in body and "TRK" not in body


async def test_verified_order_and_shipment_context():
    e = normalize_message(gmsg("m1", "t1", "dana@gmail.com", "Order AUTO-2026-1A2B3C4D", "where is my order?"))
    src = FakeSource(users=[USER], orders=[ORDER])
    ctx = await resolve_context(e, [e], src)
    assert ctx.customer == {"status": "resolved", "user_id": "u-1", "full_name": "Dana Levi"}
    assert ctx.order["status"] == "resolved" and ctx.order["order_number"] == "AUTO-2026-1A2B3C4D"
    assert ctx.shipment["status"] == "resolved" and ctx.shipment["eurosender_order_code"] == "408540-26"
    assert ctx.references() == {"customer_id": "u-1", "order_id": "o-1", "order_number": "AUTO-2026-1A2B3C4D",
                                "tracking_number": "TRK998877", "eurosender_order_code": "408540-26"}
    v = await verify_context(ctx, src)
    assert v["ok"] and v["checks"] == {"customer_exists": True, "order_exists": True,
                                       "order_belongs_to_customer": True, "shipment_matches_order": True}


async def test_context_postcondition_fails_when_entity_does_not_exist():
    e = normalize_message(gmsg("m1", "t1", "dana@gmail.com", "Order AUTO-2026-1A2B3C4D", "status?"))
    src = FakeSource(users=[USER], orders=[ORDER])
    ctx = await resolve_context(e, [e], src)
    src.orders.clear()                       # entity vanished between resolve and verify
    v = await verify_context(ctx, src)
    assert v["ok"] is False and v["checks"]["order_exists"] is False


async def test_someone_elses_order_is_a_mismatch_and_details_are_withheld():
    other = {**ORDER, "id": "o-2", "user_id": "u-OTHER"}
    e = normalize_message(gmsg("m1", "t1", "dana@gmail.com", "Order AUTO-2026-1A2B3C4D", "where is my order?"))
    ctx = await resolve_context(e, [e], FakeSource(users=[USER], orders=[other]))
    assert ctx.order == {"status": "sender_mismatch", "reason": "referenced order belongs to a different account"}
    assert ctx.shipment["status"] == "unresolved"
    assert policy.decide(classifier.classify(e, [e], ctx), ctx).requires_human is True


async def test_ambiguous_orders_remain_unresolved():
    second = {**ORDER, "id": "o-2", "order_number": "AUTO-2026-9Z8Y7X6W", "eurosender_order_code": None}
    e = normalize_message(gmsg("m1", "t1", "dana@gmail.com", "Orders",
                               "status of AUTO-2026-1A2B3C4D and AUTO-2026-9Z8Y7X6W?"))
    src = FakeSource(users=[USER], orders=[ORDER, second])
    ctx = await resolve_context(e, [e], src)
    assert ctx.order["status"] == "ambiguous" and "order_number" not in ctx.order
    assert (await verify_context(ctx, src))["checks"]["unresolved_carries_no_ids"] is True


async def test_synthetic_tracking_is_never_treated_as_a_shipment():
    fake_tracked = {**ORDER, "shipping_provider": None, "eurosender_order_code": None}
    e = normalize_message(gmsg("m1", "t1", "dana@gmail.com", "Order AUTO-2026-1A2B3C4D", "where is my order?"))
    ctx = await resolve_context(e, [e], FakeSource(users=[USER], orders=[fake_tracked]))
    assert ctx.order["status"] == "resolved" and ctx.shipment["status"] == "no_shipment"
    body = drafts.build_draft("draft_order_status", e, ctx).body
    assert "AUTO-2026-1A2B3C4D" in body and "TRK998877" not in body


async def test_eurosender_email_resolves_to_order_and_shipment():
    e = normalize_message(gmsg("m1", "t1", "noreply@eurosender.com", "Shipment update",
                               "Your order 408540-26 is in transit."))
    ctx = await resolve_context(e, [e], FakeSource(orders=[ORDER]))
    assert ctx.customer["status"] == "unresolved" and ctx.order["status"] == "resolved"
    assert ctx.shipment["eurosender_status"] == "created" and ctx.shipment["tracking_number"] == "TRK998877"
    d = policy.decide(classifier.classify(e, [e], ctx), ctx)
    assert (d.recommended_action, d.requires_human) == ("log_only", False)


async def test_supplier_resolution_by_domain_and_free_mail_exclusion():
    e = normalize_message(gmsg("m1", "t1", "sales@mail.partsgmbh.de", "Hello", "New stock"))
    ctx = await resolve_context(e, [e], FakeSource(suppliers=[SUPPLIER]))
    assert ctx.supplier["status"] == "resolved" and ctx.supplier["supplier_id"] == "s-1"
    lookalike = {**SUPPLIER, "website": "https://notpartsgmbh.de.evil.com"}
    e2 = normalize_message(gmsg("m2", "t2", "x@gmail.com", "Hello", "we are Parts GmbH"))
    ctx2 = await resolve_context(e2, [e2], FakeSource(suppliers=[SUPPLIER]))
    assert ctx2.supplier["status"] == "unresolved"
    ctx3 = await resolve_context(e, [e], FakeSource(suppliers=[lookalike]))
    assert ctx3.supplier["status"] == "unresolved"
    two = await resolve_context(e, [e], FakeSource(suppliers=[SUPPLIER, {**SUPPLIER, "id": "s-2"}]))
    assert two.supplier["status"] == "ambiguous"


def test_sender_helpers():
    assert org_domain("mail.partsgmbh.de") == "partsgmbh.de" and org_domain("a.b.co.il") == "b.co.il"
    assert sender_kind("x@notice.aliexpress.com") == "aliexpress" and sender_kind("x@gmail.com") == "free_mail"
    assert is_authenticated({"authentication-results": "mx.google.com; dmarc=pass header.from=ebay.com"}, "a@ebay.com")
    assert not is_authenticated({"authentication-results": "mx.google.com; dmarc=pass header.from=evil.com"}, "a@ebay.com")
    assert not is_authenticated({"authentication-results": "mx.google.com; dmarc=fail; spf=pass"}, "a@ebay.com")
    assert not is_authenticated({}, "a@ebay.com")
    assert "AUTO-2026-1A2B3C4D" in candidate_tokens("re: AUTO-2026-1A2B3C4D please") and candidate_tokens("hello") == []


# ── 12. human-approval policy ───────────────────────────────────────────────────
HUMAN_TEXTS = {
    "refund": "please give me a refund", "cancellation": "I want to cancel",
    "price_change": "can I get a discount?", "financial_commitment": "send me your bank details",
    "address_change": "please change my address", "dispute_legal": "I opened a chargeback",
    "security": "my account was hacked", "customs": "customs asked me for duties",
}


@pytest.mark.parametrize("flag,text", HUMAN_TEXTS.items(), ids=list(HUMAN_TEXTS))
async def test_sensitive_topics_require_human_and_get_no_draft(flag, text):
    fake = FakeGmail([gmsg("m1", "t1", "dana@gmail.com", "Order AUTO-2026-1A2B3C4D", f"About my order: {text}")])
    summary, store = await run(fake, FakeSource(users=[USER], orders=[ORDER]), gmail_drafts=True)
    row = store.rows["m1"]
    assert flag in row["risk_flags"] and row["requires_human"] is True
    assert row["recommended_action"] == "escalate_to_human" and row["policy_tier"] == policy.TIER_HUMAN
    assert row["draft_body"] is None and fake.drafts == {} and summary["drafts"] == 0


async def test_risky_attachment_requires_human_and_is_never_fetched():
    fake = FakeGmail([gmsg("m1", "t1", "dana@gmail.com", "part photo", "which part is this? price?",
                           attachments=[("invoice.pdf.exe", "application/octet-stream", 4096),
                                        ("photo.jpg", "image/jpeg", 1024)])])
    _, store = await run(fake, FakeSource(users=[USER]))
    row = store.rows["m1"]
    assert row["requires_human"] is True and "risky_attachment" in row["risk_flags"]
    assert all("attachment" not in p for p in fake.paths())          # 18: metadata only


async def test_unsafe_order_status_downgrades_to_human():
    cancelled = {**ORDER, "status": "cancelled"}
    fake = FakeGmail([gmsg("m1", "t1", "dana@gmail.com", "Order AUTO-2026-1A2B3C4D", "where is my order?")])
    _, store = await run(fake, FakeSource(users=[USER], orders=[cancelled]))
    assert store.rows["m1"]["requires_human"] is True and store.rows["m1"]["draft_body"] is None


# ── 13. prohibited autonomous send ──────────────────────────────────────────────
@pytest.mark.parametrize("method,path", [
    ("POST", "/messages/send"), ("POST", "/drafts/send"), ("POST", "/messages"), ("POST", "/messages/m1/modify"),
    ("POST", "/messages/m1/trash"), ("DELETE", "/messages/m1"), ("DELETE", "/drafts/d1"), ("PUT", "/drafts/d1"),
    ("GET", "/messages/m1/attachments/a1"), ("POST", "/threads/t1/modify"), ("POST", "/labels"),
    ("POST", "/messages/batchModify"), ("POST", "/watch")])
def test_transport_refuses_everything_outside_the_allowlist(method, path):
    fake = FakeGmail()
    with pytest.raises(SendProhibited):
        assert_allowed(method, path)
    with pytest.raises(SendProhibited):
        client_for(fake)._call(method, path)
    assert fake.calls == []                                           # refused before any network call


def test_no_send_capability_exists():
    import email_agent
    assert email_agent.SEND_ENABLED is False and policy.send_allowed() is False
    assert not [n for n in dir(GmailClient) if "send" in n.lower()]
    assert not any("send" in pat.pattern for _, pat in ALLOWED_ENDPOINTS)
    assert [m for m, _ in ALLOWED_ENDPOINTS].count("POST") == 1
    d = policy.PolicyDecision(policy.TIER_SAFE, "draft_order_status", False)
    assert d.to_dict()["sendable"] is False and policy.send_allowed(d) is False


def test_send_gate_ignores_environment(monkeypatch):
    import email_agent
    monkeypatch.setenv("EMAIL_AGENT_SEND_ENABLED", "1")
    monkeypatch.setattr(email_agent, "SEND_ENABLED", True)
    assert policy.send_allowed() is False and load_config().public()["send_enabled"] is False


def test_migration_enforces_non_sendable_at_db_level():
    path = Path(__file__).resolve().parent.parent / "alembic_pii" / "versions" / "0039_email_agent_messages.py"
    spec = importlib.util.spec_from_file_location("m0039", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert len(mod.revision) <= 32 and mod.down_revision == "0038_eurosender_shipping"
    ddl = " ".join(mod.UPGRADE_SQL[0].split())
    assert "CHECK (sendable = false)" in ddl and "gmail_message_id varchar(128) NOT NULL UNIQUE" in ddl


# ── 14 / 15. draft creation + post-condition ────────────────────────────────────
async def test_gmail_draft_is_created_in_thread_verified_and_not_sent():
    fake = FakeGmail([gmsg("m1", "t1", "Dana <dana@gmail.com>", "Order AUTO-2026-1A2B3C4D", "where is my order?")])
    summary, store = await run(fake, FakeSource(users=[USER], orders=[ORDER]), gmail_drafts=True)
    row = store.rows["m1"]
    assert summary["draft_mode"] == "gmail" and summary["drafts"] == 1 and summary["sent"] == 0
    assert row["gmail_draft_id"] == "draft-1" and row["recommended_action"] == "draft_order_status"
    assert row["requires_human"] is False and row["sendable"] is False
    stored = fake.drafts["draft-1"]["message"]
    hdr = {h["name"]: h["value"] for h in stored["payload"]["headers"]}
    assert stored["threadId"] == "t1" and stored["labelIds"] == ["DRAFT"]
    assert hdr["To"] == "dana@gmail.com" and hdr["In-Reply-To"] == "<m1@mail.test>"
    assert hdr["Subject"] == "Re: Order AUTO-2026-1A2B3C4D" and hdr["From"] == MAILBOX
    assert "AUTO-2026-1A2B3C4D" in row["draft_body"] and "shipped" in row["draft_body"]
    assert "TRK998877" in row["draft_body"] and "Dana" in row["draft_body"]
    v = row["verification"]["draft"]
    assert v["ok"] and v["mode"] == "gmail" and v["send_calls"] == 0
    assert v["checks"] == {"draft_exists": True, "draft_id_matches": True, "thread_matches": True,
                           "recipient_matches_source": True, "body_matches": True,
                           "has_draft_label": True, "not_sent": True}
    assert fake.paths("POST") == ["TOKEN", "/drafts"] and "/drafts/draft-1" in fake.paths("GET")
    assert not any("send" in p for p in fake.paths())


async def test_local_draft_mode_never_touches_gmail_drafts():
    fake = FakeGmail([gmsg("m1", "t1", "dana@gmail.com", "שאלה", "יש לכם רפידות בלם? מה המחיר?")])
    summary, store = await run(fake, FakeSource(users=[USER]))          # gmail_drafts=False
    row = store.rows["m1"]
    assert summary["draft_mode"] == "local" and row["gmail_draft_id"] is None and fake.drafts == {}
    assert row["verification"]["draft"]["mode"] == "local" and row["verification"]["draft"]["ok"]
    assert row["draft_body"].startswith("שלום Dana,") and "מספר הרישוי" in row["draft_body"]
    assert fake.paths("POST") == ["TOKEN"]


async def test_draft_scope_missing_falls_back_to_local():
    fake = FakeGmail([gmsg("m1", "t1", "dana@gmail.com", "q", "is this part in stock?")], scopes=READ)
    summary, _ = await run(fake, FakeSource(users=[USER]), gmail_drafts=True)
    assert summary["connection"]["can_draft"] is False and summary["draft_mode"] == "local" and fake.drafts == {}


@pytest.mark.parametrize("tamper,failed_check", [
    (lambda d: d["message"].update(threadId="t-other"), "thread_matches"),
    (lambda d: d["message"].update(labelIds=["SENT"]), "not_sent"),
    (lambda d: d["message"]["payload"]["body"].update(data=b64("something else")), "body_matches"),
    (lambda d: [h.update(value="attacker@evil.com") for h in d["message"]["payload"]["headers"]
                if h["name"] == "To"], "recipient_matches_source"),
], ids=["wrong_thread", "was_sent", "body_changed", "wrong_recipient"])
async def test_draft_postcondition_catches_a_bad_draft(tamper, failed_check):
    e = normalize_message(gmsg("m1", "t1", "dana@gmail.com", "Order AUTO-2026-1A2B3C4D", "where is my order?"))
    src = FakeSource(users=[USER], orders=[ORDER])
    ctx = await resolve_context(e, [e], src)
    draft = drafts.build_draft("draft_order_status", e, ctx)
    fake = FakeGmail()
    client = client_for(fake)
    did = client.create_draft(drafts.build_rfc822(draft, e, MAILBOX), "t1")["id"]
    assert drafts.verify_gmail_draft(client.get_draft(did), draft, e, did)["ok"] is True
    tamper(fake.drafts[did])
    v = drafts.verify_gmail_draft(client.get_draft(did), draft, e, did)
    assert v["ok"] is False and v["checks"][failed_check] is False
    assert drafts.verify_gmail_draft({"id": did}, draft, e, did) == {
        "ok": False, "mode": "gmail", "checks": {"draft_exists": False}, "send_calls": 0}


async def test_unverifiable_gmail_draft_is_escalated():
    class Tampering(FakeGmail):
        def __call__(self, method, url, headers, body):
            status, raw = super().__call__(method, url, headers, body)
            if method == "GET" and "/drafts/" in url:
                d = json.loads(raw)
                d["message"]["threadId"] = "t-other"
                raw = json.dumps(d).encode()
            return status, raw
    fake = Tampering([gmsg("m1", "t1", "dana@gmail.com", "Order AUTO-2026-1A2B3C4D", "where is my order?")])
    _, store = await run(fake, FakeSource(users=[USER], orders=[ORDER]), gmail_drafts=True)
    row = store.rows["m1"]
    assert row["verification"]["draft"]["ok"] is False and row["requires_human"] is True
    assert row["recommended_action"] == "escalate_to_human"


@pytest.mark.parametrize("lang,text,marker", [("he", "איפה ההזמנה שלי?", "מספר ההזמנה"),
                                              ("ar", "أين طلبي؟ لم يصل الشحن", "رقم الطلب"),
                                              ("en", "where is my order?", "order number")])
async def test_draft_language_follows_the_customer(lang, text, marker):
    e = normalize_message(gmsg("m1", "t1", "dana@gmail.com", "?", text))
    ctx = await resolve_context(e, [e], FakeSource(users=[USER]))
    d = drafts.build_draft("draft_request_order_number", e, ctx)
    assert d.language == lang and marker in d.body and "AutoSpareFinder" in d.body


# ── 16. secret / token redaction ────────────────────────────────────────────────
def test_redaction_patterns_and_registered_values():
    register_secrets(["super-secret-app-password"])
    dirty = (f"Authorization: Bearer {FAKE_ACCESS} refresh_token={FAKE_REFRESH} "
             f'{{"client_secret": "{FAKE_SECRET}"}} pw super-secret-app-password')
    clean = redact(dirty)
    for secret in (FAKE_ACCESS, FAKE_REFRESH, FAKE_SECRET, "super-secret-app-password"):
        assert secret not in clean
    assert clean.count("<redacted>") >= 4


async def test_no_secret_reaches_logs_summary_or_store(caplog):
    caplog.set_level(logging.INFO, logger="email_agent")
    fake = FakeGmail([gmsg("m1", "t1", "dana@gmail.com", "Order AUTO-2026-1A2B3C4D", "where is my order?"),
                      gmsg("m2", "t2", "dana@gmail.com", "x", "y")])
    fake.fail["/threads/t2"] = [500, 500, 500]
    summary, store = await run(fake, FakeSource(users=[USER], orders=[ORDER]), gmail_drafts=True)
    blob = caplog.text + json.dumps(summary, default=str) + json.dumps(store.rows, default=str)
    for secret in (FAKE_ACCESS, FAKE_REFRESH, FAKE_SECRET):
        assert secret not in blob
    events = [json.loads(r.getMessage().split(" ", 1)[1]) for r in caplog.records]
    processed = next(ev for ev in events if ev["event"] == "processed")
    assert processed["message_id"] == "m1" and processed["thread_id"] == "t1" and processed["sent"] is False
    assert processed["classification"] == "order_shipping" and processed["draft_id"] == "draft-1"
    assert processed["context"] == {"customer": "resolved", "order": "resolved",
                                    "shipment": "resolved", "supplier": "unresolved"}
    assert processed["draft_verified"] is True and processed["requires_human"] is False
    failed = next(ev for ev in events if ev["event"] == "failed")
    assert failed["message_id"] == "m2" and "HTTP 500" in failed["failure_reason"]
    assert "where is my order" not in caplog.text                     # body text is never logged


def test_gmail_error_message_is_redacted():
    err = GmailError("auth", f"rejected refresh_token={FAKE_REFRESH} Bearer {FAKE_ACCESS}")
    assert FAKE_REFRESH not in str(err) and FAKE_ACCESS not in str(err)


# ── 17. malformed Gmail responses ───────────────────────────────────────────────
@pytest.mark.parametrize("raw", [None, [], {}, {"id": "m1"}, {"id": "m1", "threadId": "t1"},
                                 {"id": "m1", "threadId": "t1", "payload": "nope"}])
def test_malformed_message_is_rejected(raw):
    with pytest.raises(MalformedMessage):
        normalize_message(raw)


def test_malformed_parts_do_not_crash_normalization():
    raw = {"id": "m1", "threadId": "t1", "snippet": "fallback snippet", "payload": {
        "mimeType": "multipart/mixed", "headers": [{"name": "From", "value": "a@b.com"}, "junk", {"value": "x"}],
        "parts": ["junk", None, {"mimeType": "text/plain", "body": {"data": "!!!not-base64!!!"}},
                  {"mimeType": "text/plain", "body": "nope"}]}}
    e = normalize_message(raw)
    assert e.body_source == "snippet" and e.body_text == "fallback snippet" and e.sender_email == "a@b.com"
    deep = {"mimeType": "multipart/mixed", "parts": []}
    node = deep
    for _ in range(200):
        nxt = {"mimeType": "multipart/mixed", "parts": []}
        node["parts"].append(nxt)
        node = nxt
    assert normalize_message({"id": "m", "threadId": "t", "payload": deep}).body_source == "none"


@pytest.mark.parametrize("path,body,call", [
    ("/profile", b"<html>502</html>", lambda c: c.profile()),
    ("/profile", b"{}", lambda c: c.profile()),
    ("/messages", b'{"messages": "nope"}', lambda c: c.list_message_ids("q")),
    ("/threads/t1", b'{"id": "t-other", "messages": []}', lambda c: c.get_thread("t1")),
    ("/threads/t1", b"[1,2,3]", lambda c: c.get_thread("t1")),
    ("/drafts/d1", b'{"id": "d1"}', lambda c: c.get_draft("d1")),
])
def test_malformed_api_response_raises_malformed(path, body, call):
    fake = FakeGmail()
    fake.raw_override[path] = body
    with pytest.raises(GmailError) as e:
        call(client_for(fake))
    assert e.value.kind == "malformed"


async def test_malformed_thread_fails_that_message_only():
    fake = FakeGmail([gmsg("m1", "t1", "dana@gmail.com", "q", "is this part in stock?"),
                      gmsg("m2", "t2", "dana@gmail.com", "q", "is this part in stock?")])
    fake.raw_override["/threads/t1"] = b'{"id": "t1", "messages": [{"id": "m1"}]}'
    summary, store = await run(fake, FakeSource(users=[USER]))
    assert summary["failed"] == 1 and summary["processed"] == 1
    assert store.rows["m1"]["status"] == "failed" and store.rows["m2"]["status"] == "processed"


# ── 18. attachment metadata ─────────────────────────────────────────────────────
def test_attachment_metadata_only():
    e = normalize_message(gmsg("m1", "t1", "a@b.com", "s", "see attached",
                               attachments=[("part photo.jpg", "image/jpeg", 20480),
                                            ("macro.docm", "application/vnd.ms-word", 999)]))
    assert e.attachments == [
        {"filename": "part photo.jpg", "mime_type": "image/jpeg", "size": 20480, "inline": False, "risky": False},
        {"filename": "macro.docm", "mime_type": "application/vnd.ms-word", "size": 999, "inline": False, "risky": True}]
    assert e.body_text == "see attached"
    assert not any("attachmentId" in json.dumps(a) or "data" in a for a in e.attachments)


# ── read post-condition + client retry behaviour ────────────────────────────────
def test_read_postcondition():
    e = normalize_message(gmsg("m1", "t1", "a@b.com", "s", "body"))
    assert agent_mod.verify_read(e, "m1", "t1")["ok"] is True
    assert agent_mod.verify_read(e, "m-other", "t1")["checks"]["message_id_matches"] is False
    assert agent_mod.verify_read(e, "m1", "t-other")["checks"]["thread_id_matches"] is False


def test_get_is_retried_but_post_is_never_retried():
    fake = FakeGmail()
    fake.fail["/profile"] = [503, 429]
    assert client_for(fake).profile()["emailAddress"] == MAILBOX and fake.paths("GET").count("/profile") == 3
    fake2 = FakeGmail()
    fake2.fail["/drafts"] = [503]
    with pytest.raises(GmailError) as e:
        client_for(fake2).create_draft("cmF3", "t1")
    assert e.value.kind == "transient" and fake2.paths("POST").count("/drafts") == 1


def test_token_is_cached_and_401_triggers_one_refresh():
    fake = FakeGmail()
    c = client_for(fake)
    c.profile(), c.profile()
    assert fake.paths().count("TOKEN") == 1
    fake.fail["/profile"] = [401]
    c.profile()
    assert fake.paths().count("TOKEN") == 2


def test_read_path_issues_only_get_requests():
    fake = FakeGmail([gmsg("m1", "t1", "a@b.com", "s", "b")])
    c = client_for(fake)
    c.profile(), c.list_message_ids("in:inbox"), c.get_thread("t1")
    assert {m for m, p in fake.calls if p != "TOKEN"} == {"GET"}


# ── PgStore SQL against real Postgres (opt-in, TEMP table, leaves nothing behind) ──
@pytest.mark.skipif(os.environ.get("EMAIL_AGENT_PG_TEST") != "1", reason="set EMAIL_AGENT_PG_TEST=1 to run")
async def test_pgstore_sql_against_temp_table():
    from contextlib import asynccontextmanager
    from datetime import datetime, timezone

    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from email_agent.store import PgStore

    path = Path(__file__).resolve().parent.parent / "alembic_pii" / "versions" / "0039_email_agent_messages.py"
    spec = importlib.util.spec_from_file_location("m0039", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    engine = create_async_engine(os.environ["DATABASE_PII_URL"], poolclass=NullPool)
    async with engine.connect() as conn:
        @asynccontextmanager
        async def factory():
            session = AsyncSession(bind=conn, expire_on_commit=False)
            try:
                yield session
            finally:
                await session.close()

        ddl = mod.UPGRADE_SQL[0].replace("CREATE TABLE IF NOT EXISTS email_agent_messages",
                                         "CREATE TEMP TABLE email_agent_messages")
        await conn.execute(text(ddl))
        await conn.commit()
        is_temp = (await conn.execute(text(
            "SELECT relpersistence::text FROM pg_class WHERE oid = 'email_agent_messages'::regclass"))).scalar()
        assert is_temp == "t"                      # every statement below hits the TEMP table only

        store = PgStore(factory)
        assert await store.claim("m1", "t1", 3) == "new"
        assert await store.claim("m1", "t1", 3) == "skip"
        assert await store.mark_failed("m1", f"boom refresh_token={FAKE_REFRESH}", True, 3) == "retry"
        assert FAKE_REFRESH not in (await store.get("m1"))["last_error"]
        assert await store.claim("m1", "t1", 3) == "retry"
        await store.release("m1")
        assert (await store.get("m1"))["attempts"] == 1 and await store.claim("m1", "t1", 3) == "retry"
        await store.mark_draft_attempt("m1")
        assert (await store.get("m1"))["draft_attempted_at"]
        await store.save_result("m1", {
            "sender_email": "dana@gmail.com", "subject": "נושא", "received_at": datetime.now(timezone.utc),
            "classification": "order_shipping", "confidence": 0.75, "reason": "r", "risk_flags": ["customs"],
            "policy_tier": "human_approval_required", "recommended_action": "escalate_to_human",
            "requires_human": True, "context": {"order": {"status": "unresolved"}}, "attachments": [],
            "draft_body": None, "gmail_draft_id": None, "verification": {"send_calls": 0}})
        row = await store.get("m1")
        assert row["status"] == "processed" and row["sendable"] is False and row["requires_human"] is True
        assert row["context"] == {"order": {"status": "unresolved"}} and row["verification"] == {"send_calls": 0}
        assert await store.claim("m1", "t1", 3) == "skip"
        assert await store.stats() == {"by_status": {"processed": 1}, "requires_human": 1}
        assert (await store.recent(5))[0]["gmail_message_id"] == "m1"
        for _ in range(3):
            await store.claim("m2", "t2", 3)
            last = await store.mark_failed("m2", "x", True, 3)
        assert last == "failed" and await store.claim("m2", "t2", 3) == "skip"
        with pytest.raises(IntegrityError):        # DB-level guard: a row can never be made sendable
            await conn.execute(text("UPDATE email_agent_messages SET sendable = true"))
        await conn.rollback()
    await engine.dispose()
    async with create_async_engine(os.environ["DATABASE_PII_URL"], poolclass=NullPool).connect() as check:
        assert (await check.execute(text("SELECT to_regclass('public.email_agent_messages')"))).scalar() is None
