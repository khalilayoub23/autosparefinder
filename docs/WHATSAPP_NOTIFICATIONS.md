# WhatsApp notifications — system map, contracts, failure paths, runbook

Source: FIXES_TRACKER #69 / #70. Every row below was traced in the code AND, where marked, checked against
runtime evidence (bridge logs, `whatsapp-bridge/msg_cache.json` outbound history 2026-10-05..10-19, Redis,
DB) on 2026-10-20. "code" = established from source only; "live" = observed in production data.

## 1. Components

| Component | What runs | Notes |
|---|---|---|
| Bridge | `whatsapp-bridge/index.js`, Node 20.20.2, `@whiskeysockets/baileys` **6.17.16** (installed; package.json says `^6.7.0`) | Linked (multi-device) companion of the business account 972532426920. HTTP on `127.0.0.1:3001` + docker network `autosparefinder_internal` only; **no auth on `/send`** (network isolation is the control). |
| Provider | `backend/social/whatsapp_provider.py` | The only code that talks to the bridge. Maps the owner's number to his LID (#69 §1). |
| Quiet-hours gate + queue | `BACKEND_API_ROUTES._wa_send_quiet` / `_flush_wa_quiet_queue` | Redis list `autospare:wa_quiet_queue` (≤ 50), dead letters `autospare:wa_quiet_dead` (≤ 200, 30 d, metadata only). Window 09:00–21:00 Asia/Jerusalem. |
| Owner alert API | `BACKEND_API_ROUTES.notify_owner` (+ `_alert_owner` wrapper) | `{icon} [{category}] {title}` + body; Redis cooldown per `alert_key`; routes via `_wa_send_update` (updates group if configured, else owner). |

## 2. Paths (source → construction → gate → transport)

| Path | Source / builder | Gate | Transport | Evidence |
|---|---|---|---|---|
| Owner alerts (32 call sites) | health monitor, harvest supervisor, NOA, orders, supplier sourcing, db_cleanup_agent → `notify_owner` | quiet hours (critical bypasses), cooldown by `alert_key` | `_wa_send_update` → `_wa_send_quiet` → provider | live: 411 cached outbound |
| Daily status digest (09:00 IL) / problem alert | `_status_update_loop` | quiet hours; once per local day (Redis marker, #70) | `_wa_send_update` | live: 16 digests, 2 duplicated on restart days (fixed #70) |
| Weekly harvest digest (Sun 09:00 IL) | `_harvest_supervisor_loop` | as above | `_wa_send_update` | code |
| Pending-posts reminder (daily) | health monitor, `social_posts.status='pending_approval'` > 2 h | `alert_key` per oldest post, 24 h | `notify_owner` | live: 50 posts pending since 2026-07-19 |
| Group-scan report, FB session alert | `social/facebook_browser/group_scanner.py`, `session.py` | quiet hours | `_wa_send_quiet` | live: 10-19 10:16 UTC |
| Night-queued delivery | `_flush_wa_quiet_queue` (health monitor, every 5 min while the window is open) | — | provider, text prefixed `🌙 [נשלח מאוחר — …]` | live: 135 of 411 (33 %) |
| Customer chat replies | `routes/webhooks.py` (13 sites) → `process_user_message` (12 s deadline) | none (reply in conversation) | provider | live: customer chats |
| Customer reminders (cart, payment) | `_abandoned_cart_loop`, `_pending_payment_reminder_loop` | loop-level window check + lifetime caps | provider | code |
| Post-payment tracking | `routes/utils._send_post_payment_notification` | none (transactional) | provider (result now logged, #70) | code |
| 2FA codes | `BACKEND_AUTH_SECURITY` | reserved test domains suppressed | provider | live: 27 suppressions logged |
| Admin copies | 4 health-monitor sites | `_admin_gets_wa` (explicit opt-in, never the owner) | `_wa_send_quiet` | live: 202 header-less copies before 10-18, 0 after |

## 3. Delivery semantics (what "ok" means)

- Provider `{"ok": true}` = the bridge's `sock.sendMessage()` resolved = **WhatsApp's server accepted** the
  message. It is NOT delivery or display. Delivery/read receipts are not recorded (limitation).
- The strongest display signal available is the bridge's `wa_retry` event (a recipient device could not decrypt
  → "בהמתנה להודעה זו"). Owner: 1,013 retries 10-01..10-18, **0 since the LID fix** (10-18 22:35 UTC). Customers:
  0 retries 10-01..10-18 (no evidence of the placeholder); 0 customer sends since the fix → NOT VERIFIED after it.
- `{"queued": true}` = stored for the morning; proves nothing about the channel.

## 4. Queue lifecycle (state machine)

```
caller ─► _wa_send_quiet ── empty text / no recipient ─► REFUSED  {ok:false, INVALID_PAYLOAD}   (never queued, never sent)
             │ window open or critical ─► SEND NOW (provider)
             └ night ─► QUEUED (RPUSH; same alert_key replaces the older entry)
                          └ > 50 entries ─► oldest DEAD-LETTERED (reason overflow)
flush (window open, every 5 min, FIFO, ≤ 50 per pass):
  malformed JSON / empty text / no recipient ─► DEAD-LETTERED (malformed | invalid_payload), next item
  provider ok ─► DELIVERED (accepted by WhatsApp) → removed
  provider INVALID_PAYLOAD ─► DEAD-LETTERED (permanent_error) at once, next item
  other failure (bridge down, timeout, 5xx) ─► stays at head, attempts+1, pass stops (FIFO kept)
       attempts reach 5 ─► DEAD-LETTERED (retry_exhausted), next item
after each pass: drops in the last 24 h ─► one owner summary per local day (alert_key wa_dead_letter_<date>)
```
Retry rules: the provider retries **only** when nothing can have been sent (bridge refused before
calling WhatsApp, or unreachable) within 15 s. A timeout or 500 is an **unknown outcome**: the provider does not
retry it; the flush does (next pass) — at-least-once, a rare duplicate is the accepted trade-off against loss.

## 5. Notification contracts

| Type | Source data | Required | Rendering | Failure behaviour |
|---|---|---|---|---|
| Owner alert | caller's title/body | category, title (non-empty) | `{icon} [{label}] {title}\n\n{body}`; title only when no body | send failed → logged, cooldown NOT set; critical → immediate |
| Night-queued | queued item | text, to | `🌙 [נשלח מאוחר — נשמר משעות הלילה {dd/mm HH:MM}]\n{text}` | see §4 |
| Dead-letter summary | `autospare:wa_quiet_dead` (24 h) | ≥ 1 drop | `{N} התראות WhatsApp לא נמסרו ב-24 השעות האחרונות` + one line per reason | once per local day |
| Daily status digest | `job_registry` (last 2 h, ≤ 12), **`night_pipeline_runs` for D:<today>** (#71), supervised tasks, REX todos (marked when unchanged > 1 day), catalogue, harvest queue | — | header `📅 *AutoSpareFinder — דוח יומי* (HH:MM ישראל)`; sections "עובדים (שהתחילו בשעתיים האחרונות)", "עבודות הלילה (D:…)" | 09:00 Asia/Jerusalem, once per day (persisted) |
| Pending posts (#71) | `social_posts` pending > 2 h | ≥ 1 row | NEW posts only: `📢 *NOA — N פוסטים חדשים ממתינים לאישורך*` + total + oldest date; unchanged backlog → `תזכורת: …` with the oldest date | `notify_backlog`: new ≤ 1/day, reminder ≤ 1/7 days |
| Learned keywords (#71) | `category_learned_keywords` at consensus (all, not top 15) | ≥ 1 token | NEW tokens only, each ✅/⚠️ by the evidence gate; total + oldest date; unchanged → `תזכורת: N מילות סיווג ממתינות` | same as above |
| Customer slow answer (#71) | agent > 12 s | — | `⏳ … התשובה תגיע בהודעה נפרדת … אין צורך לשלוח שוב` in he/ar/en, then the real answer when ready | deadline 120 s → honest failure text once; identical resend in flight → no second run |

## 6. Runbook (read-only)

- Queue (metadata only): `docker exec -w /app autospare_backend python3 -c "import asyncio,json;from BACKEND_AUTH_SECURITY import get_redis
async def m():
  r=await get_redis();q=await r.lrange('autospare:wa_quiet_queue',0,-1);print(len(q),[ (json.loads(x).get('alert_key'),len(json.loads(x).get('text',''))) for x in q])
asyncio.run(m())"` (tested 2026-10-20: 5 queued, all with content)
- Dead letters: same, key `autospare:wa_quiet_dead` (no message text is stored).
- Bridge: `docker logs --since 1h whatsapp_bridge | grep -E "Sent OK|Send error|SEND_REJECTED|wa_retry"`; connections
  `tail whatsapp-bridge/connection_events.log`. 428/503 disconnects from WhatsApp recover in ~1 s (124 incidents
  09-21..10-18: median 1.1 s, max 18 s).
- Never run the bridge tests inside the production container: they spawn `index.js`. Run them offline:
  `docker run --rm --network none -v <copy without auth_info>:/app -w /app node:20-alpine node test_bridge_lifecycle.mjs`.

## 7. Open items / decisions

- Customer 12 s timeout reply discards the answer, promises "within seconds", Hebrew only (1 occurrence in 2 weeks).
- 50 NOA posts pending approval since 2026-07-19 → a daily reminder forever; approving one now publishes stale content.
- Harvest "queue empty": re-alerts every 24 h (`HARVEST_IDLE_REALERT_S`) and says since when; stall stays 6 h (#71).
- No delivery/read receipts; bridge `/send` unauthenticated (network-isolated).
- Bridge console lines print only the last 4 digits of a recipient (`jidHint`) — VERIFIED live after the 2026-10-20 17:36 restart
  (`Sending to` / `Sent OK to` / `[WA IN]` → `...0397`). Error paths (send/delete/media/backend) mask numbers inside `err.message`
  (`maskNumbers`, #72) — in the repository, live after the next bridge restart. Older docker-log lines still contain full numbers.
- Delayed chat answers: E2E through the real webhook route (`tests/test_whatsapp_late_reply_e2e.py`, 7 scenarios); live customer
  path not yet exercised (#72).

## 8. Change reporting, source inventory and bypass guard (2026-11-13)

**One alert API.** Every proactive owner alert calls `notify_owner` (or `resolve_owner_alert`). Guarded by
`tests/test_owner_notification_changes.py`: an AST scan fails on any direct provider send outside a reviewed allowlist
(chat replies, 2FA, post-payment notice, the two customer reminder loops, admin console reply, the gate and the flush) and on
any `_wa_send_quiet` / `_wa_send_update` call outside the router, the digests with their own once-per-period marker, the
crashed-task callback and the opt-in admin copies.

**States (one vocabulary).** `notify_owner(..., state=, fingerprint=, remind_s=)`:

| Tag in the header | When |
|---|---|
| `🆕 חדש` | first report of a condition (`alert_key`) |
| `🔄 שינוי` | the fingerprint differs from the last DELIVERED report |
| `👉 נדרשת פעולה` | first report, caller says the owner must act |
| `⏰ תזכורת (ללא שינוי)` | same fingerprint, `remind_s` elapsed |
| `✅ נפתר` | `resolve_owner_alert` — sent once, only if an alert was delivered; the next occurrence is new again |

Same fingerprint and no reminder due = not sent (`{"suppressed": True, "error": "unchanged"}`). The fingerprint is a digest of
what the report SAYS (a reason, a state), never of volatile counters. State is stored in Redis `autospare:alert_state:<key>`
(30 d) only after a confirmed send or queue. Redis down: the alert is sent (never lost); a "resolved" is not sent (never invented).
Severities: `critical` (immediate, bypasses quiet hours) · `error` 🟠 · `warning` 🟡 · `info` · `success`.

**Sources (reconciled 2026-11-13; the call-site COUNT is superseded by §9, 2026-11-24: 76 AST-verified call sites in 12 files, guarded by a test).** The table lists the sources (the earlier audit listed 23):

| # | Source (function) | Key / dedup | Class |
|---|---|---|---|
| 1 | service down / restored (`_health_monitor_loop`) | state change per service | critical / success |
| 2 | catalog stagnation | `catalog_stagnation`, fingerprint, 24 h reminder, resolved | warning |
| 3 | high error rate | `high_error_rate` | critical |
| 4 | LLM provider blocked (HF / Cerebras) | `llm_*` | warning |
| 5 | worker silence / recovered | `worker_silence*` | warning / info |
| 6 | job failures DLQ, zombie job, task timeout | `job_failures_dlq`, per event | critical / success |
| 7 | disk level, disk monitor error | `disk_<level>:…` | by level |
| 8 | OOM watchdog | `oom_watchdog_*` | critical |
| 9 | undelivered-notification summary | `wa_dead_letter_<date>` | warning |
| 10 | Chrome watchdog | `chrome_watchdog` | warning |
| 11 | weekly maintenance | weekly | warning |
| 12 | harvest stall / idle / recovered, FlareSolverr failures | `_alert_key` per state | warning / critical |
| 13 | Meilisearch parity drift | `meili_parity_drift` | warning |
| 14 | Amayama harvester down | `amayama_harvester_down` | warning |
| 15 | watchdog kill burst (`db_update_agent.validate_watchdog_actions`) | **`watchdog_kill_burst`**, default cooldown | warning |
| 16 | watchdog anomaly (bad kill) | `watchdog_anomaly`, 30 min | warning |
| 17 | db_update_agent task failures / recovered | task-set delta; `new` / `changed` / `resolved` | error / warning / success |
| 18 | supplier country problem | `supplier_country_problem` | warning |
| 19 | learned keywords (`db_cleanup_agent`) | `notify_backlog` | info |
| 20 | NOA: post ready, pending posts, weekly brief, plan failed, coherence streak, campaign ready / publish failed, autonomous gate | per post / week / campaign | info / warning |
| 21 | NOA engagement drafts | `noa_engagement_pending_reply`, 1 h (unchanged) | info |
| 22 | NOA daily Facebook report | per day; quiet day = `noa_eod_no_activity` (fingerprint, weekly reminder) | info |
| 23 | Facebook session expired / restored | **`fb_session_expired`**, fingerprint = reason, 3 d reminder, resolved | warning |
| 24 | group scan: discoveries, empty, not run, group comment result | `group_scan_*`; "not run" is skipped while #23 is open | info / warning |
| 25 | supplier candidates (NIR), BOAZ decision, procurement approval | per set / task / order | info / warning |
| 26 | orders: stuck / manual / non-live | signature per order set | warning |
| – | Email Agent: important mail, daily digest, Gmail connection | `email_<id>`, `email_digest_<date>` | info / warning |

Digests that call `_wa_send_update` directly with their own once-per-period marker: daily status digest, night-pipeline
report, weekly harvest digest. Crashed-task alert: sync callback, `critical=True`, bounded by the restart backoff.

**Removed bypasses (2026-11-13):** Facebook re-login alert (`session.py`, raw text + 6 h file cooldown), console group-scan
summary (`group_scanner.py`), campaign WhatsApp tool (`social/tools.py`, called the provider directly, no quiet hours).

**Queue.** Enqueue = one Lua script (dedup by `alert_key` + append + overflow to dead letters). Flush = peek, send, then
remove **that payload** (`_WA_QUIET_REMOVE_LUA`) or update its attempts only while it is still the head
(`_WA_QUIET_REPLACE_HEAD_LUA`); a failed send (exception or `ok:false`) keeps the item. At-least-once.


## 9. Verified inventory of owner-notification call sites (2026-11-24, #152)

Counted with an AST scan (2026-11-25 update: **73 call sites** - three direct `_wa_send_update` report sends became digest events, see §11; the table below shows the 2026-11-24 count of 76 before that change) (`tests/notification_inventory.py`: `notify_owner`, `_alert_owner`, `notify_backlog`,
`resolve_owner_alert`, `_notify`, `_notify_res`, `_wa_send_update`, and the injected `notify` callable) over production
code (tests/devtests/legacy excluded). The earlier "57 sites" and the audit's "69 call lines" were both stale/partial:
the true number is **76 call sites in 12 files**. `tests/test_owner_alert_quality_2026_11.py::test_notification_source_inventory_is_complete`
fails when a call site is added, removed or moved, until the registry in that test AND this table are updated.

| File | Calls | Sources it carries |
|---|---|---|
| `BACKEND_API_ROUTES.py` | notify_owner 28 · _alert_owner 11 · _wa_send_update 8 · resolve_owner_alert 5 | health monitor (service down/restored, errors, disk, OOM, zombie jobs, dead letters, WhatsApp channel), harvest supervisor + FlareSolverr, Meili parity, catalog stagnation, NOA posts/briefs/daily report, orders, digests (daily status, night pipeline, weekly harvest) |
| `BACKEND_AI_AGENTS.py` | _notify 2 | agent-side owner notices |
| `boaz_tasks.py` | notify 1 | BOAZ decision requests (merged with the Email Agent alert for the same mail) |
| `db_cleanup_agent.py` | notify_owner 2 | learned keywords backlog, cleanup alerts |
| `db_update_agent.py` | _notify 4 · _notify_res 1 | night-pipeline task failures / recovered, watchdog anomalies |
| `ebay_api_watch.py` | notify_owner 1 | eBay quota |
| `email_agent/loop.py` | notify 1 | Gmail connection |
| `email_agent/owner_flow.py` | notify 3 · resolve_owner_alert 1 | important mail, digest, stale handoffs |
| `procurement.py` | notify 1 | procurement approvals |
| `social/facebook_browser/group_scanner.py` | notify_owner 1 | group scan report |
| `social/facebook_browser/session.py` | notify_owner 1 · resolve_owner_alert 1 | Facebook session expired / restored |
| `social/noa_ops.py` | _notify 3 · notify_owner 1 | NOA operations alerts |

Direct provider sends (customer chat, 2FA, post-payment, reminder loops) are NOT owner alerts and stay on the reviewed
allowlist in `tests/test_owner_notification_changes.py`.

## 10. One event = one message, sanitised, consolidated, outage-safe (2026-11-24, #152)

**Renderer.** `backend/owner_message_format.py` (pure): `he_count` (Hebrew counts - "משימה אחת", "שתי משימות", "3 משימות"),
`sanitize_body` (tracebacks, SQL, docs URLs, code fences and shell commands never reach the phone; the raw text is logged as
`[owner_error ref=…]`, the message carries a classification + stable reference), `cap_message` (1,200 chars, cut at a line),
`disk_same_storage`, `format_noa_daily`. `notify_owner` applies sanitising and the cap to EVERY alert (single enforcement point).

**Event identity.** `notify_owner(event_keys=[...])`: a report whose key was already reported within 3 days is merged
(`{"suppressed": True, "error": "same_event"}`, logged) and all its keys are recorded as seen. The Email Agent passes
`mail:<ref>` and `thread:<sha1(sender|subject)>`; BOAZ passes `mail:<email_ref>` for tasks the Email Agent filed. A different
subject from the same sender is a different event. Fingerprints stay "what the report says", never counters.

**Incident lifecycle (FlareSolverr example).** warning at start → one escalation to critical after
`HARVESTER_FS_CRITICAL_AFTER_H` (6 h) if still open → reminder every 6 h → one resolved message. The cause is stated as observed.
No alert tells the owner to run a root command (guarded by test).

**Disk.** one stream per storage: two mount points reporting the same total and ±2 GB used are one disk (the container's `st_dev`
differs for `/` and `/app/state`, which is why the old guard let both through).

**Queue (quiet hours unchanged).** Payloads carry `meta={group,state,sev,title}` and `queued_ts`. At window open
`wa_queue_plan.plan_flush` decides per item: `merged` (opened and resolved overnight, nothing critical), `superseded` (a newer
state of the same condition is queued), `digest` (>4 routine info updates → ONE "🌅 עדכוני הלילה" message), `deliver` (critical,
decisions, the rest). Delivery is paced (`WA_FLUSH_PACE_S`, 3 s). Every decision is recorded in `<queue key>:audit` (500, 14 d)
and logged. Legacy payloads without `meta` are never merged.

**Channel outage.** `wa_queue_plan.channel_error_kind`: logged out / awaiting QR / bridge unreachable = `down`. While down no
item spends an attempt and nothing is dead-lettered (the 16-17 Nov outage dead-lettered 9 alerts); the outage start is recorded
in `<queue key>:channel` and logged; immediate critical sends that provably did not leave are kept in the retry queue (72 h);
the dead-letter summary is NOT sent while the channel is down. The first accepted send after an outage sends ONE
"WhatsApp חזר לפעול" message and clears the state. Items older than `WA_QUEUE_MAX_AGE_S` (72 h) are dead-lettered as `expired`.
A crash between send and removal cannot double-send (a per-item marker, cleared after removal).

**Delivery semantics unchanged and stated honestly:** "accepted by WhatsApp" (bridge `Sent OK`) is the strongest signal; delivery
and read receipts are not recorded, no message or log line says otherwise.


## 11. ONE consolidated morning digest (mission 3, 2026-11-25) - `backend/owner_digest.py`

**Rule.** Every ROUTINE owner notification is recorded as a durable event and delivered once a day in a single Hebrew message.
Only `severity="critical"` keeps its immediate path (`_wa_send_update(critical=True)`, quiet-hours bypass, retry queue) - the
exception list is unchanged: nothing was added or removed. Kill switch: `OWNER_DIGEST_ENABLED=0` restores the previous per-alert
path; Redis unavailable -> the alert is sent directly (never lost).

**Reconciliation of the counts (VERIFIED by AST scan).** The old "57 sites" (§8) and the audit's "69 lines" were stale. The
2026-11-24 scan found 76; after this change **73** (the daily-status, problem, job-queue and weekly-harvest report sends became
digest events). Funnel: all 73 owner-alert call sites reach `notify_owner` (-> digest unless critical) or `_owner_digest_report`
(-> digest). The only non-digest owner sends left: critical alerts, the supervised-task crash alert (`critical=True`), the
job-queue FAILED-step alerts (`critical=True`), opt-in admin copies, owner-console replies (conversation), and the customer /
transactional paths of §2 (not owner notifications). Guarded by `tests/test_owner_digest.py::test_no_routine_direct_update_send_outside_the_digest`
and `test_owner_notification_changes.py` (allowlists).

**Schedule.** Health-monitor pass (every 5 min) -> `_owner_digest_pass` -> `Store.deliver`: sends only inside 09:00-21:00 IL and
not before `OWNER_DIGEST_TIME_IL` (default 09:30; the 09:00 status report is generated before it and is a digest event). One
digest per day; an event arriving after it waits for the next morning. Empty store = no message. A digest id is `D<YYYYMMDD>`
(`-2` for a second delivery the same day).

**Event lifecycle** (Redis `autospare:digest:*`, AOF-persistent, no schema change): identity = `alert_key`, else a hash of
category+title with digits removed; `event_keys` aliases (`mail:<ref>`, `thread:<hash>`) join the same event across producers
(Email Agent + BOAZ). Record outcomes: `recorded` / `merged` (same fingerprint: repeat counter only) / `replaced` (different
fingerprint: newest version, state "changed" if already delivered) / `suppressed_unchanged` (delivered within 72 h with the same
fingerprint) / reminder (`state=reminder`, only when `notify_owner` decided it is due) / `resolved` (opened+resolved before
delivery = one "נפתח ונפתר" line; resolution of a delivered event = one done line; nothing open = dropped). Fingerprint = the
caller's, else title/body first line with digits and spacing removed (a counter is never a change).

**Delivery and recovery.** `deliver` claims all pending events under a lock into a FROZEN record (id, rendered parts, attempts),
sends outside the lock (events recorded meanwhile stay pending), and only after every part was accepted writes the per-identity
"seen" memory and the checkpoint `autospare:digest:last`, exactly once. Failure (bridge down, logged out, timeout, a queued
result) keeps the frozen digest, `attempts+1`, exponential backoff 5-60 min, retry of the SAME text resuming at the first unsent
part; a frozen digest that never left and is older than a day is dissolved back into pending so the next one is complete. A crash
after the last part was accepted does not resend (`sent_parts` persisted); a crash inside the single send->persist window can
repeat the SAME digest id once (at-least-once, stated). An outage is never reported through the broken channel: the channel state
(`<queue key>:channel`) is recorded and the first delivered digest carries "WhatsApp היה מנותק מ-…". Events older than
`DIGEST_EVENT_MAX_AGE_S` (14 d) move to the dead list (visible, replayable, no alert).

**Format.** Header (id, count, coverage-since, verdict "N דורשים את פעולתך" or "אין משהו שדורש אותך"), then sections in order:
critical · health · suppliers · mail/NIR/BOAZ · import/prices/catalog · orders · NOA/agents · done/resolved · requires action.
Item = title + first body line (+ typed reply command `▶` and `#ref` for decisions). No traces/SQL/commands (sanitised upstream).
Over 3,500 chars: compact (shorter lines, then 6/4/2 items per section with "ועוד N") before any split; a split is numbered
"חלק i/n" with the shared id.

**Inspect / recover (read-only except replay).** `docker exec -w /app autospare_backend python3 /app/maintenance/owner_digest_cli.py status|pending|dead|preview`;
`replay-dead --yes` re-queues expired events. Metrics: `autospare:digest:stats` (recorded, merged_duplicate, replaced_version,
suppressed_unchanged, resolved_pending, delivery_failed, digests_delivered). A stuck digest = `status.frozen` with `attempts` and `last_error`.


## 11b. Digest delivery semantics (mission 4, 2026-11-25) - stable message id + bridge journal

**What the transport offers (VERIFIED by reading `whatsapp-bridge/index.js` and Baileys 6.x `messages-send.js`):** the bridge had no idempotency
key and no delivery-receipt lookup; `/send` returned `{ok, key}` only. Baileys *does* accept a caller-chosen `messageId` option
(`sendMessage(jid, content, {messageId})`), which becomes the WhatsApp message id.

**Chosen design (strongest the transport supports):**
1. `owner_digest.client_msg_id(digest_id, part, text)` = `3EB0` + 28 hex of SHA-256 - stable for the same (digest, part, text), different for any other text.
2. Write-ahead intent: `deliver` persists `inflight={part,msg_id}` in the frozen record BEFORE each provider call; acknowledgement (`acks[part]` incl. provider id) and `sent_parts` are persisted after it; the checkpoint only after every part.
3. `social/whatsapp_provider.send_message(..., client_msg_id)` forwards it; the bridge's `send_journal.js` (atomic file `send_journal.json`, 7 d / 5,000 entries) answers a retry of an id already recorded `sent` with the ORIGINAL key and `deduplicated:true` WITHOUT calling WhatsApp; an id left `pending` (outcome unknown) is re-sent with the SAME message id; concurrent calls share one send.
4. The outage note is frozen into part 1 on first send, so every retry is byte-identical.

**Guarantees (each proven by a crash-injection test, `tests/test_owner_digest_delivery_crash.py` + `whatsapp-bridge/test_send_journal.mjs`):**
no event is lost at any crash point (pending ∪ frozen ∪ checkpointed always covers every recorded identity); crash before send -> one send after restart;
crash after provider acceptance but before the backend persisted the ack, and crash after the ack but before the checkpoint -> **zero extra provider calls** (journal / persisted `sent_parts`);
crash between parts resumes at the unsent part; a failed/crashed retry and a long outage recover as ONE message, never a storm.

**Residual limitation (stated, not hidden): this is NOT exactly-once.** If the bridge process dies after WhatsApp accepted a message and before `sent` reached disk
(or the journal file is lost), the retry calls WhatsApp again with the SAME message id. Whether WhatsApp drops that duplicate is provider behaviour that cannot be
proven offline (test `test_RESIDUAL_...` pins `wire_sends == 2` honestly; `owner_visible == 1` there ASSUMES provider-side id dedup). Receipts (delivered/read) are still not recorded.
**Compatibility / rollback:** a bridge WITHOUT the journal ignores `client_msg_id` (behaviour = previous at-least-once); a backend without it simply omits the field. The journal needs a bridge
restart to go live (the bridge mounts `./whatsapp-bridge:/app`) - restarting the bridge reconnects the WhatsApp session, so it is a separate owner-approved step. No DB/Redis migration; new Redis fields live inside the existing frozen record.

**Related fixes found while building the representative digest:** (a) the per-alert cooldown in `notify_owner` swallowed a CHANGED report (new fingerprint) inside the cooldown hour - it now lets a changed fingerprint through; (b) repeats suppressed upstream (cooldown / same-event / unchanged) are now counted on the pending event (`Store.note_repeat`, "חזר N פעמים") so duplicate/repeat numbers are real. Evidence: `docs/evidence/2026-11-25-ui-m4/digest_example.txt` (from `tests/test_owner_digest_example.py`).
