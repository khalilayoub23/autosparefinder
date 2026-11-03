"""
Script: agents/email_console.py
Purpose: The owner's WhatsApp commands for the Email Agent (owner decision 2026-10-27). Called by
         agents/owner_console.process_owner_message, which only runs for the owner's own number.
Process:
  מיילים | mail | emails          emails waiting for the owner (need a human / have a reply draft)
  מייל <מזהה>                     one email: sender, subject, why, what the agent did, the draft text
  שלחמייל <מזהה>                  APPROVE AND SEND that email's Gmail draft (owner_flow.approve_and_send)
  סגורמייל <מזהה>                 mark handled without sending; closes its tasks
  משימות מייל | email tasks       open tasks the Email Agent filed for the other agents
  סגורמשימה <מזהה>                close one of those tasks
  <מזהה> = the first characters (4+) of the email's id, as shown in the alerts.
  Returns None for any other text so the console keeps its existing routing.
Data Imported/Modified: email_agent_messages owner columns (PII DB); agent_todos category 'email';
         Gmail drafts.send (only on שלחמייל).
Data Sources: email_agent store, Gmail API.
Missing Data Delegation: n/a.
Last Updated: 2026-10-27
"""
from __future__ import annotations

import re
from typing import Optional

_REF = r"([0-9a-fA-F][0-9a-fA-F-]{3,35})"
_SEND = re.compile(rf"^(?:שלחמייל|שלח מייל|send mail|sendmail)\s+{_REF}$", re.I)
_CLOSE = re.compile(rf"^(?:סגורמייל|סגור מייל|close mail)\s+{_REF}$", re.I)
_SHOW = re.compile(rf"^(?:מייל|mail|email)\s+{_REF}$", re.I)
_CLOSE_TASK = re.compile(rf"^(?:סגורמשימה|סגור משימה|close task)\s+{_REF}$", re.I)
_LIST = ("מיילים", "מייל", "mail", "mails", "emails", "inbox mail", "דואר")
_TASKS = ("משימות מייל", "משימות מיילים", "email tasks", "mail tasks")

HELP_LINES = (
    "📧 *מיילים*\n"
    "• *מיילים* — מיילים שממתינים לך\n"
    "• *מייל <מזהה>* — פרטי מייל + הטיוטה\n"
    "• *שלחמייל <מזהה>* — אישור ושליחת הטיוטה מ-Gmail\n"
    "• *סגורמייל <מזהה>* — סימון כטופל בלי לשלוח\n"
    "• *משימות מייל* · *סגורמשימה <מזהה>*\n\n"
)


def _deps():
    from email_agent.owner_flow import PgTodoSink
    from email_agent.store import PgStore
    return PgStore(), PgTodoSink()


async def handle(message: str) -> Optional[str]:
    msg = (message or "").strip()
    low = msg.lower()
    if not msg:
        return None
    from email_agent import owner_flow as of
    if low in _LIST:
        store, _ = _deps()
        return await of.open_items_text(store)
    if low in _TASKS:
        _, todos = _deps()
        return await of.open_tasks_text(todos)
    m = _SEND.match(msg)
    if m:
        from email_agent.config import load_config
        from email_agent.gmail_client import GmailClient
        cfg = load_config()
        if cfg.missing():
            return "חיבור Gmail של סוכן המיילים לא מוגדר — אי אפשר לשלוח."
        store, todos = _deps()
        return await of.approve_and_send(store, GmailClient(cfg), todos, m.group(1).lower())
    m = _CLOSE.match(msg)
    if m:
        store, todos = _deps()
        return await of.close_item(store, todos, m.group(1).lower())
    m = _CLOSE_TASK.match(msg)
    if m:
        _, todos = _deps()
        return await of.close_task(todos, m.group(1).lower())
    m = _SHOW.match(msg)
    if m:
        store, _ = _deps()
        return await of.describe_item(store, m.group(1).lower())
    return None
