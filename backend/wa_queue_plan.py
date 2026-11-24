"""
Script:  wa_queue_plan.py
Purpose: Pure decision logic for the WhatsApp night queue and the channel-outage lifecycle (audit 2026-11-24, #152).
         Quiet hours are NOT touched here: this only decides what to do with items that were already queued.
Process:
  channel_error_kind   "down" (logged out / awaiting QR / bridge unreachable: the CHANNEL is broken, an item's own
                       attempt counter must not be spent on it), "permanent" (payload refused), "transient".
  plan_flush           Given the queued items at window open returns what to do with each one:
                         superseded  an older item of the same group that a newer item replaces (newest state wins)
                         merged      opened AND closed while quiet (alert + its resolution, nothing critical) - neither
                                     is delivered, the pair is recorded
                         digest      > DIGEST_MIN non-critical, no-action info updates collapse into ONE morning summary
                         deliver     critical items, items that ask for a decision, and the few that remain
                       Every decision carries a reason and is written to the queue audit log by the caller, so nothing
                       disappears silently.
  build_digest         the single collapsed message (titles only; the console has the detail).
Data Imported/Modified: none (pure).
Data Sources: queue payloads {to,text,queued_at,alert_key,meta:{group,state,sev,title}} written by _wa_send_quiet.
Last Updated: 2026-11-24
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

DIGEST_MIN = 4          # non-critical info items above this count are collapsed into one message
DIGEST_MAX_LINES = 10
OPEN_STATES = {"new", "changed", "reminder", "action", ""}
CLOSED_STATES = {"resolved", "recovered"}


def channel_error_kind(error: Any) -> str:
    e = str(error or "")
    low = e.lower()
    if e.startswith("INVALID_PAYLOAD"):
        return "permanent"
    if ("not authenticated" in low or "session invalid" in low or "logged out" in low or "awaiting qr" in low
            or "re-pairing" in low or e.startswith("bridge unreachable")):
        return "down"
    return "transient"


def _meta(item: Dict[str, Any]) -> Dict[str, Any]:
    m = item.get("meta")
    return m if isinstance(m, dict) else {}


def _group(item: Dict[str, Any]) -> str:
    return str(_meta(item).get("group") or item.get("alert_key") or "")


def _is_critical(item: Dict[str, Any]) -> bool:
    return str(_meta(item).get("sev") or "") == "critical" or bool(item.get("critical"))


def _needs_action(item: Dict[str, Any]) -> bool:
    return _meta(item).get("state") == "action" or "👉" in str(item.get("text") or "") or "אשר " in str(item.get("text") or "")


def plan_flush(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """items = parsed queue payloads in queue order (oldest first). Returns one decision dict per item:
    {"index", "decision", "reason", "group"}. Items without a group/meta are always delivered untouched (legacy
    one-off sends are never merged - same rule as the enqueue dedup)."""
    n = len(items)
    decisions: List[Optional[Dict[str, Any]]] = [None] * n

    def put(i: int, decision: str, reason: str):
        decisions[i] = {"index": i, "decision": decision, "reason": reason, "group": _group(items[i])}

    by_group: Dict[str, List[int]] = {}
    for i, it in enumerate(items):
        g = _group(it) if _meta(it) else ""
        if g:
            by_group.setdefault(g, []).append(i)

    for g, idxs in by_group.items():
        if len(idxs) < 2:
            continue
        crit = any(_is_critical(items[i]) for i in idxs)
        last = idxs[-1]
        closed = _meta(items[last]).get("state") in CLOSED_STATES
        if closed and not crit and all(_meta(items[i]).get("state") in OPEN_STATES | CLOSED_STATES for i in idxs):
            for i in idxs:
                put(i, "merged", "opened and resolved while quiet: nothing for the owner to do")
            continue
        for i in idxs[:-1]:
            if not _is_critical(items[i]):
                put(i, "superseded", f"newer state of the same condition queued ({_meta(items[last]).get('state') or 'update'})")

    rest = [i for i in range(n) if decisions[i] is None]
    collapsible = [i for i in rest if _meta(items[i]) and not _is_critical(items[i]) and not _needs_action(items[i])
                   and _meta(items[i]).get("state") not in CLOSED_STATES]
    if len(collapsible) > DIGEST_MIN:
        for i in collapsible:
            put(i, "digest", f"{len(collapsible)} routine updates collapsed into one morning summary")
    for i in range(n):
        if decisions[i] is None:
            put(i, "deliver", "critical" if _is_critical(items[i]) else "needs attention or few enough to send as is")
    return [d for d in decisions if d]


def build_digest(items: List[Dict[str, Any]], queued_window: str = "") -> str:
    """One message for the collapsed items: one line per item (its title), newest state wins, capped."""
    lines = []
    for it in items:
        t = str(_meta(it).get("title") or str(it.get("text") or "").split("\n", 1)[0])
        lines.append(f"• {t[:90]}")
    shown = lines[:DIGEST_MAX_LINES]
    more = len(lines) - len(shown)
    head = f"🌅 עדכוני הלילה ({len(items)}){' · ' + queued_window if queued_window else ''}"
    tail = f"\n…ועוד {more}" if more > 0 else ""
    return head + "\n" + "\n".join(shown) + tail + "\nאין בהם דבר שדורש אותך כרגע; הפרטים בקונסולה."
