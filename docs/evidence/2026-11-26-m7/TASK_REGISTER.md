# Mission 7 task register (#157) - continues 1194686 (#156)

Status values: TODO / DOING / PASS / BLOCKED / FAIL.

## WS1 - Resume Interaction (Change/Edit continuation)
| ID | Item | Acceptance | Verification |
|---|---|---|---|
| R1 | Migration 0044: `owner_interactions` + `continuation jsonb`, `followup_expires_at`, `prompt_message_id`; state `completed` | additive, downgrade drops only the new columns | alembic upgrade/downgrade in sandbox PG |
| R2 | Resumable action registry: verb -> continuation command (`ערוך`->NOA exact text, `שנהפוסט`->NOA rewrite, `ערוךמייל`->Email draft, `שאלספק`->NIR question, `ערוךתשובה`->NOA engagement reply, `בדיקתשינוי`->selftest) | approval verbs can never be resumable | unit test |
| R3 | Tap on change/edit/details-with-question -> run the read-only prompt command, persist `awaiting_input` + continuation context, record prompt msg id | persisted before reply | PG test |
| R4 | Next owner text -> bind: quote-reply to prompt wins; else exactly one awaiting -> bind; >1 -> ambiguous reply, nothing executed; console commands / `בטל` bypass or cancel | no wrong binding | tests (2 simultaneous, ambiguous, cancel, command bypass) |
| R5 | Bound text -> atomic claim (awaiting_input -> completed) -> continuation command `<verb> <ref> <text>` through the owning agent's console handler -> revised proposal re-sent with NEW buttons; old interaction dead | double delivery runs once | concurrency test |
| R6 | NOA `שנהפוסט <id> <instructions>`: NOA (LLM, NOA system prompt + guidelines) rewrites, `update_post_content` (version bump, back to pending) | stale approval cannot publish | test with injected LLM |
| R7 | Email `ערוךמייל <ref> <text>`: new Gmail draft in the same thread (POST /drafts, allowlisted), read-back verify, store swaps `gmail_draft_id`/`draft_body`; send stays `שלחמייל` only | no send; verification recorded | test with fake Gmail client |
| R8 | NIR `שאלספק <id> <question>`: NIR answers from the candidate record only (read-only) | no state change | test with injected LLM |
| R9 | Expiry: interaction TTL + follow-up window (`OWNER_FOLLOWUP_TTL_S`, 30 min) | expired -> normal routing, owner told | test |
| R10 | Persistence across store re-instantiation / restart | new store instance binds | PG test + live |
| R11 | Unauthorized: non-owner / group text never binds or resumes | rejected + audited | test |

## WS2 - Group mentions + autocomplete
| ID | Item | Acceptance | Verification |
|---|---|---|---|
| G1 | Bridge `GET /groups/:jid/participants` live | returns real participants | live call after bridge restart |
| G2 | Group identity check: subject must be the AUTOSPAREFINDER group; configured JID `120363428683447639@g.us` via `WA_COMPOSER_GROUP_JID` | wrong subject -> 409 | test + live |
| G3 | Cache invalidation on `group-participants.update` (bridge -> backend) + TTL | stale cache dropped | test |
| G4 | Autocomplete (frontend) uses real tokens; selection inserts `@{token}` | vitest + live API | |
| G5 | Outgoing mention payload `mentions=[jid]` validated against live metadata | invalid/removed -> 400 | test + live send |
| G6 | Inbound mention parsing + routing (separate `WA_GROUP_INBOUND_ENABLED` gate in backend; group traffic never reaches the customer path) | approvals refused | tests |
| G7 | Behaviour after bridge re-instantiation | endpoints work after restart | live |

## WS3 - Decision coverage
| ID | Item |
|---|---|
| D1 | NOA post: approve / change / edit / reject (+ continuation) |
| D2 | NOA engagement drafts: per-record buttons when one draft (send / edit reply / skip) |
| D3 | Email: details / send / edit (continuation) / close |
| D4 | NIR candidate: approve / reject / ask (continuation) |
| D5 | BOAZ escalation: read-only; procurement alert: read-only details (commercial approvals stay typed) |
| D6 | Keywords: single new word -> approve/reject; batch -> list button |
| D7 | FB group comment drafts summary: details button |
| D8 | Campaign-ready alert: details button (posts list); budget = admin API (no WhatsApp decision) |
| D9 | AVI / health / harvest / orders alerts: no-decision classification with evidence |
| D10 | Coverage matrix in docs |

## X - Deploy, verify, close
| ID | Item |
|---|---|
| X1 | Tests: focused + bridge + frontend + full backend vs baseline 1194686 |
| X2 | Preflight + migration 0044 (and 0043) on PII |
| X3 | Guarded backend recreate + bridge recreate (flags) + frontend build/recreate |
| X4 | Live: selftest buttons to owner, tap, duplicate refused, audit |
| X5 | Live: change/edit continuation reaches original agent |
| X6 | Live: group participants, autocomplete API, mention send |
| X7 | Live: digest / immediate decisions / critical unchanged |
| X8 | Rollback readiness |
| X9 | Docs + FIXES_TRACKER + commit |
