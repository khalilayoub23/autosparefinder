"""
Script: email_agent/agent.py
Purpose: One Email Agent cycle: Gmail -> read -> normalize -> resolve context -> classify ->
         policy -> draft -> verify -> audit. Every step has a post-condition; a step that cannot
         be verified is recorded as such and routed to a human, never reported as success.
Process:
  check_connection()  token + profile; the authenticated mailbox MUST equal the configured
                      business mailbox (wrong-account guard) and the grant must allow reading.
  process_once()      list recent inbox messages, claim each unseen inbound one in the store
                      (idempotency), process it, write one structured audit log line.
  Draft modes: 'gmail' (EMAIL_AGENT_GMAIL_DRAFTS=1 and the grant allows drafts) creates a Gmail
  draft in the original thread and verifies it by reading it back; otherwise 'local' stores the
  draft text in the audit row only. Nothing is ever sent, and no message is marked, labelled,
  archived or deleted.
Data Imported/Modified: email_agent_messages (PII DB); Gmail drafts when enabled.
Last Updated: 2026-10-04
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from email_agent import classifier as _cls
from email_agent import drafts as _drafts
from email_agent import policy as _policy
from email_agent.config import CONFIGURED, EmailAgentConfig, can_draft, can_read
from email_agent.context import ContextSource, resolve_context, verify_context
from email_agent.gmail_client import GmailClient, GmailError
from email_agent.normalize import MalformedMessage, NormalizedEmail, normalize_thread
from email_agent.redaction import redact, safe_error

logger = logging.getLogger("email_agent")


def audit_log(event: str, **fields: Any) -> None:
    """One structured line per event. Redacted; carries ids and decisions, never body text."""
    logger.info("[email_agent] %s", redact(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str)))


def verify_read(email: NormalizedEmail, requested_id: str, requested_thread: str) -> Dict[str, Any]:
    checks = {
        "message_id_matches": bool(email.message_id) and email.message_id == requested_id,
        "thread_id_matches": bool(email.thread_id) and email.thread_id == requested_thread,
        "sender_present": "@" in email.sender_email,
        "content_normalized": bool(email.body_text.strip()) or bool(email.attachments) or bool(email.subject),
    }
    return {"ok": all(checks.values()), "checks": checks}


async def check_connection(cfg: EmailAgentConfig, client: GmailClient) -> Dict[str, Any]:
    if cfg.state != CONFIGURED:
        return {"ok": False, "state": cfg.state, "missing": cfg.missing()}
    try:
        prof = await asyncio.to_thread(client.profile)
    except GmailError as e:
        return {"ok": False, "state": cfg.state, "error_kind": e.kind, "error": str(e)}
    mailbox = str(prof.get("emailAddress", "")).lower()
    if mailbox != cfg.mailbox:
        return {"ok": False, "state": cfg.state, "error_kind": "wrong_account",
                "error": "authenticated mailbox is not the configured business mailbox"}
    scopes = client.granted_scopes
    return {"ok": can_read(scopes), "state": cfg.state, "mailbox": mailbox,
            "can_read": can_read(scopes), "can_draft": can_draft(scopes),
            **({} if can_read(scopes) else {"error_kind": "scope", "error": "grant does not include a Gmail read scope"})}


async def process_message(cfg: EmailAgentConfig, client: GmailClient, store, source: ContextSource,
                          message_id: str, thread_id: str, draft_mode: str) -> Dict[str, Any]:
    """Process one claimed message. Raises on failure (the caller records retry / failed)."""
    raw_thread = await asyncio.to_thread(client.get_thread, thread_id)
    thread = normalize_thread(raw_thread, cfg.mailbox)
    email = next((m for m in thread if m.message_id == message_id), None)
    if email is None:
        raise MalformedMessage("message is not present / not parseable in its thread")
    if not email.is_inbound:
        await store.save_result(message_id, {
            "sender_email": email.sender_email, "subject": email.subject, "received_at": email.date,
            "reason": "message was sent or drafted by this mailbox: not an inbound email",
            "recommended_action": "ignore", "policy_tier": _policy.TIER_NONE, "requires_human": False})
        audit_log("skipped_outbound", message_id=message_id, thread_id=thread_id, sent=False)
        return {"classification": None, "policy": {"requires_human": False}, "draft": {"body": None},
                "verification": {"send_calls": 0, "sendable": False}}
    read_v = verify_read(email, message_id, thread_id)
    if not read_v["ok"]:
        raise MalformedMessage(f"read post-condition failed: {read_v['checks']}")

    ctx = await resolve_context(email, thread, source)
    ctx_v = await verify_context(ctx, source)
    if not ctx_v["ok"]:
        raise RuntimeError(f"context post-condition failed: {ctx_v['checks']}")

    c = _cls.classify(email, thread, ctx)
    later_outbound = any((not m.is_inbound) and "DRAFT" not in m.labels and m.date and email.date
                         and m.date > email.date for m in thread)
    decision = _policy.decide(c, ctx, already_answered=later_outbound)
    c.recommended_action, c.requires_human = decision.recommended_action, decision.requires_human

    draft: Optional[_drafts.Draft] = None
    draft_id: Optional[str] = None
    draft_v: Dict[str, Any] = {"ok": True, "mode": "none", "checks": {}, "send_calls": 0}
    if decision.wants_draft:
        try:
            draft = _drafts.build_draft(decision.recommended_action, email, ctx)
        except _drafts.DraftNotPossible as e:
            decision = _policy.PolicyDecision(_policy.TIER_HUMAN, "escalate_to_human", True,
                                              decision.reasons + [f"draft not possible: {e}"])
            c.recommended_action, c.requires_human = decision.recommended_action, True
    if draft is not None:
        prior = await store.get(message_id) or {}
        if draft_mode == "gmail" and prior.get("draft_attempted_at") and not prior.get("gmail_draft_id"):
            # An earlier attempt may already have created a draft in Gmail: never create a second.
            draft_v = {"ok": False, "mode": "gmail", "checks": {"earlier_attempt_unverified": False}, "send_calls": 0}
            decision = _policy.PolicyDecision(_policy.TIER_HUMAN, "escalate_to_human", True,
                                              decision.reasons + ["an earlier draft attempt could not be verified"])
            c.recommended_action, c.requires_human = decision.recommended_action, True
        elif draft_mode == "gmail":
            await store.mark_draft_attempt(message_id)
            raw = _drafts.build_rfc822(draft, email, cfg.mailbox)
            created = await asyncio.to_thread(client.create_draft, raw, email.thread_id)
            draft_id = str(created["id"])
            read_back = await asyncio.to_thread(client.get_draft, draft_id)
            draft_v = _drafts.verify_gmail_draft(read_back, draft, email, draft_id)
            if not draft_v["ok"]:
                decision = _policy.PolicyDecision(_policy.TIER_HUMAN, "escalate_to_human", True,
                                                  decision.reasons + ["Gmail draft failed verification"])
                c.recommended_action, c.requires_human = decision.recommended_action, True
        else:
            draft_v = _drafts.verify_local_draft(draft, email)

    problems = _cls.validate_classification(c, ctx)
    if problems:
        raise RuntimeError("classification post-condition failed: " + "; ".join(problems))

    verification = {"read": read_v, "context": ctx_v, "classification": {"ok": True},
                    "draft": draft_v, "send_calls": 0, "sendable": False}
    record = {
        "rfc822_message_id": email.rfc822_message_id, "sender_email": email.sender_email,
        "subject": email.subject, "received_at": email.date, "classification": c.classification,
        "confidence": c.confidence, "reason": c.reason, "risk_flags": c.risk_flags,
        "policy_tier": decision.tier, "recommended_action": decision.recommended_action,
        "requires_human": decision.requires_human,
        "context": {**ctx.to_dict(), "policy_reasons": decision.reasons, "references": c.references},
        "attachments": email.attachments,
        "draft_subject": draft.subject if draft else None, "draft_body": draft.body if draft else None,
        "draft_reason": draft.reason if draft else None, "gmail_draft_id": draft_id,
        "verification": verification,
    }
    await store.save_result(message_id, record)
    audit_log("processed", message_id=message_id, thread_id=thread_id, sender_domain=email.sender_domain,
              classification=c.classification, confidence=c.confidence,
              context={k: getattr(ctx, k).get("status") for k in ("customer", "order", "shipment", "supplier")},
              recommended_action=decision.recommended_action, requires_human=decision.requires_human,
              draft_mode=draft_v["mode"], draft_id=draft_id, draft_verified=draft_v["ok"],
              attachments=len(email.attachments), sent=False)
    return {"classification": c.to_dict(), "policy": decision.to_dict(), "context": ctx.to_dict(),
            "draft": {"mode": draft_v["mode"], "gmail_draft_id": draft_id, "body": draft.body if draft else None},
            "verification": verification}


async def process_once(cfg: EmailAgentConfig, client: GmailClient, store, source: ContextSource,
                       limit: Optional[int] = None) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"state": cfg.state, "listed": 0, "new": 0, "retried": 0, "skipped": 0,
                               "processed": 0, "failed": 0, "requires_human": 0, "drafts": 0,
                               "draft_mode": "none", "sent": 0, "errors": []}
    conn = await check_connection(cfg, client)
    summary["connection"] = conn
    if not conn.get("ok"):
        audit_log("connection_not_ready", **{k: v for k, v in conn.items() if k != "mailbox"})
        return summary
    draft_mode = "gmail" if (cfg.gmail_drafts and conn.get("can_draft")) else "local"
    summary["draft_mode"] = draft_mode

    refs = await asyncio.to_thread(client.list_message_ids, cfg.query, limit or cfg.max_per_cycle)
    summary["listed"] = len(refs)
    for ref in refs:
        mid, tid = ref["id"], ref["threadId"]
        claim = await store.claim(mid, tid, cfg.max_attempts)
        if claim == "skip":
            summary["skipped"] += 1
            continue
        summary["new" if claim == "new" else "retried"] += 1
        try:
            result = await process_message(cfg, client, store, source, mid, tid, draft_mode)
            summary["processed"] += 1
            summary["requires_human"] += int(result["policy"]["requires_human"])
            summary["drafts"] += int(result["draft"]["body"] is not None)
        except GmailError as e:
            if e.kind in ("auth", "scope", "prohibited"):
                # mailbox-level failure, not this message's fault: hand the claim back and stop
                await store.release(mid)
                summary["aborted"] = e.kind
                summary["errors"].append({"message_id": mid, "status": "retry", "error": safe_error(e, 160)})
                audit_log("cycle_aborted", message_id=mid, error_kind=e.kind, failure_reason=safe_error(e, 200))
                break
            status = await store.mark_failed(mid, safe_error(e), e.retryable, cfg.max_attempts)
            summary["failed"] += 1
            summary["errors"].append({"message_id": mid, "status": status, "error": safe_error(e, 160)})
            audit_log("failed", message_id=mid, thread_id=tid, status=status, failure_reason=safe_error(e, 200))
        except Exception as e:  # recorded per message; one bad email never stops the cycle
            status = await store.mark_failed(mid, safe_error(e), False, cfg.max_attempts)
            summary["failed"] += 1
            summary["errors"].append({"message_id": mid, "status": status, "error": safe_error(e, 160)})
            audit_log("failed", message_id=mid, thread_id=tid, status=status, failure_reason=safe_error(e, 200))
    audit_log("cycle", **{k: v for k, v in summary.items() if k not in ("errors", "connection")})
    return summary
