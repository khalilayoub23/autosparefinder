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
