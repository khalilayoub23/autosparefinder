"""
Script: agents/returned_post.py
Purpose: Recognise an owner message that is an EDITED NOA POST sent back WITHOUT the `ערוך <id>` prefix, and tie it to
         the original pending post (owner mission 2026-11-13: "owner edits and returns a post, NOA does not continue").
         Before this, such a message fell through every console command into the free-chat LLM, which cannot edit a
         post: the revision was lost and the original stayed pending unchanged.
Process: match_returned_post(message, pending) -> ("match", post, new_text) | ("ambiguous", [posts], "") | ("none", None, "")
           1. an 8-char post id (the "🆔" token the owner is shown) in the text selects that post; the id token and
              any "🆔" chrome line are removed from the new text;
           2. otherwise the text must be clearly a revision of exactly ONE pending post (difflib similarity >= MIN_RATIO
              and ahead of the runner-up by >= MARGIN) and long enough not to be chat (MIN_CHARS).
         Nothing here writes: the caller applies it through owner_console.edit_post_text (the one edit path: version bump,
         approval reset, nothing published until the owner approves again).
Data Imported/Modified: none (pure).
Last Updated: 2026-11-13
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Tuple

MIN_CHARS = 60
MIN_RATIO = 0.55
MARGIN = 0.10
_ID_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{8})(?![0-9a-f])", re.I)


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").lower()).strip()


def _short(pid: str) -> str:
    return pid.replace("-", "")[:8]


def could_be_returned_post(message: str) -> bool:
    """Cheap pre-check (no DB): long enough to be a post, or carries an 8-hex id token."""
    msg = (message or "").strip()
    return len(msg) >= MIN_CHARS or bool(_ID_RE.search(msg))


def match_returned_post(message: str, pending: List[Dict[str, Any]]) -> Tuple[str, Any, str]:
    msg = (message or "").strip()
    if not msg or not pending:
        return "none", None, ""
    by_short = {_short(p["id"]): p for p in pending}
    for m in _ID_RE.finditer(msg):
        post = by_short.get(m.group(1).lower())
        if post:
            kept = "\n".join(ln for ln in msg.splitlines() if "🆔" not in ln)
            text = _ID_RE.sub("", kept, count=1).strip()
            return ("match", post, text) if len(text) >= 20 else ("none", None, "")
    if len(msg) < MIN_CHARS:
        return "none", None, ""
    scored = sorted(((SequenceMatcher(None, _norm(msg), _norm(p["content"])).ratio(), p) for p in pending),
                    key=lambda x: -x[0])
    best = scored[0]
    if best[0] < MIN_RATIO:
        return "none", None, ""
    if len(scored) > 1 and best[0] - scored[1][0] < MARGIN:
        return "ambiguous", [p for s, p in scored if best[0] - s < MARGIN], ""
    return "match", best[1], msg
