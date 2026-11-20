"""Supplier history pass + structured intake + outreach recipient guard (2026-11-20).

Root cause under test: the Email Agent scanned only `in:inbox newer_than:14d` (+spam), so SNG Barratt's authenticated
shipping reply (14 Sep 2026, before the agent was armed) was never ingested, never routed and never stored as data.
"""
import json
import urllib.parse
import uuid
from datetime import datetime, timezone

import pytest

from email_agent import agent as agent_mod
from email_agent import supplier_intake as si
from email_agent import supplier_routing as sr
from email_agent.gmail_client import API_BASE, GmailClient
from email_agent.store import InMemoryStore
import supplier_outreach as so
import supplier_qualification as Q
from tests.test_email_agent import FakeGmail, FakeSource, MAILBOX, cfg, gmsg, _armed_runtime_control  # noqa: F401

SNG_BODY = ("Good Afternoon,\n\nThank you for your enquiry, we use DHL for shipping however the shipping rates do vary "
            "almost on a daily basis at the moment. If you register online you can see shipping prices on the website. "
            "Shipping usually takes 3-5 days.\n\nKind Regards\n\nSimon Cottrill\nSales Manager – SNG Barratt (UK)\n"
            "T: +44 (0)1746 765432\nSNGBarratt.com | MossRimmer.com\n\nFrom: auto spare finder\nSent: 02 September 2026\n"
            "Subject: Shipping cost inquiry\nPlease quote a parcel to Israel")


class HistoryGmail(FakeGmail):
    """/messages honours `in:anywhere` (any label), pagination (25/page) and ignores categories we do not model."""

    def __call__(self, method, url, headers, body):
        if url.startswith(API_BASE + "/messages?") or url == API_BASE + "/messages":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            q = (qs.get("q") or [""])[0]
            self.calls.append((method, "/messages"))
            self.queries = getattr(self, "queries", []) + [q]
            pool = [m for m in self.messages if ("INBOX" in m["labelIds"] if "in:anywhere" not in q else True)
                    and "SENT" not in m["labelIds"]]
            start = int((qs.get("pageToken") or ["0"])[0])
            page = pool[start:start + 25]
            out = {"messages": [{"id": m["id"], "threadId": m["threadId"]} for m in page]}
            if start + 25 < len(pool):
                out["nextPageToken"] = str(start + 25)
            return 200, json.dumps(out).encode()
        return super().__call__(method, url, headers, body)


class HistSource(FakeSource):
    async def known_supplier_domains(self):
        return ["sngbarratt.com", "partsgmbh.de"]


def test_history_queries_cover_every_folder_and_chunk_domains():
    qs = agent_mod.history_queries([f"s{i}.com" for i in range(40)], "400d")
    assert len(qs) == 3 and all(q.startswith("in:anywhere newer_than:400d") for q in qs)
    assert all("-category:promotions" in q and "-from:me" in q for q in qs)
    assert agent_mod.history_queries(["", "nodot"], "30d") == []


async def test_old_non_inbox_supplier_reply_is_ingested_beyond_page_one(monkeypatch):
    """60 older messages from a known supplier sit before the real reply: pagination must reach it."""
    monkeypatch.setattr(agent_mod, "_HISTORY_NEXT_FULL_SCAN", 0.0)
    msgs = [gmsg(f"old{i}", f"told{i}", "Parts <a@partsgmbh.de>", f"note {i}", text="hello", labels=("CATEGORY_UPDATES",),
                 ts=1_790_000_000_000 - i) for i in range(60)]
    msgs.append(gmsg("sng1", "tsng", "Simon Cottrill <Simon.Cottrill@sngbarratt.com>", "RE:", text=SNG_BODY,
                     labels=("CATEGORY_PERSONAL",), ts=1_789_398_894_000))
    fake = HistoryGmail(msgs)
    c = cfg(scan_history=True, history_max_per_cycle=100)
    store = InMemoryStore()
    s = await agent_mod.process_once(c, GmailClient(c, transport=fake, sleep=lambda x: None), store, HistSource())
    assert s["history"]["processed"] == 61 and s["history"]["failed"] == 0
    assert (await store.get("sng1"))["source_folder"] == "history"
    assert any("in:anywhere" in q and "from:sngbarratt.com" in q for q in fake.queries)
    assert fake.drafts == {}                                  # no Gmail draft is created for old mail


async def test_history_pass_off_switch_and_idle_rest(monkeypatch):
    monkeypatch.setattr(agent_mod, "_HISTORY_NEXT_FULL_SCAN", 0.0)
    fake = HistoryGmail([gmsg("a", "ta", "x <a@sngbarratt.com>", "s", text="t", labels=("CATEGORY_PERSONAL",))])
    c = cfg(scan_history=False)
    s = await agent_mod.process_once(c, GmailClient(c, transport=fake, sleep=lambda x: None), InMemoryStore(), HistSource())
    assert "history" not in s
    c = cfg(scan_history=True)
    store = InMemoryStore()
    await agent_mod.process_once(c, GmailClient(c, transport=fake, sleep=lambda x: None), store, HistSource())
    await agent_mod.process_once(c, GmailClient(c, transport=fake, sleep=lambda x: None), store, HistSource())  # finds nothing new
    n = sum('in:anywhere' in q for q in fake.queries)
    s2 = await agent_mod.process_once(c, GmailClient(c, transport=fake, sleep=lambda x: None), store, HistSource())
    assert s2["history"].get("skipped") and sum('in:anywhere' in q for q in fake.queries) == n      # nothing new -> rests, no repeated scanning


def test_extract_facts_sng_reply():
    f = si.extract_facts(SNG_BODY, "Simon.Cottrill@sngbarratt.com", "Simon Cottrill")
    assert (f["name"], f["role"], f["phone"]) == ("Simon Cottrill", "Sales Manager - SNG Barratt (UK)", "+44 (0)1746 765432")
    assert f["carriers"] == ["DHL"] and (f["transit_days_min"], f["transit_days_max"]) == (3, 5)
    assert f["rates_variable"] is True


def test_quoted_original_is_not_read_as_the_suppliers_statement():
    f = si.extract_facts("Hello\n\nKind regards\nJo Smith\nSales Manager - X\n\nFrom: us\nWe ship UPS in 2-3 days", "j@x.com")
    assert f["carriers"] == [] and f["transit_days_min"] is None


def test_match_supplier_exactly_one_domain_only():
    sups = [{"id": "1", "website": "https://www.sngbarratt.com"}, {"id": "2", "website": "https://a.com"},
            {"id": "3", "website": "https://a.com/shop"}]
    assert si.match_supplier("sngbarratt.com", sups)["id"] == "1"
    assert si.match_supplier("a.com", sups) is None            # ambiguous -> nothing guessed
    assert si.match_supplier("evil-sngbarratt.com", sups) is None
    assert si.match_supplier("gmail.com", sups) is None


def test_authentication_requires_the_senders_own_domain():
    ok = "mx.google.com; dkim=pass header.i=@sngbarratt.com header.s=s; dmarc=pass (p=REJECT) header.from=sngbarratt.com"
    assert si.authenticated(ok, "sngbarratt.com")
    assert not si.authenticated(ok, "other.com")
    assert not si.authenticated("dkim=fail header.i=@sngbarratt.com", "sngbarratt.com")
    assert not si.authenticated("", "sngbarratt.com")


def test_merge_is_idempotent_unverified_sender_adds_no_shipping_and_never_a_price_or_address():
    facts = si.extract_facts(SNG_BODY, "Simon.Cottrill@sngbarratt.com", "Simon Cottrill")
    t = datetime(2026, 11, 20, tzinfo=timezone.utc)
    s1 = si.merge_shipping_info({"carriers": ["FedEx"], "avg_delivery_days": 14}, facts, evidence="gmail:1", verified=True, now=t)
    s2 = si.merge_shipping_info(s1, facts, evidence="gmail:1", verified=True, now=t)
    assert s1 == s2 and s1["avg_delivery_days"] == 14 and s1["carriers"] == ["FedEx"]     # existing keys untouched
    st = s1["supplier_stated"]
    assert st["rates"] == "variable_live_quote_required" and "price" not in json.dumps(st).lower()
    assert "pickup" not in json.dumps(s1).lower() and "address" not in json.dumps(s1).lower()
    assert si.merge_shipping_info({}, facts, evidence="x", verified=False) == {}
    c1 = si.merge_contact([], facts, evidence="gmail:1", verified=True, now=t)
    assert si.merge_contact(c1, facts, evidence="gmail:1", verified=True, now=t) == c1 and len(c1) == 1


class _Todos:
    def __init__(self):
        self.todos = []

    async def create(self, agent, title, description, priority, artifacts):
        self.todos.append({"agent": agent, "artifacts": artifacts})
        return f"task-{len(self.todos)}"


class _Intake:
    def __init__(self, reply=True, source=None):
        self.reply, self.source, self.calls = reply, source, 0

    async def suppliers(self):
        return [{"id": "sup-sng", "name": "SNG Barratt", "website": "https://www.sngbarratt.com", "source": self.source}]

    async def process(self, row, sup):
        self.calls += 1
        return {"is_reply": self.reply, "authenticated": True, "stored": True,
                "facts": si.extract_facts(SNG_BODY, row["sender_email"], "Simon Cottrill")}


def _row(store, mid, sender, classification="customer_other"):
    r = {"id": str(uuid.uuid4()), "gmail_message_id": mid, "gmail_thread_id": "t-" + mid, "status": "processed",
         "sender_email": sender, "classification": classification, "supplier_routed_at": None, "supplier_route": None,
         "task_ids": [], "source_folder": "history", "requires_human": False,
         "created_at": datetime.now(timezone.utc)}
    store.rows[mid] = r
    return r


async def test_manual_inquiry_reply_from_known_supplier_routes_to_boaz_with_supplier_id():
    store, ost, todos = InMemoryStore(), so.InMemoryOutreachStore(), _Todos()
    r = _row(store, "m1", "Simon.Cottrill@sngbarratt.com")
    out = await sr.route_supplier_replies(store, ost, todos, intake=_Intake())
    assert out["to_boaz"] == 1 and todos.todos[0]["agent"] == "BOAZ"
    assert todos.todos[0]["artifacts"]["supplier_id"] == "sup-sng"
    assert r["supplier_route"]["owner"] == "BOAZ" and r["supplier_route"]["facts_stored"] is True
    again = await sr.route_supplier_replies(store, ost, todos, intake=_Intake())
    assert sum(again.values()) == 0 and len(todos.todos) == 1               # no duplicate task


async def test_nir_candidate_reply_goes_to_nir_and_a_newsletter_creates_no_task():
    store, ost, todos = InMemoryStore(), so.InMemoryOutreachStore(), _Todos()
    _row(store, "m1", "a@sngbarratt.com")
    out = await sr.route_supplier_replies(store, ost, todos, intake=_Intake(source="nir_sourcing"))
    assert out["to_nir"] == 1 and todos.todos[0]["agent"] == "NIR"
    store2, todos2 = InMemoryStore(), _Todos()
    _row(store2, "m2", "news@sngbarratt.com")                                  # we never wrote to them first
    out2 = await sr.route_supplier_replies(store2, ost, todos2, intake=_Intake(reply=False))
    assert todos2.todos == [] and out2["to_boaz"] == 0


async def test_unknown_domain_is_not_attached_to_any_supplier():
    store, ost, todos = InMemoryStore(), so.InMemoryOutreachStore(), _Todos()
    r = _row(store, "m1", "x@alvadimail.com")
    intake = _Intake()
    await sr.route_supplier_replies(store, ost, todos, intake=intake)
    assert intake.calls == 0 and r["supplier_route"] == {"owner": None} and todos.todos == []


@pytest.mark.parametrize("addr", ["info@yourwebsite.com", "name@example.com", "x@autoworks.co.i", "nobody", "",
                                  "a@test.com"])
def test_placeholder_and_malformed_recipients_are_rejected(addr):
    assert not Q.valid_email(addr)


async def test_plan_and_send_refuse_a_placeholder_recipient():
    rec = Q.new_record("s1")
    rec["requirements"]["contacts"]["details"] = {"contacts": [
        {"address": "info@yourwebsite.com", "status": Q.VERIFIED, "source": "https://x"}]}
    store = so.InMemoryOutreachStore()
    assert (await so.plan(store, "s1", rec))["action"] == "skip"
    row = {"id": "r1", "supplier_id": "s1", "recipient": "info@yourwebsite.com", "purposes": ["api_info"], "state": "READY_TO_CONTACT"}
    store.rows.append(row)
    import os
    os.environ["NIR_OUTREACH_ENABLED"] = "1"
    try:
        res = await so.send_outreach(store, None, None, {"outreach": row, "subject": "s", "body": "b"}, sender=MAILBOX)
    finally:
        os.environ.pop("NIR_OUTREACH_ENABLED", None)
    assert res["sent"] is False and "valid business address" in res["reason"]


async def test_known_supplier_domains_query_has_no_bind_collision():
    """Regression: a `(?:https?://` regex inside text() was parsed as bind parameter ':https' and the live history pass
    never ran. The method must issue a colon-free statement and parse hosts in Python."""
    from email_agent.context import DbContextSource
    seen = []

    class Src(DbContextSource):
        async def _rows(self, db, sql, params):
            seen.append(sql)
            return [{"website": "https://www.SNGBarratt.com/x"}, {"website": "alvadi.co.il/en"}, {"website": "nodot"}]

    assert await Src().known_supplier_domains() == ["alvadi.co.il", "sngbarratt.com"]
    assert ":" not in seen[0]
