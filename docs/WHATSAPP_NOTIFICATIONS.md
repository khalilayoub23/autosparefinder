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
| 21 | NOA engagement drafts | `noa_engagement_pending_reply`, 1 h (N>1); #157: exactly one pending draft -> `noa_engagement_item_<id>`, 24 h, record-level buttons (decision, immediate) | info |
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

Counted with an AST scan (2026-11-26 #158: **76 call sites** - +2 health-loop failing/recovered; 2026-11-26 #157: 74 call sites - +1 single engagement-draft decision alert; 2026-11-25 update: 73 call sites - three direct `_wa_send_update` report sends became digest events, see §11; the table below shows the 2026-11-24 count of 76 before that change) (`tests/notification_inventory.py`: `notify_owner`, `_alert_owner`, `notify_backlog`,
`resolve_owner_alert`, `_notify`, `_notify_res`, `_wa_send_update`, and the injected `notify` callable) over production
code (tests/devtests/legacy excluded). The earlier "57 sites" and the audit's "69 call lines" were both stale/partial:
the true number is **76 call sites in 12 files**. `tests/test_owner_alert_quality_2026_11.py::test_notification_source_inventory_is_complete`
fails when a call site is added, removed or moved, until the registry in that test AND this table are updated.

| File | Calls | Sources it carries |
|---|---|---|
| `BACKEND_API_ROUTES.py` | notify_owner 30 · _alert_owner 11 · _wa_send_update 5 · resolve_owner_alert 6 (2026-11-26 #158 count) | health monitor (incl. its own failing/recovered alert #158, service down/restored, errors, disk, OOM, zombie jobs, dead letters, WhatsApp channel), harvest supervisor + FlareSolverr, Meili parity, catalog stagnation, NOA posts/briefs/daily report, orders, digests (daily status, night pipeline, weekly harvest) |
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

## 12. Interactive owner decisions + group @mentions (mission 6, 2026-11-25) - NOT DEPLOYED, ALL FLAGS OFF

Flags (all default OFF; unset = behaviour unchanged): `WA_INTERACTIVE_ENABLED` (bridge AND backend), `WA_GROUP_INBOUND_ENABLED` (bridge),
`WA_GROUP_COMPOSER_ENABLED` (backend). Code is bind-mounted: an unrelated restart loads it, but with the flags unset the only new live
behaviour is the bridge's inbound parser (button/list responses are forwarded to the webhook, which ignores them while the flag is off).

### 12.1 Compatibility matrix - installed `@whiskeysockets/baileys` **6.17.16** (`whatsapp-bridge/test_interactive.mjs`, 6 proofs)
| Feature | Installed support | Outgoing | Incoming | Evidence / limitation |
|---|---|---|---|---|
| Reply buttons via `sendMessage({buttons})` | **NO - silently degraded to plain text** | n/a | n/a | `generateWAMessageContent` has no buttons/sections/templateButtons path (only `buttonReply`, the *reply* direction); the call returns `extendedTextMessage` with no error. Test 1 asserts it, so a Baileys upgrade that changes it fails loudly. |
| Reply buttons, hand-built | proto supports it (`Message.InteractiveMessage.NativeFlowMessage`) | `relayMessage` of `viewOnceMessage{interactiveMessage{body,footer,nativeFlowMessage{buttons:[quick_reply{display_text,id}]}}}` + `biz/interactive/native_flow` node (`interactive.js`) | `interactiveResponseMessage.nativeFlowResponseMessage.paramsJson -> {id}` | encode/decode round trip of the exact relayed object: PASS. **Device rendering UNVERIFIED** - WhatsApp may not render nativeFlow for a non-Business linked device. |
| List (>3 actions) | same, `single_select` nativeFlow button | `sections[].rows[].id` | same shape; also `listResponseMessage.singleSelectReply.selectedRowId` | round trip PASS; rendering UNVERIFIED |
| Legacy response shapes | parsed | - | `buttonsResponseMessage.selectedButtonId`, `templateButtonReplyMessage.selectedId` | PASS (constructed protos) |
| Mentions | supported | `sendMessage({text, mentions:[jid]})` -> `extendedTextMessage.contextInfo.mentionedJid` | same field | PASS through `proto.Message.encode/decode`. Bridge re-validates every JID against live `groupMetadata`. |
| Group metadata / participants | supported | `GET /groups/<jid>/participants` (new, read-only) | - | verified live read-only: updates group `AutoSpareFinder - עדכונים מערכת` has **2** participants (the platform account + the owner), so autocomplete lists the owner only; agents are NOT group participants. |
| Profile pictures | not implemented | - | - | the composer shows an initial; adding pictures needs a backend image proxy (WhatsApp CDN URLs expire and leak the viewer's IP). |

The plain-text alert (with its typed commands) is ALWAYS sent first; the buttons are an additive second message, so a silent device-side
degradation loses nothing. **Polls stay the live fallback**: the bridge answers 409 `fallback:poll` when the flag is off and the provider then sends the poll exactly as before.

### 12.2 Architecture (`backend/owner_interactions.py`, `owner_interactions_store.py`, PII migration `0043_owner_interactions` NOT APPLIED)
`notify_owner(choices=[(label, typed_command)])` -> `whatsapp_provider.send_message` -> `create_from_choices` (**persist first**) -> bridge `interactive`
payload (button id `asf:<interaction_id>:<action_id>`; the visible Hebrew label is never an identifier; the bridge never sees the commands) -> tap ->
bridge posts `interactive_response` to the webhook -> `resolve_response`: sender must be the owner (`is_owner`, PN or LID; in a group the sender is
`key.participant`; membership grants nothing), interaction known, action offered, correlated to the original WhatsApp message id, not expired, **atomic
conditional-UPDATE consume** (concurrent/double taps execute once) -> the EXACT registered typed command runs through the normal owner console, so each
agent keeps its own gate (`אשר`, `שלחמייל`, ...): a button is never a second authorization path. `details`/`recheck` are read-only (not consumed).
`change`/`edit` with no registered command hold the interaction `awaiting_input`; the owner's NEXT text is bound to it (`take_followup`) and re-opens it for
approval of the revised proposal. Every response and every rejection reason is an `owner_interaction_events` row. Actions: approve אישור, reject דחייה,
change שינוי, edit עריכה, details פרטים נוספים, recheck בדיקה חוזרת, continue המשך, cancel ביטול - only those backed by a registered command are offered.

### 12.3 Agent coverage (verified by grep, not assumed) - SUPERSEDED by 13.3 (#157)
| Agent / workflow | Owner request today | Buttons |
|---|---|---|
| NOA - post approval (`BACKEND_API_ROUTES.py` ~5788, `post_choices`) | typed `אשר/ערוך/דחה <id>` + poll | **YES** approve / edit / reject |
| Email Agent (`email_agent/owner_flow.py:352`, `email_choices`) | typed `מייל/שלחמייל/סגורמייל <ref>` + poll | **YES** details / approve (send the one draft) / cancel |
| NOA - engagement drafts (`inbox_choices`) | `inbox_choices` is **defined but has no caller**; the alert is an aggregate "N drafts" | NO |
| NIR - sourcing candidate (`_supplier_sourcing_loop`, `supplier_candidate_choices`) | `אשרספק/דחהספק <id>`, `ספקים` | **YES, only when the alert names exactly ONE candidate** (approval must authorize what is displayed); several candidates = aggregate list, no buttons |
| BOAZ - escalation about a supplier mail (`boaz_tasks._alert`, `boaz_task_choices`) | read `מייל <ref>`, list `משימות בועז` | **YES, read-only** (details / recheck). Purchase-order and supplier-payment approval (`אשררכש`, `אשרתשלוםספק`) are deliberately NOT buttons: there is no proactive owner alert for them to attach to, and a commercial commitment needs its own owner decision + terms-hash gate |
| Marketing / advertising (campaign plan + budget) | budget is confirmed through the **admin API** (`routes/admin.py` budget confirmation), no WhatsApp owner command exists | **NO - no applicable WhatsApp decision point.** A button would need a NEW owner command bound to the exact plan hash and amount; not invented here |
| Verification workflows (category keywords `אשרמילה`, Facebook session) | daily batch of many words / cookie import by the owner | **NO** - batch alerts have no single record; keyword approval has its own evidence gate (`בכוח`) |
| AVI | owner console chat + deterministic read-only commands; `סטטוס` already maps to `recheck` | NO new decision point - AVI has no approvals of its own; granting it any would be a new permission |
| NOA engagement drafts | aggregate "N drafts" alert; `inbox_choices` has no caller | **NO** (no per-draft alert) |
Existing supplier policies are untouched; a button maps to the same typed command, so `supplier_authorization` one-shot/terms-hash rules still decide.

### 12.4 Group mentions
Composer: Admin -> social tab (`components/Admin/GroupMentionComposer.jsx`, logic `mentionLogic.js`, 4 vitest). `GET /api/v1/admin/whatsapp/group-members` (live
metadata, 60 s cache, refresh button; opaque HMAC tokens, **no phone numbers/JIDs to the UI**, own account excluded) and `POST .../group-message`
(`routes/wa_group.py`): `@{token}` -> `@<digits>` + `mentions=[jid]`, bridge re-validates against the group; unknown/removed participant = 400.
Group JID = `WA_COMPOSER_GROUP_JID` if set, else the configured updates group (`_updates_group_jid`), never guessed. **Live state 2026-11-25 (read-only): the bridge reports ONE group, `AutoSpareFinder — עדכונים מערכת` (`120363428683447639@g.us`, size 2) but `owner:updates_group_jid` is UNSET (Redis + env), so the composer answers 409 until the owner either sends `קבוצת עדכונים 1` (this also moves routine alerts to that group) or sets `WA_COMPOSER_GROUP_JID` (composer only).** Participant names could not be read live: the running bridge does not yet have the new participants endpoint. Inbound (needs `WA_GROUP_INBOUND_ENABLED=1`): only messages that mention
THIS account are forwarded; the backend answers only the owner's JID, once per message id, routes `נועה/noa ...` to NOA else AVI via the existing console,
and **refuses approval/consequential verbs from a group** (a mention is a routing signal, never an approval); the bridge drops our own messages (no loops).
Keyboard: arrows/Enter/Tab/Escape, combobox/listbox ARIA, 44px rows, RTL.

### 12.5 Notification policy
Unchanged: routine alerts -> the one 09:30 digest; owner decision requests are immediate - **fixed in this mission**: before it, `notify_owner` digested every non-critical alert including those with `choices`, so a decision would have waited for 09:30. With `WA_INTERACTIVE_ENABLED=1` an alert with `choices` skips the digest and goes to the owner 1:1 (buttons/polls are honoured only there, even when an updates group exists) through `_wa_send_quiet` (quiet hours still queue it to 09:00). Flag off = unchanged. Test: `tests/test_owner_interactive_digest_coexistence.py`; critical set and
09:00-21:00 quiet hours untouched; no polls reintroduced as the new path; FlareSolverr untouched.

### 12.6 Deploy (separate owner approval) and rollback
1. `pre_restart.sh` SAFE preflight + guarded restart rules (CLAUDE.md section 2). 2. PII `alembic upgrade 0043_owner_interactions` (additive). 3. Bridge restart
(reconnects WhatsApp; separate approval). 4. Set `WA_INTERACTIVE_ENABLED=1` ONLY AFTER the owner confirms on a real phone that a test button renders and returns
the id (the one missing evidence). 5. Optional `WA_GROUP_INBOUND_ENABLED=1`, `WA_GROUP_COMPOSER_ENABLED=1`; frontend rebuild for the composer.
Rollback: unset the flags (instant: back to text+poll, no restart needed for the backend flag read per call; the bridge reads its flag at start), then
`alembic downgrade 0042_supplier_lifecycle` (drops only the two new tables).

### 12.7 Real-device verification (the one missing evidence) - `maintenance/wa_interactive_selftest.py`
Dry run by default (prints the 3 actions אישור/דחייה/שינוי, sends nothing). Sending needs `--send --owner-approved <ref>`, `WA_INTERACTIVE_ENABLED=1`, migration 0043 and the new bridge build. The
commands are inert (`בדיקתאישור|בדיקתדחייה|בדיקתשינוי <ref>` answer with a fixed acknowledgement, no LLM, no agent action). After the owner taps one button: `--verify <interaction_id>` prints the audit trail
(created -> response, consumed action, WhatsApp message id). Pass = the right action id is recorded once and a second tap is refused.
**Known limits (#156) - FIXED in #157, see section 13.1:** the follow-up went to the generic console agent instead of the originating agent.

## 13. Resume Interaction, group mentions completed, full decision coverage (mission 7, #157, 2026-11-26)

### 13.1 Resume Interaction (Change / Edit / More info return to the ORIGINATING agent and task)
Defect fixed: in #156 an Edit tap consumed the interaction and ran `ערוך <id>` (which only displays the text); a Change follow-up
was handed to the generic console LLM with a context prefix. The originating agent never received the owner's change request.

Lifecycle (`owner_interactions.py`, `owner_interaction_flow.py`, `agents/owner_continuations.py`, PII table `owner_interactions`):
1. A button whose command verb is a **continuation verb** (`RESUMABLE` = `ערוך`, `שנהפוסט`, `ערוךתשובה`, `ערוךמייל`, `שאלספק`, `בדיקתשינוי`)
   is NOT consumed by the tap. The tap runs the verb with only its ref (a read-only prompt that shows the current proposal), sends it with a
   hint, and moves the row `open -> awaiting_input` with a persisted `continuation = {verb, ref, agent, action}`, `followup_expires_at`
   (`OWNER_FOLLOWUP_TTL_S`, 30 min) and `prompt_message_id` (the WhatsApp id of the prompt).
2. The owner's NEXT private text (`bind_followup`): a console command (`סטטוס`, `מיילים`, `אשר ...`, `@נועה ...`, any registered verb) passes through
   untouched; `בטל` cancels (`awaiting_input -> open`, buttons usable again); a quote-reply to the prompt (or the original buttons) binds exactly
   that interaction; exactly one pending request binds; **two or more without a quote = nothing is executed, the owner is asked to quote**.
3. Binding is an atomic `awaiting_input -> completed` UPDATE (a redelivered message resumes once) and yields `<verb> <ref> <text>`, which the
   **originating agent's own handler** runs (`agents/owner_continuations.py`), the same code a typed command reaches:
   | verb | agent | what the text does | never does |
   |---|---|---|---|
   | `ערוך <post> <text>` | NOA | owner's exact new text -> `update_post_content` (version bump, back to pending) | publish |
   | `שנהפוסט <post> <request>` | NOA | NOA rewrites from her owner-mode prompt + saved guidelines + the request, through `_finalize_noa_post` (G8 guards), attributed `actor=noa` | publish, invent prices |
   | `ערוךתשובה <item> <text>` | NOA | replaces the engagement reply DRAFT | send it |
   | `ערוךמייל <ref> <text>` | Email Agent | `owner_flow.revise_draft`: new Gmail draft (POST /drafts, allowlisted) in the same thread, same recipient + subject, read back and verified, then `replace_draft` swaps the row (conditional on the shown draft still being current; clears any earlier send approval). Refused when halted / sent / closed. The superseded draft stays in Gmail as a draft (no delete endpoint is allowed). | send (`שלחמייל` only) |
   | `שאלספק <supplier> <question>` | NIR | answers from the candidate's stored record only (`supplier_sourcing.candidate_record`, secrets stripped); says when it does not know | activate, contact, order |
   | `בדיקתשינוי <ref> <text>` | self-test | inert acknowledgement | anything |
4. The revised proposal is returned with a choice trailer, so the WhatsApp chokepoint creates a **new interaction** whose idempotency key includes
   the hash of the displayed text, and **supersedes** (expires) every older open interaction of the same agent + entity: a tap on a v1 message
   can never approve v2. If the agent fails, the interaction is released (`completed -> open`) and the owner is told the request is still open.
5. Group taps, non-owner taps and group text never bind or resume. An approval / commercial verb is never resumable (test pins it).
Persistence: everything above is in Postgres; a backend restart between the tap and the text resumes correctly (real-Postgres test).

### 13.2 Group participants, autocomplete and mentions
* Bridge `GET /groups/:jid/participants` returns `{subject, size, participants:[{jid, lid, phone, admin, name, is_me}]}` from live `groupMetadata`
  (`interactive.shapeParticipants`). WhatsApp group metadata carries no contact names: names are never guessed; the backend labels the owner
  from the configured owner phone/LID, everyone else "משתתף N"; our own account (`is_me`) is excluded.
* The ONE group: `WA_COMPOSER_GROUP_JID` (set to `120363428683447639@g.us`), else the updates group. Its LIVE subject must contain
  `WA_GROUP_EXPECTED_SUBJECT` ("AutoSpareFinder") or the API answers 409 - a wrong JID is never used silently. The updates-group routing of
  routine alerts (`owner:updates_group_jid`) is NOT changed by this.
* Cache 60 s; the bridge posts `group_update` on `group-participants.update` and the backend drops the cache; a send always re-reads live
  metadata and the bridge re-validates every mention JID (a removed participant = 400, cache dropped, composer refreshes).
* Bridge down -> 503 (not cached); the composer shows the reason (no group / wrong group / not connected).
* Inbound (`WA_GROUP_INBOUND_ENABLED`): only messages that mention THIS account, only in the configured group, only from the owner's JID (PN or
  LID); conversation only - any console command, approval, revision, continuation or work trigger (`עצור`, `הרץ`, `מקורות` ...) is refused with
  a pointer to the private chat. The reply is generated in the background. Group messages and taps never reach the customer chat path, flags on or off.

### 13.3 Owner decision coverage (evidence: grep of every `notify_owner` producer, 2026-11-26)
"Immediate" = bypasses the 09:30 digest (an alert with a decision action, or `decision=True`); quiet hours 09:00-21:00 still apply; critical unchanged.
| Workflow / decision point | Existing behaviour | Owner interaction now | Status | Test |
|---|---|---|---|---|
| NOA post ready (`BACKEND_API_ROUTES` NOA loop, `post_choices`) | typed אשר/ערוך/דחה + buttons | approve / **NOA change (resume)** / **edit (resume)** / reject; buttons bound to the displayed version | DONE | test_owner_resume (NOA change/edit, supersede) |
| NOA engagement reply drafts (`_noa_engagement_loop`) | aggregate "N drafts" (inbox_choices had no caller) | exactly ONE pending draft: shown in full + send / **edit draft (resume)** / skip; N>1: aggregate (no batch approval) | DONE | test_has_decision; test_engagement_edit_replaces_the_draft_and_never_sends |
| NOA campaign ready (`BACKEND_AI_AGENTS` GAP-A) | text only, routine | decision (immediate) + read-only "pending posts" list; each post approved on its own | DONE | coexistence test (decision flag) |
| NOA FB group-scan summary, EOD report, no-activity, week-plan failed | informational | none - no decision (drafts are approved per draft with `אשרתגובה`) | NO DECISION | - |
| Email Agent important mail (`owner_flow.process_owner_updates`) | details / send / close | + **edit draft (resume, new verified Gmail draft)** | DONE | test_email_edit_* |
| NIR sourcing candidate (`_supplier_sourcing_loop`) | one named candidate: approve / reject / list | approve / reject / **ask NIR (resume, read-only)** | DONE | test_nir_question_* |
| BOAZ escalation (`boaz_tasks._alert`) | read-only buttons, routine digest | read-only (mail, task list), now `decision=True` (immediate) | DONE | coexistence test |
| BOAZ procurement after a customer payment (`procurement.gate_paid_order`) | text, routine (would wait for 09:30) | `decision=True` (immediate) + read-only "רכש" details; **approving a PO / supplier payment stays typed** `אשררכש` / `אשרתשלוםספק` after reading the exact terms | DONE | test_choice_sets_are_record_bound |
| Category keyword (verification, `db_cleanup_agent`) | daily batch text | exactly ONE new word: approve / reject / list (approve runs `אשרמילה <word>`, the evidence gate still decides); batch / reminder: digest, never a batch approval | DONE | test_has_decision |
| Facebook session / FB group targets | owner action outside WhatsApp (cookie import) / `אשרגרופ` typed from the list | none: no record-level alert exists for group targets; cookie import is a server action | NO DECISION | - |
| Marketing / advertising budget | admin API budget confirmation (`routes/admin.py`) | none in WhatsApp: no owner command exists; a budget button would be a new permission bound to an amount | NO DECISION (by design) | - |
| AVI | conversational console; no approvals of its own | none (a button would be a new permission) | NO DECISION | - |
| Health / harvest / orders monitor alerts | informational; manual-handling orders need work outside the system | none | NO DECISION | - |
| Supplier outreach standing approval (`אשרפניותספקים`) | typed, no proactive alert | unchanged (commercial standing permission stays typed) | NO DECISION | - |

### 13.4 Other defects fixed in #157
* `notify_owner` capped the message (1200 chars) AFTER attaching the choice trailer - every long NOA post alert lost its buttons and poll. Cap first, then attach (test).
* Read-only list buttons made a routine alert "immediate" (any `choices`); now only a decision action or `decision=True` does (test).
* With `WA_GROUP_INBOUND_ENABLED=1` and the backend interactive flag off, a group mention fell through to the CUSTOMER chat path as sender "group"; a tap with the flag off did the same with an empty body. Both are dropped before the customer path now (webhook tests with a DB that fails on any use).
* A group mention reused the owner's PRIVATE console history in its prompt (and could repeat private turns into the group), extended that history and moved the private chat's sticky agent; NOA directives from the group were saved as standing guidelines. Group conversations now never read/write private history, never change the sticky agent and never save guidelines (test).
* The bridge's `group_update` (empty body) was dropped by the webhook's empty-message guard before cache invalidation; let through (test asserts the cache is dropped).
* The group-mention reply blocked the webhook for up to 120 s (bridge timeout 90 s): now background with a strong reference.

### 13.5 Configuration (all default OFF in docker-compose; production values live in `.env`)
backend: `WA_INTERACTIVE_ENABLED`, `WA_GROUP_INBOUND_ENABLED`, `WA_GROUP_COMPOSER_ENABLED`, `WA_COMPOSER_GROUP_JID`, `WA_GROUP_EXPECTED_SUBJECT`, `OWNER_FOLLOWUP_TTL_S`;
bridge: `WA_INTERACTIVE_ENABLED`, `WA_GROUP_INBOUND_ENABLED` (bridge reads them at start). Migration: PII `0043_owner_interactions` (amended
before its first production apply: continuation columns + `completed` state).

## 14. Mission 8 (#158, 2026-11-26) - group sends, `@` mentions, conversation quality, presentation

### 14.1 What `@` autocomplete is (and is not)
* The `@` participant menu inside the **WhatsApp app** is WhatsApp's own client UI. Nothing on our side can make it open or
  change it. It appears only in a **group** chat, never in the 1:1 chat with the bot.
* The autocomplete we own is the admin page composer (`frontend/src/components/Admin/GroupMentionComposer.jsx`, Admin page,
  tab *social* - `Admin.jsx`): `@` opens the live member list from `GET /admin/whatsapp/group-members` (bridge
  `/groups/<jid>/participants`, our own account excluded), filters as you type, keyboard/mouse select, sends a real mention
  JID validated by the bridge against the live group metadata.
* The updates group has **2 members** (owner + the bot account, both LID-only). So the app's `@` menu in that group can only
  ever offer the bot, and the admin composer can only offer the owner.

### 14.2 Group sends and LID addressing
Every send to the group failed `not-acceptable` because the installed Baileys build (6.17.16, a published old-code release that `^6.7.0` resolved to) encoded member devices of a
LID-addressed group as phone JIDs. Pinned `@whiskeysockets/baileys@6.7.24` (sets `addressing_mode`, encodes devices by the
group's addressing mode), loaded via ESM `import * as baileys`. Rollback: restore the pre-#158 `node_modules`
snapshot + `package.json`/`package-lock.json` and recreate the bridge (guarded).

### 14.3 Non-chat JIDs
`whatsapp-bridge/jid_policy.js`: `status@broadcast`, `*@broadcast`, `*@newsletter` are dropped on inbound (before any
routing) and refused by `/send` (400, not retryable). Contacts' Statuses used to reach the customer brain and get answered.

### 14.4 Decision buttons and follow-ups
* Button label = the producer's own label when it fits 20 chars (`owner_interactions.button_label`), else the vocabulary.
* The buttons message names its subject (`buttons_body`, the alert title) - two pending decisions are distinguishable.
* `change` = the agent rewrites from your notes (`🔁 NOA תשכתב` / `שינוי לפי הערות`); `edit` = your own text replaces it
  word for word (`✏️ אכתוב נוסח משלי` / `נוסח משלי`).
* Only the BUTTON follow-up of `edit` is checked (`notes_verdict`): strong evidence of notes (talks about notes/wording,
  or a short instruction) -> NOA rewrites and says so; weak evidence (price/link/hashtags gone) -> nothing changes, both
  commands are offered; otherwise it is the post. A TYPED `ערוך <id> <text>` is always word for word.
* A post whose latest revision is owner notes that arrived via WhatsApp is not published (`_approve_and_publish`);
  `אשר <id> בכוח` overrides. Admin-panel edits (`source=web`) are never blocked.

### 14.5 Owner console conversation rules
Shared voice block: continuity (short follow-ups refer to the last topic, never re-ask the last question, never restart),
intent classes (question / change / approval-execution / complaint), name `חליל` (never `החליל`, also fixed in
`_clean_wa_reply`), money: nobody pays or orders from a chat sentence - deterministic reply with `רכש` / `אשררכש` /
`אשרתשלוםספק`. Button continuations are written into the console history (`record_exchange`) so the next turn knows what
just happened. History keeps 20 messages, the prompt sees 12.

### 14.6 Notification policy - unchanged
Routine -> 09:30 IL digest; decisions immediate (1:1); critical immediate; quiet hours 09:00-21:00 IL. #158 also restores
the health monitor itself (it had been failing every cycle since the 08:47 deploy).

### 14.7 Delivery evidence and deploy order
* `whatsapp-bridge/receipt_log.js`: `[ConnLog] RECEIPT scope=group|dm kind=delivery|read|played id=<last 6 of our message id>`
  (from `messages.update` / `message-receipt.update`) and `RETRY_RECV scope=... id=...` (raw retry receipt = the recipient could
  not decrypt). No JID, participant or text. "Sent OK" is only the server ack; a group message counts as delivered only with a
  `RECEIPT scope=group` for its id and no `RETRY_RECV`.
* Bridge dependency change order (the container runs `npm install` at start; the new lock fetches libsignal from git, which
  `node:20-alpine` lacks): prove `npm install` is a no-op offline -> preflight SAFE -> snapshots -> guarded stop -> swap
  node_modules + package files -> guarded start. Rollback: restore node_modules + package files, KEEP the live `auth_info`.
