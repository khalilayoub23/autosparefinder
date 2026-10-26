# Email Agent (Gmail) — foundation

The Email Agent reads the business Gmail mailbox, classifies each inbound email, resolves it
against real AutoSpareFinder data, applies a response policy and prepares a draft reply.

**This phase never sends email.** It also never marks, labels, archives or deletes mail.

Status on 2026-10-04: code, tests and wiring are in the repo. The agent is **off** by default
and **unconfigured** — no Gmail OAuth grant exists yet (see "Google setup"). No live mailbox
has been read.

## Live state (verified 2026-10-25)

The agent is **running in production in draft-only mode**: it creates Gmail drafts and never sends.
Activated 2026-10-25 20:43 UTC (backend recreate; previous state: read-only processing since 2026-10-10).

| Setting | Live value |
|---|---|
| `EMAIL_AGENT_ENABLED` | `1` (in `.env`, since 2026-10-10 07:06 UTC) |
| Runtime control | `run` (armed 07:19:45 UTC by the CLI) |
| `EMAIL_AGENT_GMAIL_DRAFTS` | `1` (in `.env` since 2026-10-25): drafts are created in Gmail (`POST drafts`) and read back |
| Gmail OAuth grant | `gmail.readonly` + `gmail.compose` (owner re-consent 2026-10-25). `compose` could technically send; the no-send boundary below is enforced in code and in the database, not by the scope. |
| Sending | impossible by construction (see "The no-send boundary") |
| Bounds | 25 messages per cycle, one cycle every 600 s, query `in:inbox newer_than:14d` |

**Controlled verification 2026-10-25 (owner test message from his own Hotmail, subject "ASF draft test 2510"):** processed 20:44:28 UTC by the first cycle after activation (`order_shipping` → `draft_request_order_number`); one Gmail draft `Re: ASF draft test 2510` addressed to the sender, in the same thread, label `DRAFT` only; agent read-back all checks true, `send_calls: 0`; independent check through the allow-listed client: the thread holds the inbound message + 1 draft, 0 `SENT`; the next cycle skipped it (`new: 0, drafts: 0, sent: 0`), still 1 DB row and 1 draft (idempotent). Kill switch without restart: `email_agent_cli.py halt`. To return to read-only drafts: `EMAIL_AGENT_GMAIL_DRAFTS=0` + backend recreate.

What it does today: reads the inbox, classifies each message, resolves context by exact DB
match, decides a policy tier and stores the result (and, where policy allows, a proposed reply
text) in `email_agent_messages`. It changes nothing in Gmail and nothing in orders or users.

Not active, each needing a separate decision: Gmail drafts (needs a `gmail.compose` re-consent
and `EMAIL_AGENT_GMAIL_DRAFTS=1`), any review surface for the stored results (not built), and
sending (a reviewed code change plus a migration).

To stop it now: `docker exec autospare_backend python3 /app/maintenance/email_agent_cli.py halt`.
The running loop notices at its next wake (at most one interval, 600 s, when idle; at the next
checkpoint when mid-cycle). A manual run and any Gmail write are refused immediately.

## Where it lives

| Piece | File |
|---|---|
| Package | `backend/email_agent/` |
| Background loop | `email_agent/loop.py`, registered in `BACKEND_API_ROUTES.startup()` as `email_agent_loop` |
| Status endpoint | `routes/email_agent_routes.py` → `GET /api/v1/system/email-agent` (`X-Collect-Secret`) |
| Operator CLI | `maintenance/email_agent_cli.py` (`status`, `run-once`, `control`, `halt`, `arm`) |
| Runtime kill switch | `email_agent/control.py`, state file `/app/state/email_agent_control.json` |
| OAuth setup helper (host) | `maintenance/gmail_oauth_setup.py` (`auth-url`, `callback-status`, `exchange`) |
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
| `EMAIL_AGENT_MIN_REQUEST_INTERVAL_MS` | `1500` | minimum gap between Gmail API calls (the project's per-user quota is 6,000 units per minute) |

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
4. On the host: `python3 backend/maintenance/gmail_oauth_setup.py auth-url --read-only`
   (drop `--read-only` to also request `gmail.compose`). The URL carries a one-time `state`
   and is valid for one hour. Open it signed in as the business mailbox and approve.
   **After approving, the browser shows the normal AutoSpareFinder homepage.** That is
   expected: the redirect goes to `https://autosparefinder.co.il/?state=…&code=…&scope=…`,
   and the homepage does not react to it. Nothing needs to be copied.
5. Within eight minutes run `python3 backend/maintenance/gmail_oauth_setup.py exchange`.
   It finds the redirect that carries our `state` in the edge nginx access log, exchanges the
   code server-side, writes the three `GMAIL_OAUTH_*` values to `.env` and prints only key
   names and scopes. `callback-status` shows, without any secret, whether the redirect
   arrived, its age, the granted scope and whether Google returned a code or an error.
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

Evidence, in this order. The subject is never used alone.

1. Gmail's SPAM label.
2. An authenticated platform sender domain (Eurosender, eBay, AliExpress, payment, Google).
3. Auto-reply or auto-generated headers (`Auto-Submitted`, `X-Autoreply`) → `automated_notification`,
   whoever the sender is. An auto-reply is never answered.
4. Bulk-mail headers (`List-Unsubscribe`, `List-Id`, `Precedence: bulk`) → `newsletter_marketing`,
   **even when the sender domain matches a known supplier**. A supplier's marketing mail is not
   supplier correspondence. Exception: mail from a resolved customer is never dropped as a
   newsletter; it is classified by content and flagged `bulk_headers` for a human.
5. A no-reply style address on its own is a weak signal: from an unknown sender →
   `automated_notification`; from a known supplier → still `supplier`, flagged `automated_sender`
   for a human.
6. A resolved supplier → `supplier`.
7. Keywords (Hebrew, English, Arabic) in the new text of the message, then in earlier inbound
   messages of the thread.
8. `unknown`.

Risk flags are detected on the meaningful text only (`normalize.meaningful_text`):

- Human-written mail: the whole message minus boilerplate lines (unsubscribe, legal, "sent from
  my …"). Text after a sign-off still counts, and a footer-looking line written in the first
  person is kept.
- A human message with no newly-authored text is flagged `empty_message` and always goes to a
  human. "Newly-authored" is the text above the quoted original, minus boilerplate lines; it
  counts only if it holds at least two letters or digits, so whitespace, dots, punctuation and
  the mail client's divider line are empty. The quoted original is still read for identifiers
  (an order number in the quote resolves the order, ownership enforced), but it never supplies
  the category, a risk flag or a reason to reply.
- Automated notices: the subject plus the opening of the body, up to the sign-off or the first
  footer line (at most 1,500 characters). Order-summary tables, promo lines and legal paragraphs
  below that are ignored.

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
                     financial_commitment, address_change, account_change, security, customs,
                     delivery_problem, wrong_item,
                     risky_attachment, unauthenticated_sender, bulk_headers, automated_sender,
                     empty_message
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

Thread association (`normalize.associated_messages`). A Gmail thread is not a conversation: Gmail
also groups independent emails that only share a subject. Context and the category fallback may
therefore use only:

- the current message itself: subject, newly-authored text, and the quoted original it carries
  (quoted identifiers resolve context; quoted text never sets the category or a risk flag);
- the sender's own earlier messages whose Message-ID the current message names in `In-Reply-To`
  or `References`.

Other messages in the same Gmail thread are ignored. If the current message's own words cite a
second order reference of the same shape as the resolved order (same prefix up to the last `-`,
same length) that matches no order, the order is `ambiguous` and goes to a human.

`verify_context()` re-reads every resolved entity by primary key and re-checks ownership.

## Response policy

| Tier | When | Result |
|---|---|---|
| Safe for future automation | verified order status for the authenticated account owner; missing order number; missing vehicle / part details; acknowledgement to a verified **and active** supplier | a draft is prepared |
| Human approval required | refund, cancellation, price change, financial commitment, address change, legal / dispute, complaint, security / account recovery, customs, payment / billing, unknown category, risky attachment, unauthenticated sender, a claim that an order did not arrive or arrived wrong or damaged, a message with no newly-authored text, inactive or pending supplier, no-reply supplier address, ambiguous or conflicting context, order status the templates do not cover, a draft that failed verification | no draft, `requires_human = true` |
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

## Gmail errors

`gmail_client.classify_error` reads Google's own reason; an HTTP 403 is not one thing.

| Kind | Meaning | What the agent does |
|---|---|---|
| `rate_limit` | quota or rate limit (HTTP 429, or a 403 "Quota exceeded") | backs off 5 s then 15 s; if still limited, hands the message back without using an attempt and ends the cycle (`throttled`). No owner alert. |
| `config` | Gmail API disabled or never enabled in the project | stops the cycle, alerts the owner |
| `scope` | the grant lacks the scope | stops the cycle, alerts the owner |
| `auth` | invalid, expired or revoked credentials | stops the cycle, alerts the owner |
| `permission` | authorization or delegation denied | stops the cycle, alerts the owner |
| `unknown` | a 403 with no recognizable reason | not retried in the call; the message is retried next cycle, up to the attempt limit |
| `transient` | 5xx or network | retried in the call, then next cycle |

Calls are paced (`EMAIL_AGENT_MIN_REQUEST_INTERVAL_MS`) and a thread is read once per cycle even
when it holds several new messages.

## Runtime kill switch

`EMAIL_AGENT_ENABLED` comes from the container environment, so changing it needs a backend
recreation. The runtime control stops the agent without one.

- **State:** one JSON file on the persistent `worker_state` volume
  (`EMAIL_AGENT_CONTROL_FILE`, default `/app/state/email_agent_control.json`). The background
  loop (inside uvicorn) and a `docker exec` CLI run read the same file. No Redis, DB or network
  is involved.
- **Fail closed:** processing is allowed only when the file exists, is valid JSON and says
  `{"mode": "run"}`. A missing file means halted (`not_armed`, the default). An unreadable file,
  invalid JSON, an unknown mode or a file over 4 KB also mean halted.
- **Two keys to run:** the background loop needs `EMAIL_AGENT_ENABLED=1` and an armed control.
  `run-once` ignores `EMAIL_AGENT_ENABLED` (it is an explicit operator action) but never runs
  while halted.

Operator commands (each takes effect at the next checkpoint, with no restart):

```
docker exec autospare_backend python3 /app/maintenance/email_agent_cli.py halt      # stop now
docker exec autospare_backend python3 /app/maintenance/email_agent_cli.py control   # show state (read-only)
docker exec autospare_backend python3 /app/maintenance/email_agent_cli.py arm       # allow processing again
```

Deleting the file also halts the agent.

Checkpoints:

| Where | Effect when halted |
|---|---|
| Loop, every iteration | No Gmail client, no heartbeat, no DB access. Logs `idle state=halted` once and re-checks every 60 s. |
| `process_once` start | No token refresh, no Gmail call, no store write. Returns `halted`. |
| Before each message claim | The cycle ends; the message is not claimed. |
| `process_message`, before the draft step | Raises `Halted`; the claim is released (status `retry`, attempt not used). |
| `process_message`, before saving the result | Same as before the draft step, **unless a Gmail draft was already created for this message**: that record is always saved, so the system never loses track of a draft that exists. |
| `GmailClient._call`, any non-GET request (`POST drafts`), before the token step | Raises `Halted`; no network call at all, not even a token refresh. |
| `GmailClient._call`, write intent around the request itself | Marker written, control re-read, then sent. Raises `Halted` and sends nothing if halted, or if the marker cannot be written. |
| CLI `run-once` (incl. `--dry-run`, `--gmail-drafts`) | Exit 3. No Gmail client, no DB store. |

**Latency.** A checkpoint is one small file read: 17 µs with no file, 45 µs when armed
(measured in the backend container). A halt affects nothing that
has already finished, and nothing new starts after it.
- **Idle loop:** halted before its next cycle can start.
- **During a cycle:** stops at the next checkpoint. At most the Gmail GET already in flight
  completes first: the 25 s timeout, up to 3 attempts with backoff (2–4 s, or 5–15 s on a rate
  limit), so about 95 s in the worst case. Reads only.
- **A draft create:** checked before the token step and again, inside the write intent,
  immediately before the request leaves.

**What a halt guarantees, and what it does not.**

The switch *prevents new writes after a halt*. It does **not** *cancel a request that was
already authorized and sent*: no code on this side can recall an HTTP request that has left.
Those are two different guarantees and only the first is claimed.

What makes the first one exact is the write intent (`control.write_intent`). No lock is held
across the network request:

- A Gmail write records a marker file **first**, **then** reads the control, sends, and removes
  the marker.
- `halt` writes the control **first**, **then** reads the markers.

For any write, one of two things is therefore true: the writer saw the halt and sent nothing,
or `halt` saw the marker and lists it under `writes_in_flight`. So:

- `halt` prints `"writes_in_flight": []` → no Gmail write is in flight and none will start
  until someone arms the agent.
- `halt` lists a write → that request was authorized before the halt. It is not cancelled. It
  finishes within the 25 s transport timeout (a POST is never retried); run `control` again
  to see it clear. The result is at most one more *draft*, and its record is saved.

A lock held across the request (so that `halt` waits for it) was considered and rejected: a
hung request would block the kill switch for up to 25 s, and a crashed holder could block it
for longer.

**Limits that remain.**
1. **A write listed by `halt` completes.** See above: reported, never cancelled.
2. **A killed process leaves its marker.** It is shown as `stale: true` after 180 s and removed
   by the next write. A stale marker means "unknown outcome": check the mailbox's drafts.
3. **Halt between the agent's draft checkpoint and the client's check.** Nothing is sent, but
   `draft_attempted_at` was already written, so after re-arming that one message goes to a
   human instead of risking a duplicate draft. Conservative by design (tested).
4. **Store writes after a halt.** A halt mid-message hands the claim back (`release`), a draft
   that already exists gets its record saved, and a message that fails for another reason is
   still marked failed. These are local bookkeeping rows, never Gmail calls.
5. **Reads in flight.** A Gmail GET already running completes (about 95 s worst case, above).
6. **The control file is not access-controlled.** Anyone who can write `/app/state` in the
   backend container can arm or halt. That is the same trust boundary as `.env`.

Sending is not controlled by this switch. It is impossible by construction (see "The no-send
boundary"); the switch adds a halt for drafts and processing.

## Observability

- One JSON log line per event, prefix `[email_agent]`, events `processed`, `failed`,
  `skipped_outbound`, `cycle`, `cycle_aborted`, `cycle_throttled`, `connection_not_ready`, `idle`, `cycle_error`,
  `halted` (runtime kill switch; carries the stage and reason).
  A `processed` line carries message id, thread id, sender domain, classification, confidence,
  context statuses, recommended action, requires_human, draft mode, draft id, verification
  result, `sent: false`. Body text is never logged.
- `email_agent_messages` holds the full record, including the verification result and the
  failure reason.
- `GET /api/v1/system/email-agent` returns configuration state, the runtime kill-switch state,
  the last cycle, counts by status and the 20 most recent rows.
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
docker exec autospare_backend python3 /app/maintenance/email_agent_cli.py arm        # run-once refuses while halted
docker exec autospare_backend python3 /app/maintenance/email_agent_cli.py run-once --limit 5 --dry-run
docker exec autospare_backend python3 /app/maintenance/email_agent_cli.py halt
```

`--dry-run` keeps everything in memory: no database row and no Gmail draft. It issues only GET
requests to Gmail.

## Not built yet

- Gmail push (`users.watch` + Pub/Sub). The agent polls. `watch` is deliberately not on the
  endpoint allowlist; adding push means adding that endpoint, a Pub/Sub topic in the business
  project and an authenticated webhook route.
- A dedicated OAuth callback path. The redirect lands on the homepage, so the authorization
  code is written to the nginx access log (and to same-origin `Referer` headers). A code is
  single-use and expires within minutes, but the clean design is a callback location with
  `access_log off`, like the AliExpress one. It needs a new redirect URI on the Google client,
  a backend route and an nginx restart.
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

## Deployment runbook (prepared 2026-10-09 — NOT executed; every step needs owner approval)

Current state: code and config on disk (`deploy/nginx.conf`, `docker-compose.yml`, `backend/`), not
loaded. Running backend started 2026-10-04 (no `GMAIL_OAUTH_*` / `EMAIL_AGENT_*` in its environment),
PII migration at 0038, agent disabled, Gmail grant `gmail.readonly` only.

**0. Before anything** — quiet window (the car-parts.ie harvester and supervised loops restart with
the backend); `bash backend/scripts/pre_restart.sh`; confirm the latest PII backup exists.

**1. nginx — OAuth log redaction** (`deploy/nginx.conf`, single-file mount → needs a container
RESTART, a reload reads the old file)
- validate: `docker run --rm --network none -v $PWD/deploy/nginx.conf:/etc/nginx/nginx.conf:ro -v autosparefinder_frontend_build:/usr/share/nginx/html:ro nginx:alpine nginx -t`
- apply: `docker restart autospare_nginx`
- verify: `curl -s -o /dev/null "https://autosparefinder.co.il/?state=CHECK1&code=CHECK2"`, then
  `docker logs --since 2m autospare_nginx | grep -c CHECK2` must be 0 and the line must show
  `code=<redacted>`; site `/` and `/api/v1/system/health` return 200; host test
  `python3 backend/tests/test_nginx_oauth_log_redaction.py` passes.
- rollback: revert the OAuth hunks in `deploy/nginx.conf`, `docker restart autospare_nginx`.
- note: lines written before the fix stay in the existing container log (expired / already used
  codes); they disappear only when the nginx container is recreated or its log file is rotated.

**`--no-deps` is mandatory (preflight 2026-10-09).** `postgres_catalog` and `postgres_pii` run with a
`POSTGRES_PASSWORD` environment value that differs from what `.env` renders today (the variable is only
read when a data directory is first initialised, so the databases are unaffected — but Compose sees
config drift). Recreating the backend without `--no-deps` also brings up the backend's
dependencies and would RECREATE BOTH DATABASES; Compose's own dry run confirms it
(`docker compose up --dry-run -d backend` → "postgres_pii Recreate", "postgres_catalog Recreate"; with
`--no-deps` only the backend is recreated). Re-run both dry runs immediately before executing.

**2. Backend — load code, env and migration 0039** (`docker compose up -d --no-deps backend`; a plain
`docker restart` does NOT load new environment variables)
- the start command runs `alembic -c alembic_pii.ini upgrade head` → creates `email_agent_messages`.
- verify: `alembic -c alembic_pii.ini current` = `0039_email_agent_messages`; the table exists with
  `CHECK (sendable = false)`; `GET /api/v1/system/email-agent` (header `X-Collect-Secret`) returns
  `config.state = "disabled"`, `send_enabled = false`, no `store_error`; one `[email_agent] idle`
  log line; `/api/v1/system/health` 200; the harvester relaunched; `bash backend/scripts/post_restart.sh`.
- rollback: the agent is off, so the code is inert; the migration rolls back with
  `alembic -c alembic_pii.ini downgrade 0038_eurosender_shipping` (drops the still-empty table).

**3. Read-only verification in the running container** (no switch change)
- `docker exec autospare_backend python3 /app/maintenance/email_agent_cli.py status` → `ok`,
  mailbox `autosparefinder2024@gmail.com`, `can_read: true`, `can_draft: false`.
- `... email_agent_cli.py run-once --limit 5 --dry-run` → Gmail GET only, nothing stored.

**4. Activation — separate approval** (`EMAIL_AGENT_ENABLED=1` in `.env`, then `docker compose up -d --no-deps backend`)
- keep `EMAIL_AGENT_GMAIL_DRAFTS=0`: draft text is stored in `email_agent_messages` only.
- verify after the first cycle: rows appear; every row `sendable = false`; refund / cancellation /
  address / contact / not-received / wrong-item / cross-customer / empty rows all
  `requires_human = true`; Gmail calls GET only; no `cycle_error` loop.
- rollback criteria (any one): a non-GET Gmail request; a sensitive or cross-customer row with
  `requires_human = false`; repeated `cycle_error`; backend unhealthy; quota / auth alerts.
  Rollback: `EMAIL_AGENT_ENABLED=0` + `docker compose up -d --no-deps backend` (there is no runtime kill
  switch; the switch is read at start).

**5. Gmail drafts — later, separate approval**: needs a new consent with `gmail.compose` and
`EMAIL_AGENT_GMAIL_DRAFTS=1`. Drafts are created in the mailbox and read back for verification;
sending stays impossible in code.

**OAuth re-consent after the log fix**: the redirect still lands on `/`, but its code is no longer
in any log, so `gmail_oauth_setup.py exchange` reports `code_redacted` and needs `--code` copied from
the browser address bar. A server-side capture without logging needs a dedicated callback route and a
new redirect URI on the Google client (not built).

### Step 2 execution plan — backend recreation (verified preflight 2026-10-09; EXECUTED 2026-10-09 06:58 UTC, see FIXES_TRACKER)

> Since 2026-10-10 `backend/scripts/pre_restart.sh` runs `scripts/restart_preflight.py` first and
> aborts with exit 4 if any work would be cut, including in-process work such as the 10:00 IL
> Facebook group scan. Treat a BLOCKED preflight as a failed gate. When only code changed, add
> `--force-recreate` to the recreate command: compose otherwise reports the backend as up to date.

Gates — all must hold at the moment of execution (a snapshot from earlier does not count):
1. `docker compose up --dry-run -d --no-deps backend` lists only `autospare_backend`.
2. No running job: `job_registry` has no `status='running'` row; `restart_workers.py` scan finds no importer;
   the harvester log shows "queue drained … Resting" (it is relaunched by its supervised loop after start).
3. Outside the night-pipeline window (01:00–07:00 Israel time) and not within ~10 min of the next
   `run_all_tasks` (it runs ~every 3 h; the last ones started 20:40, 01:29, 04:38 UTC on 8–9 Oct).
4. Host memory available > 2 GB; no waiting locks in either database.

Commands (in order):
```
bash backend/scripts/pre_restart.sh          # NOTE: marks running job_registry rows 'superseded' (DB write)
                                             # and SIGTERMs importer subprocesses; with gate 2 met it changes nothing
docker compose up --dry-run -d --no-deps backend
docker compose up -d --no-deps backend
```
Expected start: alembic catalog = no-op (already at head `0061_noa_ops_audit`), PII `0038 → 0039`
(creates only `email_agent_messages`), then uvicorn. The migration chain runs in the background of the
start command, so uvicorn may answer before it finishes; with the agent disabled nothing reads the table.
`container_start.sh` then clears the `db_update_agent` Redis lock, marks leftover `running` jobs `dead`,
and resumes captured workers. `run_all_tasks` starts ~90 s after start (~10–12 min of DB writes).

Verify (in this order):
- `docker inspect autospare_backend` → running, health `healthy`; `autospare_postgres_*` start times unchanged.
- `docker exec -w /app autospare_backend alembic -c alembic_pii.ini current` → `0039_email_agent_messages (head)`;
  the table has `CHECK ((sendable = false))` (`pg_get_constraintdef`).
- `docker exec autospare_backend sh -c 'echo $EMAIL_AGENT_ENABLED $EMAIL_AGENT_GMAIL_DRAFTS'` → `0 0`;
  `GMAIL_OAUTH_REFRESH_TOKEN` present (check presence only, never print it).
- `GET /api/v1/system/email-agent` with `X-Collect-Secret` → `config.state="disabled"`, `send_enabled=false`,
  no `store_error`; one `[email_agent] {"event": "idle", "state": "disabled"…}` log line.
- `docker exec autospare_backend python3 /app/maintenance/email_agent_cli.py status` → ok, `can_read: true`,
  `can_draft: false` (one token refresh + one Gmail GET).
- `/api/v1/system/health` 200 through the public domain; harvester process back; `bash backend/scripts/post_restart.sh`.
- nginx keeps proxying to the new backend address without a reload (resolver `valid=10s` + `server backend:8000 resolve`).

Rollback criteria: backend not `healthy` within 5 min; migration error in the start log; any Postgres
container recreated; a non-GET Gmail request; `email_agent` state not `disabled`.
Rollback: the agent is disabled, so the code is inert. Migration: `docker exec -w /app autospare_backend
alembic -c alembic_pii.ini downgrade 0038_eurosender_shipping` (drops the empty table; verified in isolation).
If the backend itself will not start: check the start log, revert the offending change on disk,
`docker compose up -d --no-deps backend` again.
