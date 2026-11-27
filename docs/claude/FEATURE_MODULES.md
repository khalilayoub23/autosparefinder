# Feature Modules (moved verbatim from CLAUDE.md §9 on 2026-11-27, FIXES_TRACKER #160)

> Read the matching block BEFORE touching: categorization/category_map/LLM keyword learning; warranty_policy;
> the local embedding model; dept-* marketing skills / gstack design skills / SHIRA-NOA prompt grounding;
> the Partner API (`/api/public/v1`); NOA social engagement (`social/engagement.py`); NIR supplier sourcing;
> part thumbnails / S3 / images. CLAUDE.md §9 keeps the hard rules and the triggers.
> Content below is byte-identical to the former CLAUDE.md §9 body, except one corrected CPU-count fact (marked).

<!-- BEGIN VERBATIM -->


**`backend/category_map.py` is the single source of truth for part categorization.**
If you add a keyword, add it THERE — nowhere else. `categories.py` is a deprecated
re-export shim. An AST check enforces that no other file defines a module-level
category rule set.

**One storable vocabulary.** `parts_catalog.category` holds ENGLISH SLUGS
(`brakes`, `body-exterior`) plus `כללי`. `CANONICAL` is DERIVED from
`part_type_taxonomy` family ids so the two cannot drift. Hebrew/Arabic names are
**display-only** (`category_map.DISPLAY`) and must never be written to the column.
Before the merge, three vocabularies were in play and `normalize_categories` was
validating a Hebrew-name mapping against an English-label set — so nearly every
mapping branch was silently discarded and parts only ever flowed INTO `כללי`.

**Matching is LONGEST-KEYWORD-WINS**, never declaration order. Specificity lives in
the keyword itself, which is what makes it safe for the RULES list to carry bare
head-nouns (`brake`, `door`, `belt`) as a lowest-precedence tier. Short RTL keywords
(≤3 chars) match with a boundary pattern that permits one Hebrew proclitic
(ה ו ב ל מ ש כ) before the word but forbids another RTL letter after it.

**The only fallback is `כללי`.** Never return `general`, `service-general`,
`accessories` or `tools-equipment` as a default — they are real categories reachable
only by a genuine match. Every importer calls `categorize_on_ingest()`; none may
hard-code a fallback. Bare fasteners (`bolt`/`screw`/`בורג`) deliberately stay in
`כללי` — a wrong category is worse than the catch-all, and in context
(`"Bolt Cylinder Head"`) they still classify correctly.

### LLM assist — a helper for a STUCK worker, not the worker (owner rule)

The deterministic keyword matcher does the work. The LLM is only consulted about
parts it genuinely could not place, and **everything the LLM answers is mined back
into permanent keyword rules**, so the matcher handles that shape of name unaided
next time and LLM demand SHRINKS instead of being constant.

| Layer | Role |
|---|---|
| `category_map` RULES | Deterministic, free. Handles ~74% of the backlog alone. |
| `db_cleanup_agent.task3b` | Asks the LLM about a small, budgeted sample of genuinely-stuck rows. |
| `category_learning.mine_tokens` | Extracts the token that predicted the category. |
| `category_learned_keywords` | One row per (token, category) VOTE. Consensus accumulates ACROSS calls. |
| `category_map.register_learned_keywords` | Activates a token once it clears the gates. |

**OWNER APPROVAL GATE (added 2026-07-27).** A keyword the LLM proposes does **not**
go live on its own — one token can move thousands of parts (`bolt` matches 16,541),
so it waits for the owner exactly like NOA's post drafts. Lifecycle:
`pending` → owner approves → `approved` (loaded + bulk-applied) or `rejected`
(never loaded, never re-proposed; the rejected set is rehydrated at startup).
WhatsApp console: **`מילים`** lists what's waiting · **`אשרמילה <word>`** approves ·
**`דחהמילה <word>`** rejects. The owner is pinged once/day via `_wa_send_quiet`
(quiet-hours-safe) when tokens are waiting. If the owner is away, learning pauses —
the correct failure mode for something that can mis-file 16,000 parts.

**EVIDENCE GATE + REVERSIBILITY (owner requirement, 2026-08-02).** The owner's
standing rule is *"fix the categories BEFORE the backfill so I don't get wrong
parts sitting in wrong categories."* Two mechanisms serve it:

1. **`category_learning.evidence_profile()`** — before a keyword can activate, it
   is checked against parts **already filed in a real category**, which is
   evidence independent of the votes. Consensus alone is not enough: it measures
   agreement *among votes cast on catch-all parts*, so it reached 96-99% on
   `קופסת`→gearbox (really storage/relay/control boxes) and `note`→service
   (really "Low Note Horn"). Two tests: **MISMATCH** (proposal isn't the
   evidence's top category) and **MARGIN** (top must beat runner-up ≥1.8×).
   Enforced in `approve()`, not just the display. Owner override:
   `אשרמילה <word> בכוח`, with the real spread shown first.
   *Absolute share was tried first and was WRONG* — it blocked `lens` (48.6%
   lighting), a good rule, because a part name has many words and its stored
   category reflects whichever word won.
2. **Provenance → exact undo.** `_bulk_apply_new_keywords` stamps
   `specifications.category_by` (the keyword) and `category_prev` (where the
   part came from). `undo_keyword()` reverses exactly those rows, and `reject()`
   calls it — so rejecting a live keyword undoes its effect instead of merely
   stopping future ones. **A gate reduces the chance of a bad rule; provenance
   makes one survivable.** One keyword moves thousands of rows (`מים` matches
   1,277), so "we'll untangle it later" is not a plan.

**NEVER-LEARN BLOCKLIST — enforced at THREE layers.** Four classes may never become a
rule: **brand names** (164 entries loaded LIVE from `car_brands` +
`parts_catalog.manufacturer`, split on spaces AND hyphens so "Mercedes-Benz" blocks
`mercedes` and `benz`), **bare fasteners** (`bolt`/`washer`/`screw`/`בורג`/`shim`/
`ring`/`nut`), **position words** (`front`/`rear`/`קד`/`אח`/`ימין` — they say *where*
a part sits, not *what* it is), and **size/quantity codes** (anything with a digit,
`xxl`, `pcs`). Enforced in `mine_tokens()` (producer), `load_into_matcher()`
(consumer) **and** `purge_blocklisted()` at startup — because filtering only on write
does not protect rows written before the filter existed (that hole went live on
2026-07-27: `bolt`/`washer` activated as `service-general` and `rover`/`land` were one
vote from mis-filing ~26,000 Land Rover parts).

**THROUGHPUT — optimise the keyword, not the part.** Per-part LLM classification of
the 403,327-part backlog would take **107 days** (25/call × 150 calls/day). Token
analysis of the real stuck population shows the **top 400 unknown tokens cover 73.6%
(~297,000 parts)**. So task3b samples the parts containing the most FREQUENT unknown
token (every call teaches a high-leverage rule), and `_bulk_apply_new_keywords()`
re-runs the matcher over the whole catch-all the moment a keyword is approved — one
keyword fixes thousands of parts at once, not the 25 that taught it.

**Guarantees (all verified live 2026-07-27):**
1. A learned keyword **can never override a hand-written rule** — `_covered_by_handwritten`
   rejects it, and on an equal-length tie the stable sort keeps hand-written first.
   (Proved: 4 votes for `brake→cooling` left `"brake pad" → brakes` intact.)
2. A token activates only at **`MIN_CONSENSUS = 3` cumulative observations** with
   **`MIN_AGREEMENT = 0.8`**. Consensus must accumulate across calls — a 25-part
   batch almost never repeats a token, so gating inside one batch would learn nothing.
   (Proved: activated on the 3rd batch, not the 1st or 2nd.)
3. Conflicting votes never activate. (Proved: engine/brakes/lighting ×1 each → stays `כללי`.)
4. The LLM may only choose from `CANONICAL`; `_VALID_CATEGORIES` is derived from it.

**Budget — why the 2026-07-27 quota blowout cannot recur.** That incident was not
"the LLM is expensive", it was an unbounded caller: `batch_size=500` inside a loop
ticking every 30s, with nothing ever learned, so demand was constant and infinite.
Three independent limits now make that shape impossible (`docker-compose.yml`):
`CLEANUP_LLM_BATCH=25` (never 500) · `CLEANUP_LLM_MIN_INTERVAL_S=180` ·
`CLEANUP_LLM_DAILY_MAX_CALLS=150`, plus provider-failure backoff. **Any new
background task that calls an LLM must have all three: a small batch, a minimum
interval, and a daily ceiling — and should feed its output back into a
deterministic rule so the LLM is needed less over time, not forever.**




**`backend/warranty_policy.py` is the single source of truth for part warranty**,
the same way `_customer_price_fields` is for price. Never re-implement the default
or the parsing in an importer.

- **Storage:** `supplier_parts.warranty_months` (+ `warranty_source`). Warranty
  belongs to the SUPPLIER OFFER, not the part — two suppliers can warrant the
  same part differently. Already returned by the search/compare API.
- **`resolve(*candidates) -> (months, source)`** takes whatever fields a source
  offers (numeric or free text, priority order) and always returns a usable
  value. `parse_months()` handles the real catalog forms —
  `אחריות לשנתיים כולל עבודה`→24, `ל 6 חודשים או 10000 ק"מ`→6, `24חודשים`→24,
  `12 months / 100,000 km`→12, and the misspelled `חריות לשנה`→12. Anything it
  does not recognise returns None rather than a guess.
- **The default is EVIDENCE-BASED:** 12 months is the dominant real value in our
  own catalog (2,919,823 of 3,734,378 populated rows, 78.2%). Override with
  `PLATFORM_DEFAULT_WARRANTY_MONTHS`. It is a PLATFORM policy (we are the
  seller), not a claim about the supplier.
- **Provenance is mandatory.** `warranty_source` is `'platform_default'` when we
  applied our own default and `'supplier'`/NULL when the figure came from the
  source. **NULL means legacy supplier data** (3.7M rows predate the column and
  were deliberately not rewritten). **Always test with
  `warranty_policy.is_supplier_stated()`, never `== 'supplier'`** — the bare
  comparison silently treats every legacy row as non-supplier. A surface that
  says "supplier warranty" MUST check this, or it presents our default as the
  manufacturer's promise.
- **Captured at source:** `catalog_scraper.py` (REX) now calls `resolve()` on
  every `supplier_parts` insert. It previously wrote no warranty at all, which
  made "Official Manufacturer Sites" the single biggest gap (293,263 rows).
- Coverage went 88.9% → **100.00%** (4,158,026 rows): 36,136 derived from real
  source data, 423,648 platform default. Any NEW importer must call `resolve()`.




`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (117.7M params,
Apache-2.0) runs LOCALLY via **ONNX Runtime, not PyTorch** — torch adds ~800MB to
the image and ~1.5GB RSS, and this box has 12GB with **NO SWAP**, so an OOM is a
hard kill. Weights live in the persistent `worker_state` volume
(`/app/state/models/minilm-multilingual`, 458MB), NOT in the image; fetch with
`maintenance/fetch_embed_model.py`. Deps in `requirements.txt`.

**The old CLAUDE.md note rejecting local models ("~185 MB free") was STALE** — it
predated the 2026-07-20 upgrade. Live check found 5.3 GiB available. *Re-verify a
capacity claim against the live box before repeating it.*

### EVALUATE BY INPUT TYPE, NOT ONLY BY LANGUAGE (owner insight)

Scoring the model by language alone conflated two different failures: a part
NUMBER or SIZE CODE scoring badly is not a language weakness, it is a **semantic
limitation** — embeddings encode meaning and identifiers carry none. Language-only
segmentation pointed at the wrong fix ("improve Hebrew") instead of the right one
("route identifiers away from the model"). Measured precision, 11,110 ground-truth
parts:

| input type | n | fire@.75 | prec@.75 | fire@.85 | prec@.85 |
|---|---|---|---|---|---|
| english single-word | 267 | 94% | **99%** | 87% | **100%** |
| english short descriptive | 3,603 | 83% | **94%** | 49% | 96% |
| english long descriptive | 6,157 | 16% | 85% | 2% | 93% |
| hebrew descriptive | 788 | 64% | 79% | 39% | 85% |
| size/spec format | 217 | 3% | 100% | 0% | — |
| part number / code | 18 | 17% | 100% | 0% | — |
| supersession/placeholder | 59 | 5% | **33%** | 0% | — |

Findings language-splitting had hidden: (1) **short names are the BEST case**
(99-100%), long descriptive the weak English case — more words = more competing
concepts for a nearest-exemplar match; (2) codes/sizes barely fire at all, so they
were never the real danger — the earlier "it mis-files codes" claim came from a
0.45 threshold; (3) the genuinely dangerous type is **supersession/placeholder,
33% precision AND it fires** — it needs a hard pre-filter, not a threshold.

### THE CONTROLLED WRITE PATH (owner directive — NOT an open write path)

**PHASE 1 (active).** The model only sees types proven reliable, it **creates
classification RULES rather than writing categories**, and bulk apply happens only
after owner approval. `category_input_type.POLICY` blocks size/spec, part numbers,
supersession and brand-only *before* the model runs. Everything else flows through
the existing gates: blocklist → `MIN_CONSENSUS=3` cumulative + 80% agreement →
**owner `אשרמילה`** → bulk apply.

**PHASE 2 (earned, per type).** Auto-write may be enabled only for types that have
**proven consistently accurate in recorded history**. Every proposal is stamped
with its `input_type`, so every owner approve/reject is attributable evidence.
`embed_policy.autowrite_enabled()` requires ALL of: type eligible on measured
precision · ≥`EMBED_PROMOTE_MIN_DECISIONS` (50) owner decisions · ≥90% approval ·
global `EMBED_AUTOWRITE_ENABLED` · type named in `EMBED_AUTOWRITE_TYPES`. Evidence
makes promotion POSSIBLE; the owner still makes the call. Console: **`מודל`**
shows the per-type scorecard.

**Rule: never promote a model to autonomous writing on a benchmark alone — collect
per-type approval history in production first, and make the gate refuse by default.**

### What the model earned immediately
It exposed a real pre-existing bug: `_TYRE_RE` allowed ONE letter (`[rz]`) but
speed-rated tyres write TWO (`225/45ZR18`), so **every ZR tyre was invisible** to
the wheels-bearings rule. Now `[rz]{1,2}`. It also solves word-order structurally
(`Clamp Hose` ↔ `Hose Clamp`, 0.94) which keywords can only handle by enumeration.




A **third category**, distinct from the two agent layers above — do not confuse it with
either. 23 Claude Code Skills (`.claude/skills/dept-*`, git-tracked, project-scoped) built
in an isolated sandbox (`gstack_sandbox` container, its own fenced-off docker network, no
route to `internal`/`public`), security-audited, then migrated into this repo. They are
**Markdown instruction sets a Claude Code session reads and acts on** — NOT autonomous
Python agents. Nothing under `dept-*` runs inside `autospare_backend`, calls Cerebras, or has
a `job_registry` row. They only do anything when an active Claude Code session invokes them
(on-demand today; a scheduled Claude Code session is the intended path for `dept-cmo`'s
recurring target check-ins — see its own SKILL.md — not a new backend worker).

**Roster**: `dept-cmo` (department command + daily/weekly target tracking, OKR-style, every
number evidence-gated per the Truth-Only rule below), `dept-brand`, `dept-positioning`,
`dept-competitor-intel`, `dept-content`, `dept-b2b-leads`, `dept-seo-programmatic`,
`dept-seo-technical`, `dept-ppc`, `dept-crm-email`, `dept-cro`, `dept-analytics`,
`dept-market-research`, `dept-internal-comms`, `dept-keyword-seo`, `dept-geo-content`,
`dept-campaign-launch`, `dept-sop-library`, `dept-context` (shared foundation doc every
section reads), plus 4 design skills — `dept-design-review`, `dept-design-consultation`,
`dept-design-shotgun`, `dept-design-html` — adapted from `garrytan/gstack` (124k★).

**Design-skill execution is CONFINED TO THE SANDBOX — do not run the compiled `browse`/
`design` binaries directly on the production host.** Their SKILL.md/sections/vendor files
live in the real repo (harmless — markdown/JSON, no executable code), but they depend on a
~450MB shared `gstack/` runtime (`~/.claude/skills/gstack/` — Bun-compiled binaries, does
live Playwright/Chromium browser automation) that is installed at the HOST level, NOT
git-tracked. A Claude Code safety classifier blocked direct host execution of these binaries
when attempted 2026-07-27 — **that block is correct, not a false positive**: these are
third-party compiled binaries with one disclosed CVE-style vuln already found+patched (see
below); "patched the one we knew about" is not the same as "trusted to run unconfined on the
box holding customer PII, Stripe keys, and the live DB." Actual invocation of
`/dept-design-*` stays inside the isolated `gstack_sandbox` container (still running, no
network route to `internal`/`public`) pointed at a code-only copy of the frontend or a safe
read-only path to the real dev server. If host-level execution is ever wanted, it requires
the owner explicitly adding a Bash permission rule — never work around the classifier.

**Patched vulnerability (must survive any re-clone/update of `gstack/`)**: a real, publicly
disclosed critical vuln (GitHub issue #1324) let a local auth token leak via the `browse`
daemon's `/health` endpoint (`.startsWith('chrome-extension://')` check + optional/empty
`BROWSE_EXTENSION_ID`). Patched in `browse/src/server.ts` (Host-header allowlist for
`surface==='local'`, `/health` token only returned on exact origin match) and
`browse/src/terminal-agent.ts` (WebSocket origin must exactly match `BROWSE_EXTENSION_ID`,
no longer just "any chrome-extension://"). Verified via live attack replay after the patch,
re-verified after a fresh server restart, and confirmed intact on the version installed on
the real host. If `gstack/` is ever re-cloned or updated from upstream, re-apply and
re-verify this patch before use — do not trust a fresh clone.

**SHIRA/NOA wiring (2026-07-27)**: both live customer/social-facing agents (Layer A, in
`BACKEND_AI_AGENTS.py`) now have a small, additive grounding block folded into their
`system_prompt` — SHIRA (`MarketingAgent`) gets `dept-brand`'s voice rules + `dept-positioning`'s
"fitment verification before payment" differentiator when discussing value/discounts; NOA
(`SocialMediaManagerAgent`) gets the same positioning differentiator as an optional angle, not
forced into every post. **This is intentionally surgical** — condensed, hand-picked extracts
folded directly into the live prompt, NOT the full SKILL.md content (which is full of
Claude-Code-specific automation instructions irrelevant to a live chat/generation prompt) and
NOT a runtime file-read of the skill markdown (would add latency + a new failure mode to
every live agent call for marginal benefit at this scale). If `dept-brand`/`dept-positioning`
change, manually re-sync the relevant block in `BACKEND_AI_AGENTS.py` — there is no automatic
sync between the skill file and the live prompt; this is a deliberate simplicity tradeoff,
revisit only if drift becomes a real problem in practice. Every existing guardrail in both
prompts (SHIRA's Truth Rule, NOA's full personality/format/prohibition/Google-marketing
sections — see the G8 "Never regress" note above) was left untouched; changes were additive
insertions only, verified via a live import+regression check before restart.

**Verification performed (2026-07-27)**: syntax-checked, imported in isolation inside the
running container (confirmed new content present + all original guardrail text unchanged —
no regression), then a REAL live generation call through the full pipeline for SHIRA
succeeded end-to-end (Cerebras→fallback model→Gemini all 429'd that day; correctly fell
through to Groq and produced a real, on-policy Hebrew reply, Truth Rule intact). That same
test surfaced a genuine pre-existing bug (see `docs/POSTMORTEMS.md`, 2026-07-27 entry) — fixed and
re-verified live before restart. `pre_restart.sh` run, container restarted clean, zero errors
in startup logs, `HealthMonitor` pass complete. Did NOT route a synthetic test through
`process_user_message` (writes real rows to the live PII DB) since nothing in that layer was
touched — the isolated in-container test exercises the exact same agent classes/prompt/
`hf_client.py` that a real request would.

**Truth-Only Guardrail (MANDATORY, same as every other section of this platform)**: every
`dept-*` skill's own SKILL.md carries this rule already — no invented programs/discounts/
coverage numbers, every claim traced to a real query result. Applies doubly once folded into
a LIVE prompt (SHIRA/NOA above): a grounding block that encourages a stronger claim must not
weaken the existing Truth Rule enforcement already in that prompt.

---



A small, API-key-authenticated surface for external sites/devs. **Right-sized by design: it
exposes only what a partner needs and NEVER internal data** (supplier names, our cost, the 45%
margin, `base_price`, `importer_price_ils`/`online_price_ils`, or any internal flag).

- **Base path:** `/api/public/v1/` — `health` (no auth), `search`, `parts/{id}`, `fitment`,
  `manufacturers`. Registered in `BACKEND_API_ROUTES.py` via `include_router(public_api_router)`.
- **Auth:** `X-API-Key` header → sha256 → `api_keys` table (catalog DB). Per-key Redis rate limit
  (`apikey_rl:{id}`, default 60/min, set per key). Issue/list/revoke keys with
  `python3 /app/maintenance/issue_api_key.py --partner "Name" [--rate N]` (raw key shown once;
  only the sha256 is stored).
- **Pricing:** reuses `_customer_price_fields` (routes/parts.py) so the API returns EXACTLY the
  customer-facing price — `cost × 1.45 + CONDITIONAL VAT` (18% IL suppliers, 0% foreign). Never a
  raw or flat-VAT price. (Verified 2026-07-18: IL part 38.53 → VAT 6.94 → 45.47; foreign part
  VAT 0.)
- **Search** uses Meilisearch (`/indexes/parts/search`, needs the `Authorization: Bearer
  $MEILI_MASTER_KEY` header) then prices the hits from the DB. **Fitment** resolves the fitting
  part-ids FIRST (pvf trgm/norm indexes + LIMIT) then prices only those — never price-sort the
  whole match set (that was 28s → 0.198s).
- **Response schema (the ONLY exposed fields):** `part_id, oem_number, name, name_he,
  manufacturer, category, barcode, available, price{amount, vat, total, currency, vat_included}`.
  Any new field added here must pass the "no internal data" bar.
- **Any NEW public/partner endpoint MUST**: require `X-API-Key` (`Depends(require_api_key)`),
  price via `_customer_price_fields`, return the masked schema via `_shape`, and add a
  `check_rate_limit`. Partner-facing docs: `docs/PUBLIC_API.md` (keep it in sync).

---



NOA's publishers (`social/*_publisher.py`) only PUBLISH. `social/engagement.py` adds the
other half: **READ** comments/mentions/DMs on our own social pages and **REPLY** to them,
owner-approval-gated.

- **Uniform per-platform contract** (like `social/registry.py`): each platform exposes
  `async fetch_new(limit) -> [EngagementItem]` + `async post_reply(external_id, text) ->
  {ok,id,error}`. A platform with a missing/expired token returns `[]` / fails gracefully —
  it never raises and never blocks the other platforms. `PLATFORMS` registry (5) +
  `configured_platforms()`. **All hand-rolled on the FREE official APIs** (owner: no paid
  aggregator like Ayrshare/Blotato):
  - **Facebook** — Page comments (Graph v21.0; `FACEBOOK_PAGE_TOKEN`).
  - **Instagram** — media comments via the linked FB page (`instagram_business_account`).
  - **Telegram** — groups/channels/DMs via the **NOA admin bot** (`@Noa_autosparefinder_bot`,
    `TELEGRAM_ADMIN_BOT_TOKEN` — distinct from the customer bot `@Askparty_bot`/`TELEGRAM_BOT_TOKEN`).
    **WEBHOOK-FED**, not polled: the bot already holds a webhook (`/webhooks/telegram-admin`), so
    getUpdates would 409 — instead `routes/webhooks.py` calls `engagement.ingest_telegram_update()`
    on every inbound plain message (records status `new`; owner messages skipped), and the loop's
    `draft_new_items()` drafts them. Replies go out via `sendMessage` (`telegram_post_reply`).
    Privacy is OFF (`/setprivacy → Disable`, done 2026-07-25) so it reads all group chatter;
    else in groups it only sees mentions/replies. Opt into polling for a truly dedicated
    non-webhooked bot with `NOA_TELEGRAM_POLL=1` + `NOA_TELEGRAM_BOT_TOKEN`.
    - **Webhook secret (learned 2026-07-25):** the `/telegram-admin` handler enforces
      `X-Telegram-Bot-Api-Secret-Token == TELEGRAM_WEBHOOK_SECRET`. If a bot's webhook is
      registered WITHOUT that secret, Telegram's updates 403 (this had silently killed the
      admin approval buttons too). Both bots' webhooks MUST be `setWebhook` with
      `secret_token=TELEGRAM_WEBHOOK_SECRET`. The NOA bot was re-registered with it +
      `allowed_updates=[message,edited_message,channel_post,callback_query]`.
    - **Owner-skip is a toggle:** `ingest_telegram_update` RECORDS the owner's own DMs by
      default (so the owner can self-test by DMing the bot); `NOA_ENGAGEMENT_SKIP_OWNER=1`
      restores skipping. Replies are approval-gated regardless, so recording owner DMs is safe.
  - **Reddit** — subreddit comments + inbox (OAuth script app: `REDDIT_CLIENT_ID/SECRET/
    USERNAME/PASSWORD/SUBREDDIT`).
  - **Discord** — server-channel messages + DMs via `DISCORD_BOT_TOKEN` +
    `DISCORD_ENGAGE_CHANNELS` (REST; reply via `message_reference`). **LIVE.**
  - **Google Business** — reply to Google **reviews** (Business Profile API v4). Config:
    `GOOGLE_BUSINESS_CLIENT_ID/SECRET/REFRESH_TOKEN` (+ optional `_ACCOUNT`/`_LOCATION`, else
    auto-discovered). `_gbp_token` refreshes the OAuth access token; `external_id` is the review
    resource name. Adapter BUILT but **DROPPED by owner 2026-07-26** — GBP verification is for
    physical storefronts; an online-only marketplace can't pass it. Kept in code (harmless, not_configured).
  - **YouTube** — read + reply to comments on our channel (Data API v3, **free**, 10k units/day).
    Config: `YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN` (+ optional `YOUTUBE_CHANNEL_ID`, else
    `channels?mine=true`). `_yt_token` refreshes OAuth (scope `youtube.force-ssl`); reads
    `commentThreads?allThreadsRelatedToChannelId`, replies via `comments.insert` (`external_id` =
    top-level comment id = the reply parentId). Adapter BUILT; lights up when a channel + youtube-scoped
    refresh token land. (**X/Twitter dropped 2026-07-26 — pay-per-use, not free.**)
  - **`external_id` is COMPOSITE** (`chat/channel:message`) for Telegram/Discord so
    `post_reply(external_id,text)` can route without extra context; FB/IG use the raw comment
    id; Reddit uses the `t1_…` fullname. Shared `_http_json` helper; all urllib, no new deps.
  - **Walled for everyone (verified 2026-07-25, not faked):** Facebook **Groups** (Meta
    retired the Groups API 2024-04-22), **X** reading (paywalled), **TikTok** comments
    (approval-gated), **Meta DMs** (Messenger/IG-direct need app review). The only
    API-reachable "groups" are Telegram groups / Discord servers / Reddit subreddits.
- **`social_inbox` table** (catalog DB, self-created via `ensure_inbox_table`): dedupe
  `UNIQUE(platform, external_id)`; status lifecycle `new → pending_approval → replied |
  skipped`. Helpers: `record_item` (returns new row id or None on dup), `set_draft`,
  `pending_for_owner`, `resolve_inbox` (by 8-char id prefix), `mark_replied`, `mark_skipped`,
  `send_reply` (dispatches via registry). **Never use `:id::uuid` in a `text()` query** — the
  `::` cast collides with SQLAlchemy's `:name` param parser (`syntax error at or near ":"`);
  use `CAST(:id AS uuid)`.
- **NOA reply generator** `draft_reply_text(item)` — LLM (`hf_text`), replies in the SAME
  language the customer wrote (he/ar/en via `_detect_lang`), short/human/on-brand, plate-search
  CTA, **never invents prices/stock**, no hashtags. Same truth-only rules as the customer
  agents apply.
- **Public-reply cap + hand-off role (owner rule 2026-07-25):** NOA does NOT run an endless
  public conversation. `_draft_or_handoff` counts how many replies we've already SENT to that
  customer (`social_inbox` status='replied', per platform+author); once it reaches
  `NOA_ENGAGEMENT_MAX_REPLIES` (default 3), instead of another public answer she drafts a
  localized (he/ar/en) **hand-off** inviting them to a private support chat — the `/api/v1/go`
  channel picker (WhatsApp/Telegram/web chat), `NOA_CONNECT_URL` overridable. Applies on every
  platform (poll- and webhook-fed). Fresh customers still get a normal helpful reply.
- **`poll_once(db, autoreply=False)`** — one pass: fetch→record(dedupe)→draft→`pending_approval`
  (or auto-send if autoreply); skips our own page's replies (`SOCIAL_PAGE_NAME`); exception-safe
  per platform. Returns a summary dict for logging.
- **Supervised loop** `_noa_engagement_loop()` in `BACKEND_API_ROUTES.py` (registered at
  `startup()`): every `NOA_ENGAGEMENT_INTERVAL_S` (900s); idles ≥1h when nothing configured;
  WhatsApps the owner (via `_wa_send_quiet`, quiet-hours-safe) when drafts await approval.
  Toggles: `NOA_ENGAGEMENT_ENABLED` (default 1), `NOA_ENGAGEMENT_AUTOREPLY` (default 0 =
  owner approves each).
- **Owner WhatsApp console** (`agents/owner_console.py`): `תגובות`/`inbox` lists NOA's drafts;
  `ענה <id> [text]` approves+sends (own text overrides the draft); `דלג <id>` skips.
- **Credential state:** **Facebook — FULLY LIVE + E2E-verified 2026-07-25.** `FACEBOOK_APP_ID`+
  `FACEBOOK_APP_SECRET` in `.env`; `FACEBOOK_PAGE_TOKEN` is a **non-expiring PAGE token**
  (`expires_at:0`) minted by exchanging a fresh user token (`fb_exchange_token` → long-lived →
  `/me/accounts`) and carries `pages_read_engagement`+`pages_manage_engagement`. Live E2E proven
  (comment→read→NOA draft→reply posted on FB→verified→deleted). To refresh if ever revoked, re-run
  that exchange with a fresh user token that includes `pages_manage_engagement`. **Instagram —
  blocked by Meta:** the IG account is under review and NOT linked to the FB page
  (`instagram_business_account:none`, business IG edge permission-denied), so the FB-Graph IG path
  can't reach it; the alternative is the Instagram-Login API (`graph.instagram.com`, separate
  `AutoSpareFinder Social-IG` app) which needs an IG User token the owner can't generate until
  verification clears. IG adapter code is ready; activates when a token lands. The loop activates
  automatically once a valid token lands in `.env` — no code
  change. **Telegram + Discord are LIVE** (Telegram via `TELEGRAM_ADMIN_BOT_TOKEN` webhook;
  Discord bot `autosparefinder` in the AutoSpareFinder server watching `#general`
  `DISCORD_ENGAGE_CHANNELS=1528455754787328122`, `DISCORD_BOT_TOKEN` set — invite needed
  *Requires OAuth2 Code Grant* OFF + Message Content Intent ON; E2E send+read verified). **Reddit
  adapter is BUILT** — lights up when a free script app's creds are added. X/TikTok/Meta-DMs/FB-Groups
  stay walled (see FIXES_TRACKER 2026-07-25b). Test: `devtests/engagement_lifecycle_test.py`.



NIR's "superpower": find new sellers on the web, vet them, and onboard them so their offers
enrich search + the price **compare**, and orders route to them e2e.

- **Real web search** = `hf_client.gemini_web_search(query)` — Gemini **Google-Search grounding**
  (returns a grounded answer + the source URLs it used). The server IP is anti-bot blocked for
  direct HTTP, so this is how backend agents "search the web." The free Gemini key 429s often →
  `discover_sellers()` **falls back** to an LLM-propose (`hf_text`: HF/DeepInfra → Groq) + a **live domain
  fetch verify** (`_verify_domain`). **Never fabricates** — unverified/low-score candidates are
  skipped (score gate 0.55 + `http_ok` required).
- **Onboarding** (`onboard_seller`): dedupe by `lower(name)` OR domain; INSERT into `suppliers`
  with **`is_active=FALSE`** and sourcing metadata in the `credentials` JSONB
  (`{source:'nir_sourcing', status:'pending_review'|'pending_credentials'|'approved', needs, reasons, signals}`).
  Nothing goes live in customer-facing compare without **owner approval**.
- **Owner review** (WhatsApp console): `ספקים` list pending · `מקורות` run a discovery cycle now ·
  `אשרספק <id>` approve (→`is_active=TRUE`) · `דחהספק <id>` reject.
- **Proactive loop**: `_supplier_sourcing_loop()` (supervised, weekly; `SUPPLIER_SOURCING_INTERVAL_S`,
  toggle `SUPPLIER_SOURCING_ENABLED`) derives gap queries from `search_misses`, onboards pending
  candidates, WhatsApps the owner to review.
- **Order e2e / compare need NO new plumbing**: a seller becomes orderable + shows in compare the
  moment it is `is_active=TRUE` AND has priced `supplier_parts` rows — routing is by
  `OrderItem.supplier_part_id` → `SupplierPart→Supplier` (trigger_supplier_fulfillment) and compare
  keys on `s.is_active`. So onboarding a seller still needs a **per-seller price connector/importer**
  to write `supplier_parts` before it enriches compare (same as any importer). Automated dropship
  **auto-buy** (`place_order`) is still a stub on every connector — build it per seller when that
  seller's API credentials arrive; do NOT ship a speculative generic connector (each API differs).
- **The 5 NIR dropship todos** (Turn 14, Keystone, Meyer, ATD, ASAP Network) are registered as
  `pending_credentials` supplier rows with the exact owner step in `needs`; each activates when the
  owner supplies its account/token. **ASAP Network is the highest leverage** (one token = a whole
  network of suppliers + millions of ACA-standard SKUs w/ fitment).



Part images are re-hosted as clean thumbnails in a **Contabo Object Storage (S3-compatible)**
bucket and served from our own domain. Source supplier images are often contaminated with
**supplier ads/placeholders** (e.g. "PRODUCT IMAGE COMING SOON / SOUK AUTO PARTS / CONTACT US"),
so they are **filtered, never blindly re-hosted** (owner rule: a thumbnail may carry the part
image + the part name only — never a supplier link/ad).

- **Config (secret only in `.env`, gitignored):** `S3_ENDPOINT=https://eu2.contabostorage.com`,
  `S3_REGION=eu2`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET=part-thumbnails`,
  `THUMB_PUBLIC_BASE=https://autosparefinder.co.il/api/v1/thumbnails`. Also referenced in
  `docker-compose.yml` backend env. Client: `s3_storage.py` (boto3, s3v4).
- **Bucket is PRIVATE.** Contabo does NOT serve anonymous public GETs even with a public-read
  policy (verified: 401). Thumbnails are streamed by the backend `routes/thumbnails.py` →
  `GET /api/v1/thumbnails/{key:path}` with `Cache-Control: public, max-age=31536000, immutable`
  so **Cloudflare edge-caches** each one (backend fetches from S3 at most once). Serving only
  the image bytes guarantees no supplier link/ad can ride along.
- **nginx:** a dedicated `location ~* ^/api/v1/thumbnails/` is declared **before** the
  `\.(jpg|png|…)$` static regex (which would otherwise hijack keys ending in `.jpg` as missing
  static files → 404), un-rate-limited. Single-file mount → validate in a throwaway container on
  the `autosparefinder_internal` network, then **restart** `autospare_nginx` (not reload).
- **Cleanup pipeline:** `maintenance/build_part_thumbnails.py` — for each part with a source
  image and no `part_thumbnails` row: fetch (upgrade eBay `s-l225`→`s-l500`), **OCR the image
  (tesseract) and REJECT it** if it (a) contains supplier/promo text ("coming soon", "contact us",
  "auto parts", a URL, "whatsapp", hotline…) OR (b) is **text/label/brand-heavy** — more than
  `THUMB_MAX_OCR_WORDS` (default 3) real words ⇒ it's a label / OEM-box / brand-card / ad, NOT a
  clean part picture (owner rule: the picture must have **NO label or brand name**). Then
  standardize: auto-trim, fit to a clean 500×500 white square, compress ≤150 KB progressive JPEG,
  **NO caption/label/brand text is ever drawn**. **Dedup = content-addressed keys**: the object
  key is `thumbs/<ab>/<sha256(bytes)>.jpg`, so an identical image is stored **once** and reused by
  every part that shares it (no duplicate uploads; `object_exists` short-circuits). Outcome in the
  separate **`part_thumbnails(part_id, url, status)`** table (`ok | rejected_ad | no_source |
  failed`; NOT a column on the 4M-row parts_catalog → no DDL lock storms). `url` points at the
  shared content-addressed object. Run: `python3 /app/maintenance/build_part_thumbnails.py --limit 500`.
- **Source images — harvest→thumbnail connection (added 2026-07-18):** the thumbnail pipeline
  only cleans what the harvesters capture. The two parts-CREATING harvesters now write the part
  photo to **`parts_images`** (the pipeline's input): **(1)** `car_parts_ie_flaresolverr_harvester.py`
  `parse_parts()` extracts the block image via a markup-agnostic `_extract_image()` (lazy attrs
  `data-src`/`data-original`/… first, then `src`, then a CSS `background-image`; skips
  placeholder/logo/`data:` URIs; absolutizes) into `part["image_url"]`; it rides the existing
  `/collect` pass-through (no field whitelist) into `car_parts_ie_import_generic.py`, which INSERTs
  a `parts_images` row (`is_primary`, `NOT EXISTS` dedup guard — there is **no** unique
  `(part_id,url)` index, so never use `ON CONFLICT` here; and `url` is `varchar` → cast `$2::varchar`
  or you hit `AmbiguousParameterError`). **(2)** `oempartsonline_importer.py` — the scraper already
  captured `image_url`; the importer now writes the same `parts_images` row. From there the
  thumbnail supervisor picks the part up automatically (it scans parts lacking a `part_thumbnails`
  row). `amayama`/`rockauto` relays are **price-fill only** (match existing parts by OEM) — they
  create no parts and need no image write. Any NEW parts-creating harvester MUST also write
  `parts_images` or its parts get no thumbnail. (Was 0.7% of the catalog imaged before this — the
  car-parts.ie bulk captured none.)
- **Supervisor (watches + handles the import):** `_thumbnail_import_loop()` in
  `BACKEND_API_ROUTES.py`, registered at `startup()` via
  `_supervised_task("thumbnail_import_loop", …)` (so a crash of the loop auto-restarts). It runs
  `maintenance/build_part_thumbnails.py` continuously in modest batches (`--limit
  THUMBNAIL_IMPORT_BATCH=300`) as a **subprocess** (isolates the synchronous OCR/PIL off the event
  loop) at **low CPU priority** (`os.nice(15)`) so it never starves the flaresolverr harvesters on
  this 6-vCPU box (was 4-core before the 2026-07-20 upgrade; corrected 2026-11-27); a **40-min hard cap** kills a stuck batch, a **`THUMBNAIL_IMPORT_SLEEP=90`s**
  pause sits between batches, and it **exponentially backs off** (up to 1h) when the backlog is
  drained (then re-checks for newly-imported parts). Toggle with `THUMBNAIL_IMPORT_ENABLED=0`.
  Observe it at **`GET /api/v1/system/thumbnail-import`** (last cycle + live coverage: ok /
  rejected_ad / no_source / distinct_images / dedup_saved) and in `docker logs` (`[thumbnail_import]`).
  **Do NOT** also run a manual `docker exec -d … build_part_thumbnails` — the supervisor owns the import.
- **Search wiring:** `routes/parts.py` surfaces ONLY the clean bucket thumbnail as
  `primary_image` (LEFT JOIN `part_thumbnails` status='ok'); raw supplier image URLs are
  **never returned** to customers. The frontend `_partImageCandidates` already reads
  `primary_image`. Any new surface that shows a part image MUST use the bucket thumbnail, never
  a raw supplier URL.
- **Tooling** baked into the backend image (Dockerfile): `tesseract-ocr` + `pytesseract` +
  `boto3` + `fonts-dejavu-core`.
- **Verified 2026-07-18:** S3 round-trip; a real part (Land Rover LR016621) → clean 500×500
  ≤150 KB JPEG served through the domain; the SOUK ad image → OCR-rejected; search "oil filter"
  → returns the part with `primary_image` = the bucket URL. Test: `devtests/thumbnail_pipeline_test.py`.
- **Security (audited 2026-07-18):** the S3 secret lives ONLY in `.env` (gitignored — never in a
  tracked file/log/response). Bucket is **fully private** — the test-time public-read policy was
  removed → anonymous LIST/GET both return 401 (Contabo denies anonymous by default; only the
  backend, with credentials, reads). **Do NOT set a Contabo `public-access-block`** — its
  implementation blocks even authenticated PutObject (breaks the import); rely on no-bucket-policy
  + default-deny. The serving proxy returns **only image bytes** (no `x-amz-*`/
  bucket/endpoint headers leak) and rejects anything that isn't a `thumbs/…` or `parts/…` key —
  traversal (`..`, `%2e`, leading `/`, `\\`) and other prefixes all 404. 404s carry a negative Cache-Control
  so a flood of random keys is absorbed at the edge (the un-rate-limited location's only DoS
  vector). **Residual:** Contabo access keys are ACCOUNT-WIDE (can reach every bucket) — if the
  key is ever exposed, rotate it in the Contabo panel and update `.env`.
<!-- END VERBATIM -->
