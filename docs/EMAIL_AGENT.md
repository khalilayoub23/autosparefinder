# Email Agent (Gmail) — foundation

The Email Agent reads the business Gmail mailbox, classifies each inbound email, resolves it
against real AutoSpareFinder data, applies a response policy and prepares a draft reply.

**This phase never sends email.** It also never marks, labels, archives or deletes mail.

Status on 2026-10-04: code, tests and wiring are in the repo. The agent is **off** by default
and **unconfigured** — no Gmail OAuth grant exists yet (see "Google setup"). No live mailbox
has been read.

## Where it lives

| Piece | File |
|---|---|
| Package | `backend/email_agent/` |
| Background loop | `email_agent/loop.py`, registered in `BACKEND_API_ROUTES.startup()` as `email_agent_loop` |
| Status endpoint | `routes/email_agent_routes.py` → `GET /api/v1/system/email-agent` (`X-Collect-Secret`) |
| Operator CLI | `maintenance/email_agent_cli.py` (`status`, `run-once`) |
| OAuth setup helper (host) | `maintenance/gmail_oauth_setup.py` (`auth-url`, `exchange`) |
| Audit / idempotency table | `email_agent_messages`, PII DB, migration `alembic_pii/versions/0039_email_agent_messages.py` |
| Tests | `backend/tests/test_email_agent.py` |

It follows the same shape as NOA's inbound engine (`social/engagement.py`): an inbound item is
recorded once, a reply is drafted, and a human decides. It is a pipeline worker, not a second
agent framework, and it makes no LLM call.

## Flow of one cycle

```
Gmail ──► check connection (token, mailbox == business mailbox, read scope)
      ──► list inbox messages (GET)
      ──► claim message in email_agent_messages        (idempotency)
      ──► read full thread, normalize                   post-condition: ids match, sender + content present
      ──► resolve context from the DB                   post-condition: every entity re-read by primary key
      ──► classify                                      post-condition: schema valid, no unverified identifier
      ──► policy                                        safe / human / no reply; always non-sendable
      ──► draft (local text, or Gmail draft)            post-condition: draft read back and compared
      ──► audit row + one structured log line
```

| Module | Responsibility |
|---|---|
| `config.py` | `disabled` / `unconfigured` / `configured` |
| `gmail_client.py` | OAuth refresh, allowlisted Gmail calls |
| `normalize.py` | headers, multipart, HTML → text, quoted-reply stripping, attachment metadata |
| `senders.py` | sender kind and Gmail's authentication verdict |
| `context.py` | customer / order / shipment / supplier resolution |
| `classifier.py` | category, confidence, reason, risk flags |
| `policy.py` | response policy |
| `drafts.py` | draft text, RFC822, draft verification |
| `store.py` | `PgStore` (production), `InMemoryStore` (tests, dry runs) |
| `agent.py` | orchestration and post-conditions |
| `redaction.py` | secret scrubbing |

## Configuration

All values come from the environment (`.env` → `docker-compose.yml`, same handling as the
YouTube OAuth values). Changing them needs a backend restart.

| Variable | Default | Meaning |
|---|---|---|
| `EMAIL_AGENT_ENABLED` | `0` | `1` lets the background loop run |
| `GMAIL_OAUTH_CLIENT_ID` | — | OAuth web client (business project) |
| `GMAIL_OAUTH_CLIENT_SECRET` | — | secret, never logged |
| `GMAIL_OAUTH_REFRESH_TOKEN` | — | secret, never logged |
| `EMAIL_AGENT_MAILBOX` | `autosparefinder2024@gmail.com` | the mailbox the grant must belong to |
| `EMAIL_AGENT_GMAIL_DRAFTS` | `0` | `1` creates Gmail drafts; `0` keeps draft text in the audit row only |
| `EMAIL_AGENT_QUERY` | `in:inbox newer_than:14d` | Gmail search used to list messages |
| `EMAIL_AGENT_MAX_PER_CYCLE` | `25` | messages per cycle |
| `EMAIL_AGENT_INTERVAL_S` | `600` | seconds between cycles |
| `EMAIL_AGENT_MAX_ATTEMPTS` | `3` | attempts per message before it is marked failed |

States:

- **disabled** — `EMAIL_AGENT_ENABLED` is not `1`. The loop logs once and sleeps.
- **unconfigured** — enabled but an OAuth value is missing. The loop logs the missing variable
  names once and sleeps at least an hour. No crash loop, no Gmail call.
- **configured** — the loop runs. A token failure, a wrong account or a missing scope stops the
  cycle, backs off for an hour and notifies the owner at most once per 24 hours.

## Google setup (not done yet)

Everything must be under `autosparefinder2024@gmail.com` and project `valid-moment-444021-r6`.

1. In the Cloud console, confirm the active account and project, then enable the **Gmail API**.
2. On the OAuth consent screen (app "AutoSpareFinder"), add the scopes
   `gmail.readonly` and `gmail.compose`.
3. Use the existing web client, or create a new one, with the redirect URI
   `https://autosparefinder.co.il/`.
4. On the host: `python3 backend/maintenance/gmail_oauth_setup.py auth-url`
   (add `--read-only` to request `gmail.readonly` alone). Open the URL signed in as the
   business mailbox and approve.
5. Copy the `code` value from the redirect URL and run
   `python3 backend/maintenance/gmail_oauth_setup.py exchange --code <code>`.
   It writes the three `GMAIL_OAUTH_*` values to `.env` and prints only key names and scopes.
6. Restart the backend with the normal restart procedure. Migration 0039 creates the table.
7. `docker exec autospare_backend python3 /app/maintenance/email_agent_cli.py status`
   must show `ok: true`, the business mailbox, `can_read: true`.

Both Gmail scopes are in Google's *restricted* class. For the owner's own account an
unverified app works after the "unverified app" warning. Whether Google applies any further
limit to this project has not been checked.

### Scopes

| Scope | Needed for | Note |
|---|---|---|
| `gmail.readonly` | reading messages and threads | sufficient when `EMAIL_AGENT_GMAIL_DRAFTS=0` |
| `gmail.compose` | creating and reading back drafts | Google has no draft-only scope: this scope would also permit sending |

Because `gmail.compose` technically permits sending, the no-send boundary is enforced in code,
not by the scope (next section). `gmail.modify` and `https://mail.google.com/` are not requested.

## The no-send boundary

1. `GmailClient` has no send method.
2. Every Gmail request passes `assert_allowed()`. The allowlist is six endpoints:
   `GET profile`, `GET messages`, `GET messages/{id}`, `GET threads/{id}`, `GET drafts/{id}`,
   `POST drafts`. Anything else — `messages/send`, `drafts/send`, modify, trash, delete, labels,
   attachments, watch — raises `SendProhibited` before any network call.
3. `policy.send_allowed()` returns `False` unconditionally. It reads no flag and no environment
   variable.
4. Every policy decision has `sendable = False`.
5. The table has `CHECK (sendable = false)`: the database rejects a sendable row.
6. `POST drafts` is never retried automatically, so a retry cannot create a second draft.

## Classification

One category per email:

`customer_inquiry`, `order_shipping`, `supplier`, `shipping_eurosender`, `ebay`, `aliexpress`,
`payment_billing`, `account_security`, `complaint_dispute`, `refund_cancellation`,
`automated_notification`, `newsletter_marketing`, `spam_irrelevant`, `unknown`.

Evidence, in order: Gmail's SPAM label → an authenticated platform sender domain → bulk or
automated headers → a resolved supplier → keywords (Hebrew, English, Arabic) in the new text of
the message, then in earlier inbound messages of the thread → `unknown`. The subject is never
used alone.

Record schema (`Classification.to_dict()`, also stored in the audit row):

```
classification       one of the categories above
confidence           0.0 – 1.0
reason               the evidence that decided it
thread_id            Gmail thread id
message_id           Gmail message id
sender               sender address
references           customer_id / order_id / order_number / tracking_number /
                     eurosender_order_code / supplier_id — DB-verified values only
risk_flags           refund, cancellation, dispute_legal, complaint, price_change,
                     financial_commitment, address_change, security, customs,
                     risky_attachment, unauthenticated_sender
recommended_action   draft_order_status | draft_request_order_number | draft_request_details |
                     draft_supplier_ack | escalate_to_human | log_only | ignore
requires_human       true / false
```

`validate_classification()` rejects an unknown category, a missing or out-of-range confidence,
a missing reason or id, an unknown action, and any reference that context resolution did not
verify. A future LLM classifier must pass the same validator.

## Context resolution

| Entity | Rule |
|---|---|
| Customer | `users.email` equals the sender address, and Gmail authenticated the sender (DMARC pass, or aligned DKIM pass) |
| Order | a token in the thread equals `orders.order_number`, `tracking_number`, `eurosender_order_code` or `order_items.supplier_order_id` exactly |
| Shipment | read from the resolved order; reported only for `shipping_provider = 'eurosender'` with an order code |
| Supplier | `suppliers.website` host equals the sender's registrable domain, exactly one match; never for free-mail addresses |

What stays unresolved:

- an order that belongs to a different account, or cited by a sender who is not verified as its
  customer → `sender_mismatch`, details withheld;
- more than one candidate → `ambiguous`, details withheld;
- a sender Gmail did not authenticate → no customer or supplier match;
- tracking on an order without a real carrier shipment (the test-cycle `auto_fake_tracking`
  flag writes synthetic tracking) → `no_shipment`, never quoted.

`verify_context()` re-reads every resolved entity by primary key and re-checks ownership.

## Response policy

| Tier | When | Result |
|---|---|---|
| Safe for future automation | verified order status for the authenticated account owner; missing order number; missing vehicle / part details; acknowledgement to a verified supplier | a draft is prepared |
| Human approval required | refund, cancellation, price change, financial commitment, address change, legal / dispute, complaint, security / account recovery, customs, payment / billing, unknown category, risky attachment, unauthenticated sender, ambiguous or conflicting context, order status the templates do not cover, a draft that failed verification | no draft, `requires_human = true` |
| No reply | newsletter, spam, automated or platform notification without a serious flag, thread already answered by us | logged only |

Drafts are fixed Hebrew / English / Arabic templates filled only with verified context. They
have no slot for a price, a discount, a promise or a supplier name.

## Draft modes and verification

- **local** (default) — draft text is stored in `email_agent_messages.draft_body`. Gmail is
  not touched.
- **gmail** (`EMAIL_AGENT_GMAIL_DRAFTS=1` and the grant includes a draft scope) — the draft is
  created in the original thread with `In-Reply-To` / `References`, then read back and checked:
  draft exists, id matches, thread matches, recipient equals the source sender, body matches,
  label `DRAFT` present, label `SENT` absent. A draft that fails any check is escalated.

If the process stops between creating a draft and recording it, the next attempt sees the
`draft_attempted_at` marker without a draft id and does not create another draft.

## Idempotency and retries

`gmail_message_id` is unique. A message is claimed once; later cycles skip it. A transient
failure leaves the row in `retry` until `EMAIL_AGENT_MAX_ATTEMPTS`, then `failed`. A
mailbox-level failure (auth, scope) hands the claim back without using an attempt and stops the
cycle.

## Observability

- One JSON log line per event, prefix `[email_agent]`, events `processed`, `failed`,
  `skipped_outbound`, `cycle`, `cycle_aborted`, `connection_not_ready`, `idle`, `cycle_error`.
  A `processed` line carries message id, thread id, sender domain, classification, confidence,
  context statuses, recommended action, requires_human, draft mode, draft id, verification
  result, `sent: false`. Body text is never logged.
- `email_agent_messages` holds the full record, including the verification result and the
  failure reason.
- `GET /api/v1/system/email-agent` returns configuration state, the last cycle, counts by
  status and the 20 most recent rows.
- Tokens, client secrets and passwords are redacted by pattern and by exact value in every log
  line, stored error and CLI output.

## Running the tests

```
docker exec -w /app autospare_backend python3 -m pytest tests/test_email_agent.py -q -p no:cacheprovider
```

The store's SQL is also tested against real Postgres on a session-local TEMP table (nothing
persists):

```
docker exec -w /app -e EMAIL_AGENT_PG_TEST=1 autospare_backend python3 -m pytest tests/test_email_agent.py -q -p no:cacheprovider
```

## Live read-only check (after Google setup)

```
docker exec autospare_backend python3 /app/maintenance/email_agent_cli.py status
docker exec autospare_backend python3 /app/maintenance/email_agent_cli.py run-once --limit 5 --dry-run
```

`--dry-run` keeps everything in memory: no database row and no Gmail draft. It issues only GET
requests to Gmail.

## Not built yet

- Gmail push (`users.watch` + Pub/Sub). The agent polls. `watch` is deliberately not on the
  endpoint allowlist; adding push means adding that endpoint, a Pub/Sub topic in the business
  project and an authenticated webhook route.
- Owner review commands in the WhatsApp console.
- LLM-written drafts and LLM classification.

## Enabling sending later

Sending is a separate, reviewed change, not a configuration switch. It needs, at minimum:

1. a recorded history of owner decisions on drafts, per category, showing the drafts are right;
2. a send endpoint added to the allowlist and a send function that takes only a verified,
   approved draft id;
3. `policy.send_allowed()` rewritten to allow only the safe tier, with a verified context and
   an explicit owner approval per message (or a per-category release earned from the history);
4. a migration replacing `CHECK (sendable = false)`;
5. rate limits, the same quiet-hours handling as other outbound messages, and a post-condition
   that reads the sent message back.
