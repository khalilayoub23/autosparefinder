"""
Script: maintenance/email_agent_cli.py
Purpose: Operator CLI for the Email Agent - inspect its state and run ONE safe cycle on demand
         (used for the live read-only E2E once Gmail OAuth is configured). It cannot send email.
Process:
  status              configuration state + (if configured) Gmail connection check: token,
                      mailbox == business mailbox, granted read / draft capability.
  run-once [--limit N] [--dry-run] [--gmail-drafts]
                      one agent cycle. --dry-run keeps everything in memory (no DB row, no Gmail
                      draft). Without --dry-run rows go to email_agent_messages; Gmail drafts are
                      created only with --gmail-drafts or EMAIL_AGENT_GMAIL_DRAFTS=1.
                      Works even when EMAIL_AGENT_ENABLED=0 (the flag gates the background loop),
                      but NEVER while the runtime kill switch is halted (exit 3, no Gmail call).
  control             show the runtime kill switch (email_agent/control.py). Read-only.
  halt                stop processing and every Gmail write now - no restart needed.
  arm                 allow processing again (the loop still also needs EMAIL_AGENT_ENABLED=1).
Data Imported/Modified: email_agent_messages (PII DB) unless --dry-run; Gmail drafts only when asked.
Data Sources: Gmail API v1; PII + catalog DB (SELECT).
Missing Data Delegation: n/a.
Last Updated: 2026-10-10

Run: docker exec autospare_backend python3 /app/maintenance/email_agent_cli.py status
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from email_agent.agent import check_connection, process_once  # noqa: E402
from email_agent import control  # noqa: E402
from email_agent.config import load_config  # noqa: E402
from email_agent.context import DbContextSource  # noqa: E402
from email_agent.gmail_client import GmailClient  # noqa: E402
from email_agent.redaction import redact  # noqa: E402
from email_agent.store import InMemoryStore, PgStore  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["status", "run-once", "control", "halt", "arm"])
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--gmail-drafts", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.command in ("control", "halt", "arm"):
        state = (control.read_control() if args.command == "control"
                 else control.set_mode(control.HALT if args.command == "halt" else control.RUN, by="cli"))
        # After a halt: [] = no Gmail write is in flight and none will start. A listed write was
        # already authorized before the halt; it is not cancelled and finishes within ~25 s.
        state.setdefault("writes_in_flight", control.writes_in_flight())
        print(json.dumps({"runtime_control": state}, indent=2))
        return 0
    if args.command == "run-once" and not control.read_control()["running"]:
        print(json.dumps({"halted": control.read_control()["reason"], "send_enabled": False}, indent=2))
        return 3

    cfg = dataclasses.replace(load_config(), enabled=True)   # CLI runs are explicit operator actions
    if args.command == "run-once":
        cfg = dataclasses.replace(cfg, gmail_drafts=(cfg.gmail_drafts or args.gmail_drafts) and not args.dry_run)
    if cfg.missing():
        print(json.dumps({"state": "unconfigured", "missing": cfg.missing(), "send_enabled": False}, indent=2))
        return 2
    client = GmailClient(cfg)
    if args.command == "status":
        print(redact(json.dumps({"config": cfg.public(), "runtime_control": control.read_control(),
                                 "connection": await check_connection(cfg, client)},
                                indent=2, ensure_ascii=False)))
        return 0
    store = InMemoryStore() if args.dry_run else PgStore()
    summary = await process_once(cfg, client, store, DbContextSource(), limit=args.limit)
    print(redact(json.dumps(summary, indent=2, ensure_ascii=False, default=str)))
    return 0 if summary.get("connection", {}).get("ok") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
