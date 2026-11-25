"""
Script:  owner_interactions.py
Purpose: ONE interactive-decision layer for every WhatsApp-connected agent (owner buttons), 2026-11-25.
Process:
  1. A caller that wants the owner to decide already passes `choices=[(label, typed_command), ...]` to
     `notify_owner` (NOA post/inbox approvals, Email Agent drafts, NIR/BOAZ supplier items, ...).
     `social/whatsapp_provider.send_message` (the single chokepoint) hands those to `create_from_choices()`.
  2. `create_from_choices` classifies each typed command into a STABLE action id (approve/reject/edit/details/
     change/recheck/continue/cancel) with `classify()`, attributes the owning agent, and PERSISTS the interaction
     (state, allowed actions, expiry, idempotency key) BEFORE anything is shown on WhatsApp.
  3. The bridge sends reply buttons (list when >3). Button ids are `asf:<interaction_id>:<action_id>`; a visible
     label is never an identifier.
  4. A tap arrives at the webhook as `interactive_response`; `resolve_response()` validates the sender (owner JID,
     never a display name / group membership), the interaction (exists, not expired, action allowed, correlated to the
     original WhatsApp message) and ATOMICALLY consumes it, then returns the EXACT typed command that was registered
     for that action. The webhook runs that command through the normal owner console, so every agent keeps its own
     existing gate (`אשר`, `שלחמייל`, `אשרספק`, ...) - a button is never a second authorization path.
  5. change/edit without a registered command start a structured follow-up: the owner's NEXT free-text message is
     bound to the interaction (`take_followup`) and routed with that context instead of being a new request.
Data Imported/Modified: PII DB tables owner_interactions + owner_interaction_events (migration 0043, NOT yet applied).
Data Sources: none.
Missing Data Delegation: n/a.
Last Updated: 2026-11-25

Switch: WA_INTERACTIVE_ENABLED=1 (default OFF) - with it off nothing here is called and the existing text + poll path is
unchanged. Device rendering of the buttons is UNVERIFIED (see docs/WHATSAPP_NOTIFICATIONS.md section 10).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Protocol, Sequence, Tuple

Choice = Tuple[str, str]

# ── vocabulary ──────────────────────────────────────────────────────────────────────────────────
ACTION_LABELS = {
    "approve": "אישור", "reject": "דחייה", "change": "שינוי", "edit": "עריכה",
    "details": "פרטים נוספים", "recheck": "בדיקה חוזרת", "continue": "המשך", "cancel": "ביטול",
}
# Read-only actions never consume the interaction; the rest are one-shot.
READ_ONLY_ACTIONS = frozenset({"details", "recheck"})
# change/edit hold the interaction open until the owner's follow-up text arrives.
FOLLOWUP_ACTIONS = frozenset({"change", "edit"})

# typed-command verb -> (action id, owning agent). The verbs ARE the existing owner-console commands.
_VERBS = {
    "אשר": ("approve", "NOA"), "דחה": ("reject", "NOA"), "ערוך": ("edit", "NOA"),
    "ענה": ("approve", "NOA"), "דלג": ("reject", "NOA"),
    "שלחמייל": ("approve", "EMAIL"), "מייל": ("details", "EMAIL"), "סגורמייל": ("cancel", "EMAIL"),
    "אשרספק": ("approve", "NIR"), "דחהספק": ("reject", "NIR"), "ספקים": ("details", "NIR"),
    "אשרפניותספקים": ("approve", "NIR"), "אשררכש": ("approve", "BOAZ"), "אשרתשלוםספק": ("approve", "BOAZ"),
    "אשרמילה": ("approve", "CATEGORIES"), "דחהמילה": ("reject", "CATEGORIES"),
    # inert self-test commands (device verification only; handled in owner_console, no side effects)
    "בדיקתאישור": ("approve", "SELFTEST"), "בדיקתדחייה": ("reject", "SELFTEST"), "בדיקתשינוי": ("change", "SELFTEST"),
    "משימות": ("recheck", "BOAZ"),
    "סטטוס": ("recheck", "AVI"), "בדוק": ("recheck", "AVI"), "המשך": ("continue", "AVI"), "בטל": ("cancel", "AVI"),
}

DEFAULT_TTL_S = int(os.getenv("OWNER_INTERACTION_TTL_S", str(48 * 3600)))
MAX_BUTTONS = 10


def enabled() -> bool:
    return os.getenv("WA_INTERACTIVE_ENABLED", "0") == "1"


def classify(command: str) -> Tuple[str, str]:
    """(action_id, agent) for a typed owner command; ('custom', 'AVI') for anything unknown."""
    verb = str(command or "").strip().split(" ", 1)[0]
    return _VERBS.get(verb, ("custom", "AVI"))


@dataclass
class Action:
    id: str
    label: str
    command: str = ""

    def public(self) -> dict:  # what the bridge may see: never the command
        return {"id": self.id, "label": self.label}


@dataclass
class Interaction:
    id: str
    agent: str
    task_ref: str
    entity_id: str
    actions: list
    state: str = "open"            # open | awaiting_input | consumed | expired
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    wa_message_id: str = ""
    consumed_action: str = ""
    pending_action: str = ""
    idempotency_key: str = ""

    def action(self, action_id: str) -> Optional[Action]:
        return next((a for a in self.actions if a.id == action_id), None)


class Store(Protocol):  # implemented by PgStore (production) and MemoryStore (tests)
    async def insert(self, ix: Interaction) -> Interaction: ...
    async def get(self, interaction_id: str) -> Optional[Interaction]: ...
    async def set_wa_message_id(self, interaction_id: str, wa_message_id: str) -> None: ...
    async def consume(self, interaction_id: str, action_id: str) -> bool: ...
    async def begin_followup(self, interaction_id: str, action_id: str) -> bool: ...
    async def pending_followup(self) -> Optional[Interaction]: ...
    async def reopen(self, interaction_id: str) -> None: ...
    async def event(self, interaction_id: str, kind: str, detail: dict) -> None: ...


def _id() -> str:
    return "ix_" + secrets.token_urlsafe(8).replace("-", "a").replace("_", "b")


def idempotency_key(agent: str, task_ref: str, commands: Iterable[str]) -> str:
    raw = json.dumps([agent, task_ref, sorted(commands)], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def build(choices: Sequence[Choice], *, task_ref: str = "", entity_id: str = "", ttl_s: int = DEFAULT_TTL_S) -> Optional[Interaction]:
    """Interaction from the existing (label, command) choices, or None when fewer than 1 valid action. Duplicate
    action ids collapse (first wins) so a button id is unambiguous; only actions backed by a command are offered."""
    actions: list = []
    agents: list = []
    for _label, command in choices or []:
        command = str(command or "").strip()
        if not command:
            continue
        aid, ag = classify(command)
        if aid == "custom" or any(a.id == aid for a in actions):
            continue
        actions.append(Action(aid, ACTION_LABELS[aid], command[:200]))
        agents.append(ag)
    if not actions:
        return None
    # the owning agent is the one behind the first DECISION action; an all-read-only set belongs to the last (the originator)
    agent = next((g for a, g in zip(actions, agents) if a.id not in READ_ONLY_ACTIONS), agents[-1])
    now = datetime.now(timezone.utc)
    ref = task_ref or (str(choices[0][1]).split(" ", 1) + [""])[1].strip()
    return Interaction(id=_id(), agent=agent, task_ref=ref[:80], entity_id=(entity_id or ref)[:80], actions=actions[:MAX_BUTTONS],
                       created_at=now, expires_at=now + timedelta(seconds=ttl_s),
                       idempotency_key=idempotency_key(agent, ref, [a.command for a in actions]))


async def create_from_choices(store: Store, choices: Sequence[Choice], **kw) -> Optional[Interaction]:
    """Persist FIRST (so a tap can never reach an unknown interaction), then return it for sending."""
    ix = build(choices, **kw)
    if ix is None:
        return None
    ix = await store.insert(ix)
    await store.event(ix.id, "created", {"agent": ix.agent, "actions": [a.id for a in ix.actions], "ref": ix.task_ref})
    return ix


def bridge_payload(ix: Interaction, body: str) -> dict:
    return {"body": body[:1000], "footer": f"🆔 {ix.task_ref or ix.id}"[:60], "interaction_id": ix.id,
            "actions": [a.public() for a in ix.actions]}


# ── response handling ───────────────────────────────────────────────────────────────────────────
@dataclass
class Resolution:
    status: str                 # execute | reply | followup | ignored
    command: str = ""           # run through the owner console when status == execute
    reply: str = ""             # text for the owner (status reply/followup, or a note with execute)
    interaction_id: str = ""
    action_id: str = ""
    agent: str = ""


_EXPIRED = "⌛ הבקשה הזו כבר לא פעילה (פג תוקף או טופלה). בקש מהסוכן הצעה חדשה."


async def resolve_response(store: Store, resp: dict, *, sender_is_owner: bool) -> Resolution:
    """Validate and resolve one inbound button/list tap. Never executes anything itself."""
    iid, aid = str(resp.get("interaction_id") or ""), str(resp.get("action_id") or "")
    if not sender_is_owner:
        await store.event(iid or "-", "rejected_not_owner", {"action": aid})
        return Resolution("ignored", interaction_id=iid, action_id=aid)
    ix = await store.get(iid) if re.fullmatch(r"ix_[A-Za-z0-9]{4,40}", iid) else None
    if ix is None:
        await store.event(iid or "-", "rejected_unknown", {"action": aid})
        return Resolution("reply", reply=_EXPIRED, interaction_id=iid, action_id=aid)
    act = ix.action(aid)
    if act is None:
        await store.event(ix.id, "rejected_action_not_allowed", {"action": aid})
        return Resolution("reply", reply=_EXPIRED, interaction_id=ix.id, action_id=aid, agent=ix.agent)
    # correlation: the tap must quote the message we sent (when we know its id)
    orig = str(resp.get("original_message_id") or "")
    if ix.wa_message_id and orig and orig != ix.wa_message_id:
        await store.event(ix.id, "rejected_correlation", {"action": aid})
        return Resolution("reply", reply=_EXPIRED, interaction_id=ix.id, action_id=aid, agent=ix.agent)
    if ix.expires_at and datetime.now(timezone.utc) > ix.expires_at:
        await store.event(ix.id, "rejected_expired", {"action": aid})
        return Resolution("reply", reply=_EXPIRED, interaction_id=ix.id, action_id=aid, agent=ix.agent)
    if ix.state in ("consumed", "expired"):
        await store.event(ix.id, "rejected_consumed", {"action": aid})
        return Resolution("reply", reply=_EXPIRED, interaction_id=ix.id, action_id=aid, agent=ix.agent)

    if aid in READ_ONLY_ACTIONS:
        await store.event(ix.id, "response", {"action": aid})
        return Resolution("execute", command=act.command, interaction_id=ix.id, action_id=aid, agent=ix.agent)

    if aid in FOLLOWUP_ACTIONS and not act.command:
        if not await store.begin_followup(ix.id, aid):
            return Resolution("reply", reply=_EXPIRED, interaction_id=ix.id, action_id=aid, agent=ix.agent)
        await store.event(ix.id, "followup_started", {"action": aid})
        what = "מה לשנות?" if aid == "change" else "מה הנוסח / הערך החדש?"
        return Resolution("followup", reply=f"✏️ {what} כתוב לי בהודעה הבאה ({ix.task_ref or ix.id}).",
                          interaction_id=ix.id, action_id=aid, agent=ix.agent)

    if not await store.consume(ix.id, aid):   # atomic: a double tap / concurrent tap loses here
        await store.event(ix.id, "rejected_duplicate", {"action": aid})
        return Resolution("reply", reply=_EXPIRED, interaction_id=ix.id, action_id=aid, agent=ix.agent)
    await store.event(ix.id, "response", {"action": aid, "command": act.command.split(" ", 1)[0]})
    return Resolution("execute", command=act.command, interaction_id=ix.id, action_id=aid, agent=ix.agent)


async def take_followup(store: Store, text: str) -> Optional[Tuple[Interaction, str]]:
    """If an interaction awaits the owner's change/edit text, bind this message to it: returns (interaction, text with
    the original context prefixed) and re-opens the interaction so the REVISED proposal can be approved. Else None."""
    ix = await store.pending_followup()
    if ix is None or not str(text or "").strip():
        return None
    if ix.expires_at and datetime.now(timezone.utc) > ix.expires_at:
        return None
    await store.reopen(ix.id)
    await store.event(ix.id, "followup_received", {"action": ix.pending_action, "chars": len(text)})
    verb = "שינוי" if ix.pending_action == "change" else "עריכה"
    return ix, (f"[{verb} לבקשה {ix.task_ref or ix.id} של {ix.agent} — הצג הצעה מתוקנת לאישור, אל תבצע]\n{text.strip()}")


def can_route_group_text(text: str) -> bool:
    """A group message (mention) may route to an agent for conversation, but never carries an approval/consequential
    command: those only run from the owner's private chat or a button tap."""
    words = [w for w in str(text or "").split() if not re.fullmatch(r"@\S*", w)]  # leading/embedded @mentions are not verbs
    verb = words[0] if words else ""
    return _VERBS.get(verb, ("", ""))[0] not in ("approve", "reject", "edit", "cancel")
