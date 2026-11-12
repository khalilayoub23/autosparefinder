"""
Script: email_agent/owner_flow.py
Purpose: The owner side of the Email Agent (owner decision 2026-10-27): tell the owner about mail,
         hand each matter to the agent that owns it, and send a reply ONLY after the owner
         approves that reply in the WhatsApp console.
Process:
  process_owner_updates()  called by email_agent/loop.py after every cycle (agent armed only):
     1. Each newly processed email is considered once (owner_notified_at). IMPORTANT mail - it
        needs a human, has a reply draft, or is a customer / order / refund / complaint /
        payment / security / supplier email - gets
          a. a task in agent_todos for the owning agent (TASK_ROUTES), once (task_ids), and
          b. an immediate WhatsApp alert through notify_owner (quiet hours 09:00-21:00 apply;
             alert_key per email so it is never sent twice).
        Everything else (newsletters, automated notices, marketplace mail) waits for the digest.
     2. Once a day from 09:00 Israel time: a digest of every email not yet in a digest, plus the
        list of emails still waiting for the owner (open_items).
  approve_and_send()  the console's "שלחמייל <ref>": checks the email has a Gmail draft and is
        not sent / closed, re-reads the draft, RECORDS the approval, then sends exactly that draft
        (GmailClient.send_approved_draft with an OwnerApproval) and records the Gmail message id.
        The table enforces CHECK (sent_at IS NULL OR send_approved_at IS NOT NULL).
  close_item() / describe_item() / open_items_text() / open_tasks_text(): console helpers.
  Nothing here sends autonomously: policy.send_allowed() stays False; a send needs the owner.
Data Imported/Modified: email_agent_messages owner columns (PII DB, alembic_pii 0041);
         agent_todos rows with category 'email' (catalog DB) - sender DOMAIN and email ref only,
         no address / subject / body leaves the PII database.
Data Sources: email_agent store, Gmail API (drafts.get, drafts.send - owner-approved only).
Missing Data Delegation: the mapped chat agents (LIOR / DANA / TAL / BOAZ / NIR / OREN) do not
         work their todos on their own yet; the owner sees and closes them in the console.
Last Updated: 2026-10-27
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from email_agent import control as _control
from email_agent.gmail_client import GmailError, OwnerApproval
from email_agent.redaction import safe_error

IL = ZoneInfo("Asia/Jerusalem")
DIGEST_HOUR_IL = 9

IMPORTANT_CATEGORIES = ("customer_inquiry", "order_shipping", "refund_cancellation", "complaint_dispute",
                        "payment_billing", "account_security", "supplier")
# classification -> (agent that owns the matter, agent_todos priority)
TASK_ROUTES: Dict[str, tuple] = {
    "order_shipping": ("LIOR", "medium"),
    "refund_cancellation": ("DANA", "high"),
    "complaint_dispute": ("DANA", "high"),
    "payment_billing": ("TAL", "high"),
    "supplier": ("BOAZ", "medium"),
    # marketplace / logistics suppliers (2026-10-27: 14 AliExpress mails, 2 needed a human) - BOAZ owns them.
    # Filed only when the mail is important (needs a human / has a draft); notifications stay in the digest.
    "aliexpress": ("BOAZ", "medium"),
    "ebay": ("BOAZ", "medium"),
    "shipping_eurosender": ("BOAZ", "medium"),
    "customer_inquiry": ("NIR", "medium"),
    "account_security": ("OREN", "high"),
}
CATEGORY_HE = {
    "customer_inquiry": "פניית לקוח", "order_shipping": "הזמנה / משלוח", "refund_cancellation": "החזר / ביטול",
    "complaint_dispute": "תלונה", "payment_billing": "תשלום / חיוב", "account_security": "אבטחת חשבון",
    "supplier": "ספק", "aliexpress": "AliExpress", "ebay": "eBay", "shipping_eurosender": "Eurosender (משלוחים)", "automated_notification": "הודעה אוטומטית",
    "newsletter_marketing": "ניוזלטר / שיווק", "spam_irrelevant": "ספאם", "unknown": "לא מסווג",
}
ACTION_HE = {
    "escalate_to_human": "מחכה לך — דורש החלטה אנושית",
    "draft_order_status": "הכין טיוטת תשובה על סטטוס ההזמנה",
    "draft_request_order_number": "הכין טיוטה שמבקשת מספר הזמנה",
    "draft_request_details": "הכין טיוטה שמבקשת פרטים",
    "ignore": "לא נדרשת פעולה", "no_reply": "לא נדרשת תשובה",
}

Notify = Callable[..., Awaitable[Dict[str, Any]]]


def ref(row: Dict[str, Any]) -> str:
    return str(row.get("id") or "")[:8]


def _sender_domain(row: Dict[str, Any]) -> str:
    s = str(row.get("sender_email") or "")
    return s.split("@", 1)[1].lower() if "@" in s else "?"


def _json(v, default):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return default
    return v if v is not None else default


def _route(row: Dict[str, Any]) -> Dict[str, Any]:
    return _json(row.get("supplier_route"), {}) or {}


def is_important(row: Dict[str, Any]) -> bool:
    return bool(row.get("requires_human") or row.get("gmail_draft_id") or row.get("draft_body")
                or row.get("classification") in IMPORTANT_CATEGORIES
                or _route(row).get("owner") in ("NIR", "exception")
                or bool(_route(row).get("procurement_id"))
                or (row.get("source_folder") == "spam"))


def _draft_state(row: Dict[str, Any]) -> str:
    if row.get("sent_at"):
        return "נשלחה ✅"
    if row.get("gmail_draft_id"):
        return "טיוטה מוכנה ב-Gmail"
    if row.get("draft_body"):
        return "טיוטה פנימית (לא ב-Gmail)"
    return "אין"


def _order_line(row: Dict[str, Any]) -> str:
    order = (_json(row.get("context"), {}) or {}).get("order") or {}
    if order.get("status") == "resolved" and order.get("order_number"):
        return f"הזמנה: {order['order_number']} ({order.get('order_status') or order.get('status_text') or 'נמצאה'})"
    if order.get("status") in ("sender_mismatch", "ambiguous"):
        return "הזמנה: הוזכרה אך לא אומתה מול השולח"
    return ""


def format_alert(row: Dict[str, Any], task_agent: Optional[str]) -> tuple:
    cat = row.get("classification") or "unknown"
    title = f"מייל חדש — {CATEGORY_HE.get(cat, cat)}"
    flags = [f for f in (_json(row.get("risk_flags"), []) or []) if isinstance(f, str)]
    r = ref(row)
    lines = [f"מאת: {row.get('sender_email') or '?'}",
             f"נושא: {str(row.get('subject') or '(ללא נושא)')[:120]}"]
    if row.get("reason"):
        lines.append(f"למה: {str(row['reason'])[:160]}")
    if flags:
        lines.append("דגלים: " + ", ".join(flags[:6]))
    ol = _order_line(row)
    if ol:
        lines.append(ol)
    lines.append(f"הסוכן: {ACTION_HE.get(row.get('recommended_action') or '', row.get('recommended_action') or '-')}")
    lines.append(f"טיוטה: {_draft_state(row)}")
    if row.get("source_folder") == "spam":
        lines.append("📥 נמצא בתיקיית הספאם — סמן 'לא ספאם' ב-Gmail אם זה לגיטימי (לסוכן אין הרשאה לשנות תוויות)")
    rt = _route(row)
    if rt.get("owner") == "NIR":
        lines.append("↩️ תשובה לפנייה של NIR לספק — הועבר ל-NIR לבדיקה")
    elif rt.get("owner") == "exception":
        lines.append(f"⚠️ התאמת ספק לא חד-משמעית: {str(rt.get('reason') or '')[:120]} — דורש החלטה שלך")
    if task_agent:
        lines.append(f"משימה נפתחה ל-{task_agent}")
    cmds = [f"*מייל {r}* לפרטים"]
    if row.get("gmail_draft_id"):
        cmds.append(f"*שלחמייל {r}* לשליחת הטיוטה")
    cmds.append(f"*סגורמייל {r}* לסגירה")
    lines.append(" · ".join(cmds))
    return title, "\n".join(lines)


def format_digest(rows: List[Dict[str, Any]], open_rows: List[Dict[str, Any]]) -> Optional[tuple]:
    """Daily digest = what CHANGED since the previous digest. Nothing received -> no digest (the
    backlog alone used to be re-sent every morning with the same eight oldest items). Open items
    are listed only when new; the unchanged backlog is one count line."""
    if not rows:
        return None
    by_cat: Dict[str, int] = {}
    for r in rows:
        c = r.get("classification") or "unknown"
        by_cat[c] = by_cat.get(c, 0) + 1
    lines = [f"התקבלו מאז הסיכום הקודם: {len(rows)}"]
    for c, n in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        lines.append(f"• {CATEGORY_HE.get(c, c)}: {n}")
    quiet = [r for r in rows if not is_important(r)]
    if quiet:
        lines.append("")
        lines.append("ללא צורך בפעולה (לדוגמה):")
        for r in quiet[:5]:
            lines.append(f"• {_sender_domain(r)} — {str(r.get('subject') or '')[:60]}")
    lines.append("")
    if open_rows:
        new_ids = {str(r.get("id")) for r in rows}
        fresh = [r for r in open_rows if str(r.get("id")) in new_ids]
        older = len(open_rows) - len(fresh)
        if fresh:
            lines.append(f"🆕 חדשים שממתינים לך: {len(fresh)}")
            for r in fresh[:8]:
                lines.append(f"• {ref(r)} · {CATEGORY_HE.get(r.get('classification') or 'unknown', '?')} · "
                             f"{_sender_domain(r)} · {_draft_state(r)}")
        if older:
            lines.append(f"ממתינים מקודם (ללא שינוי): {older}")
        lines.append(f"סה\"כ ממתינים: {len(open_rows)} — כתוב *מיילים* לרשימה המלאה")
    else:
        lines.append("אין מיילים שממתינים לך ✅")
    return "סיכום מיילים יומי", "\n".join(lines)


class InMemoryTodoSink:
    def __init__(self):
        self.todos: List[Dict[str, Any]] = []

    async def create(self, agent: str, title: str, description: str, priority: str, artifacts: Dict[str, Any]) -> str:
        tid = f"todo-{len(self.todos) + 1:04d}0000"
        self.todos.append({"id": tid, "agent": agent, "title": title, "description": description,
                           "priority": priority, "artifacts": artifacts, "status": "not_started"})
        return tid

    async def open_email_tasks(self, limit: int = 15) -> List[Dict[str, Any]]:
        return [t for t in self.todos if t["status"] in ("not_started", "in_progress")][:limit]

    async def complete(self, task_ids: List[str]) -> int:
        n = 0
        for t in self.todos:
            if t["id"] in task_ids and t["status"] in ("not_started", "in_progress"):
                t["status"] = "completed"
                n += 1
        return n

    async def by_prefix(self, prefix: str) -> List[Dict[str, Any]]:
        return [t for t in self.todos if t["id"].startswith(prefix)]


class PgTodoSink:
    """agent_todos in the CATALOG DB. Only the sender domain and the email ref go here."""

    def __init__(self, session_factory=None):
        self._factory = session_factory

    def _session(self):
        if self._factory is None:
            from BACKEND_DATABASE_MODELS import async_session_factory
            self._factory = async_session_factory
        return self._factory()

    async def create(self, agent: str, title: str, description: str, priority: str, artifacts: Dict[str, Any]) -> str:
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text("""
                INSERT INTO agent_todos (id, title, description, status, priority, assigned_to_agent,
                                         category, tags, artifacts, created_at, updated_at)
                VALUES (gen_random_uuid(), :t, :d, 'not_started', :p, :a, 'email',
                        ARRAY['email_agent']::varchar[], CAST(:art AS jsonb), NOW(), NOW())
                RETURNING id::text
            """), {"t": title[:250], "d": description, "p": priority, "a": agent,
                   "art": json.dumps(artifacts, ensure_ascii=False)})
            tid = res.scalar()
            await db.commit()
        return tid

    async def open_email_tasks(self, limit: int = 15) -> List[Dict[str, Any]]:
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text(
                "SELECT id::text AS id, title, assigned_to_agent AS agent, priority, status, created_at "
                "FROM agent_todos WHERE category = 'email' AND status IN ('not_started', 'in_progress') "
                "ORDER BY created_at DESC LIMIT :n"), {"n": limit})
            return [dict(r._mapping) for r in res.fetchall()]

    async def complete(self, task_ids: List[str]) -> int:
        if not task_ids:
            return 0
        from sqlalchemy import text
        async with self._session() as db:
            res = await db.execute(text(
                "UPDATE agent_todos SET status = 'completed', completed_at = NOW(), updated_at = NOW() "
                "WHERE id = ANY(CAST(:ids AS uuid[])) AND category = 'email' "
                "AND status IN ('not_started', 'in_progress')"), {"ids": list(task_ids)})
            await db.commit()
            return res.rowcount or 0

    async def by_prefix(self, prefix: str) -> List[Dict[str, Any]]:
        from sqlalchemy import text
        prefix = "".join(ch for ch in (prefix or "").lower() if ch in "0123456789abcdef-")
        if len(prefix) < 4:
            return []
        async with self._session() as db:
            res = await db.execute(text(
                "SELECT id::text AS id, title, assigned_to_agent AS agent, status FROM agent_todos "
                "WHERE category = 'email' AND id::text LIKE :p LIMIT 5"), {"p": prefix + "%"})
            return [dict(r._mapping) for r in res.fetchall()]


async def _file_task(store, todos, row: Dict[str, Any]) -> Optional[str]:
    """One task per important email for the agent that owns the matter. Returns the agent name."""
    owner = _route(row).get("owner")
    if owner == "NIR":                    # supplier_routing already filed NIR's task for this reply
        return "NIR"
    if owner == "BOAZ" and _route(row).get("procurement_id"):   # purchase-order reply: task already filed
        return "BOAZ"
    if owner == "exception":              # ambiguous supplier match: the owner decides, no agent guesses
        return None
    route = TASK_ROUTES.get(row.get("classification") or "")
    if not route:
        return None
    if _json(row.get("task_ids"), []):
        return route[0]
    agent, priority = route
    cat = row.get("classification")
    title = f"מייל: {CATEGORY_HE.get(cat, cat)} מ-{_sender_domain(row)}"
    description = (f"Email Agent ref {ref(row)} · {cat} · action {row.get('recommended_action')} · "
                   f"draft: {_draft_state(row)}. Details in the owner console: 'מייל {ref(row)}'.")
    tid = await todos.create(agent, title, description, priority,
                             {"source": "email_agent", "email_ref": ref(row), "classification": cat,
                              "requires_human": bool(row.get("requires_human"))})
    await store.set_task_ids(row["id"], [tid])
    return agent


def _delivered(result: Dict[str, Any]) -> bool:
    """notify_owner outcome that means "the owner will get it": sent, queued for 09:00, or an alert
    with this key was already delivered (cooldown)."""
    return bool(result.get("ok") or result.get("queued") or result.get("suppressed"))


async def process_owner_updates(store, notify: Notify, todos, now: Optional[datetime] = None,
                                limit: int = 25) -> Dict[str, Any]:
    out = {"alerted": 0, "tasks": 0, "quiet": 0, "alert_failed": 0, "digest": False}
    for row in await store.owner_pending(limit):
        if not is_important(row):
            await store.mark_owner_notified(row["id"])            # considered; goes to the digest
            out["quiet"] += 1
            continue
        try:
            had_task = bool(_json(row.get("task_ids"), []))
            agent = await _file_task(store, todos, row)
            out["tasks"] += int(bool(agent) and not had_task)
        except Exception as e:                                     # a task failure never blocks the alert
            agent = None
            print(f"[email_agent] task filing failed for {ref(row)}: {safe_error(e, 160)}", flush=True)
        title, body = format_alert(row, agent)
        result = await notify("email", title, body,
                              severity="warning" if row.get("requires_human") else "info",
                              alert_key=f"email_{row['id']}", cooldown_s=30 * 86400)
        if _delivered(result or {}):
            await store.mark_owner_notified(row["id"])
            out["alerted"] += 1
        else:
            out["alert_failed"] += 1                               # retried next cycle (bounded by interval)
    now = now or datetime.now(timezone.utc)
    now_il = now.astimezone(IL)
    last = await store.last_digest_at()
    if now_il.hour >= DIGEST_HOUR_IL and (last is None or last.astimezone(IL).date() < now_il.date()):
        rows = await store.digest_pending()
        opened = await store.open_items(50)
        msg = format_digest(rows, opened)
        if msg:
            result = await notify("email", msg[0], msg[1], severity="info",
                                  alert_key=f"email_digest_{now_il:%Y-%m-%d}", cooldown_s=86400)
            if _delivered(result or {}) and rows:
                await store.mark_digested([r["id"] for r in rows])
            out["digest"] = _delivered(result or {})
    return out


# ── console helpers (agents/email_console.py) ─────────────────────────────────────
async def _one(store, token: str):
    rows = await store.by_ref(token)
    if not rows:
        return None, f"לא נמצא מייל עם המזהה {token}. כתוב *מיילים* לרשימה."
    if len(rows) > 1:
        return None, f"המזהה {token} מתאים ליותר ממייל אחד — כתוב עוד תווים."
    return rows[0], None


async def open_items_text(store) -> str:
    rows = await store.open_items(20)
    if not rows:
        return "📧 אין מיילים שממתינים לך ✅"
    lines = [f"📧 מיילים שממתינים לך ({len(rows)}):"]
    for r in rows:
        when = r["created_at"].astimezone(IL).strftime("%d/%m %H:%M") if r.get("created_at") else ""
        lines.append(f"• *{ref(r)}* · {when} · {CATEGORY_HE.get(r.get('classification') or 'unknown', '?')} · "
                     f"{r.get('sender_email') or '?'} · {_draft_state(r)}")
    lines.append("פקודות: *מייל <מזהה>* · *שלחמייל <מזהה>* · *סגורמייל <מזהה>* · *משימות מייל*")
    return "\n".join(lines)


async def describe_item(store, token: str) -> str:
    row, err = await _one(store, token)
    if err:
        return err
    title, body = format_alert(row, None)
    parts = [f"📧 {title}", body]
    if row.get("draft_body"):
        parts.append("— הטיוטה —\n" + str(row["draft_body"])[:900])
    if row.get("send_error"):
        parts.append(f"ניסיון שליחה אחרון נכשל: {row['send_error'][:200]}")
    return "\n\n".join(parts)


async def close_item(store, todos, token: str) -> str:
    row, err = await _one(store, token)
    if err:
        return err
    if row.get("owner_closed_at") or row.get("sent_at"):
        return f"המייל {ref(row)} כבר טופל."
    await store.close_item(row["id"])
    done = await todos.complete(_json(row.get("task_ids"), []) or [])
    return f"✅ המייל {ref(row)} סומן כטופל (ללא שליחה)" + (f" · {done} משימות נסגרו" if done else "")


async def approve_and_send(store, client, todos, token: str, approver: str = "owner_whatsapp") -> str:
    row, err = await _one(store, token)
    if err:
        return err
    r = ref(row)
    if row.get("sent_at"):
        return f"המייל {r} כבר נשלח."
    if row.get("owner_closed_at"):
        return f"המייל {r} סומן כטופל — לא נשלח."
    draft_id = row.get("gmail_draft_id")
    if not draft_id:
        return (f"למייל {r} אין טיוטה ב-Gmail, לכן אין מה לשלוח. "
                f"אפשר לענות ישירות מ-Gmail ואז *סגורמייל {r}*.")
    if not _control.read_control()["running"]:
        return "סוכן המיילים עצור (halt) — לא נשלח כלום. כתוב בשרת: email_agent_cli.py arm"
    try:
        await asyncio.to_thread(client.get_draft, draft_id)        # the draft must still exist in Gmail
    except GmailError as e:
        await store.mark_send_error(row["id"], f"draft not found: {e}")
        return f"הטיוטה של {r} לא נמצאה ב-Gmail (אולי נמחקה או כבר נשלחה ידנית). לא נשלח כלום."
    await store.mark_send_approved(row["id"], approver)
    approval = OwnerApproval(row_id=row["id"], draft_id=draft_id, approved_by=approver, approved_at=time.time())
    try:
        sent = await asyncio.to_thread(client.send_approved_draft, approval)
    except _control.Halted:
        return "סוכן המיילים נעצר באמצע — הטיוטה לא נשלחה."
    except GmailError as e:
        await store.mark_send_error(row["id"], str(e))
        hint = " (חסרה הרשאת Gmail לשליחה)" if getattr(e, "kind", "") == "scope" else ""
        return f"❌ השליחה של {r} נכשלה{hint}: {safe_error(e, 160)}"
    await store.mark_sent(row["id"], str(sent.get("id")))
    done = await todos.complete(_json(row.get("task_ids"), []) or [])
    return (f"✅ נשלח: התשובה ל-{row.get('sender_email') or '?'} (מייל {r})"
            + (f" · {done} משימות נסגרו" if done else ""))


async def open_tasks_text(todos) -> str:
    rows = await todos.open_email_tasks(15)
    if not rows:
        return "📋 אין משימות פתוחות מהמיילים ✅"
    lines = [f"📋 משימות פתוחות מהמיילים ({len(rows)}):"]
    for t in rows:
        lines.append(f"• *{str(t['id'])[:8]}* · {t.get('agent') or '?'} · {t.get('title')}")
    lines.append("לסגירה: *סגורמשימה <מזהה>*")
    return "\n".join(lines)


async def close_task(todos, token: str) -> str:
    rows = await todos.by_prefix(token)
    if not rows:
        return f"לא נמצאה משימת מייל עם המזהה {token}."
    if len(rows) > 1:
        return f"המזהה {token} מתאים ליותר ממשימה אחת — כתוב עוד תווים."
    n = await todos.complete([rows[0]["id"]])
    return f"✅ המשימה {str(rows[0]['id'])[:8]} נסגרה." if n else "המשימה כבר סגורה."
