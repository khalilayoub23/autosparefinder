"""Email Agent owner flow (owner decision 2026-10-27): alert important mail, file a task for the
owning agent, a 09:00 IL digest, and send a reply ONLY after the owner's console approval.

No network, no DB: InMemoryStore + InMemoryTodoSink + a fake Gmail client.
"""
import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from email_agent import owner_flow as of
from email_agent.gmail_client import GmailClient, GmailError, OwnerApproval
from email_agent.store import InMemoryStore

UTC = timezone.utc


def _row(store, **kw):
    rid = str(uuid.uuid4())
    row = {"id": rid, "gmail_message_id": "m" + rid[:6], "status": "processed", "sendable": False,
           "created_at": datetime(2026, 10, 27, 8, 0, tzinfo=UTC), "sender_email": "a@shop.example",
           "subject": "s", "classification": "order_shipping", "requires_human": True,
           "recommended_action": "reply", "gmail_draft_id": None, "draft_body": None,
           "owner_notified_at": None, "digest_at": None, "owner_closed_at": None, "task_ids": [],
           "send_approved_at": None, "send_approved_by": None, "sent_at": None,
           "gmail_sent_message_id": None, "send_error": None}
    row.update(kw)
    store.rows[row["gmail_message_id"]] = row
    return row


class _Notify:
    def __init__(self, result=None):
        self.calls, self.result = [], result or {"ok": True}

    async def __call__(self, category, title, body, **kw):
        self.calls.append((category, title, body, kw))
        return self.result


class _Gmail:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def get_draft(self, draft_id):
        return {"id": draft_id, "message": {}}

    def send_approved_draft(self, approval):
        assert isinstance(approval, OwnerApproval)
        if self.fail:
            raise self.fail
        self.sent.append(approval.draft_id)
        return {"id": "sent1", "labelIds": ["SENT"]}


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setattr(of._control, "read_control", lambda *a, **k: {"running": True, "reason": "test"})


MORNING = datetime(2026, 10, 27, 5, 0, tzinfo=UTC)     # 07:00 IL - before the digest hour


async def test_important_mail_alerts_once_and_files_one_task():
    store, todos, notify = InMemoryStore(), of.InMemoryTodoSink(), _Notify()
    r = _row(store)
    out = await of.process_owner_updates(store, notify, todos, now=MORNING)
    assert (out["alerted"], out["tasks"]) == (1, 1)
    assert notify.calls[0][0] == "email" and notify.calls[0][3]["alert_key"] == f"email_{r['id']}"
    assert todos.todos[0]["agent"] == of.TASK_ROUTES["order_shipping"][0]
    assert "a@shop.example" not in str(todos.todos[0])           # task carries the domain only
    out2 = await of.process_owner_updates(store, notify, todos, now=MORNING)
    assert out2["alerted"] == 0 and len(notify.calls) == 1 and len(todos.todos) == 1


async def test_unimportant_mail_waits_for_the_digest():
    store, todos, notify = InMemoryStore(), of.InMemoryTodoSink(), _Notify()
    _row(store, classification="newsletter", requires_human=False)
    out = await of.process_owner_updates(store, notify, todos, now=MORNING)
    assert out["quiet"] == 1 and notify.calls == [] and todos.todos == []


async def test_failed_alert_is_retried_next_cycle():
    store, todos = InMemoryStore(), of.InMemoryTodoSink()
    _row(store)
    out = await of.process_owner_updates(store, _Notify({"ok": False}), todos, now=MORNING)
    assert out["alert_failed"] == 1
    out = await of.process_owner_updates(store, _Notify(), todos, now=MORNING)
    assert out["alerted"] == 1 and len(todos.todos) == 1           # no duplicate task on retry


async def test_digest_once_per_day_after_nine_israel_time():
    store, todos, notify = InMemoryStore(), of.InMemoryTodoSink(), _Notify()
    _row(store, classification="newsletter", requires_human=False)
    await of.process_owner_updates(store, notify, todos, now=MORNING)
    assert not [c for c in notify.calls if "digest" in c[3]["alert_key"]]
    nine = datetime(2026, 10, 27, 7, 5, tzinfo=UTC)               # 09:05 IL (UTC+2 after Oct 25)
    out = await of.process_owner_updates(store, notify, todos, now=nine)
    assert out["digest"] is True
    out = await of.process_owner_updates(store, notify, todos, now=nine + timedelta(hours=2))
    assert out["digest"] is False                                 # not twice the same day


async def test_send_requires_a_gmail_draft(armed):
    store, todos = InMemoryStore(), of.InMemoryTodoSink()
    r = _row(store)
    gm = _Gmail()
    msg = await of.approve_and_send(store, gm, todos, r["id"][:8])
    assert "אין טיוטה" in msg and gm.sent == [] and store.rows[r["gmail_message_id"]]["sent_at"] is None


async def test_owner_approved_send_records_approval_then_sent(armed):
    store, todos, notify = InMemoryStore(), of.InMemoryTodoSink(), _Notify()
    r = _row(store, gmail_draft_id="d1")
    await of.process_owner_updates(store, notify, todos, now=MORNING)
    gm = _Gmail()
    msg = await of.approve_and_send(store, gm, todos, r["id"][:8])
    row = store.rows[r["gmail_message_id"]]
    assert gm.sent == ["d1"] and row["send_approved_at"] and row["sent_at"] and "✅" in msg
    assert todos.todos[0]["status"] == "completed"
    again = await of.approve_and_send(store, gm, todos, r["id"][:8])
    assert "כבר נשלח" in again and gm.sent == ["d1"]


async def test_send_refused_while_halted(monkeypatch):
    monkeypatch.setattr(of._control, "read_control", lambda *a, **k: {"running": False, "reason": "x"})
    store, todos = InMemoryStore(), of.InMemoryTodoSink()
    r = _row(store, gmail_draft_id="d1")
    gm = _Gmail()
    msg = await of.approve_and_send(store, gm, todos, r["id"][:8])
    assert "עצור" in msg and gm.sent == [] and store.rows[r["gmail_message_id"]]["send_approved_at"] is None


async def test_send_failure_is_recorded_not_marked_sent(armed):
    store, todos = InMemoryStore(), of.InMemoryTodoSink()
    r = _row(store, gmail_draft_id="d1")
    msg = await of.approve_and_send(store, _Gmail(fail=GmailError("scope", "insufficient")), todos, r["id"][:8])
    row = store.rows[r["gmail_message_id"]]
    assert "❌" in msg and row["sent_at"] is None and row["send_error"]


async def test_close_without_sending_closes_tasks():
    store, todos, notify = InMemoryStore(), of.InMemoryTodoSink(), _Notify()
    r = _row(store)
    await of.process_owner_updates(store, notify, todos, now=MORNING)
    msg = await of.close_item(store, todos, r["id"][:8])
    assert "סומן כטופל" in msg and todos.todos[0]["status"] == "completed"
    assert await of.open_items_text(store) == "📧 אין מיילים שממתינים לך ✅"


async def test_store_refuses_sent_without_approval():
    store = InMemoryStore()
    r = _row(store)
    with pytest.raises(ValueError):
        await store.mark_sent(r["id"], "x")


def test_send_approved_draft_rejects_missing_approval():
    client = GmailClient.__new__(GmailClient)
    with pytest.raises(Exception):
        client.send_approved_draft(None)
    with pytest.raises(Exception):
        client.send_approved_draft(OwnerApproval(row_id="r", draft_id="", approved_by="o", approved_at=1.0))


async def test_console_routes_only_email_commands(monkeypatch):
    from agents import email_console
    assert await email_console.handle("סטטוס") is None
    assert await email_console.handle("שלום") is None
    store, todos = InMemoryStore(), of.InMemoryTodoSink()
    monkeypatch.setattr(email_console, "_deps", lambda: (store, todos))
    assert "אין מיילים" in await email_console.handle("מיילים")
    assert "אין משימות" in await email_console.handle("משימות מייל")


def test_migration_0041_ties_send_to_approval_and_does_not_replay_history():
    path = Path(__file__).resolve().parent.parent / "alembic_pii" / "versions" / "0041_email_agent_owner_flow.py"
    spec = importlib.util.spec_from_file_location("m0041", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    sql = " ".join(m.UPGRADE_SQL)
    assert m.down_revision == "0040_messages_chat_req_uniq"
    assert "CHECK (sent_at IS NULL OR send_approved_at IS NOT NULL)" in sql
    assert "SET owner_notified_at = created_at, digest_at = created_at" in sql
    assert "sendable" not in sql                                  # the never-autonomous CHECK is untouched


@pytest.mark.parametrize("cat", ["supplier", "aliexpress", "ebay", "shipping_eurosender"])
async def test_supplier_mail_needing_a_human_goes_to_boaz(cat):
    store, todos, notify = InMemoryStore(), of.InMemoryTodoSink(), _Notify()
    _row(store, classification=cat, requires_human=True)
    await of.process_owner_updates(store, notify, todos, now=MORNING)
    assert [t["agent"] for t in todos.todos] == ["BOAZ"]


async def test_routine_marketplace_notification_files_no_task():
    store, todos, notify = InMemoryStore(), of.InMemoryTodoSink(), _Notify()
    _row(store, classification="aliexpress", requires_human=False)
    await of.process_owner_updates(store, notify, todos, now=MORNING)
    assert todos.todos == [] and notify.calls == []
