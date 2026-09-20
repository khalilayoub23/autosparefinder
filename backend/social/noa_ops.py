"""
Script: social/noa_ops.py
Purpose: ONE shared operations layer for NOA's Facebook engagement (Page comments + Groups),
         used identically by Phase 1 (owner approval) and Phase 2 (autonomous). There is a
         single approve+post implementation per surface; the ONLY difference between the
         phases is WHO is recorded as the approver:
           Phase 1  : actor='owner'       (owner console command)
           Phase 2  : actor='autonomous'  (same functions, after safety+rate+readiness gates)
         Every other control (relevance, dedup, rate limit, audit, failure handling) is shared.

Process:
  - record_scan_run(): one persisted row per scan cycle (noa_scan_runs) - the ONLY persisted
    source for scan volume / duplicate-prevention / session-failure metrics.
  - safety_check(): deterministic gate applied to autonomous output (never to the owner's text).
  - rate_limit_ok(): DB-backed (survives restarts) per-group hourly cap + autonomous hour/day caps.
  - claim_group_draft()/post_claimed_group_draft(): the single group approve+post path.
  - send_page_reply(): the single Page-comment reply path.
  - readiness_report()/autonomous_gate(): 13-point release check (the 12 required + owner-channel delivery); the gate is FAIL-CLOSED and
    can never enable autonomy by itself - NOA_ENGAGEMENT_AUTOREPLY=1 is a deliberate operator act.
  - daily_metrics(): EOD/observability aggregates from existing tables + noa_scan_runs.

Data Imported/Modified: group_comment_drafts, social_inbox (audit columns), noa_scan_runs.
Data Sources: existing NOA tables only (no external calls except the Facebook write handlers).
Last Updated: 2026-09-20
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import sqlalchemy as sa

log = logging.getLogger("noa_ops")

AUTONOMOUS_FLAG = "NOA_ENGAGEMENT_AUTOREPLY"


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def group_hourly_cap() -> int:
    return _int("NOA_GROUP_COMMENTS_PER_HOUR", 3)


def max_attempts() -> int:
    return _int("NOA_COMMENT_MAX_ATTEMPTS", 3)


def auto_max_per_hour() -> int:
    return _int("NOA_AUTONOMOUS_MAX_PER_HOUR", 3)


def auto_max_per_day() -> int:
    return _int("NOA_AUTONOMOUS_MAX_PER_DAY", 10)


def draft_budget_per_cycle() -> int:
    return _int("NOA_GROUP_DRAFT_MAX_PER_CYCLE", 20)


def autonomous_flag_on() -> bool:
    return os.getenv(AUTONOMOUS_FLAG, "0").strip() == "1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _notify(title: str, body: str, *, alert_key: str, cooldown_s: int = 3600, severity: str = "info") -> None:
    try:
        from BACKEND_API_ROUTES import notify_owner  # in-process only
        await notify_owner("social", title, body, severity=severity, alert_key=alert_key, cooldown_s=cooldown_s)
    except Exception as exc:  # never let a notification failure break the pipeline
        log.warning("noa_ops: owner notify skipped (%s)", str(exc)[:100])


# ── scan-run ledger ───────────────────────────────────────────────────────────
async def record_scan_run(db, source: str, started_at: datetime, *, items_scanned: int = 0, relevant: int = 0,
                          rejected: int = 0, drafted: int = 0, duplicates: int = 0, failures: int = 0,
                          session_failed: bool = False, error: Optional[str] = None,
                          detail: Optional[Dict[str, Any]] = None) -> None:
    """Persist one scan cycle. Never raises."""
    try:
        await db.execute(sa.text("""
            INSERT INTO noa_scan_runs (source, started_at, items_scanned, relevant, rejected, drafted,
                                       duplicates, failures, session_failed, error, detail)
            VALUES (:src, :st, :sc, :rel, :rej, :dr, :dup, :fa, :sf, :err, CAST(:det AS jsonb))
        """), {"src": source, "st": started_at, "sc": int(items_scanned), "rel": int(relevant), "rej": int(rejected),
               "dr": int(drafted), "dup": int(duplicates), "fa": int(failures), "sf": bool(session_failed),
               "err": (error or None) and str(error)[:300], "det": json.dumps(detail or {}, default=str)})
        await db.commit()
    except Exception as exc:
        log.warning("noa_ops: record_scan_run failed: %s", str(exc)[:150])
        try:
            await db.rollback()
        except Exception:
            pass


# ── deterministic safety gate (autonomous output only) ────────────────────────
_UNSAFE_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("reasoning_leak", re.compile(r"(?i)(<think>|draft option|option\s*\d\s*[:.)]|let me think|as an ai\b|chain of thought)")),
    ("internal_pricing", re.compile(r"(?i)(1\.45|45\s*%|margin|מרווח|עלות ספק|supplier cost)")),
    ("commercial_claim", re.compile(r"(?i)(₪\s*\d|\d\s*₪|\d+\s*ש\"ח|[$€£]\s*\d|\d+\s*%\s*(off|הנחה)|הנחה|קופון|coupon|discount|free shipping|משלוח חינם)")),
    ("phone_or_foreign_link", re.compile(r"(https?://(?!(www\.)?autosparefinder\.co\.il)|\b0\d{1,2}[-\s]?\d{7}\b)")),
    ("hashtag", re.compile(r"#\w")),
]


def safety_check(text: str, *, max_chars: int = 300) -> Tuple[bool, str]:
    """Deterministic gate for AUTONOMOUS output. Fails closed: any doubt => owner review."""
    t = (text or "").strip()
    if not t:
        return False, "empty"
    if len(t) > max_chars:
        return False, f"too_long:{len(t)}>{max_chars}"
    if not re.search(r"[A-Za-z֐-׿؀-ۿ]", t):
        return False, "no_language_content"
    for name, rx in _UNSAFE_PATTERNS:
        if rx.search(t):
            return False, name
    return True, "ok"


# ── DB-backed rate limits ─────────────────────────────────────────────────────
async def rate_limit_ok(db, *, actor: str, group_target_id: Optional[str] = None) -> Tuple[bool, str]:
    """Per-group hourly cap applies to every actor; hour/day ceilings apply to autonomous."""
    if group_target_id:
        n = (await db.execute(sa.text("""
            SELECT count(*) FROM group_comment_drafts
             WHERE group_target_id = CAST(:g AS uuid) AND posted_at > NOW() - INTERVAL '1 hour'
        """), {"g": group_target_id})).scalar() or 0
        if n >= group_hourly_cap():
            return False, f"group_hourly_cap:{n}/{group_hourly_cap()}"
    if actor == "autonomous":
        for label, interval, cap in (("hour", "1 hour", auto_max_per_hour()), ("day", "24 hours", auto_max_per_day())):
            n = (await db.execute(sa.text(f"""
                SELECT (SELECT count(*) FROM group_comment_drafts
                         WHERE approved_by='autonomous' AND posted_at > NOW() - INTERVAL '{interval}')
                     + (SELECT count(*) FROM social_inbox
                         WHERE approved_by='autonomous' AND replied_at > NOW() - INTERVAL '{interval}')
            """))).scalar() or 0
            if n >= cap:
                return False, f"autonomous_{label}_cap:{n}/{cap}"
    return True, "ok"


# ── group drafts: pre-draft dedup, save, and the single approve+post path ─────
async def has_active_draft(db, post_url: str) -> bool:
    """True when this post already has a live/in-flight/failed draft (never re-draft it)."""
    r = await db.execute(sa.text("""
        SELECT 1 FROM group_comment_drafts
         WHERE post_url = :u AND status IN ('pending_approval','approved','posted','failed') LIMIT 1
    """), {"u": post_url[:500]})
    return r.first() is not None


async def claim_group_draft(db, token: str, *, actor: str) -> Optional[Dict[str, Any]]:
    """Atomically move ONE pending draft to 'approved' and record who approved it.
    The UPDATE...WHERE status='pending_approval' claim means two approvals can never both win."""
    res = await db.execute(sa.text("""
        UPDATE group_comment_drafts d
           SET status='approved', approved_at=NOW(), approved_by=:actor
         WHERE d.id = (SELECT id FROM group_comment_drafts
                        WHERE status='pending_approval' AND (id::text LIKE :tok OR id::text = :full)
                        ORDER BY created_at LIMIT 1)
           AND d.status='pending_approval'
        RETURNING d.id::text AS id, d.group_target_id::text AS gid, d.post_url, d.draft_comment
    """), {"actor": actor, "tok": f"{token}%", "full": token})
    row = res.first()
    await db.commit()
    if not row:
        return None
    m = row._mapping
    gu = (await db.execute(sa.text("SELECT group_url FROM group_targets WHERE id = CAST(:g AS uuid)"),
                           {"g": m["gid"]})).scalar() if m["gid"] else ""
    return {"id": m["id"], "group_target_id": m["gid"], "post_url": m["post_url"],
            "draft": m["draft_comment"], "group_url": gu or ""}


async def post_claimed_group_draft(claimed: Dict[str, Any], *, actor: str) -> Dict[str, Any]:
    """Publish an already-claimed ('approved') group draft and record the outcome.

    Outcomes (this is what closes the duplicate-comment hole):
      posted                       -> status 'posted', posted_at set
      submitted but unverified     -> status 'failed' (NOT reverted: it may already be public)
      not submitted (auth/DOM/...) -> attempts+1; back to 'pending_approval' until max attempts, then 'failed'
      rate limited                 -> back to 'pending_approval', attempt not consumed
    """
    from BACKEND_DATABASE_MODELS import async_session_factory
    from social.facebook_browser.group_agent import GroupAgent

    did = claimed["id"]
    async with async_session_factory() as db:
        ok_rate, why = await rate_limit_ok(db, actor=actor, group_target_id=claimed.get("group_target_id"))
        if not ok_rate:
            await db.execute(sa.text("""
                UPDATE group_comment_drafts SET status='pending_approval', approved_at=NULL, approved_by=NULL,
                       last_error=:e WHERE id=CAST(:id AS uuid)"""), {"e": f"rate_limited:{why}"[:300], "id": did})
            await db.commit()
            return {"ok": False, "outcome": "rate_limited", "error": why}

        try:
            result = await GroupAgent().submit_approved_comment(
                post_url=claimed["post_url"], comment_text=claimed["draft"], group_url=claimed.get("group_url", ""))
        except Exception as exc:
            result = {"ok": False, "error": f"exception:{str(exc)[:200]}", "submitted": False}

        if result.get("ok"):
            await db.execute(sa.text("""
                UPDATE group_comment_drafts SET status='posted', posted_at=NOW(), attempts=attempts+1,
                       last_attempt_at=NOW(), last_error=NULL WHERE id=CAST(:id AS uuid)"""), {"id": did})
            outcome = "posted"
        elif result.get("submitted"):
            await db.execute(sa.text("""
                UPDATE group_comment_drafts SET status='failed', attempts=attempts+1, last_attempt_at=NOW(),
                       last_error=:e WHERE id=CAST(:id AS uuid)"""),
                {"e": ("unverified_after_submit: " + str(result.get("error")))[:300], "id": did})
            outcome = "unverified"
        else:
            row = (await db.execute(sa.text("""
                UPDATE group_comment_drafts SET attempts=attempts+1, last_attempt_at=NOW(), last_error=:e,
                       status = CASE WHEN attempts+1 >= :mx THEN 'failed' ELSE 'pending_approval' END,
                       approved_at = CASE WHEN attempts+1 >= :mx THEN approved_at ELSE NULL END,
                       approved_by = CASE WHEN attempts+1 >= :mx THEN approved_by ELSE NULL END
                 WHERE id=CAST(:id AS uuid) RETURNING status"""),
                {"e": str(result.get("error"))[:300], "mx": max_attempts(), "id": did})).first()
            outcome = "failed" if row and row[0] == "failed" else "retry_pending"
        await db.commit()

    if outcome == "posted":
        await _notify("NOA — תגובה פורסמה בקבוצה", f"({actor}) {claimed['draft'][:140]}\n{claimed['post_url'][:120]}",
                      alert_key=f"group_comment_result_{did}", cooldown_s=86400)
    else:
        await _notify("NOA — פרסום תגובה בקבוצה נכשל",
                      f"סטטוס: {outcome}\nשגיאה: {str(result.get('error'))[:160]}\n{claimed['post_url'][:120]}"
                      + ("\n⚠️ ייתכן שהתגובה כבר פורסמה — בדוק ידנית לפני אישור חוזר." if outcome == "unverified" else ""),
                      alert_key=f"group_comment_result_{did}", cooldown_s=86400, severity="warning")
    return {"ok": outcome == "posted", "outcome": outcome, "error": result.get("error")}


async def maybe_autonomous_group_post(db, post_url: str) -> str:
    """Phase 2 hook, called for each freshly saved group draft. Returns quickly ('disabled')
    while the flag is off. When on, it runs the SAME claim+post path as the owner command,
    behind the safety, rate-limit and readiness gates. Anything doubtful stays pending for the owner."""
    if not autonomous_flag_on():
        return "disabled"
    allowed, reasons = await autonomous_gate(db)
    if not allowed:
        return "gate_closed:" + ",".join(reasons)[:120]
    row = (await db.execute(sa.text("""
        SELECT id::text, draft_comment FROM group_comment_drafts
         WHERE post_url=:u AND status='pending_approval' LIMIT 1"""), {"u": post_url[:500]})).first()
    if not row:
        return "no_pending_draft"
    ok, why = safety_check(row[1])
    if not ok:
        await db.execute(sa.text("UPDATE group_comment_drafts SET last_error=:e WHERE id=CAST(:id AS uuid)"),
                         {"e": f"safety:{why}", "id": row[0]})
        await db.commit()
        return f"safety_blocked:{why}"
    claimed = await claim_group_draft(db, row[0], actor="autonomous")
    if not claimed:
        return "already_claimed"
    res = await post_claimed_group_draft(claimed, actor="autonomous")
    return res["outcome"]


# ── Page comment replies: the single reply path ───────────────────────────────
async def send_page_reply(db, item: Dict[str, Any], text: str, *, actor: str) -> Dict[str, Any]:
    """Send a reply to a social_inbox item and record the outcome. actor: 'owner' | 'autonomous'."""
    from social import engagement as _eng
    if actor == "autonomous":
        ok, why = safety_check(text, max_chars=500)
        if not ok:
            await db.execute(sa.text("UPDATE social_inbox SET last_error=:e WHERE id=CAST(:id AS uuid)"),
                             {"e": f"safety:{why}", "id": item["id"]})
            await db.commit()
            return {"ok": False, "error": f"safety:{why}", "blocked": True}
        ok, why = await rate_limit_ok(db, actor=actor)
        if not ok:
            return {"ok": False, "error": why, "blocked": True}
    res = await _eng.send_reply(item["platform"], str(item["external_id"]), text)
    if res.get("ok"):
        await _eng.mark_replied(db, item["id"], res.get("id"))
        await db.execute(sa.text("""
            UPDATE social_inbox SET approved_by=:a, attempts=attempts+1, last_error=NULL
             WHERE id=CAST(:id AS uuid)"""), {"a": actor, "id": item["id"]})
    else:
        await db.execute(sa.text("""
            UPDATE social_inbox SET attempts=attempts+1, last_error=:e WHERE id=CAST(:id AS uuid)"""),
            {"e": str(res.get("error"))[:300], "id": item["id"]})
    await db.commit()
    return res


# ── readiness (12 checks) and the fail-closed autonomous gate ─────────────────
_SESSION_FRESH_H = 36  # group loop is daily (+2h startup stagger)


async def _owner_delivery_state() -> Optional[Dict[str, Any]]:
    """Last real owner-notification outcome, recorded by BACKEND_API_ROUTES.notify_owner."""
    try:
        from BACKEND_API_ROUTES import get_redis  # in-process only
        raw = await (await get_redis()).get("autospare:owner_delivery:last")
        return json.loads(raw) if raw else None
    except Exception:
        return None


# ── owner WhatsApp channel: truthful state (never trust bridge /health.connected alone) ───────
# The bridge's /health.connected is `!!waSocket.user`, which is true whenever stale credentials exist,
# even after WhatsApp rejects the login (401). It was "connected" through both the 2026-07-29 and the
# 2026-09-14 outages. Truth comes from (a) an authenticated server round-trip (/groups - existing,
# read-only, no user-visible effect) and (b) the real outcome of owner sends.
CHANNEL_STATES = ("DELIVERY_VERIFIED", "AUTHENTICATED", "CONNECTED_BUT_NOT_AUTHENTICATED", "DELIVERY_FAILED",
                  "AWAITING_QR", "DISCONNECTED", "WRONG_ACCOUNT", "BRIDGE_UNREACHABLE")
_USABLE_STATES = ("DELIVERY_VERIFIED", "AUTHENTICATED")
_DEAD_SESSION_MARKERS = ("connection closed", "not connected", "not authenticated", "logged out", "401")


def _looks_dead(err: Optional[str]) -> bool:
    e = (err or "").lower()
    return any(m in e for m in _DEAD_SESSION_MARKERS)


def classify_owner_channel(health: Optional[Dict[str, Any]], probe_ok: Optional[bool], probe_err: Optional[str],
                           delivery: Optional[Dict[str, Any]], *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Pure function. Returns {state, usable, detail}.

    Precedence: bridge unreachable > wrong account > awaiting QR > disconnected, then for a bridge that CLAIMS
    to be connected: failed authenticated probe => CONNECTED_BUT_NOT_AUTHENTICATED; else the freshest real send
    outcome decides DELIVERY_VERIFIED / DELIVERY_FAILED; a passing probe with no fresh send evidence is
    AUTHENTICATED (session proven by the server, delivery not yet observed)."""
    now = now or _now()
    d_age = None
    if delivery and delivery.get("ts"):
        d_age = (now - datetime.fromisoformat(delivery["ts"])).total_seconds() / 3600.0

    def out(state: str, detail: str) -> Dict[str, Any]:
        return {"state": state, "usable": state in _USABLE_STATES, "detail": detail}

    if health is None:
        return out("BRIDGE_UNREACHABLE", "bridge /health unreachable")
    if health.get("account_mismatch"):
        return out("WRONG_ACCOUNT", "bridge linked to a different WhatsApp account")
    if health.get("awaiting_qr_scan"):
        return out("AWAITING_QR", "bridge waiting for a QR scan")
    if not health.get("connected"):
        return out("DISCONNECTED", "bridge reports not connected")
    # health says connected -> verify with the server
    if probe_ok is False:
        return out("CONNECTED_BUT_NOT_AUTHENTICATED",
                   f"/health says connected but the authenticated round-trip failed: {probe_err}")
    fresh_fail = bool(delivery) and not delivery.get("ok") and d_age is not None and d_age <= 0.5
    if fresh_fail and probe_ok is True and _looks_dead(delivery.get("error")):
        # A dead-session send failure ("Connection Closed"...) older than a probe that the server just
        # accepted is stale (e.g. sent before a re-pair) - the live round-trip is the fresher truth.
        fresh_fail = False
    if fresh_fail:
        st = "CONNECTED_BUT_NOT_AUTHENTICATED" if _looks_dead(delivery.get("error")) else "DELIVERY_FAILED"
        return out(st, f"last owner send failed {d_age:.2f}h ago: {delivery.get('error')}")
    if delivery and delivery.get("ok") and d_age is not None and d_age <= 48:
        return out("DELIVERY_VERIFIED", f"last owner send delivered {d_age:.1f}h ago")
    if probe_ok is None and delivery and not delivery.get("ok"):  # no probe available: the send outcome is all we have
        st = "CONNECTED_BUT_NOT_AUTHENTICATED" if _looks_dead(delivery.get("error")) else "DELIVERY_FAILED"
        return out(st, f"probe unavailable; last owner send failed: {delivery.get('error')}")
    if probe_ok:
        return out("AUTHENTICATED", "server accepted an authenticated round-trip; no fresh owner-send evidence yet")
    return out("DISCONNECTED", "no probe and no delivery evidence")


def _bridge_base() -> str:
    return (os.getenv("WHATSAPP_BRIDGE_URL", "http://whatsapp-bridge:3001/send")).rsplit("/send", 1)[0]


async def probe_owner_channel() -> Dict[str, Any]:
    """Read-only: GET /health, then (only if it claims connected) GET /groups = a real authenticated
    server round-trip with no message and no user-visible effect."""
    import httpx
    res: Dict[str, Any] = {"health": None, "probe_ok": None, "probe_err": None}
    try:
        async with httpx.AsyncClient(timeout=20.0) as cx:
            res["health"] = (await cx.get(_bridge_base() + "/health")).json()
            if res["health"].get("connected"):
                r = await cx.get(_bridge_base() + "/groups")
                body = r.json()
                res["probe_ok"] = bool(body.get("ok"))
                res["probe_err"] = None if res["probe_ok"] else str(body.get("error"))[:120]
    except Exception as exc:
        res["probe_err"] = f"{type(exc).__name__}: {str(exc)[:100]}"
    return res


async def owner_channel_state() -> Dict[str, Any]:
    p = await probe_owner_channel()
    return classify_owner_channel(p["health"], p["probe_ok"], p["probe_err"], await _owner_delivery_state())


async def _latest_run(db, source: str) -> Optional[Dict[str, Any]]:
    r = (await db.execute(sa.text("""
        SELECT finished_at, items_scanned, relevant, drafted, failures, session_failed, error, detail
          FROM noa_scan_runs WHERE source=:s ORDER BY finished_at DESC LIMIT 1"""), {"s": source})).first()
    return dict(r._mapping) if r else None


def _age_h(ts: Optional[datetime]) -> float:
    return 1e9 if ts is None else (_now() - ts).total_seconds() / 3600.0


async def readiness_report(db) -> Dict[str, Any]:
    """The release check (12 required checks + owner-channel delivery). Read-only; NEVER changes any flag."""
    checks: List[Dict[str, Any]] = []

    def add(n: int, name: str, ok: bool, detail: str) -> None:
        checks.append({"n": n, "name": name, "ok": bool(ok), "detail": detail})

    grun = await _latest_run(db, "group")
    prun = await _latest_run(db, "page")
    profile = os.getenv("FB_PROFILE_DIR") or os.path.join(os.getenv("STATE_DIR", "/app/state"), "fb_native_test", "profile")
    prof_ok = os.path.exists(os.path.join(profile, "Default", "Cookies"))

    ok1 = bool(grun) and not grun["session_failed"] and _age_h(grun["finished_at"]) <= _SESSION_FRESH_H and prof_ok
    add(1, "Facebook persistent session healthy", ok1,
        "no group scan recorded yet" if not grun else
        f"last group scan {_age_h(grun['finished_at']):.1f}h ago, session_failed={grun['session_failed']}, profile_present={prof_ok}")

    page_iv_h = max(1.0, _int("NOA_ENGAGEMENT_INTERVAL_S", 900) * 3 / 3600.0)
    pdet = (prun or {}).get("detail") or {}
    if isinstance(pdet, str):
        pdet = json.loads(pdet)
    ok2 = bool(prun) and _age_h(prun["finished_at"]) <= max(page_iv_h, 1.0) and bool(pdet.get("facebook_ok")) and not prun["error"]
    add(2, "Page monitoring healthy", ok2,
        "no page cycle recorded yet" if not prun else
        f"last page cycle {_age_h(prun['finished_at']):.2f}h ago, facebook_ok={pdet.get('facebook_ok')}, error={prun['error']}")

    ok3 = bool(grun) and _age_h(grun["finished_at"]) <= _SESSION_FRESH_H and not grun["session_failed"] and (grun["items_scanned"] or 0) > 0
    add(3, "Group monitoring healthy", ok3,
        "no group scan recorded yet" if not grun else f"last scan items_scanned={grun['items_scanned']} relevant={grun['relevant']}")

    dr14 = (await db.execute(sa.text("SELECT count(*) FROM group_comment_drafts WHERE created_at > NOW() - INTERVAL '14 days'"))).scalar() or 0
    add(4, "Draft pipeline healthy", dr14 > 0, f"{dr14} group drafts created in the last 14 days")

    stuck = (await db.execute(sa.text("SELECT count(*) FROM group_comment_drafts WHERE status='approved' AND approved_at < NOW() - INTERVAL '30 minutes'"))).scalar() or 0
    cols = {r[0] for r in (await db.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='group_comment_drafts'"))).all()}
    need = {"approved_at", "approved_by", "posted_at", "attempts", "last_error"}
    add(5, "Approval pipeline healthy", need <= cols and stuck == 0,
        f"missing_cols={sorted(need - cols)} stuck_in_approved={stuck}")

    idx = {r[0] for r in (await db.execute(sa.text(
        "SELECT indexname FROM pg_indexes WHERE indexname IN ('uq_group_comment_drafts_active_post','social_inbox_platform_external_id_key')"))).all()}
    add(6, "Duplicate protection healthy", len(idx) == 2, f"unique indexes present: {sorted(idx)}")

    rl_ok, rl_why = await rate_limit_ok(db, actor="autonomous")
    caps = (group_hourly_cap(), auto_max_per_hour(), auto_max_per_day())
    add(7, "Rate limiting active", all(c > 0 for c in caps),
        f"group/hour={caps[0]} autonomous/hour={caps[1]} autonomous/day={caps[2]} (query ok, current={rl_why})")

    bad_ok, _ = safety_check("מחיר ₪120 הנחה 20%")
    good_ok, _ = safety_check("שלום, כדאי לבדוק מספר שלדה לפני הזמנת החלק")
    try:
        from social.facebook_browser.group_agent import _relevance_score  # noqa: F401
        rel = True
    except Exception:
        rel = False
    add(8, "Safety/relevance checks active", (not bad_ok) and good_ok and rel,
        f"safety rejects unsafe={not bad_ok}, accepts safe={good_ok}, relevance scorer importable={rel}")

    inbox_cols = {r[0] for r in (await db.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='social_inbox'"))).all()}
    runs = (await db.execute(sa.text("SELECT count(*) FROM noa_scan_runs"))).scalar() or 0
    add(9, "Audit trail active", {"approved_by", "attempts", "last_error"} <= inbox_cols and runs > 0,
        f"social_inbox audit cols={'ok' if {'approved_by','attempts','last_error'} <= inbox_cols else 'missing'}, noa_scan_runs rows={runs}")

    cdef = (await db.execute(sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='group_comment_drafts_status_check'"))).scalar() or ""
    fh_cols = {"attempts", "last_error"} <= cols
    add(10, "Failure handling active", "failed" in cdef and fh_cols,
        f"terminal 'failed' state={'failed' in cdef}, attempts/last_error columns present={fh_cols}")

    eod = None
    try:
        import sys
        mod = sys.modules.get("BACKEND_API_ROUTES")
        t = getattr(mod, "_SUPERVISED_TASKS", {}).get("noa_eod_report_loop") if mod else None
        eod = (t is not None) and (not t.done())
    except Exception:
        eod = None
    add(11, "EOD reporting active", eod is True,
        "supervised task running" if eod else ("not evaluable outside the app process" if eod is None else "task missing/finished"))

    add(12, "Autonomous mode still DISABLED", not autonomous_flag_on(),
        f"{AUTONOMOUS_FLAG}={os.getenv(AUTONOMOUS_FLAG, '0')!r}")

    ch = await owner_channel_state()
    add(13, "Owner channel usable (WhatsApp authenticated / delivery verified)", ch["usable"],
        f"{ch['state']}: {ch['detail']}")

    return {"checks": checks, "flag_on": autonomous_flag_on(),
            "ready_for_release": all(c["ok"] for c in checks),
            # 12 is the "still disabled" state itself, so it is excluded from the runtime gate.
            "gate_ok": all(c["ok"] for c in checks if c["n"] != 12)}


_GATE_CACHE: Dict[str, Any] = {"t": 0.0, "v": (False, ["not_evaluated"])}


async def autonomous_gate(db) -> Tuple[bool, List[str]]:
    """Runtime gate. True ONLY when the operator set the flag AND checks 1-11 pass. Fail-closed:
    any error => False (owner approval stays mandatory). Cached 5 minutes."""
    if not autonomous_flag_on():
        return False, ["flag_off"]
    if time.time() - _GATE_CACHE["t"] < 300:
        return _GATE_CACHE["v"]
    try:
        rep = await readiness_report(db)
        failing = [f"{c['n']}:{c['name']}" for c in rep["checks"] if c["n"] != 12 and not c["ok"]]
        v = (not failing, failing)
    except Exception as exc:
        v = (False, [f"gate_error:{str(exc)[:80]}"])
    _GATE_CACHE.update(t=time.time(), v=v)
    if not v[0]:
        await _notify("NOA — מצב אוטונומי מבוקש אך לא מאושר",
                      "הדגל פעיל אבל בדיקות המוכנות נכשלו — נשארים באישור בעלים:\n" + "\n".join(v[1][:6]),
                      alert_key="noa_autonomous_gate_closed", cooldown_s=86400, severity="warning")
    return v


def format_readiness(rep: Dict[str, Any]) -> str:
    lines = ["🧭 *מוכנות NOA למצב אוטונומי* (בדיקה בלבד — לא משנה שום הגדרה)", ""]
    for c in rep["checks"]:
        lines.append(f"{'✅' if c['ok'] else '❌'} {c['n']}. {c['name']} — {c['detail'][:110]}")
    lines.append("")
    lines.append(f"מצב אוטונומי כעת: {'⚠️ פעיל' if rep['flag_on'] else '🔒 כבוי (אישור בעלים חובה)'}")
    lines.append("מוכן לשחרור: " + ("כן — ההפעלה היא פעולת מפעיל מכוונת בלבד (NOA_ENGAGEMENT_AUTOREPLY=1)" if rep["ready_for_release"] else "עדיין לא"))
    return "\n".join(lines)


# ── metrics (EOD + observability) ─────────────────────────────────────────────
async def daily_metrics(db, since: datetime) -> Dict[str, Any]:
    """Aggregates over [since, now). `since` may be naive-UTC or tz-aware (tables are timestamptz)."""
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    p = {"ws": since}
    runs = (await db.execute(sa.text("""
        SELECT source,
               COALESCE(sum(items_scanned),0) scanned, COALESCE(sum(relevant),0) relevant,
               COALESCE(sum(rejected),0) rejected, COALESCE(sum(duplicates),0) duplicates,
               count(*) FILTER (WHERE session_failed) session_failures,
               count(*) FILTER (WHERE error IS NOT NULL OR failures > 0) scanner_failures, count(*) cycles
          FROM noa_scan_runs WHERE finished_at >= :ws AND source IN ('page','group') GROUP BY source"""), p)).all()
    by_src = {r[0]: dict(r._mapping) for r in runs}

    g = (await db.execute(sa.text("""
        SELECT count(*) FILTER (WHERE created_at >= :ws) created,
               count(*) FILTER (WHERE approved_at >= :ws AND approved_by IS NOT NULL) approved,
               count(*) FILTER (WHERE skipped_at >= :ws) rejected,
               count(*) FILTER (WHERE status='pending_approval') pending,
               count(*) FILTER (WHERE posted_at >= :ws) published,
               count(*) FILTER (WHERE status='failed' AND last_attempt_at >= :ws) failed,
               count(*) FILTER (WHERE approved_by='autonomous' AND posted_at >= :ws) autonomous_published,
               avg(EXTRACT(EPOCH FROM (approved_at - created_at))) FILTER (WHERE approved_at >= :ws AND approved_by IS NOT NULL) lat
          FROM group_comment_drafts"""), p)).first()._mapping
    i = (await db.execute(sa.text("""
        SELECT count(*) FILTER (WHERE created_at >= :ws AND platform='facebook') created,
               count(*) FILTER (WHERE replied_at >= :ws AND platform='facebook') published,
               count(*) FILTER (WHERE status='pending_approval' AND platform='facebook') pending,
               count(*) FILTER (WHERE status='skipped' AND created_at >= :ws AND platform='facebook') rejected,
               count(*) FILTER (WHERE last_error IS NOT NULL AND status <> 'replied' AND platform='facebook') failed,
               avg(EXTRACT(EPOCH FROM (replied_at - created_at))) FILTER (WHERE replied_at >= :ws AND platform='facebook') lat
          FROM social_inbox"""), p)).first()._mapping
    return {"page": by_src.get("page", {}), "group": by_src.get("group", {}), "group_drafts": dict(g), "page_inbox": dict(i)}


def _mins(sec) -> str:
    return "n/a" if sec is None else f"{float(sec) / 60:.0f}m"


def format_metrics(m: Dict[str, Any], rep: Optional[Dict[str, Any]] = None) -> str:
    pg, gr, gd, pi = m["page"], m["group"], m["group_drafts"], m["page_inbox"]
    L = [
        "OPERATIONS (Phase 1 monitoring — 24h)",
        f"- Page items scanned: {pg.get('scanned', 0)} · new/relevant: {pg.get('relevant', 0)} · cycles: {pg.get('cycles', 0)}",
        f"- Group items scanned: {gr.get('scanned', 0)} · relevant: {gr.get('relevant', 0)} · rejected by relevance: {gr.get('rejected', 0)}",
        f"- Drafts created: groups {gd['created']} · page {pi['created']}",
        f"- Group drafts approved {gd['approved']} · rejected {gd['rejected']} · pending {gd['pending']} · published {gd['published']} · failed {gd['failed']}",
        f"- Page replies published {pi['published']} · pending {pi['pending']} · skipped {pi['rejected']} · failed-attempt items {pi['failed']}",
        f"- Duplicate-prevention events: {(gr.get('duplicates') or 0) + (pg.get('duplicates') or 0)}",
        f"- Session/auth failures: {(gr.get('session_failures') or 0) + (pg.get('session_failures') or 0)} · scanner failures: {(gr.get('scanner_failures') or 0) + (pg.get('scanner_failures') or 0)}",
        f"- Avg approval latency: groups {_mins(gd['lat'])} · page {_mins(pi['lat'])}",
        f"- Autonomous publications (24h): {gd['autonomous_published']} · autonomous mode: {'ON' if autonomous_flag_on() else 'OFF'}",
    ]
    if rep is not None:
        bad = [f"{c['n']}" for c in rep["checks"] if not c["ok"]]
        L.append(f"- Autonomous release readiness: {'READY' if rep['ready_for_release'] else 'not ready (failing: ' + ','.join(bad) + ')'}")
    return "\n".join(L)
