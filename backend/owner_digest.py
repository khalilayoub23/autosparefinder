"""
Script:  owner_digest.py
Purpose: ONE consolidated morning WhatsApp digest for every ROUTINE owner notification (mission 3, 2026-11-25).
         Routine events are recorded durably and delivered once, at the Israel-local digest time, inside the
         09:00-21:00 window. CRITICAL alerts (severity "critical") are never recorded here: they keep their
         immediate delivery path unchanged.
Process:
  1. `notify_owner` (the single owner-alert API) calls `record_event` for every non-critical alert; the three
     periodic reports (daily status, weekly harvest, job-queue progress) call it as `kind="report"`.
  2. `record_event` keeps ONE live event per identity (alert_key, else a hash of category+title with digits
     removed; `event_keys` aliases such as "mail:<ref>" join the same event across producers). A repeat with the
     same fingerprint is counted, never re-listed; a different fingerprint is a "changed" version; a due reminder
     is a "reminder"; a resolution closes the event (opened+resolved before any delivery = listed once as done).
  3. `deliver` (health-monitor pass, every 5 min) claims all pending events under a lock into a FROZEN digest
     record (id D<YYYYMMDD>[-n], rendered text, parts), sends it, and ONLY on success writes the "seen" memory and
     the checkpoint. A failed send keeps the frozen record and retries it unchanged with backoff (resuming at the
     first unsent part); a digest that never left and is older than a day is dissolved back into pending so the
     next one is complete. A crash between send and checkpoint re-sends the SAME digest id (at-least-once).
  4. Nothing is deleted without a trace: events older than DIGEST_EVENT_MAX_AGE_S go to the dead list (visible,
     replayable) and never raise an alert; an outage is never reported through the broken channel.
Data Imported/Modified: Redis keys under `autospare:digest:` (AOF-persistent). No Postgres, no schema.
Data Sources: callers of notify_owner.
Missing Data Delegation: n/a.
Last Updated: 2026-11-25
Inspect: `python3 /app/maintenance/owner_digest_cli.py status|pending|dead|replay-dead|preview`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

import owner_message_format as omf

NS = "autospare:digest:"
K_EVENTS, K_LAST, K_STATS, K_LOCK, K_SENDING = NS + "events", NS + "last", NS + "stats", NS + "lock", NS + "sending"
K_FROZEN, K_DEAD, K_LOG = NS + "frozen", NS + "dead", NS + "log"
TZ = ZoneInfo("Asia/Jerusalem")

SEEN_TTL_S = 30 * 86400
ALIAS_TTL_S = 3 * 86400
SUPPRESS_REPEAT_S = int(os.getenv("DIGEST_SUPPRESS_REPEAT_S", str(72 * 3600)))
EVENT_MAX_AGE_S = int(os.getenv("DIGEST_EVENT_MAX_AGE_S", str(14 * 86400)))
MSG_LIMIT = int(os.getenv("DIGEST_MSG_LIMIT", "3500"))          # WhatsApp text cap is 4096; headroom for numbering
LINE_LIMIT = 170
DIGEST_HOUR, DIGEST_MINUTE = (int(x) for x in os.getenv("OWNER_DIGEST_TIME_IL", "09:30").split(":"))
MAX_ATTEMPT_BACKOFF_S = 3600

SECTIONS = [  # (id, title) in message order
    ("critical", "🔴 דורש טיפול מיידי"),
    ("health", "🏥 בריאות המערכת ותפעול"),
    ("supplier", "🔌 ספקים ומקורות"),
    ("mail", "📧 מייל · NIR · BOAZ"),
    ("catalog", "🛰️ ייבוא, מחירים וקטלוג"),
    ("orders", "📦 הזמנות"),
    ("noa", "📣 NOA וסוכנים"),
    ("done", "✅ הושלם ונפתר"),
    ("action", "👉 דורש את פעולתך"),
]
_CAT_SECTION = {"health": "health", "system": "health", "supplier": "supplier", "email": "mail",
                "harvest": "catalog", "orders": "orders", "social": "noa"}
_STATE_TAG = {"changed": "🔄 ", "reminder": "⏰ ", "resolved": "✅ ", "action": "", "new": ""}


def enabled() -> bool:
    return os.getenv("OWNER_DIGEST_ENABLED", "1").strip().lower() not in ("0", "false", "off", "no")


# ── identity & classification (pure) ─────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return re.sub(r"[\d\s]+", " ", str(text or "")).strip().lower()


def identity_for(category: str, title: str, alert_key: str = "") -> str:
    if alert_key:
        return "k:" + str(alert_key)[:120]
    return "t:" + hashlib.sha1(f"{category}|{_norm(title)}".encode()).hexdigest()[:16]


def fingerprint_for(title: str, body: str, severity: str, caller_fp: str = "") -> str:
    """What the report SAYS: the caller's fingerprint when given, else the text with digits/spacing removed so a
    counter in the title or body is never a 'change'."""
    if caller_fp:
        return "c:" + str(caller_fp)
    first = str(body or "").strip().splitlines()[0] if str(body or "").strip() else ""
    return "h:" + hashlib.sha1(f"{severity}|{_norm(title)}|{_norm(first)[:200]}".encode()).hexdigest()[:16]


def section_for(ev: dict) -> str:
    if ev.get("sev") == "critical":
        return "critical"
    if ev.get("state") == "resolved" or ev.get("sev") == "success":
        return "done"
    if ev.get("actions") or ev.get("ref") or ev.get("state") == "action":
        return "action"
    return ev.get("section") or _CAT_SECTION.get(ev.get("cat", ""), "noa" if ev.get("cat") == "social" else "health")


# ── rendering (pure) ──────────────────────────────────────────────────────────────────────────────────

def _first_line(body: str, limit: int) -> str:
    for ln in str(body or "").splitlines():
        ln = ln.strip(" -•*\t")
        if ln and not (ln.endswith(":") and len(ln) <= 24):                    # skip label-only lines ("🆕 *חדש:*")
            ln = ln.replace("*", "")
            return ln if len(ln) <= limit else ln[: limit - 1].rstrip() + "…"
    return ""


def render_item(ev: dict, *, compact: bool = False) -> str:
    tag = {"error": "🟠 ", "warning": "🟡 "}.get(ev.get("sev", ""), "") + _STATE_TAG.get(ev.get("state", ""), "")
    title = str(ev.get("title", "")).strip()
    line = f"• {tag}{title}"
    if ev.get("opened_resolved"):
        line += " — נפתח ונפתר מאז הדיגסט הקודם"
    if ev.get("kind") == "report":
        body_lines = [b.strip() for b in str(ev.get("body", "")).splitlines() if b.strip()][: 3 if compact else 8]
        return "\n".join([line] + [f"  {b[:LINE_LIMIT]}" for b in body_lines])
    detail = _first_line(ev.get("body", ""), 90 if compact else LINE_LIMIT)
    if detail and not ev.get("opened_resolved"):
        line += f"\n  {detail}"
    if ev.get("repeats", 0) >= 2 and not compact:
        line += f"  (חזר {int(ev['repeats'])} פעמים)"
    for cmd in (ev.get("actions") or [])[:2]:
        line += f"\n  ▶ {str(cmd)[:80]}"
    if ev.get("ref"):
        line += f"  #{ev['ref']}"
    return line


def _order_key(ev: dict):
    sev_rank = {"critical": 0, "error": 1, "warning": 2, "info": 3, "success": 4}
    return (sev_rank.get(ev.get("sev", "info"), 3), ev.get("first_ts", 0))


def group_events(events: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {sid: [] for sid, _ in SECTIONS}
    for ev in sorted(events, key=_order_key):
        out[section_for(ev)].append(ev)
    return out


def _header(digest_id: str, events: list[dict], since_ts: Optional[float], now_local: datetime, note: str) -> list[str]:
    n_action = sum(1 for e in events if section_for(e) == "action")
    lines = [f"🌅 *AutoSpareFinder — עדכון בוקר* · {now_local:%d/%m}",
             f"מזהה {digest_id} · {omf.he_count(len(events), 'item')}"
             + (f" · מכסה מאז {datetime.fromtimestamp(since_ts, TZ):%d/%m %H:%M}" if since_ts else "")]
    lines.append(f"👉 {omf.he_count(n_action, 'item')} דורשים את פעולתך" if n_action else "✔️ אין משהו שדורש אותך כרגע")
    if note:
        lines.append(f"ℹ️ {note}")
    return lines


def render_digest(digest_id: str, events: list[dict], *, now_local: datetime, since_ts: Optional[float] = None,
                  note: str = "", limit: int = MSG_LIMIT) -> list[str]:
    """Returns the message parts (usually one). Compacts before it splits; splits only when unavoidable, with a
    shared digest id and 'חלק i/n' numbering. Never returns an empty list for non-empty events."""
    if not events:
        return []
    groups = group_events(events)
    head = _header(digest_id, events, since_ts, now_local, note)

    def build(max_items: Optional[int], compact: bool) -> list[tuple[str, str]]:
        blocks: list[tuple[str, str]] = []
        for sid, title in SECTIONS:
            evs = groups[sid]
            if not evs:
                continue
            shown = evs if max_items is None else evs[:max_items]
            body = [render_item(e, compact=compact) for e in shown]
            if len(shown) < len(evs):
                body.append(f"• ועוד {omf.he_count(len(evs) - len(shown), 'item')} — הפרטים בקונסולה: *דיגסט*")
            blocks.append((sid, f"*{title}*\n" + "\n".join(body)))
        return blocks

    for max_items, compact in ((None, False), (None, True), (6, True), (4, True), (2, True)):
        blocks = build(max_items, compact)
        text = "\n".join(head) + "\n\n" + "\n\n".join(b for _, b in blocks)
        if len(text) <= limit:
            return [text]
    # split by whole sections (compact, 2 items each) - technically unavoidable
    parts: list[str] = []
    cur: list[str] = []
    cur_len = len("\n".join(head)) + 20
    for _, block in blocks:
        if cur and cur_len + len(block) + 2 > limit:
            parts.append("\n\n".join(cur)); cur, cur_len = [], 40
        if len(block) > limit - 80:
            block = block[: limit - 100].rsplit("\n", 1)[0] + "\n• … (המשך בקונסולה: *דיגסט*)"
        cur.append(block); cur_len += len(block) + 2
    if cur:
        parts.append("\n\n".join(cur))
    total = len(parts)
    out = []
    for i, p in enumerate(parts, 1):
        h = "\n".join(head) if i == 1 else f"🌅 *AutoSpareFinder — עדכון בוקר* · {now_local:%d/%m} · מזהה {digest_id}"
        out.append(f"{h}\n(חלק {i}/{total})\n\n{p}")
    return out


# ── store ─────────────────────────────────────────────────────────────────────────────────────────────

def _s(v: Any) -> str:
    return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)


class Store:
    """All state in Redis; `r` is an asyncio redis client. `clock` is injectable for tests."""

    def __init__(self, r, clock: Callable[[], float] = time.time):
        self.r, self.now = r, clock

    # lock ----------------------------------------------------------------------------------------------
    async def _lock(self, wait_s: float = 3.0) -> str:
        token = uuid.uuid4().hex
        deadline = time.time() + wait_s
        while True:
            if await self.r.set(K_LOCK, token, nx=True, ex=30):
                return token
            if time.time() >= deadline:
                raise TimeoutError("digest lock busy")
            await asyncio.sleep(0.05)

    async def _unlock(self, token: str) -> None:
        try:
            if _s(await self.r.get(K_LOCK) or "") == token:
                await self.r.delete(K_LOCK)
        except Exception:
            pass

    async def _stat(self, name: str, n: int = 1) -> None:
        try:
            await self.r.hincrby(K_STATS, name, n)
        except Exception:
            pass

    async def _getj(self, key: str):
        raw = await self.r.get(key)
        return json.loads(_s(raw)) if raw else None

    # record --------------------------------------------------------------------------------------------
    async def record(self, *, category: str, title: str, body: str = "", severity: str = "info", state: str = "new",
                     alert_key: str = "", fingerprint: str = "", ref: str = "", actions: Optional[list] = None,
                     event_keys: Optional[list] = None, kind: str = "alert", section: str = "") -> dict:
        now = self.now()
        title, body = str(title or "").strip(), str(body or "").strip()
        if not title:
            return {"ok": False, "error": "empty title"}
        keys = [str(k) for k in (event_keys or []) if k]
        tok = await self._lock()
        try:
            ident = identity_for(category, title, alert_key)
            for k in keys:                                  # cross-producer identity: first known alias wins
                al = await self.r.get(NS + "alias:" + k)
                if al:
                    ident = _s(al)
                    break
            for k in keys:
                await self.r.set(NS + "alias:" + k, ident, ex=ALIAS_TTL_S)
            fp = fingerprint_for(title, body, severity, fingerprint)
            prev_raw = await self.r.hget(K_EVENTS, ident)
            prev = json.loads(_s(prev_raw)) if prev_raw else None
            seen = await self._getj(NS + "seen:" + ident)
            ev = {"id": ident, "cat": category, "sev": severity, "state": state, "title": title[:160], "body": body[:600],
                  "ref": ref, "actions": [str(a)[:120] for a in (actions or [])][:3], "fp": fp, "first_ts": now,
                  "last_ts": now, "repeats": 1, "kind": kind, "section": section, "keys": keys[:6]}
            if state == "resolved":
                if prev:
                    prev.update(state="resolved", last_ts=now, sev="success", opened_resolved=not seen,
                                body=body or prev.get("body", ""), fp=fp)
                    await self.r.hset(K_EVENTS, ident, json.dumps(prev, ensure_ascii=False))
                    await self._stat("resolved_pending")
                    return {"ok": True, "event_id": ident, "action": "resolved_pending"}
                if not seen:
                    await self._stat("resolved_unknown")
                    return {"ok": False, "suppressed": True, "error": "nothing_to_resolve"}
                await self.r.hset(K_EVENTS, ident, json.dumps(ev, ensure_ascii=False))
                await self._stat("recorded")
                return {"ok": True, "event_id": ident, "action": "resolved_new"}
            if prev:
                if prev.get("fp") == fp and state != "reminder":
                    prev["repeats"] = int(prev.get("repeats", 1)) + 1
                    prev["last_ts"] = now
                    await self.r.hset(K_EVENTS, ident, json.dumps(prev, ensure_ascii=False))
                    await self._stat("merged_duplicate")
                    return {"ok": True, "event_id": ident, "action": "merged", "dup": True}
                ev["first_ts"] = prev.get("first_ts", now)
                ev["repeats"] = int(prev.get("repeats", 1)) + 1
                if state == "new" and (seen or prev.get("state") == "changed"):
                    ev["state"] = "changed"
                elif state == "new" and prev.get("state") in ("action",):
                    ev["state"] = prev["state"]
                await self.r.hset(K_EVENTS, ident, json.dumps(ev, ensure_ascii=False))
                await self._stat("replaced_version")
                return {"ok": True, "event_id": ident, "action": "replaced"}
            if seen and seen.get("fp") == fp and state != "reminder" and now - float(seen.get("ts", 0)) < SUPPRESS_REPEAT_S:
                await self._stat("suppressed_unchanged")
                return {"ok": False, "suppressed": True, "error": "digest_unchanged", "event_id": ident}
            if seen and state == "new":
                ev["state"] = "changed"
            await self.r.hset(K_EVENTS, ident, json.dumps(ev, ensure_ascii=False))
            await self._stat("recorded")
            return {"ok": True, "event_id": ident, "action": "recorded"}
        finally:
            await self._unlock(tok)

    # read ----------------------------------------------------------------------------------------------
    async def pending(self) -> list[dict]:
        raw = await self.r.hgetall(K_EVENTS)
        out = []
        for _, v in (raw or {}).items():
            try:
                out.append(json.loads(_s(v)))
            except Exception:
                continue
        return sorted(out, key=lambda e: e.get("first_ts", 0))

    async def frozen(self) -> Optional[dict]:
        return await self._getj(K_FROZEN)

    async def status(self) -> dict:
        st = await self.r.hgetall(K_STATS)
        return {"pending": len(await self.pending()), "frozen": await self.frozen() and {
                    k: v for k, v in (await self.frozen()).items() if k not in ("parts", "events")},
                "last": await self._getj(K_LAST), "dead": await self.r.llen(K_DEAD),
                "stats": {_s(k): int(_s(v)) for k, v in (st or {}).items()}}

    # lifecycle -----------------------------------------------------------------------------------------
    async def _expire_old(self) -> int:
        now, n = self.now(), 0
        for ev in await self.pending():
            if now - float(ev.get("first_ts", now)) > EVENT_MAX_AGE_S:
                await self.r.lpush(K_DEAD, json.dumps(dict(ev, dead_reason="expired", dead_ts=now), ensure_ascii=False))
                await self.r.ltrim(K_DEAD, 0, 499)
                await self.r.hdel(K_EVENTS, ev["id"])
                n += 1
        return n

    async def _digest_id(self, now_local: datetime) -> str:
        base = f"D{now_local:%Y%m%d}"
        last = await self._getj(K_LAST)
        if last and str(last.get("id", "")).startswith(base):
            n = int(str(last["id"]).split("-")[1]) + 1 if "-" in str(last["id"]) else 2
            return f"{base}-{n}"
        return base

    async def _dissolve(self, frozen: dict) -> None:
        """Put a never-sent frozen digest's events back into pending (a newer version of the same identity wins)."""
        for ev in frozen.get("events", []):
            cur = await self.r.hget(K_EVENTS, ev["id"])
            if cur:
                c = json.loads(_s(cur))
                c["first_ts"] = min(c.get("first_ts", 0), ev.get("first_ts", 0))
                c["repeats"] = int(c.get("repeats", 1)) + int(ev.get("repeats", 1))
                await self.r.hset(K_EVENTS, ev["id"], json.dumps(c, ensure_ascii=False))
            else:
                await self.r.hset(K_EVENTS, ev["id"], json.dumps(ev, ensure_ascii=False))
        await self.r.delete(K_FROZEN)

    async def deliver(self, send: Callable[[str], Awaitable[dict]], *, now_local: Optional[datetime] = None,
                      window_open: bool = True, note: str = "", force: bool = False) -> dict:
        """One delivery pass. `send(text)` -> {"ok": bool, "error": str}. Idempotent; safe to call from several loops."""
        now = self.now()
        now_local = now_local or datetime.fromtimestamp(now, TZ)
        if not window_open:
            return {"skipped": "quiet_hours"}
        if not force and (now_local.hour, now_local.minute) < (DIGEST_HOUR, DIGEST_MINUTE):
            return {"skipped": "before_digest_time"}
        if not await self.r.set(K_SENDING, "1", nx=True, ex=180):
            return {"skipped": "another_pass_running"}
        try:
            await self._expire_old()
            tok = await self._lock()
            try:
                fz = await self.frozen()
                if fz and not fz.get("sent_parts") and now - float(fz.get("created", now)) > 86400:
                    await self._dissolve(fz)                                    # never left, stale: rebuild complete
                    fz = None
                if fz and fz.get("next_try", 0) > now and not force:
                    return {"skipped": "backoff", "digest": fz["id"], "next_try": fz["next_try"]}
                if not fz:
                    events = await self.pending()
                    if not events:
                        return {"skipped": "empty"}                              # never an empty digest
                    did = await self._digest_id(now_local)
                    last = await self._getj(K_LAST)
                    parts = render_digest(did, events, now_local=now_local, since_ts=(last or {}).get("ts"), note=note)
                    fz = {"id": did, "events": events, "parts": parts, "sent_parts": 0, "attempts": 0,
                          "created": now, "next_try": 0}
                    for ev in events:
                        await self.r.hdel(K_EVENTS, ev["id"])
                    await self.r.set(K_FROZEN, json.dumps(fz, ensure_ascii=False))
            finally:
                await self._unlock(tok)
            # send outside the lock: events arriving now stay pending for the next digest
            for i in range(int(fz["sent_parts"]), len(fz["parts"])):
                text = fz["parts"][i]
                if note and i == 0 and note not in text:                       # outage note is known only at send time
                    ls = text.split("\n")
                    ls.insert(min(3, len(ls)), f"ℹ️ {note}")
                    text = "\n".join(ls)
                try:
                    res = await send(text)
                except Exception as exc:  # noqa: BLE001
                    res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                if not res.get("ok") or res.get("queued"):
                    fz["attempts"] = int(fz["attempts"]) + 1
                    fz["next_try"] = now + min(MAX_ATTEMPT_BACKOFF_S, 300 * 2 ** min(fz["attempts"] - 1, 4))
                    fz["last_error"] = str(res.get("error") or "queued")[:120]
                    await self.r.set(K_FROZEN, json.dumps(fz, ensure_ascii=False))
                    await self._stat("delivery_failed")
                    print(f"[owner_digest] delivery failed digest={fz['id']} part={i + 1}/{len(fz['parts'])} "
                          f"attempt={fz['attempts']} error={fz['last_error']} (events kept, retry in "
                          f"{int(fz['next_try'] - now)}s)")
                    return {"ok": False, "digest": fz["id"], "error": fz["last_error"], "attempts": fz["attempts"]}
                fz["sent_parts"] = i + 1
                await self.r.set(K_FROZEN, json.dumps(fz, ensure_ascii=False))
            # fully sent: checkpoint exactly once
            tok = await self._lock()
            try:
                for ev in fz["events"]:
                    if ev.get("state") == "resolved":
                        await self.r.delete(NS + "seen:" + ev["id"])
                    else:
                        await self.r.set(NS + "seen:" + ev["id"], json.dumps({"fp": ev["fp"], "ts": now,
                                         "digest": fz["id"]}), ex=SEEN_TTL_S)
                await self.r.set(K_LAST, json.dumps({"id": fz["id"], "ts": now, "events": [e["id"] for e in fz["events"]],
                                                     "attempts": fz["attempts"], "parts": len(fz["parts"])}))
                await self.r.lpush(K_LOG, json.dumps({"id": fz["id"], "ts": now, "events": [e["id"] for e in fz["events"]]}))
                await self.r.ltrim(K_LOG, 0, 59)
                await self.r.delete(K_FROZEN)
                await self._stat("digests_delivered")
            finally:
                await self._unlock(tok)
            return {"ok": True, "digest": fz["id"], "events": len(fz["events"]), "parts": len(fz["parts"]),
                    "attempts": fz["attempts"]}
        finally:
            await self.r.delete(K_SENDING)

    async def replay_dead(self) -> int:
        n = 0
        while True:
            raw = await self.r.rpop(K_DEAD)
            if not raw:
                return n
            ev = json.loads(_s(raw))
            ev.pop("dead_reason", None); ev.pop("dead_ts", None)
            ev["first_ts"] = self.now()
            await self.r.hset(K_EVENTS, ev["id"], json.dumps(ev, ensure_ascii=False))
            n += 1
