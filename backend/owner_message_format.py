"""
Script:  owner_message_format.py
Purpose: ONE shared formatter for every proactive owner WhatsApp message (audit 2026-11-24, FIXES_TRACKER #152).
         Pure functions, no I/O, so every rule is unit-tested and every source gets it through `notify_owner`.
Process:
  he_count / he_plural   correct Hebrew counts ("משימה אחת", "2 משימות") - fixes the "1 משימות" defect.
  sanitize_body          strips what must never reach the owner's phone: Python/SQLAlchemy tracebacks, SQL fragments,
                         docs URLs ("Background on this error"), shell commands in code fences / `fallocate`-style
                         lines. The raw text is returned separately so the CALLER can log it; the message carries a
                         short Hebrew explanation + a stable reference (`ref_for_error`).
  ref_for_error          stable 6-hex reference of an error text (same error -> same ref), used to find the log line.
  cap_message            hard length cap (WhatsApp is read on a phone) that never cuts mid-line.
  disk_same_storage      two mount points reporting the same underlying storage (same total, near-equal usage).
Data Imported/Modified: none.
Data Sources: none (pure).
Last Updated: 2026-11-24
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, Optional

# ── Hebrew counts ────────────────────────────────────────────────────────────────────────────────────

def he_plural(n: int, one: str, two: Optional[str], many: str, feminine: bool = True) -> str:
    """Hebrew noun phrase for a count. `one` = singular ("משימה"), `two` = dual or None, `many` = plural
    ("משימות"). 1 -> "משימה אחת" (feminine) / "פריט אחד" (masculine); 0 and >=2 -> "N משימות"."""
    n = int(n)
    if n == 1:
        return f"{one} {'אחת' if feminine else 'אחד'}"
    if n == 2 and two:
        return two
    return f"{n} {many}"


def he_count(n: int, noun: str) -> str:
    """Count for the nouns the alerts use. Unknown nouns fall back to "N noun" (never a wrong singular)."""
    table = {
        "task": ("משימה", "שתי משימות", "משימות", True),
        "item": ("פריט", "שני פריטים", "פריטים", False),
        "post": ("פוסט", "שני פוסטים", "פוסטים", False),
        "word": ("מילה", "שתי מילים", "מילים", True),
        "mail": ("מייל", "שני מיילים", "מיילים", False),
        "alert": ("התראה", "שתי התראות", "התראות", True),
        "failure": ("כשל", "שני כשלים", "כשלים", False),
        "order": ("הזמנה", "שתי הזמנות", "הזמנות", True),
        "hour": ("שעה", "שעתיים", "שעות", True),
        "minute": ("דקה", "שתי דקות", "דקות", True),
        "day": ("יום", "יומיים", "ימים", False),
    }
    if noun not in table:
        return f"{int(n)} {noun}"
    one, two, many, fem = table[noun]
    return he_plural(n, one, two, many, fem)


# ── error sanitising ─────────────────────────────────────────────────────────────────────────────────

_TRACE_MARKERS = (
    "Traceback (most recent call last)", "sqlalche.me", "Background on this error", "[SQL:", "[parameters:",
    "asyncpg.exceptions", "sqlalchemy.exc", "psycopg", "File \"/", "UPDATE parts_catalog", "SELECT ", "INSERT INTO",
    "RETURNING id", "AND is_active",
)
_URL_RE = re.compile(r"https?://\S+")
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_FENCE_RE = re.compile(r"```.*?```", re.S)
_SHELL_RE = re.compile(r"^\s*(docker|fallocate|mkswap|swapon|sudo|rm |chmod|python3? /app|bash |sh )\S*.*$", re.I | re.M)
_DB_ERR_NAMES = ("ProgrammingError", "IntegrityError", "OperationalError", "DataError", "InterfaceError",
                 "DBAPIError", "UndefinedColumn", "UniqueViolation", "DeadlockDetected", "QueryCanceled")


def looks_technical(text: str) -> bool:
    """True when the text carries an internal diagnostic that must not be sent to the owner as-is."""
    t = str(text or "")
    return any(m in t for m in _TRACE_MARKERS) or any(n in t for n in _DB_ERR_NAMES) or "```" in t


def ref_for_error(text: str) -> str:
    """Stable 6-hex reference: digits/uuids/quoted values are removed first so one defect keeps one reference."""
    norm = _UUID_RE.sub("", str(text or ""))
    norm = re.sub(r"\d+", "", norm)
    norm = re.sub(r"'[^']*'", "''", norm)
    norm = re.sub(r"\s+", " ", norm).strip()[:300]
    return hashlib.sha1(norm.encode("utf-8", "replace")).hexdigest()[:6]


def classify_error(text: str) -> str:
    """Short Hebrew label of WHAT kind of failure it was (never the raw text)."""
    t = str(text or "")
    low = t.lower()
    if "timeout" in low or "timed out" in low:
        m = re.search(r"after\s+(\d+)\s*s", t)
        return f"חרגה ממגבלת הזמן{f' ({int(m.group(1)) // 60} דקות)' if m else ''}"
    if "deadlock" in low:
        return "נתקעה בנעילת בסיס נתונים"
    if any(n in t for n in _DB_ERR_NAMES) or "sqlalche.me" in t or "[SQL:" in t or "asyncpg" in low:
        return "שגיאת בסיס נתונים"
    if "connection" in low and ("refused" in low or "reset" in low or "closed" in low):
        return "נותק החיבור לשירות"
    if "memory" in low or "oom" in low:
        return "נגמר זיכרון"
    if "429" in t or "rate limit" in low or "quota" in low:
        return "הספק חסם עקב עומס/מכסה"
    return "שגיאה פנימית"


def sanitize_error(text: str) -> tuple[str, str]:
    """(owner_text, reference) for a failure reason. The reference is what the operator greps for in the logs
    (`[owner_error ref=…]`); the raw text is NEVER part of owner_text."""
    ref = ref_for_error(text)
    return f"{classify_error(text)} · אסמכתה {ref}", ref


def sanitize_body(body: str) -> tuple[str, list]:
    """Remove diagnostics from an owner message body. Returns (clean_body, [(ref, raw_text), ...]) - the caller logs
    the raw text. Works on BLOCKS: a bullet ("• name: reason") plus the continuation lines a multi-line error leaves
    under it. A block that looks technical becomes "• name: <classification> · אסמכתה <ref>"; Hebrew prose, blank
    lines and "(15 הצליחו ...)" summary lines end a block and are kept. Code fences and bare shell commands are
    dropped (raw text still returned for the log)."""
    raw: list = []
    if not body:
        return "", raw
    text = _FENCE_RE.sub(lambda m: (raw.append((ref_for_error(m.group(0)), m.group(0))) or ""), str(body))
    lines = text.split("\n")
    out: list = []
    i = 0
    bullet_re = re.compile(r"^(\s*[•\-*]\s*[\w.\-/]+):\s*(.*)$")
    prose_re = re.compile(r"^\s*([\u0590-\u05FF]|[🆕🔄✅⚠️]|\(\d+\s)")
    while i < len(lines):
        line = lines[i]
        if _SHELL_RE.match(line):
            raw.append((ref_for_error(line), line))
            i += 1
            continue
        m = bullet_re.match(line)
        block = [line]
        j = i + 1
        if m:
            while j < len(lines) and lines[j].strip() and not bullet_re.match(lines[j]) and not prose_re.match(lines[j]):
                block.append(lines[j])
                j += 1
        joined = "\n".join(block)
        if looks_technical(joined):
            reason = (m.group(2) + "\n" + "\n".join(block[1:])) if m else joined
            clean, ref = sanitize_error(reason)
            raw.append((ref, joined))
            out.append(f"{m.group(1)}: {clean}" if m else clean)
        elif m and m.group(2).strip() and not re.search(r"[\u0590-\u05FF]", m.group(2)):
            # an English-only reason ("Task timeout after 1800s") is an internal string, not an owner message
            clean, ref = sanitize_error(m.group(2))
            raw.append((ref, joined))
            out.append(f"{m.group(1)}: {clean}")
        else:
            out.append(line if not m else joined)
        i = j
    clean = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return clean, raw


def cap_message(text: str, max_chars: int = 900, more_hint: str = "") -> str:
    """Hard cap that cuts at a line boundary and says so (never a silent mid-word cut)."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    cut = cut[: cut.rfind("\n")] if "\n" in cut else cut
    return cut.rstrip() + ("\n…" + (f" {more_hint}" if more_hint else ""))


# ── disk ─────────────────────────────────────────────────────────────────────────────────────────────

def disk_same_storage(a: dict, b: dict, tol_bytes: int = 2 * 10**9) -> bool:
    """Two samples (`disk_alert.sample`) describe the SAME storage when total size is equal and used space differs by
    less than `tol_bytes`. Inside the container `/` (overlay) and `/app/state` (volume) have different st_dev but sit
    on one host disk and report identical numbers - the st_dev test missed it (2 alerts per disk event)."""
    return a["total"] == b["total"] and abs(a["used"] - b["used"]) <= tol_bytes


def distinct_samples(samples: Iterable[dict]) -> list:
    keep: list = []
    for s in samples:
        if not any(disk_same_storage(s, k) for k in keep):
            keep.append(s)
    return keep


# ── NOA daily Facebook report ────────────────────────────────────────────────────────────────────────

def format_noa_daily(*, groups_identified: int, groups_approved: int, groups_published: int, groups_pending_24h: int,
                     groups_pending_total: int, page_identified: int, page_published: int, page_posts: int,
                     published_all: int, pending_posts: int, approved_unpublished: int, notable: list) -> str:
    """Hebrew daily report (audit 2026-11-24: the old one was an English counter table with a pending figure that
    mixed 24 h and total, and never asked for a decision). Verdict first, numbers only where they matter, one
    decision request when drafts wait. `notable` = Hebrew sentences about scan reliability."""
    lines: list = []
    if groups_identified == 0 and page_identified == 0 and published_all == 0:
        lines.append("אין פעילות פייסבוק ב-24 השעות האחרונות.")
    else:
        lines.append(f"בקבוצות: {groups_identified} פוסטים רלוונטיים, {groups_identified} טיוטות תגובה; "
                     f"אושרו {groups_approved}, פורסמו בפועל {groups_published}.")
        lines.append(f"בעמוד: {page_identified} פריטים רלוונטיים, {page_published} תגובות פורסמו, "
                     f"{page_posts} פוסטים מקוריים.")
    plat = f"פוסטים בכל הפלטפורמות: {published_all} פורסמו"
    if pending_posts:
        plat += f", {he_count(pending_posts, 'post')} ממתינים לאישורך"
    if approved_unpublished:
        plat += f", {he_count(approved_unpublished, 'post')} אושרו אך לא פורסמו במלואם"
    lines.append(plat + ".")
    for n in notable:
        lines.append(f"שימו לב: {n}")
    if groups_pending_total:
        lines.append(f"👉 {groups_pending_total} טיוטות תגובה בקבוצות ממתינות להחלטתך (מהן {groups_pending_24h} חדשות מאתמול). "
                     "כתוב *תגובות* לצפייה.")
    return "\n".join(lines)
