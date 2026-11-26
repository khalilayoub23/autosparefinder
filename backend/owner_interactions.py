"""
Script:  owner_interactions.py
Purpose: ONE interactive-decision layer for every WhatsApp-connected agent (owner buttons) + the persistent
         Resume Interaction mechanism that returns a Change/Edit/More-info follow-up to the ORIGINATING agent and task.
Process:
  1. A caller that wants the owner to decide passes `choices=[(label, typed_command), ...]` to `notify_owner`.
     `social/whatsapp_provider.send_message` (the single chokepoint) hands them to `create_from_choices()`, which
     classifies each typed command into a STABLE action id, attributes the owning agent, binds the interaction to the
     DISPLAYED text (body hash in the idempotency key) and PERSISTS it before anything is shown. A new version of the
     same agent+entity supersedes (expires) the older open interactions, so a tap on v1 can never approve v2.
  2. Button ids are `asf:<interaction_id>:<action_id>`; a visible label is never an identifier.
  3. `resolve_response()` validates sender (owner only), interaction, action, correlation, expiry, then ATOMICALLY
     consumes one-shot actions and returns the exact registered typed command (each agent keeps its own gate).
  4. RESUME: an action whose verb is in RESUMABLE (edit/change/ask) is NOT consumed by the tap. The tap runs its
     read-only prompt command and moves the interaction to `awaiting_input` with a persisted continuation
     {verb, ref, agent}. `bind_followup()` binds the owner's NEXT private text to exactly one awaiting interaction
     (quote-reply to the prompt wins; one awaiting = bind; several = ask, nothing executed; a console command passes
     through; "בטל" cancels), claims it atomically (awaiting_input -> completed) and returns the continuation command
     `<verb> <ref> <text>`, which the owner console routes to the ORIGINATING agent's own handler
     (agents/owner_continuations.py). Its revised proposal comes back with NEW buttons (a new interaction).
Data Imported/Modified: PII DB tables owner_interactions + owner_interaction_events (migration 0043).
Data Sources: none.
Missing Data Delegation: n/a.
Last Updated: 2026-11-26

Switch: WA_INTERACTIVE_ENABLED=1 (default OFF) - with it off nothing here is called and the text + poll path is unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, List, Optional, Protocol, Sequence, Tuple

Choice = Tuple[str, str]

# ── vocabulary ──────────────────────────────────────────────────────────────────────────────────
ACTION_LABELS = {
    # change = the agent rewrites from the owner's notes; edit = the owner's own text replaces it word for word.
    # (#158: "עריכה" read as "editing notes" - the owner's notes were pasted into a post twice on 2026-11-26.)
    "approve": "אישור", "reject": "דחייה", "change": "שינוי לפי הערות", "edit": "נוסח משלי",
    "details": "פרטים נוספים", "recheck": "בדיקה חוזרת", "continue": "המשך", "cancel": "ביטול",
}
# Read-only actions never consume the interaction; the rest are one-shot.
READ_ONLY_ACTIONS = frozenset({"details", "recheck"})

# typed-command verb -> (action id, owning agent). The verbs ARE the existing owner-console commands.
_VERBS = {
    "אשר": ("approve", "NOA"), "דחה": ("reject", "NOA"), "ערוך": ("edit", "NOA"), "שנהפוסט": ("change", "NOA"),
    "ענה": ("approve", "NOA"), "דלג": ("reject", "NOA"), "ערוךתשובה": ("edit", "NOA"),
    "פוסטים": ("details", "NOA"), "תגובות": ("details", "NOA"), "דראפטים": ("details", "NOA"),
    "אשרתגובה": ("approve", "NOA"), "דלגתגובה": ("reject", "NOA"),
    "שלחמייל": ("approve", "EMAIL"), "מייל": ("details", "EMAIL"), "סגורמייל": ("cancel", "EMAIL"),
    "ערוךמייל": ("edit", "EMAIL"),
    "אשרספק": ("approve", "NIR"), "דחהספק": ("reject", "NIR"), "ספקים": ("details", "NIR"), "שאלספק": ("details", "NIR"),
    "אשרפניותספקים": ("approve", "NIR"), "אשררכש": ("approve", "BOAZ"), "אשרתשלוםספק": ("approve", "BOAZ"),
    "רכש": ("details", "BOAZ"), "בדוקרכש": ("recheck", "BOAZ"),
    "אשרמילה": ("approve", "CATEGORIES"), "דחהמילה": ("reject", "CATEGORIES"), "מילים": ("details", "CATEGORIES"),
    # inert self-test commands (device verification only; handled in owner_console, no side effects)
    "בדיקתאישור": ("approve", "SELFTEST"), "בדיקתדחייה": ("reject", "SELFTEST"), "בדיקתשינוי": ("change", "SELFTEST"),
    "משימות": ("recheck", "BOAZ"),
    "סטטוס": ("recheck", "AVI"), "בדוק": ("recheck", "AVI"), "המשך": ("continue", "AVI"), "בטל": ("cancel", "AVI"),
}

# Continuation verbs: the tap runs `<verb> <ref>` (a read-only prompt showing the current proposal) and the owner's NEXT
# text is resumed as `<verb> <ref> <text>` by the originating agent (agents/owner_continuations.py). An approval or a
# commercial verb is NEVER resumable - a follow-up text can revise a proposal, never authorize it.
RESUMABLE = frozenset({"ערוך", "שנהפוסט", "ערוךמייל", "שאלספק", "ערוךתשובה", "בדיקתשינוי"})
# Verbs that change a proposal: refused from a group (a mention is a routing signal, never a mutation or approval).
MUTATING_ACTIONS = frozenset({"approve", "reject", "edit", "change", "cancel"})

# Plain owner-console words that are commands in their own right: while a follow-up is pending they pass through
# untouched (they never become "the new text"). One predicate for every bind decision (is_console_command).
_MENU_WORDS = frozenset({
    "עזרה", "תפריט", "help", "menu", "סטטוס", "status", "סקירה", "מצב", "דיגסט", "שאיבה", "קטלוג", "הזמנות", "orders",
    "פוסטים", "posts", "מיילים", "mails", "emails", "ספקים", "suppliers", "מקורות", "תגובות", "inbox", "מילים", "words",
    "רכש", "procurement", "משימות", "queue", "תור", "קבוצות", "groups", "גרופים", "דראפטים", "עובדים", "workers",
    "יציאה", "צא", "exit", "back", "חזרה", "הנחיות", "מוכנות", "מדדים", "שאיבות", "משימות בועז", "משימות מייל",
})
CANCEL_WORDS = frozenset({"בטל", "ביטול", "cancel", "בטל עריכה", "בטל שינוי"})

DEFAULT_TTL_S = int(os.getenv("OWNER_INTERACTION_TTL_S", str(48 * 3600)))
FOLLOWUP_TTL_S = int(os.getenv("OWNER_FOLLOWUP_TTL_S", str(30 * 60)))
MAX_BUTTONS = 10


def enabled() -> bool:
    return os.getenv("WA_INTERACTIVE_ENABLED", "0") == "1"


def _verb(command: str) -> str:
    return str(command or "").strip().split(" ", 1)[0]


def classify(command: str) -> Tuple[str, str]:
    """(action_id, agent) for a typed owner command; ('custom', 'AVI') for anything unknown."""
    return _VERBS.get(_verb(command), ("custom", "AVI"))


def is_console_command(text: str) -> bool:
    """True when the text is an owner-console command on its own (so it must never be bound as follow-up text)."""
    t = " ".join(str(text or "").split())
    low = t.lower()
    if not t:
        return False
    if low in _MENU_WORDS or low in CANCEL_WORDS:
        return True
    first = low.split(" ", 1)[0]
    if first.startswith("@") or first in ("נועה", "noa", "אבי", "avi"):
        return True       # an explicit agent switch is a new conversation, not the revision text
    return _verb(t) in _VERBS


def has_decision(choices) -> bool:
    """True when the choices offer the owner an actual decision (not only read-only list/details/recheck)."""
    return any(classify(c[1])[0] not in READ_ONLY_ACTIONS | {"custom"} for c in (choices or []) if len(c) == 2)


@dataclass
class Action:
    id: str
    label: str
    command: str = ""

    def public(self) -> dict:  # what the bridge may see: never the command
        return {"id": self.id, "label": self.label}

    @property
    def resume_verb(self) -> str:
        v = _verb(self.command)
        return v if v in RESUMABLE else ""


@dataclass
class Interaction:
    id: str
    agent: str
    task_ref: str
    entity_id: str
    actions: list
    state: str = "open"            # open | awaiting_input | consumed | completed | cancelled | expired
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    wa_message_id: str = ""
    consumed_action: str = ""
    pending_action: str = ""
    idempotency_key: str = ""
    continuation: dict = field(default_factory=dict)
    prompt_message_id: str = ""
    followup_expires_at: Optional[datetime] = None

    def action(self, action_id: str) -> Optional[Action]:
        return next((a for a in self.actions if a.id == action_id), None)


class Store(Protocol):  # implemented by PgStore (production) and MemoryStore (tests)
    async def insert(self, ix: Interaction) -> Interaction: ...
    async def get(self, interaction_id: str) -> Optional[Interaction]: ...
    async def set_wa_message_id(self, interaction_id: str, wa_message_id: str) -> None: ...
    async def set_prompt_message_id(self, interaction_id: str, message_id: str) -> None: ...
    async def consume(self, interaction_id: str, action_id: str) -> bool: ...
    async def begin_followup(self, interaction_id: str, action_id: str, continuation: dict, until: datetime) -> bool: ...
    async def awaiting(self) -> List[Interaction]: ...
    async def lapse_followups(self) -> List[Interaction]: ...
    async def claim_followup(self, interaction_id: str) -> bool: ...
    async def cancel_followup(self, interaction_id: str) -> bool: ...
    async def release_followup(self, interaction_id: str) -> None: ...
    async def supersede(self, agent: str, entity_id: str, keep_id: str) -> int: ...
    async def event(self, interaction_id: str, kind: str, detail: dict) -> None: ...


def _id() -> str:
    return "ix_" + secrets.token_urlsafe(8).replace("-", "a").replace("_", "b")


def idempotency_key(agent: str, task_ref: str, commands: Iterable[str], body: str = "") -> str:
    """Same agent + task + commands + DISPLAYED text = same interaction. The body hash binds buttons to the exact
    version the owner saw: a revised proposal is a different interaction (and supersedes the old one)."""
    raw = json.dumps([agent, task_ref, sorted(commands), hashlib.sha256(str(body or "").encode()).hexdigest()],
                     ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


MAX_BUTTON_LABEL = 20   # WhatsApp quick-reply limit (whatsapp-bridge/interactive.js MAX_LABEL)


def button_label(producer_label: str, action_id: str) -> str:
    """The producer's own label when it fits a WhatsApp button ("✅ אשר ופרסם", "🔁 NOA תשכתב"), else the generic
    vocabulary. #158: the generic word was ALWAYS used, so "NOA rewrites" and "I write it myself" both reached the
    owner as the ambiguous "שינוי"/"עריכה" and his editing notes were pasted into a post."""
    lab = " ".join(str(producer_label or "").split())
    units = len(lab.encode("utf-16-le")) // 2        # the bridge cuts by JS (UTF-16) length: an astral emoji counts 2
    return lab if 0 < units <= MAX_BUTTON_LABEL else ACTION_LABELS[action_id]


def build(choices: Sequence[Choice], *, task_ref: str = "", entity_id: str = "", body: str = "",
          ttl_s: int = DEFAULT_TTL_S) -> Optional[Interaction]:
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
        actions.append(Action(aid, button_label(_label, aid), command[:200]))
        agents.append(ag)
    if not actions:
        return None
    # the owning agent is the one behind the first DECISION action; an all-read-only set belongs to the last (the originator)
    agent = next((g for a, g in zip(actions, agents) if a.id not in READ_ONLY_ACTIONS), agents[-1])
    now = datetime.now(timezone.utc)
    ref = task_ref or (str(choices[0][1]).split(" ", 1) + [""])[1].strip()
    return Interaction(id=_id(), agent=agent, task_ref=ref[:80], entity_id=(entity_id or ref)[:80], actions=actions[:MAX_BUTTONS],
                       created_at=now, expires_at=now + timedelta(seconds=ttl_s),
                       idempotency_key=idempotency_key(agent, ref, [a.command for a in actions], body))


async def create_from_choices(store: Store, choices: Sequence[Choice], **kw) -> Optional[Interaction]:
    """Persist FIRST (so a tap can never reach an unknown interaction), then return it for sending. A NEW interaction
    supersedes the older open ones of the same agent + entity (an old message's buttons die with its version)."""
    ix = build(choices, **kw)
    if ix is None:
        return None
    new_id = ix.id
    ix = await store.insert(ix)
    if ix.id == new_id:
        await store.event(ix.id, "created", {"agent": ix.agent, "actions": [a.id for a in ix.actions], "ref": ix.task_ref})
        if ix.entity_id:
            n = await store.supersede(ix.agent, ix.entity_id, ix.id)
            if n:
                await store.event(ix.id, "superseded_older", {"count": n})
    return ix


def buttons_body(subject: str) -> str:
    """Text of the separate buttons message. It names what the buttons decide (#158: it was always just "בחר פעולה:",
    so two pending decisions showed two identical button blocks)."""
    subj = " ".join(str(subject or "").split())[:120]
    return f"{subj}\nבחר פעולה:" if subj else "בחר פעולה:"


def bridge_payload(ix: Interaction, body: str) -> dict:
    return {"body": body[:1000], "footer": f"🆔 {ix.task_ref or ix.id}"[:60], "interaction_id": ix.id,
            "actions": [a.public() for a in ix.actions]}


# ── response handling ───────────────────────────────────────────────────────────────────────────
@dataclass
class Resolution:
    status: str                 # execute | followup | reply | ignored
    command: str = ""           # run through the owner console (execute: the action; followup: the read-only prompt)
    reply: str = ""             # text for the owner (status reply), or the hint appended to the prompt (followup)
    interaction_id: str = ""
    action_id: str = ""
    agent: str = ""


_EXPIRED = "⌛ הבקשה הזו כבר לא פעילה (פג תוקף, טופלה או הוחלפה בגרסה חדשה). בקש מהסוכן הצעה חדשה."


def followup_hint(ix: Interaction, action_id: str) -> str:
    what = {"change": f"כתוב בהודעה הבאה מה לשנות, במילים שלך. {ix.agent} יכתוב גרסה חדשה לאישורך",
            "edit": "שלח בהודעה הבאה את הנוסח המלא. הוא יחליף את הקיים מילה במילה (להערות לסוכן: *שינוי לפי הערות*)",
            "details": "מה תרצה לשאול? כתוב בהודעה הבאה"}.get(action_id, "כתוב את התשובה בהודעה הבאה")
    mins = max(1, FOLLOWUP_TTL_S // 60)
    return (f"✏️ {what}.\n"
            f"אפשר גם להשיב בציטוט להודעה הזו. ביטול: *בטל* · ממתין {mins} דק' · בקשה {ix.task_ref or ix.id}")


async def resolve_response(store: Store, resp: dict, *, sender_is_owner: bool) -> Resolution:
    """Validate and resolve one inbound button/list tap. Never executes anything itself."""
    iid, aid = str(resp.get("interaction_id") or ""), str(resp.get("action_id") or "")
    if not sender_is_owner:
        await store.event(iid or "-", "rejected_not_owner", {"action": aid})
        return Resolution("ignored", interaction_id=iid, action_id=aid)
    if resp.get("is_group"):
        # buttons are only honoured in the owner's private chat; a group tap is never an approval
        await store.event(iid or "-", "rejected_group", {"action": aid})
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
    if ix.state not in ("open", "awaiting_input"):
        await store.event(ix.id, "rejected_consumed", {"action": aid, "state": ix.state})
        return Resolution("reply", reply=_EXPIRED, interaction_id=ix.id, action_id=aid, agent=ix.agent)

    if act.resume_verb:
        if ix.state == "awaiting_input":
            await store.event(ix.id, "followup_already_pending", {"action": aid})
            return Resolution("reply", reply=f"⏳ כבר ממתין לטקסט שלך לבקשה {ix.task_ref or ix.id}. לביטול: *בטל*.",
                              interaction_id=ix.id, action_id=aid, agent=ix.agent)
        cont = {"verb": act.resume_verb, "ref": ix.task_ref, "agent": ix.agent, "action": aid}
        until = datetime.now(timezone.utc) + timedelta(seconds=FOLLOWUP_TTL_S)
        if not await store.begin_followup(ix.id, aid, cont, until):
            return Resolution("reply", reply=_EXPIRED, interaction_id=ix.id, action_id=aid, agent=ix.agent)
        await store.event(ix.id, "followup_started", {"action": aid, "verb": act.resume_verb})
        return Resolution("followup", command=act.command, reply=followup_hint(ix, aid),
                          interaction_id=ix.id, action_id=aid, agent=ix.agent)

    if aid in READ_ONLY_ACTIONS:
        await store.event(ix.id, "response", {"action": aid})
        return Resolution("execute", command=act.command, interaction_id=ix.id, action_id=aid, agent=ix.agent)

    if not await store.consume(ix.id, aid):   # atomic: a double tap / concurrent tap loses here
        await store.event(ix.id, "rejected_duplicate", {"action": aid})
        return Resolution("reply", reply=_EXPIRED, interaction_id=ix.id, action_id=aid, agent=ix.agent)
    await store.event(ix.id, "response", {"action": aid, "command": _verb(act.command)})
    return Resolution("execute", command=act.command, interaction_id=ix.id, action_id=aid, agent=ix.agent)


# ── Resume Interaction: bind the owner's next private text to the originating agent + task ──────────────────────
@dataclass
class Binding:
    status: str                 # bound | ambiguous | cancelled | duplicate
    command: str = ""           # bound: the continuation command for the owner console
    reply: str = ""             # ambiguous / cancelled: text for the owner
    interaction: Optional[Interaction] = None


def _pick(cands: List[Interaction], quoted_message_id: str) -> Tuple[Optional[Interaction], bool]:
    """(chosen, ambiguous). A quote-reply to the prompt (or to the original alert) chooses exactly; otherwise only a
    single pending follow-up is unambiguous."""
    q = str(quoted_message_id or "")
    if q:
        hit = [c for c in cands if q in (c.prompt_message_id, c.wa_message_id)]
        if len(hit) == 1:
            return hit[0], False
    if len(cands) == 1:
        return cands[0], False
    return None, len(cands) > 1


async def bind_followup(store: Store, text: str, quoted_message_id: str = "") -> Optional[Binding]:
    """None = no follow-up applies (normal console routing). Never binds a console command, never binds in a group
    (the webhook calls this only for the owner's private chat), never binds when two requests could match."""
    text = str(text or "").strip()
    if not text:
        return None
    for lapsed in await store.lapse_followups():   # follow-up window over: back to open (buttons still usable)
        await store.event(lapsed.id, "followup_lapsed", {})
    cands = await store.awaiting()
    if not cands:
        return None
    low = " ".join(text.split()).lower()
    if low in CANCEL_WORDS:
        ix, amb = _pick(cands, quoted_message_id)
        if amb:
            return Binding("ambiguous", reply="יש כמה בקשות שממתינות לטקסט שלך — השב *בטל* בציטוט להודעת הבקשה שברצונך לבטל:\n"
                           + "\n".join(f"• {c.agent} · {c.task_ref or c.id}" for c in cands[:6]))
        if ix and await store.cancel_followup(ix.id):
            await store.event(ix.id, "followup_cancelled", {})
            return Binding("cancelled", reply=f"↩️ בוטל. הבקשה {ix.task_ref or ix.id} של {ix.agent} חזרה למצב הקודם (הכפתורים עדיין פעילים).",
                           interaction=ix)
        return None
    if is_console_command(text):
        return None
    ix, amb = _pick(cands, quoted_message_id)
    if amb:
        return Binding("ambiguous", reply="יש כמה בקשות שממתינות לטקסט שלך, ולא ברור לאיזו מהן ההודעה שייכת. "
                       "לא ביצעתי כלום — השב בציטוט להודעת הבקשה הנכונה:\n"
                       + "\n".join(f"• {c.agent} · {c.task_ref or c.id}" for c in cands[:6]))
    if ix is None:
        return None
    if not await store.claim_followup(ix.id):      # atomic: a redelivered message resumes once
        await store.event(ix.id, "followup_duplicate", {})
        return Binding("duplicate", interaction=ix)
    cont = ix.continuation or {}
    verb, ref = str(cont.get("verb") or ""), str(cont.get("ref") or ix.task_ref)
    if verb not in RESUMABLE or not ref:
        await store.event(ix.id, "followup_invalid_continuation", {"verb": verb})
        return None
    await store.event(ix.id, "followup_received", {"action": ix.pending_action, "verb": verb, "chars": len(text)})
    return Binding("bound", command=f"{verb} {ref} {text}", interaction=ix)


_AGENT_PREFIXES = ("נועה", "noa", "אבי", "avi")
# deterministic console words that START work (job queue, scans, task/harvester runs) - never from a group
_GROUP_REFUSED_WORDS = frozenset({"עצור", "stop", "עצירה", "המשך", "resume", "continue", "הרץ", "run", "מקורות", "discover",
                                  "sourcing", "סרוק", "סריקת", "קבוצת", "updates", "יציאה", "exit"})


def can_route_group_text(text: str) -> bool:
    """A group message that mentions us may reach an agent for CONVERSATION only. Any console command (approval,
    revision, continuation, list/menu command, or a word that starts work) is refused: those run only from the owner's
    private chat or a button tap there. A leading agent name (נועה/אבי) is an addressee, not a command."""
    words = [w for w in str(text or "").split() if not re.fullmatch(r"@\S*", w)]  # @mentions are not verbs
    if words and words[0].lower().strip(",:") in _AGENT_PREFIXES:
        words = words[1:]
    rest = " ".join(words)
    if not rest:
        return False
    first = words[0].lower()
    if first in _GROUP_REFUSED_WORDS or first in RESUMABLE:
        return False
    return not is_console_command(rest)
