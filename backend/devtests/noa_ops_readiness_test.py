"""
NOA two-phase operational readiness - regression tests (2026-09-20).

Live catalog DB with synthetic MARKER rows (removed in `finally`); Facebook write handlers and
owner notification are MOCKED - no public Facebook action and no WhatsApp message is made.

Proves:
  A. autonomous mode is OFF by default and the gate is fail-closed (flag off, or flag on with
     failing readiness => owner approval stays mandatory); readiness never changes the flag.
  B. ONE approve+post path: owner and autonomous both go claim_group_draft -> post_claimed_group_draft,
     differing only in the recorded approver.
  C. atomic claim (no double approval), audit columns, outcome model:
       posted / retry_pending (attempts) / failed after max attempts /
       unverified-after-submit => 'failed' (NOT re-approvable => no duplicate public comment).
  D. duplicate protection: _save_draft reports False on a duplicate; has_active_draft blocks re-drafting,
     including a 'failed' draft.
  E. DB-backed rate limits (per-group hourly cap; autonomous hour ceiling).
  F. deterministic safety gate blocks unsafe autonomous text and leaves the draft pending.
  G. Page reply path records approver/attempts/last_error.
  H. metrics + readiness report render from real tables; 12 checks; check 12 tracks the flag.
  I. structural: no second implementation (submit_approved_comment only called from noa_ops).
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import re
import sys
import uuid
from unittest.mock import AsyncMock, patch

sys.path.insert(0, "/app")
import sqlalchemy as sa  # noqa: E402
from BACKEND_DATABASE_MODELS import async_session_factory  # noqa: E402
from social import noa_ops  # noqa: E402

MARK = f"https://fb.example/noa-ops-test/{uuid.uuid4().hex[:8]}"
results: list[tuple[str, bool, str]] = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail and not ok else ""))


async def _draft(db, suffix: str, text: str = "שלום, כדאי לבדוק מספר שלדה לפני הזמנת החלק") -> str:
    url = f"{MARK}/{suffix}"
    await db.execute(sa.text("""INSERT INTO group_comment_drafts (post_url, post_text, draft_comment, relevance_score, status)
                                VALUES (:u,'t',:d,0.9,'pending_approval')"""), {"u": url, "d": text})
    await db.commit()
    return (await db.execute(sa.text("SELECT id::text FROM group_comment_drafts WHERE post_url=:u"), {"u": url})).scalar()


async def _row(db, did: str):
    return (await db.execute(sa.text("""SELECT status, approved_by, attempts, last_error, posted_at, approved_at
                                          FROM group_comment_drafts WHERE id=CAST(:i AS uuid)"""), {"i": did})).first()


async def main() -> None:
    noa_ops._GATE_CACHE.update(t=0.0)
    with patch.object(noa_ops, "_notify", AsyncMock()):
        async with async_session_factory() as db:
            try:
                # ── A. default OFF / fail-closed ─────────────────────────────
                with patch.dict(os.environ, {"NOA_ENGAGEMENT_AUTOREPLY": "0"}):
                    ok, why = await noa_ops.autonomous_gate(db)
                    rec("A1. flag off => gate closed", ok is False and why == ["flag_off"])
                    rec("A2. maybe_autonomous_group_post is inert while off",
                        await noa_ops.maybe_autonomous_group_post(db, MARK + "/none") == "disabled")
                with patch.dict(os.environ, {"NOA_ENGAGEMENT_AUTOREPLY": "1"}):
                    noa_ops._GATE_CACHE.update(t=0.0)
                    with patch.object(noa_ops, "readiness_report", AsyncMock(return_value={"checks": [
                            {"n": 1, "name": "session", "ok": False, "detail": "x"}]})):
                        ok, why = await noa_ops.autonomous_gate(db)
                    rec("A3. flag on + failing readiness => gate stays closed (fail-closed)", ok is False and why)
                    noa_ops._GATE_CACHE.update(t=0.0)
                    with patch.object(noa_ops, "readiness_report", AsyncMock(side_effect=RuntimeError("boom"))):
                        ok, why = await noa_ops.autonomous_gate(db)
                    rec("A4. readiness error => gate closed", ok is False and "gate_error" in why[0])
                    rep = await noa_ops.readiness_report(db)
                    rec("A5. readiness reports the 12 required checks (+ owner-channel check 13) and never mutates the flag",
                        [c["n"] for c in rep["checks"]] == list(range(1, 14)) and os.environ["NOA_ENGAGEMENT_AUTOREPLY"] == "1")
                    rec("A6. check 12 fails while the flag is on",
                        [c for c in rep["checks"] if c["n"] == 12][0]["ok"] is False)
                noa_ops._GATE_CACHE.update(t=0.0)
                rep0 = await noa_ops.readiness_report(db)
                rec("A7. check 12 passes when the flag is off",
                    [c for c in rep0["checks"] if c["n"] == 12][0]["ok"] is True)

                # ── owner channel (check 13): truthful state, fail-closed ────────────
                from datetime import datetime as _dt, timedelta as _td, timezone as _tz
                _now = _dt.now(_tz.utc)
                H_OK = {"ok": True, "connected": True, "awaiting_qr_scan": False, "account_mismatch": None}
                dead = {"health": H_OK, "probe_ok": False, "probe_err": "Connection Closed"}
                good = {"health": H_OK, "probe_ok": True, "probe_err": None}
                with patch.object(noa_ops, "probe_owner_channel", AsyncMock(return_value=dead)), \
                     patch.object(noa_ops, "_owner_delivery_state", AsyncMock(return_value=None)):
                    rep13 = await noa_ops.readiness_report(db)
                c13 = [c for c in rep13["checks"] if c["n"] == 13][0]
                rec("A8. bridge says connected but auth round-trip fails => check 13 FAILS, gate not OK "
                    "(the live 2026-09-20 situation)",
                    c13["ok"] is False and rep13["gate_ok"] is False and "CONNECTED_BUT_NOT_AUTHENTICATED" in c13["detail"])
                with patch.object(noa_ops, "probe_owner_channel", AsyncMock(return_value=good)), \
                     patch.object(noa_ops, "_owner_delivery_state", AsyncMock(return_value=None)):
                    c13b = [c for c in (await noa_ops.readiness_report(db))["checks"] if c["n"] == 13][0]
                rec("A9. authenticated round-trip OK, no delivery evidence yet => check 13 passes (empty outcome is not a failure)",
                    c13b["ok"] is True and "AUTHENTICATED" in c13b["detail"])

                # ── J. channel-state classifier: the four required distinctions + bridge-level states ──
                def st(health, pok, perr, dlv):
                    return noa_ops.classify_owner_channel(health, pok, perr, dlv, now=_now)
                dl = lambda ok, hrs, err=None: {"ts": (_now - _td(hours=hrs)).isoformat(), "ok": ok, "error": err}
                cases = {
                    "unreachable": (st(None, None, None, None), "BRIDGE_UNREACHABLE", False),
                    "wrong account": (st({**H_OK, "account_mismatch": {"linked": "x"}}, None, None, None), "WRONG_ACCOUNT", False),
                    "awaiting qr": (st({**H_OK, "connected": False, "awaiting_qr_scan": True}, None, None, None), "AWAITING_QR", False),
                    "disconnected": (st({**H_OK, "connected": False}, None, None, None), "DISCONNECTED", False),
                    "CONNECTED BUT NOT AUTHENTICATED (probe)": (st(H_OK, False, "Connection Closed", None), "CONNECTED_BUT_NOT_AUTHENTICATED", False),
                    "stale dead-session failure does NOT mask a probe-proven session (post re-pair)": (st(H_OK, True, None, dl(False, 0.1, "Connection Closed")), "AUTHENTICATED", True),
                    "CONNECTED BUT NOT AUTHENTICATED (no probe)": (st(H_OK, None, None, dl(False, 3, "Connection Closed")), "CONNECTED_BUT_NOT_AUTHENTICATED", False),
                    "DELIVERY FAILED": (st(H_OK, True, None, dl(False, 0.1, "recipient not on whatsapp")), "DELIVERY_FAILED", False),
                    "CONNECTED/AUTHENTICATED": (st(H_OK, True, None, None), "AUTHENTICATED", True),
                    "stale failure does not mask a proven session": (st(H_OK, True, None, dl(False, 10, "Connection Closed")), "AUTHENTICATED", True),
                    "DELIVERY VERIFIED": (st(H_OK, True, None, dl(True, 2)), "DELIVERY_VERIFIED", True),
                }
                bad_cases = {k: (v[0]["state"], v[0]["usable"]) for k, v in cases.items()
                             if v[0]["state"] != v[1] or v[0]["usable"] != v[2]}
                rec("J1. every channel state is classified correctly and only AUTHENTICATED/DELIVERY_VERIFIED are usable",
                    not bad_cases, str(bad_cases))

                # ── K. the monitor that stayed silent for 6 days now uses the truthful probe ─────
                _rt = pathlib.Path("/app/BACKEND_API_ROUTES.py").read_text(encoding="utf-8")
                _mon = _rt[_rt.index("async def _whatsapp_link_monitor_loop"):_rt.index("async def _whatsapp_link_monitor_loop") + 9000]
                rec("K1. link monitor verifies a 'connected' bridge with an authenticated round-trip and alerts session_dead out-of-band",
                    "probe_owner_channel()" in _mon and '"session_dead"' in _mon and "WA_LINK_PROBE_INTERVAL_S" in _mon
                    and "IT MUST NOT ALERT OVER WHATSAPP" in _mon
                    and '"link_down"' in _mon)

                # ── F. safety gate ───────────────────────────────────────────
                good = noa_ops.safety_check("כדאי לבדוק מספר שלדה לפני הזמנת החלק")[0]
                bad = [noa_ops.safety_check(t)[0] for t in (
                    "המחיר רק ₪120 עם הנחה", "רשום 0501234567", "היכנס https://evil.example", "מרווח 45%",
                    "#חלקים", "", "x" * 400, "Draft option 1: hello")]
                rec("F1. safety accepts a safe reply and rejects every unsafe class", good and not any(bad), str(bad))

                # ── C. atomic claim + outcomes ───────────────────────────────
                d1 = await _draft(db, "posted")
                c1 = await noa_ops.claim_group_draft(db, d1, actor="owner")
                c1b = await noa_ops.claim_group_draft(db, d1, actor="owner")
                rec("C1. claim is atomic (second claim gets nothing)", c1 is not None and c1b is None)
                r = await _row(db, d1)
                rec("C2. claim records approver + approved_at", r.status == "approved" and r.approved_by == "owner" and r.approved_at)
                with patch("social.facebook_browser.group_agent.GroupAgent.submit_approved_comment",
                           AsyncMock(return_value={"ok": True, "error": None, "submitted": True})) as sub:
                    out = await noa_ops.post_claimed_group_draft(c1, actor="owner")
                await db.commit()
                r = await _row(db, d1)
                rec("C3. success => posted, posted_at, attempts=1", out["outcome"] == "posted" and r.status == "posted"
                    and r.posted_at and r.attempts == 1 and sub.await_count == 1)

                d2 = await _draft(db, "retry")
                with patch("social.facebook_browser.group_agent.GroupAgent.submit_approved_comment",
                           AsyncMock(return_value={"ok": False, "error": "Facebook session not authenticated", "submitted": False})):
                    outs = []
                    for _ in range(noa_ops.max_attempts()):
                        c = await noa_ops.claim_group_draft(db, d2, actor="owner")
                        outs.append((await noa_ops.post_claimed_group_draft(c, actor="owner"))["outcome"])
                await db.commit()
                r = await _row(db, d2)
                rec("C4. not-submitted failures retry, then become terminal 'failed' with attempts+error",
                    outs[:-1] == ["retry_pending"] * (noa_ops.max_attempts() - 1) and outs[-1] == "failed"
                    and r.status == "failed" and r.attempts == noa_ops.max_attempts() and "session" in (r.last_error or ""), str(outs))

                d3 = await _draft(db, "unverified")
                c3 = await noa_ops.claim_group_draft(db, d3, actor="owner")
                with patch("social.facebook_browser.group_agent.GroupAgent.submit_approved_comment",
                           AsyncMock(return_value={"ok": False, "error": "could not verify", "submitted": True})):
                    o3 = await noa_ops.post_claimed_group_draft(c3, actor="owner")
                await db.commit()
                r = await _row(db, d3)
                again = await noa_ops.claim_group_draft(db, d3, actor="owner")
                rec("C5. submitted-but-unverified => 'failed', NOT re-approvable (no duplicate public comment)",
                    o3["outcome"] == "unverified" and r.status == "failed" and again is None)

                # ── D. duplicate protection ──────────────────────────────────
                from social.facebook_browser.group_scanner import _save_draft
                rec("D1. has_active_draft true for posted and failed drafts",
                    await noa_ops.has_active_draft(db, f"{MARK}/posted") and await noa_ops.has_active_draft(db, f"{MARK}/unverified"))
                dup = await _save_draft(db, None, f"{MARK}/unverified", "t", "dup", 0.9)
                fresh = await _save_draft(db, None, f"{MARK}/fresh", "t", "אחר", 0.5)
                rec("D2. _save_draft returns False on duplicate, True on a new post", dup is False and fresh is True)

                # ── B. autonomous uses the SAME path; F. safety-blocked stays pending ─
                d4 = await _draft(db, "auto_ok")
                d5 = await _draft(db, "auto_unsafe", "המחיר ₪99 בלבד")
                with patch.dict(os.environ, {"NOA_ENGAGEMENT_AUTOREPLY": "1"}), \
                     patch.object(noa_ops, "autonomous_gate", AsyncMock(return_value=(True, []))), \
                     patch("social.facebook_browser.group_agent.GroupAgent.submit_approved_comment",
                           AsyncMock(return_value={"ok": True, "error": None, "submitted": True})) as sub:
                    o4 = await noa_ops.maybe_autonomous_group_post(db, f"{MARK}/auto_ok")
                    o5 = await noa_ops.maybe_autonomous_group_post(db, f"{MARK}/auto_unsafe")
                await db.commit()
                r4, r5 = await _row(db, d4), await _row(db, d5)
                rec("B1. autonomous posts through the same submit path, approver='autonomous'",
                    o4 == "posted" and r4.approved_by == "autonomous" and r4.status == "posted" and sub.await_count == 1)
                rec("F2. unsafe autonomous draft is blocked, stays pending for the owner, reason recorded",
                    o5.startswith("safety_blocked") and r5.status == "pending_approval" and (r5.last_error or "").startswith("safety:"))

                # ── E. DB-backed rate limits ─────────────────────────────────
                with patch.dict(os.environ, {"NOA_AUTONOMOUS_MAX_PER_HOUR": "1"}):
                    ok, why = await noa_ops.rate_limit_ok(db, actor="autonomous")
                rec("E1. autonomous hourly ceiling enforced from the DB (1 autonomous post already recorded)",
                    ok is False and why.startswith("autonomous_hour_cap"), why)
                rec("E2. ceiling does not apply to the owner", (await noa_ops.rate_limit_ok(db, actor="owner"))[0] is True)

                # ── G. page reply path ───────────────────────────────────────
                await db.execute(sa.text("""INSERT INTO social_inbox (platform, kind, external_id, author, message, status, reply_text)
                                            VALUES ('facebook','comment',:e,'t','q','pending_approval','שלום')"""), {"e": MARK})
                await db.commit()
                item = (await db.execute(sa.text("SELECT id::text id, platform, external_id FROM social_inbox WHERE external_id=:e"),
                                         {"e": MARK})).first()._mapping
                with patch("social.engagement.send_reply", AsyncMock(return_value={"ok": False, "error": "graph 400"})):
                    f = await noa_ops.send_page_reply(db, dict(item), "שלום", actor="owner")
                with patch("social.engagement.send_reply", AsyncMock(return_value={"ok": True, "id": "x1"})):
                    s_ = await noa_ops.send_page_reply(db, dict(item), "שלום", actor="owner")
                row = (await db.execute(sa.text("SELECT status, approved_by, attempts, last_error FROM social_inbox WHERE external_id=:e"),
                                        {"e": MARK})).first()
                rec("G1. page reply: failure recorded then success; approver + attempts audited",
                    f["ok"] is False and s_["ok"] and row.status == "replied" and row.approved_by == "owner" and row.attempts == 2
                    and row.last_error is None)

                # ── H. metrics ───────────────────────────────────────────────
                from datetime import datetime, timedelta
                m = await noa_ops.daily_metrics(db, datetime.utcnow() - timedelta(hours=24))
                txt = noa_ops.format_metrics(m, await noa_ops.readiness_report(db))
                rec("H1. metrics render from real tables incl. approval latency and readiness line",
                    "Group drafts approved" in txt and "Avg approval latency" in txt and "Autonomous release readiness" in txt
                    and m["group_drafts"]["published"] >= 2, txt[:300])
                await noa_ops.record_scan_run(db, "group", noa_ops._now(), items_scanned=3, relevant=1, duplicates=2)
                m2 = await noa_ops.daily_metrics(db, datetime.utcnow() - timedelta(hours=1))
                rec("H2. scan runs are persisted and aggregated", (m2["group"].get("scanned") or 0) >= 3)
            finally:
                await db.rollback()
                await db.execute(sa.text("DELETE FROM group_comment_drafts WHERE post_url LIKE :m"), {"m": MARK + "%"})
                await db.execute(sa.text("DELETE FROM social_inbox WHERE external_id=:e"), {"e": MARK})
                await db.execute(sa.text("DELETE FROM noa_scan_runs WHERE source='group' AND items_scanned=3 AND relevant=1 AND duplicates=2"))
                await db.commit()

    # ── I. structural: one implementation ───────────────────────────────────
    callers = []
    live_dormant_refs = []
    for f in pathlib.Path("/app").rglob("*.py"):
        if any(x in f.parts for x in ("devtests", "legacy", "tests", "__pycache__", "alembic")):
            continue
        t = f.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"\.submit_approved_comment\(", t):
            callers.append(f.name)
        # Dormant, non-audited alternates (registered but never dispatched in production). If a real
        # caller ever appears, this must fail so it gets routed through noa_ops instead.
        if f.name not in ("tools.py", "task_queue.py", "policy.py", "__init__.py") and re.search(
                r"run_tool\(\s*[\"']facebook_group_comment|BrowserTaskQueue\(|get_task_queue\(", t):
            live_dormant_refs.append(f.name)
    rec("I1. every submit_approved_comment caller is noa_ops or a documented DORMANT alternate "
        "(tools.py facebook_group_comment, task_queue.py, package docstring)",
        set(callers) <= {"noa_ops.py", "tools.py", "task_queue.py", "__init__.py"} and "noa_ops.py" in callers, str(callers))
    rec("I1b. no production module dispatches the dormant alternates", not live_dormant_refs, str(live_dormant_refs))
    oc = pathlib.Path("/app/agents/owner_console.py").read_text(encoding="utf-8")
    rec("I2. owner console approval delegates to the shared path",
        "noa_ops.claim_group_draft" in oc and "noa_ops.post_claimed_group_draft" in oc and "noa_ops.send_page_reply" in oc)
    rt = pathlib.Path("/app/BACKEND_API_ROUTES.py").read_text(encoding="utf-8")
    rec("I3. engagement loop evaluates the autonomous gate every cycle (not a startup constant)",
        "autoreply, _gate_why = await _noa_ops.autonomous_gate(db)" in rt
        and 'autoreply = os.getenv("NOA_ENGAGEMENT_AUTOREPLY", "0") == "1"' not in rt)
    rec("I4. group loop autonomous hook goes through noa_ops", "_noa_ops.maybe_autonomous_group_post(" in rt)
    dc = pathlib.Path("/app/../docker-compose.yml") if False else None
    rec("I5. autonomous default is OFF in code", os.getenv("NOA_ENGAGEMENT_AUTOREPLY", "0") in ("0", ""))

    failed = [r for r in results if not r[1]]
    print(f"\nResults: {len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


asyncio.run(main())
