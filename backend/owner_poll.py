"""
Script:  owner_poll.py
Purpose: Tap-to-pick WhatsApp polls and a cleaner layout for the OWNER's decision alerts.
Process:
  1. A caller that wants the owner to choose passes `choices=[(label, command), ...]` to
     `notify_owner`; `attach()` appends a hidden, machine-readable trailer to the alert text.
  2. The trailer travels through the EXISTING quiet-hours / retry queues untouched (they store text),
     so a night alert is still delivered with its poll at 09:00 - no queue format change.
  3. `social/whatsapp_provider.send_message` is the single chokepoint: `split()` strips the trailer,
     sends the text, and (owner 1:1 chat only) sends the poll through the bridge.
  4. The bridge remembers poll-id -> {option: command}; a vote is posted back to the normal inbound
     webhook as if the owner had TYPED that command, so there is NO new approval logic: the typed
     commands keep working and remain the fallback.
Data Imported/Modified: none (pure functions; the bridge persists the poll map).
Data Sources: none.
Missing Data Delegation: n/a.
Last Updated: 2026-11-13

Rules: a poll is only ever honoured for the owner's own number (`is_owner_target`) - a customer-facing
text that happened to contain the trailer is sent as plain text with the trailer removed.
"""
from __future__ import annotations

import json
import os
import re
from typing import Iterable, Optional, Sequence, Tuple

_OPEN = "⁣⁣ASFPOLL⁣"
_CLOSE = "⁣⁣END"
_TRAILER_RE = re.compile(r"\n?" + re.escape(_OPEN) + r"(\{.*?\})" + re.escape(_CLOSE), re.S)

MIN_OPTIONS, MAX_OPTIONS = 2, 6
MAX_NAME, MAX_LABEL, MAX_COMMAND = 90, 40, 200

Choice = Tuple[str, str]


def _clean_choices(choices: Iterable[Choice]) -> list[Choice]:
    out: list[Choice] = []
    seen: set[str] = set()
    for item in choices or []:
        try:
            label, command = item
        except (TypeError, ValueError):
            continue
        label, command = str(label or "").strip(), str(command or "").strip()
        if not label or not command or label in seen:
            continue
        seen.add(label)
        out.append((label[:MAX_LABEL], command[:MAX_COMMAND]))
    return out[:MAX_OPTIONS]


def attach(text: str, name: str, choices: Sequence[Choice]) -> str:
    """Return `text` with the poll trailer appended, or `text` unchanged when the choices are not
    a usable poll (fewer than two valid options) - the alert is then simply the plain text."""
    clean = _clean_choices(choices)
    if len(clean) < MIN_OPTIONS or not str(name or "").strip():
        return text
    spec = {"name": str(name).strip()[:MAX_NAME],
            "values": [c[0] for c in clean], "commands": [c[1] for c in clean]}
    return f"{text}\n{_OPEN}{json.dumps(spec, ensure_ascii=False)}{_CLOSE}"


def split(text: str) -> Tuple[str, Optional[dict]]:
    """(text without any trailer, validated poll spec or None). Always strips the trailer."""
    raw = str(text or "")
    m = _TRAILER_RE.search(raw)
    if not m:
        return raw, None
    clean_text = _TRAILER_RE.sub("", raw).rstrip()
    try:
        spec = json.loads(m.group(1))
        values, commands = spec.get("values"), spec.get("commands")
        pairs = _clean_choices(zip(values, commands)) if isinstance(values, list) and isinstance(commands, list) else []
        name = str(spec.get("name") or "").strip()
    except Exception:
        return clean_text, None
    if len(pairs) < MIN_OPTIONS or not name:
        return clean_text, None
    return clean_text, {"name": name[:MAX_NAME], "values": [p[0] for p in pairs],
                        "commands": [p[1] for p in pairs]}


def _digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or "").split("@")[0])


def is_owner_target(to: str) -> bool:
    """True only for the owner's own 1:1 number (or its LID mapping done later at the bridge)."""
    owner = _digits(os.getenv("OWNER_WHATSAPP_PHONE", ""))
    return bool(owner) and _digits(to) == owner


# ── layout ────────────────────────────────────────────────────────────────────────────────────
def layout(header: str, body: str = "", *, ref: str = "", footer: str = "") -> str:
    """Cleaner owner alert: bold title, the body as-is, then ONE small footer line (id / note)."""
    parts = [f"*{header.strip()}*"]
    if body and body.strip():
        parts.append(body.strip())
    foot = " · ".join(x for x in (f"🆔 {ref}" if ref else "", footer.strip()) if x)
    if foot:
        parts.append(f"_{foot}_")
    return "\n\n".join(parts)


def late_footer(text: str, queued_at: str) -> str:
    """Delivered-late note as a trailing footer instead of a leading bracket line."""
    note = f"🌙 נשמר משעות הלילה {queued_at}".strip()
    return f"{text.rstrip()}\n\n_{note}_"


# ── choice sets (labels are the taps; commands are the SAME typed commands) ─────────────────
# Continuation verbs (ערוך / שנהפוסט / ערוךמייל / ערוךתשובה / שאלספק) open a follow-up: the owner's next text returns to
# the originating agent (owner_interactions.RESUMABLE, agents/owner_continuations.py).
def post_choices(post_id: str) -> list[Choice]:
    # #158: "NOA rewrites from your notes" vs "your own text replaces it word for word" must be unmistakable.
    return [("✅ אשר ופרסם", f"אשר {post_id}"), ("🔁 NOA תשכתב", f"שנהפוסט {post_id}"),
            ("✏️ אכתוב נוסח משלי", f"ערוך {post_id}"), ("❌ דחה", f"דחה {post_id}")]


def email_choices(ref: str, *, has_draft: bool) -> list[Choice]:
    out: list[Choice] = [(f"📖 פרטים", f"מייל {ref}")]
    if has_draft:
        out.append(("✅ שלח טיוטה", f"שלחמייל {ref}"))
        out.append(("✏️ נוסח משלי", f"ערוךמייל {ref}"))
    out.append(("☑️ סגור", f"סגורמייל {ref}"))
    return out


def inbox_choices(item_id: str) -> list[Choice]:
    return [("✅ שלח תשובה", f"ענה {item_id}"), ("✏️ נוסח משלי", f"ערוךתשובה {item_id}"), ("⏭️ דלג", f"דלג {item_id}")]


def supplier_candidate_choices(supplier_id: str) -> list[Choice]:
    """NIR: ONE named candidate (activation never implies messaging, ordering or paying - see supplier_authorization)."""
    ref = str(supplier_id or "")[:8]
    return [("✅ הפעל ספק", f"אשרספק {ref}"), ("❌ דחה", f"דחהספק {ref}"), ("❓ שאל את NIR", f"שאלספק {ref}")] if ref else []


def boaz_task_choices(email_ref: str) -> list[Choice]:
    """BOAZ escalation about a supplier's mail: read it, or re-list BOAZ's open tasks. Read-only commands; any reply,
    offer, purchase order or payment stays a separate typed owner approval (supplier_authorization)."""
    return [("📖 קרא את המייל", f"מייל {email_ref}"), ("🔁 משימות בועז", "משימות בועז")] if email_ref else []


def procurement_choices() -> list[Choice]:
    """Procurement prepared after a customer payment: read-only details. Approving a purchase order or a supplier
    payment stays the typed `אשררכש <id>` / `אשרתשלוםספק <id>` after the owner has read the exact terms."""
    return [("📋 פרטי הרכש", "רכש"), ("🔁 משימות בועז", "משימות בועז")]


def keyword_choices(word: str) -> list[Choice]:
    """ONE new category keyword (record-level). A batch alert gets only the list button - never a batch approval."""
    w = " ".join(str(word or "").split())
    return [("✅ אשר מילה", f"אשרמילה {w}"), ("❌ דחה מילה", f"דחהמילה {w}"), ("📋 כל המילים", "מילים")] if w else []


def list_choices(command: str, label: str) -> list[Choice]:
    """A single read-only 'show me the list' action for an aggregate alert (N items): no record is decided from it."""
    return [(label, command), ("📊 סטטוס", "סטטוס")]


def selftest_choices(ref: str) -> list[Choice]:
    return [("אישור", f"בדיקתאישור {ref}"), ("דחייה", f"בדיקתדחייה {ref}"), ("שינוי", f"בדיקתשינוי {ref}")]
