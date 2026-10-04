"""
Script: email_agent/ (package)
Purpose: AutoSpareFinder Email Agent foundation - reads the business Gmail mailbox, normalizes
         and classifies inbound mail, resolves it against real platform data (customer / order /
         shipment / supplier), applies a response policy and prepares DRAFT replies.
         PHASE 1 NEVER SENDS EMAIL: there is no send function, the Gmail transport is an
         endpoint allowlist that contains no send endpoint, the policy marks every decision
         non-sendable and the audit table carries CHECK (sendable = false).
Process:
  config.py      disabled / unconfigured / configured state from the environment
  redaction.py   secret scrubbing for every log line and stored error
  gmail_client.py  OAuth refresh-token flow + allowlisted Gmail REST calls (read, drafts)
  normalize.py   Gmail payload -> NormalizedEmail (multipart, html->text, attachment metadata)
  senders.py     sender kind (platform / free-mail / business) + sender authentication
  context.py     DB-backed context resolution; an identifier is resolved only by exact DB match
  classifier.py  deterministic classifier with an explicit, validated schema
  policy.py      safe-for-future-automation vs human-approval-required; sending disabled
  drafts.py      truth-only draft text, RFC822 build, draft post-condition verification
  store.py       idempotency + audit (email_agent_messages, PII DB)
  agent.py       orchestration of one cycle with post-conditions per step
  loop.py        supervised background loop (registered in BACKEND_API_ROUTES.startup)
Data Imported/Modified: email_agent_messages (PII DB). With EMAIL_AGENT_GMAIL_DRAFTS=1 it also
         creates Gmail drafts. It never marks, labels, archives, deletes or sends mail.
Data Sources: Gmail API v1 (gmail.googleapis.com), PII DB (users, orders), catalog DB (suppliers).
Missing Data Delegation: unresolved context is recorded as unresolved and routed to a human.
Last Updated: 2026-10-04
"""

# Phase-1 hard boundary. Not read from the environment on purpose: enabling sending is a code
# change with its own review (see docs/EMAIL_AGENT.md, "Enabling sending later").
SEND_ENABLED = False
