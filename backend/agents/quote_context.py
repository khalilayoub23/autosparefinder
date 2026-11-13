"""
Script: agents/quote_context.py
Purpose: Associate an owner's quote-reply (WhatsApp `quotedMessage`, Telegram `reply_to_message`) with the NOA post
         the quoted notification was about, so a reply is a command ON THAT POST and never free chat (2026-11-13).
Process: pure text rewrite. The quoted notification names the post by its 8-hex id (the same id the console prints).
         - exactly ONE distinct id in the quote, and the reply has no id of its own:
             approve/reject/history word alone -> that command + id;
             a reply of >= MIN_EDIT_CHARS characters that is not a known command -> `ערוך <id> <reply>`;
             anything else (a short question) -> unchanged text + the quoted excerpt as context for the agent.
         - zero or several ids -> never guess: only the excerpt context is added.
         The rewritten command still goes through _resolve_post (id must be a pending post), so a stale or wrong
         id changes nothing.
Data Imported/Modified: none.
Last Updated: 2026-11-13
"""
from __future__ import annotations

import re

MIN_EDIT_CHARS = 25
_ID = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{8}(?![0-9a-fA-F])")
_HAS_ID = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{8}(?![0-9a-fA-F])")
_APPROVE = {"אשר", "approve", "אישור", "מאשר"}
_REJECT = {"דחה", "reject", "דחייה"}
_HIST = {"היסטוריה", "history", "גרסאות"}
AMBIGUOUS = "quote-ambiguous"      # matches no post: _resolve_post answers "not found", nothing is changed
_COMMAND_START = re.compile(r"^(approve|אשר|edit|ערוך|reject|דחה|history|היסטוריה|גרסאות|ענה|דלג)\b", re.I)


def quoted_ids(quoted_text: str) -> list[str]:
    seen: list[str] = []
    for m in _ID.findall(quoted_text or ""):
        if m not in seen:
            seen.append(m)
    return seen


def rewrite(message: str, quoted_text: str) -> str:
    msg = (message or "").strip()
    q = (quoted_text or "").strip()
    if not q or not msg:
        return msg
    if _HAS_ID.search(msg):
        return msg                                  # the owner named the post himself
    ids = quoted_ids(q)
    if len(ids) == 1:
        pid, word = ids[0], msg.lower().strip(" .!")
        if word in _APPROVE:
            return f"אשר {pid}"
        if word in _REJECT:
            return f"דחה {pid}"
        if word in _HIST:
            return f"היסטוריה {pid}"
        if len(msg) >= MIN_EDIT_CHARS and not _COMMAND_START.match(msg):
            return f"ערוך {pid} {msg}"
    word = msg.lower().strip(" .!")
    if len(ids) > 1 and word in (_APPROVE | _REJECT | _HIST):
        return f"{'אשר' if word in _APPROVE else 'דחה' if word in _REJECT else 'היסטוריה'} {AMBIGUOUS}"
    if _COMMAND_START.match(msg):
        return msg
    return f"{msg}\n\n(בתגובה להודעה: «{q[:300]}»)"
