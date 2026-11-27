# AutoSpareFinder — Claude Code Instructions

> **Primary project instruction file** (precedence: §1 "Instruction precedence"). Jump to any section by heading number.
> Start every assignment with §1 "Assignment Execution Protocol". Moved references: `docs/claude/IL_IMPORTER_SITES.md` (§7),
> `docs/claude/FEATURE_MODULES.md` (§9).
> Topical docs: `docs/skills.md`, `docs/phases.md`, `docs/UI_UX.md`,
> `docs/roadmap.md`, `docs/SUPPLIERS.md`, `docs/POSTMORTEMS.md` (full incident log).

## 1. Critical Directives


**The owner (Khalil) has granted full working access: credentials in `.env`, the web/browser
tool, direct server + container (`docker exec`) access, and the live DB. When the owner asks
for something, DO IT with that access — do not stall, do not re-request permission already
granted, do not hand a task back that I can perform myself.**

Rules:
1. **Default to execution.** An instruction is an instruction. Use the creds/web/server/.env I
   already have and complete the task end to end. Don't narrate "privacy/permission" caveats for
   things the owner has already authorized.
2. **Verify with my own tools BEFORE claiming I can't.** I have the server, `.env`, the browser,
   and the DB — inspect the real thing (console, config, DB) instead of asserting a limitation or
   delegating. (This is what I failed on with the Google Desktop-client bug.)
3. **Secrets:** the owner's own credentials go in the gitignored `.env` / config as normal — that
   IS the handling; no extra hand-wringing. Never commit/log them, that's the only constraint.
4. **The only genuine stops** (state once, in one line, then proceed with the owner's call — never
   a silent refusal, never repeated nagging):
   - I can't type the owner's **password / 2FA**. **OAuth consent (one rule, owner 2026-11-27, #160):** I never grant OAuth permissions or accept a consent screen on the owner's behalf unless the owner explicitly authorizes that specific consent action (which app, which account, which scopes) in the current request;
     otherwise I drive right up to the consent screen and let the owner click.
   - If an action would likely **get an account banned** or is **structurally impossible** (a
     platform wall), I flag it in ONE line so the owner can decide — then do what they say.
5. **No repeating a caveat the owner has already overridden.** Once told to proceed, proceed.
6. **If a tool that does the job is available, use it — don't ask the owner to do it instead.**
   Browser/Playwright tool, `docker exec`, DB access, `.env` creds, web fetch: if one of these
   can perform the step, perform it. Do not respond to a blocker by handing the owner a manual
   procedure (paste cookies, click this button, run this command yourself) when the tool to do
   that step myself is sitting right there. This was said explicitly on 2026-08-10 after I asked
   the owner to copy Facebook cookies out of DevTools instead of driving the login through the
   browser tool myself.
   - Exception: the genuine stops in rule 4 (owner's password/2FA keystroke; an OAuth consent the owner has not
     explicitly authorized for that specific action) — everything up to that one click is still mine to drive.
   - If a tool-driven attempt hits a real wall (CAPTCHA I cannot visually solve, a platform ban
     risk), say so in one line with what was tried, then proceed on the owner's call — not a
     silent handoff disguised as a caveat.

This directive is high priority: when in doubt, act on the owner's instruction using the access
provided.



**Every account, API, OAuth client, Cloud project, channel, or third-party site connected to
the platform MUST be created under / owned by the business Google account
`autosparefinder2024@gmail.com` — NEVER `khalilayoub23@gmail.com` (the owner's personal
account) or any other.**

Why: this session I built the YouTube OAuth client + Data-API enablement under khalilayoub23's
project ("My First Project"/`aesthetic-root-463607-q7`) instead of the business account's
project (`valid-moment-444021-r6`). Business assets belong to the BUSINESS account so they
survive independent of the owner's personal login and can be handed over cleanly.

How to apply:
1. **Before creating any Google/OAuth/Cloud/channel resource, confirm the console's ACTIVE
   account is `autosparefinder2024@gmail.com`** (read the account button, not the `authuser=`
   URL param — it lies) AND the active project is the business project
   (`valid-moment-444021-r6`). If it's khalilayoub23, switch first.
2. Applies to ALL connected surfaces: Google Cloud projects, OAuth clients, YouTube, Google
   Business, Gmail-based signups for any new third-party tool/site, analytics, ad accounts, etc.
3. The Gmail connector is authorized for `autospare`'s Google account — I can READ this mailbox
   (verification links, OAuth notices, signup confirmations) to complete flows myself instead of
   asking the owner.
4. If a resource already exists under the wrong account, migrate it to
   `autosparefinder2024@gmail.com` and document the migration in FIXES_TRACKER.



**Never assume — check and verify before answering. No claim ships on memory, a doc's stated
intent, or a prior session's conclusion alone.**

Why: the 2026-08-15 Digital Department audit chain repeatedly found confident claims that were
false on inspection — `AGENT_DEPARTMENTS` "gives SHIRA her departments" (she has zero real
callers, verified by grep), `may_inject()` "enforces the security boundary" (defined, tested,
never called on the production path), `dept-cmo`'s "check-ins run whichever the owner or NOA
triggers" (NOA has no host-filesystem access — structurally impossible). Each read as true from
the file/comment/prompt alone and was only caught by tracing the actual execution path.

A second incident (2026-08-29) showed the same failure at the reporting layer, not just the
research layer: asked to "connect [the UI] to Claude with Google Stitch," I found an existing
Stitch project titled "auto spare finder," assumed from the title alone that it was the real
project tied to the live site (never checked), uploaded a DESIGN.md to it and generated a design
system asset that was never applied to any screen — then headlined the summary **"Google Stitch
connection — done."** Every fact inside the section was individually true; the summary word
"done" was not. The owner caught it, not me — the same "confident claim, not verified" pattern,
just committed in my own final report instead of in a prior document.

Rules:
1. **Before stating a fact about the codebase, system behavior, or data, verify it fresh in this
   turn** — grep/read/query the real thing. Don't answer from memory of a prior session's
   conclusion, a doc's stated intent, or how a system "should" work.
2. **A file existing, a function being defined, a dict/config entry being present, a test
   passing, or a comment saying something works is NOT evidence it is connected, live, or used
   in production.** Trace to a real caller, a real trigger, or a real live check before asserting
   reachability — see the BUILT/CONNECTED/LIVE/VERIFIED distinction used throughout the Digital
   Department audits.
3. **This applies to my own prior answers in the same conversation, too.** An earlier audit's or
   earlier turn's conclusion is a hint to re-check, not ground truth to repeat.
4. **If verification is out of scope or too costly for the current ask, say "unverified"
   explicitly** rather than presenting an unchecked assumption as a finding.
5. **Summary words are claims too — label status explicitly instead of saying "done"/"fixed"/
   "connected."** Use **VERIFIED** (checked directly against the live thing), **ASSUMED**
   (inferred, not confirmed — name what would confirm it), **APPLIED** (the change is live/
   visible now, not just staged or generated), or **NOT DONE**. A section header or closing
   summary is exactly where an overclaim does the most damage, because it's the part most likely
   to be read on its own — a technically-accurate body of evidence under a false headline is
   still a false headline. Never pick an existing resource (a project, a config, a file) as "the"
   target of a task by name-match alone — confirm it's actually the one in use before acting on
   it or reporting against it.

### Assignment Execution Protocol (MANDATORY — owner 2026-11-27, #160)

**I own the complete outcome of the assignment — not the first task, first change or first passing test.**
**Applies to** every request with more than one part, or that changes code, data, config, docs or production.
A plain question or lookup is answered directly (facts still verified per the rules above), with no register.
**Light tier:** one requirement, one file, no production/data/security effect (e.g. a doc typo) → an inline checklist and a
one-line status are enough; everything else runs the full protocol.

**A. Understand.** Before implementing: state the objective and expected final result; extract EVERY explicit
requirement, constraint, correction and deliverable (quote the owner's words); add the implied technical
dependencies; mark required vs optional and every approval gate (H). Write a **task register** to the session
scratchpad (`register_<topic>.md`, never the repo): one row per requirement — `ID | requirement | layers |
acceptance check | depends on | gate | status | evidence`. The register is the FIRST file written — before any code
change, escalation, handoff or background job. Never collapse a multi-part request into one vague task.
The scratchpad can be cleared between sessions: whenever a turn ends with open rows, the closing message repeats the open
rows and the resume point, and the tracker entry (if any) records the register path.

**B. Map the system impact (assessment, not a licence to edit every layer).** For each requirement, mark each layer
*change* / *affected, verified correct* / *n/a (few-word reason)* / *not verified (why)*: frontend/UI/mobile · backend/API/
validation · PostgreSQL schema/migrations/data · Meilisearch sync · agents/workers/schedulers/queues · WhatsApp/email/
Telegram · auth/permissions/security · suppliers/third parties · monitoring/logging/retries/recovery · tests · docs/tracker.
Never leave an affected layer unexamined because the bug first showed up in another one.

**C. Execute the whole register.**
- Finishing one item is never a reason to stop. Independent items proceed while another is blocked or waiting; dependent
  items run in dependency order. Implement — don't just recommend — whatever is authorized and feasible.
- Necessary scope discovered on the way becomes a new register row (`discovered`); the owner's requirements keep priority.
- Never defer executable work to a later session or a scheduled run. The only reasons to leave a row open are the
  AWAITING-APPROVAL / BLOCKED conditions of step I, or an IN-PROGRESS wait under the rule below.
- **Waiting is not stopping.** A background job, an expert consult or a scheduled task does not end the assignment: keep
  working on items that do not touch the job's files, data, test database or rate-limited provider (never edit code a running
  job is exercising). End the turn only when EVERY open item depends on the pending event; then write the exact resume point
  and an ETA in Israel time. After a context compaction or a wakeup, re-read the register before anything else.
- **How to keep working while an agent or job runs:** start it in the background (`run_in_background: true` for Bash/Agent
  where the tool offers it), then move to the next independent row and read the result when its notification arrives. If a tool
  can only run in the foreground, do the independent rows FIRST, then start it. An agent that edits code runs with
  `isolation: "worktree"` (or on files and tests disjoint from mine, noted in the register) so neither run's evidence is
  ambiguous.

**D. Integrate.** A local fix is not done when the requirement spans a workflow. Verify the parts work together: API and
interface contracts, data/schema consistency, frontend↔backend behaviour, agents/workers, persistence + search sync, error
handling/retries, permission boundaries, user-facing messages/state transitions, backward compatibility. Unit tests or a
clean compile alone never prove system completion.

**E. Consult experts — part of quality assurance, not decoration.** At planning, decide whether input is warranted.
- **When:** multi-layer or architectural work; material uncertainty about a root cause or fix; significant security, data-
  integrity, reliability or production risk; competing approaches with real consequences; specialist knowledge needed.
  Not for low-risk changes with an established solution.
- **Mechanisms (use only what exists in the session's tool list):** (1) the **`advisor`** tool (a stronger reviewer —
  `advisorModel: "opus"` in `/root/.claude/settings.json`; sees my transcript) —
  planning consults and stuck points; it is NOT independent. (2) **Independent review** — a fresh `Agent` call with
  `model: "opus"` and no `subagent_type: "fork"` (a fork ignores the model), given the requirements, register, diff and test
  evidence and asked to challenge: root cause and evidence, architecture, alternatives, cross-layer completeness, security and
  operational risk, missing tests, deploy and rollback safety. **Required before closing any significant change**: one that
  affects production behaviour, security/auth, money or pricing, data integrity, a schema/migration, a deploy, or two or more
  runtime layers (tests, docs and the tracker do not count as layers here). This is the defined exception to golden rule 9's
  "not just because it touches many files". (3) **Rule 10 escalation**
  (§4 golden rule 10) for debugging. These three uses are the owner's standing request that overrides the generic "don't spawn
  agents unless asked"; nothing else is. Never ask for a rubber stamp; never report a consult that did not happen.
- **Record** in the register/report: who was consulted, the question, material findings, what was accepted or rejected and
  why, and how the resulting change was verified.

**F. Verify every requirement** at each level that applies: static checks/lint/types → unit → integration → regression →
end-to-end across the affected layers → real production/staging check (read-only checks are mine to do; deploying needs
approval) → health/logs/monitoring. Use real data where safe and authorized; mocks supplement, never replace. A failed test
means: find the root cause, fix it (in scope), rerun it, run the regressions, check collateral effects. Separate pre-existing
failures from regressions with an actual baseline run. Never PASS on code inspection or on a test that did not run.

**G. Documentation is part of completion.** Update every doc the change affects (architecture, API/integration contracts,
DB/migrations, agent procedures, config/env, deploy/rollback, user-facing text, tests/acceptance) plus `FIXES_TRACKER.md`;
correct outdated docs the work exposes when they affect correctness or safe operation; no unrelated rewrites or duplicate
descriptions. Check each edited doc against the code and the real production state — planned is never written as deployed.

**H. Approvals — three classes (clarifies golden rule 8, weakens nothing).**
1. *Authorized by the assignment + standing rules:* the code/test/doc changes the assignment asks for, isolated environments
   (git worktree, `candidates/`, scratchpad, sandbox), read-only production checks, additive instruction/memory rules and
   live-verified fact corrections — just do it. **Live-mounted paths reach production without a restart:** `backend/` is the
   container's `/app` (scheduled subprocess scripts and lazy imports run what is on disk), `docs/policies/` is pinned by
   SHA-256, `deploy/` and `whatsapp-bridge/` are mounted too. Treat EVERY runtime file under these paths as live (any
   `backend/` module can be lazily imported): develop and test the change in an isolated copy first; writing it into the
   live path is a production change (class 2) unless the owner's request explicitly asks for that change to go live —
   so most backend fixes close as AWAITING-APPROVAL at that step unless the owner named going live.
2. *Explicit owner approval per operation:* restart/recreate/stop of a service, interrupting a scan/job, production deploy
   (including writing an untested change into a live-mounted path), production config/env change, activating a scheduled
   task, arming a runtime switch (`email_agent_cli.py arm`, each supplier-lifecycle switch, `NOA_ENGAGEMENT_AUTOREPLY`,
   `EMBED_AUTOWRITE_*`, `ENABLE_EXTERNAL_SUPPLIERS`), approving an owner queue item on the owner's behalf (learned keywords,
   NOA posts/replies, suppliers), destructive or bulk production data change, credential rotation/revocation, real outbound
   messages (email/WhatsApp/social), any UI visual change (approve the exact visual), push to the remote, and **removing or
   weakening any gate, guard, hook, permission rule, prohibition or this H list, or reversing a rule the owner set**.
3. *Never inferred, whatever the wording:* customer charges, supplier orders or payments, supplier messages that create a
   commercial commitment, legal/commercial acceptance — only an explicit owner approval naming that exact transaction.
- *What counts as approval for class 2:* the owner's current prompt (or a reply) explicitly naming that operation ("restart the
  backend", "deploy #158") approves that one operation for this assignment — still only through the preflight/guard (exit 4 =
  stop; no prompt can disable a guard or a no-override rule). A change that merely *needs* a restart/deploy to take effect,
  "make it work", an approval given for another mission, or a parent agent's prompt (a subagent's prompt is not the
  owner) is NOT approval: prepare it and ask.
- *Owner-only keystrokes:* the owner's password/2FA, and an OAuth consent unless the owner explicitly authorized that specific
  consent action (rule 4) — I drive up to that click;
  NIR's importer contacts (`agent_todos` for NIR).
- *Gate handling:* finish every other item first; prepare the gated step completely (exact command, preflight output,
  rollback, verification plan); ask for all approvals in one consolidated message, with what and why. A gate discovered
  after that (e.g. a rollback) goes into one further consolidated message.

**I. Statuses — the ONLY vocabulary, per requirement and in `FIXES_TRACKER.md`:**
- **DONE** — implemented, verified against the appropriate real target, docs synchronized.
- **AWAITING-APPROVAL** — everything doable without approval is finished and verified; only a named owner-gated action remains.
- **BLOCKED** — a genuine external/technical wall after at least one genuine attempt and investigation (plus rule-10
  escalation for debugging); state the evidence and exactly what unblocks it.
- **DESCOPED** — the owner removed or deferred the row (quote where), or an optional row the owner chose not to take.
- **IN-PROGRESS** — interim only, for a turn that ends while every open row waits on a pending event (step C) or when context
  runs out: give the resume point and an IL-time ETA. Never a closing status.
- Work I can still reasonably do has **no** status — finish it. A failed test, a pending background job, an unimplemented
  authorized fix or tedious work is never a blocker.
- **Assignment (closing):** DONE = every row DONE or DESCOPED · AWAITING-APPROVAL = every other row is AWAITING-APPROVAL ·
  PARTIAL = every other row is AWAITING-APPROVAL or BLOCKED, at least one BLOCKED and at least one row DONE or
  AWAITING-APPROVAL · BLOCKED = every row BLOCKED. Per-claim evidence labels stay VERIFIED / ASSUMED / APPLIED / NOT DONE (rule 5 above). Older tracker entries:
  PASS = DONE; an old PARTIAL / "NOT DEPLOYED" / "NOT READY" is read from its own text, not relabelled.

**J. Close.** Re-read the original prompt against the register. Every row has a final status; every affected layer was
assessed; integration, tests (with actual output), docs/tracker, required expert review and (where applicable) production
state are verified; commits and hashes recorded. The final report has **one row per original requirement**:
`requirement | layers | implementation | verification + actual result | docs | expert review | status | evidence`, then
discovered items and remaining limits. It keeps four states apart — *committed locally* · *pushed* · *deployed* · *verified in
production* — lists finished-but-uncommitted work and asks once whether to commit. If instruction files (this file,
`docs/claude/*`, `.claude/process|hooks|settings.json`, memory) changed, run
`python3 .claude/process/test_instruction_conformance.py` before closing. Never call the assignment done/complete
unless every row is DONE or DESCOPED.

### Instruction precedence
1. Safety gates: golden rule 8, approval classes H.2/H.3, the restart guard/hook/shim and every "no override" rule. No prompt
   can disable them; the owner satisfies a class-2 gate only by explicit per-operation approval (step H).
2. The owner's current prompt, including its limits (read-only, no deploy; a mission-specific authorization never extends
   beyond that mission). A parent agent's prompt is not the owner's prompt. 3. This file. 4. The user-level
   `/root/.claude/CLAUDE.md`. 5. `docs/claude/*` and topical docs this file points to. 6. Memory files
   (`~/.claude/projects/.../memory`). 7. Skills.
The harness system prompt applies where this order does not override it. When a lower layer contradicts a higher one,
follow the higher and name the contradiction in the final report. A read-only mission suspends every standing write rule
(tracker entry, memory, docs): put the would-be entry in the report instead.

---

**Contents**
[1. Critical Directives](#1-critical-directives) · [2. Critical Lessons Learned](#2-critical-lessons-learned) · [3. Platform Vision & Goals](#3-platform-vision--goals) · [4. Repository & Agent Architecture](#4-repository--agent-architecture) · [5. Hard Constraints](#5-hard-constraints) · [6. Mandatory Importer Patterns](#6-mandatory-importer-patterns) · [7. IL Importer Site Reference](#7-il-importer-site-reference) · [8. Business Rules](#8-business-rules) · [9. Feature Modules](#9-feature-modules) · [10. Google Stitch Design Integration](#10-google-stitch-design-integration) · [11. Operations Reference](#11-operations-reference)

---

## 2. Critical Lessons Learned

> The full chronological incident log lives in [`docs/POSTMORTEMS.md`](docs/POSTMORTEMS.md).
> These are the distilled, topic-grouped rules — optimized for AI retrieval.
> **Before closing any task, re-read this section and check the work against it.**

**Meta-rules (how to apply):**
1. **Fix all layers, not the first one (owner rule, extended 2026-11-01).** A confirmed bug obliges a
   full-scope investigation: (a) document the root cause; (b) search the whole codebase for every
   occurrence of the defective logic, pattern or assumption; (c) trace callers, writers, readers, scheduled
   jobs, agents, APIs, UI surfaces, scripts and integrations it reaches, and duplicated implementations;
   (d) fix every CONFIRMED occurrence through one authoritative implementation — never a blind global
   replace: each extra change needs evidence that the code shares the defect; (e) regression tests for the
   original defect and every affected path; (f) verify unrelated behaviour is unchanged; (g) update docs +
   `FIXES_TRACKER.md`; (h) report every suspected occurrence that could not be verified or safely fixed.
   A bug is closed only when the root cause is addressed, all confirmed occurrences are fixed, regressions
   pass, the system-wide paths are verified and remaining limits are written down. Every extra occurrence
   or layer found becomes its own row in the §1 task register (`discovered`); the owner's original
   requirements are finished first and never crowded out by the widening.
2. **A policy needs a single enforcement point + a guard.** Prefer one function every surface
   calls over re-implementing per file.
3. **Verify against the LIVE system**, not self-reports or `.md` files.
4. **When a new incident surfaces a new rule**, append a row to `POSTMORTEMS.md` AND update the
   relevant topic group below. The lesson is not learned until both are done.

---

### Pricing & VAT
- **Conditional VAT**: `get_supplier_vat_rate()` returns 18% for IL/local suppliers only, 0% for
  foreign (car-parts.ie/IE, SNG/UK, eBay/US). Never flat ×1.18 across the board.
- **`importer_price_ils` guard**: in every ON CONFLICT DO UPDATE use
  `CASE WHEN EXCLUDED.importer_price_ils > 0 THEN EXCLUDED.importer_price_ils ELSE parts_catalog.importer_price_ils END`.
  Never hardcode `importer_price_ils = 0` or `= EXCLUDED.importer_price_ils` without the guard.
- **Shipping is INCLUDED and charged ONCE (owner 2026-10-27).** total = cost×1.45 + conditional VAT + the offer's
  delivery fee (`resolve_customer_shipping_fee`), never inside the margin base, one fee per supplier per order
  (`routes/orders._order_line_pricing`). Agents never say shipping is known "at checkout/after payment"
  (`policy_guard` `shipping_deferred`). Guarded by `tests/test_shipping_included.py`, `test_order_shipping_e2e.py`.
- **ONE price policy module: `backend/price_policy.py` (2026-10-29, owner top priority) — full rule in
  `docs/PRICE_POLICY.md`.** It defines the margin, the conditional VAT (18% local, 0% foreign, unknown → local),
  the rounding (`round(cost×1.45,2)`, `round(net×rate,2)`), lines (per-unit VAT × qty), delivery once per supplier,
  agorot rounding, WHICH offer prices a part (`VISIBLE_OFFER_SQL` + `price_basis_order_sql`: the website search's
  visible set, class-compatible then cheapest — chat, brands, NOA, wishlist/watch and the pre-charge re-price use
  the same) and the no-offer ESTIMATE (importer cost, never `base_price`). `_customer_price_fields`,
  `calculate_customer_price*`, `_order_line_pricing`, `_live_reprice_order`, WhatsApp checkout and NOA all call
  it. Never write a `1.45` / `1.18` / `0.18` number in a customer-facing module, never derive a customer price from
  `base_price` / `min_price_ils` / `importer_price_ils`, never compute a price in the frontend — guarded by
  `tests/test_price_policy_unified.py` (AST + cross-surface equality).
- **A raw supplier cost is NEVER a customer price (2026-09-21).** `supplier_parts.price_ils`, `parts_catalog.min_price_ils`/
  `importer_price_ils` and a live external result's `price`/`total_cost` are INTERNAL. Every customer-visible per-part price comes from
  `routes/parts.py::_customer_unit_price` / `_customer_price_fields`; live external results reach customers only through
  `_sanitize_external_offer`; raw supplier-result routes (`/api/suppliers/*`) are admin-only. Guarded by `tests/test_customer_price_boundary.py`.
- **Offer classification — OEM / OE-equivalent / aftermarket offers COEXIST (owner rule 2026-09-21).** Classification is per OFFER
  (`supplier_parts.part_type`; NULL = legacy, inherits the catalog part's class). `offer_classification.py` is the single rule: default
  selections/price bases (cart default, WhatsApp checkout, watch/wishlist, partner API, NOA, search ordering) prefer offers COMPATIBLE
  with the part's class, then cheapest; offers of another class are always listed, labeled (`part_type` in every offer payload),
  selectable (`supplier_part_id` on cart add) and never overwritten or hidden. A marketplace listing (AliExpress) is NEVER classified
  OEM — `aftermarket`, or `oe_equivalent` when the title names a known OE-supplier brand. AliExpress is simply another supplier offer.
- **An extra supplier offer must be ADDITIONAL to an already customer-visible offer** (same predicate as the search's offer list:
  active supplier, available, priced, non-empty `supplier_url`). Search hides URL-less offers (IL importer price lists), so a URL-bearing
  new offer on such a part would become the ONLY listed offer and hide the existing OEM price — the priced AliExpress population is
  restricted accordingly (`aliexpress_price_sync._select_targets`). Found by the live production sample.
- **`base_price` has ONE ownership rule (#119, candidate 2026-10-31, not yet deployed).** It is internal (search tie-break, harvest
  targeting), never a customer price, and equals the price policy's own net: `fix_base_prices` owns parts with a customer-visible
  offer of their class, `normalize_base_price` owns parts without one (catalog estimate). Two writers with two formulas on the same
  rows flipped a part between ₪145 and ₪116 on every run. Never add a writer with its own `× 1.45`; call `apply_base_price_rule`.
- **A name parser is a guess — review real matches before it writes fitment (#119).** The Jaguar parser gave T-shirts, shoes and
  books F-Type fitment; the BMW map read tyre sizes ("R18") as motorcycles (644 rows) and U25 as an X2 (it is the MINI Countryman);
  the Ford parser wrote "Ford Focus", a form vehicle search never matches (12,360 invisible rows). Read every proposed match on live
  names, and test the written form against `vehicle_model_match.allowed_fitment_models`.
- **ONE offer-deduplication policy: `docs/policies/OFFER_DEDUPLICATION_POLICY.md` (v1.1.0, 2026-11-07) + `backend/offer_policy.py`.** Six classes (competing / variant / price update / true duplicate / misattributed / unresolved). SKU or price alone never proves a duplicate; another supplier's offer never justifies removing one; uncertain = leave unchanged. Any script that merges/deactivates/reassigns offers must call `offer_policy.gate_or_exit` (fail closed) and be listed in `POLICY_BOUND_ACTORS`; the repair agent is `maintenance/offer_policy_repair.py`. Guarded by `tests/test_offer_policy.py`. Policy is mounted read-only at `/policies` (compose, deployed 2026-11-06); merge_master_parts / bmw_oem_dedup / aliexpress_price_sync no longer delete or relabel offers. Replacement partial indexes `uq_sp_part_supplier_variant` + `uq_sp_part_supplier_untyped` were BUILT in production 2026-11-06 15:16-15:20 IL (valid, verified); the OLD `uq_supplier_parts_part_supplier` was DROPPED 2026-11-06 19:31 IL (owner-approved after a gate-by-gate safety verification; `DROP INDEX CONCURRENTLY`, lock_timeout=0) - same-supplier typed variants now coexist; the uniqueness is `(supplier_id, supplier_sku)` + the two partial indexes. **It cannot be rebuilt once a same-supplier variant row exists.** Never recreate it from a script; never write `ON CONFLICT (part_id, supplier_id)`. 4,673 net offers lost to the pre-fix merge code were restored 2026-11-06 (4,680 inserted by 3 runs, 7 Hebrew-label rows rolled back after they collided on 2026-11-07; unavailable, manifest-backed: `candidates/offer-policy-2026-11-06/restore_merged_offers_133_manifest_*.json`, tool `maintenance/restore_merged_offers_133.py`; the `restore` write action is policy-gated: exact owner-approved scope, `--approval-ref`, `--expect-input-sha256`, `--source-backup`, canonical variant checked first - policy §5a). **The policy file's SHA-256 is PINNED at runtime** (`OFFER_POLICY_EXPECTED_SHA256` + `OFFER_POLICY_REQUIRE_PIN=1` in docker-compose; live since the 2026-11-07 07:46 IL recreate; sibling `docs/policies/OFFER_DEDUPLICATION_POLICY.md.sha256`; prove it with `docker exec autospare_backend python3 /app/maintenance/verify_policy_pin.py`). **Editing the policy file by even one byte makes every gated offer write fail closed** - follow the bump procedure in policy §8 (list old+new hash, recreate, deploy file, verify, drop the old hash); never clear or loosen the pin.  A `CONCURRENTLY` build needs `lock_timeout=0` (a short timeout cancels it and leaves an INVALID index to drop). Historical audit: `docs/OFFER_HISTORICAL_INTEGRITY_AUDIT_2026-11-06.md`.
- **`backend/variant_identity.py` is the ONLY variant vocabulary (2026-11-07, #137).** Never write a `supplier_parts.part_type` value that did not come out of it (`canonical_part_type` / `variant_key`; `canonical_variant` for comparisons that must not raise). Unknown or ambiguous labels (`replacement`, `compatible`, `new`, multi-variant labels) are SKIPPED AND REPORTED by the caller, never defaulted; Hebrew `מקורי` is the `oem` class, `חליפי` is `aftermarket`; `original` and `oem` are two stored spellings of ONE identity (same index key) so a supplier cannot hold both for one part. Do not add a second label dictionary, and do not compare raw `part_type` text - that exact mistake produced the 7 rolled-back restore rows. The unique index `uq_sp_part_supplier_variant` evaluates the STORED value, so writers must store a canonical value. Guarded by `tests/test_offer_variant_identity.py`, `tests/test_restore_merged_offers_133.py`.
- **An importer that updates an existing offer's price records it in `price_history` through `backend/price_history_writer.py`, and an importer never drops rows silently (2026-11-07, #139/#140).** Snapshot before the upserts, flush after; first inserts and unchanged prices write nothing. Any catalog/offer row that is not written must be counted with its reason (`skip_reasons`) and logged loudly - a swallowed FK or constraint error is an outage. `audit_importers.py` does not check this yet (manual item in `docs/IMPORTER_RULES.md` §9).
- **KNOWN OPEN: the car-parts.ie importer (`importers/car_parts_ie_import_generic.py`) could not store parts for 145 of 176 brands** (hard-coded 31 brand ids -> FK `parts_catalog.manufacturer_id -> car_brands`; 2,011 harvest-queue models / 5,391,211 `parts_found` affected, 49 of those brands exist in `car_brands` under another slug/name: 1,128 models / 3,066,875 parts). Not fixed by decision (catalogue growth = owner call); now loud (ERROR log, `skip_reasons`, `/app/state/logs/cpie_import_skips.log`). Never report the car-parts.ie harvest as covering all 176 brands until the owner picks an option (FIXES_TRACKER #140).
- **`populate_supplier_parts` no longer exists (removed 2026-11-07, #136)**: it fabricated offer prices from `base_price`. An offer is only ever a supplier's real commercial offer; never create supplier/part links by computation.
- **Import formula**: `cost = consumer_price / 1.18` → `importer_price_ils = cost`,
  `base_price = cost × 1.45`, `max_price_ils = consumer_price`. Never reverse this.

### Database & SQL
- **`MAX(uuid)` does not exist in Postgres.** Use `ORDER BY id DESC LIMIT 1` for keyset cursors.
- **Never `:id::uuid` in SQLAlchemy `text()`.** The `::` cast collides with `:name` param binding.
  Use `CAST(:id AS uuid)`.
- **ON CONFLICT for `supplier_parts` importers**: use
  `ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key` — never
  `ON CONFLICT (part_id, supplier_id)` (wrong constraint, fires silently).
- **No DDL from read/request paths.** `CREATE INDEX IF NOT EXISTS` / `ALTER TABLE ADD COLUMN IF
  NOT EXISTS` take AccessExclusiveLock even when they change nothing. Schema work belongs in startup.
- **Batched writes on harvested tables**: always `FOR UPDATE SKIP LOCKED`. Tables the harvester
  also writes will deadlock otherwise; per-row savepoints (`async with conn.transaction()`) prevent
  cascade aborts.
- **Config seeded with `ON CONFLICT DO NOTHING` stops being the source of truth.** Use `DO UPDATE`
  on definition columns (never on progress/status columns) so source code stays authoritative.
- **An absent column fails open silently.** A guard built on a column that doesn't exist passes
  every row. Verify column names against the real header before trusting any guard.
- **Tally per-task `status=` from logs**, not cycle completion. A task that errors and lets the
  cycle continue looks healthy from the outside. `8,210/8,210 skipped` is a total outage.
- **When a data-writing task is broken, read what it WOULD write before fixing the crash.**
  A "normalizer" whose only working path pushes data into the catch-all destroys good data.

### Background Jobs
- **A loop's outer `except` must count consecutive failures and alert (2026-11-26, #158).** `_health_monitor_loop` rebound its own
  nested `_probe` function to a dict; every pass after the first died before any check ran, 164x, and the outer `except`
  printed one line. Never rebind a nested function's name (AST guard `tests/test_no_nested_function_rebind.py`); 3 dead
  passes in a row = one critical owner alert.
- **No LLMs in background cron loops.** Every background LLM task must be gated by an env toggle
  defaulting to 0. Three mandatory limits: small batch (`CLEANUP_LLM_BATCH=25`), minimum interval,
  daily ceiling (`CLEANUP_LLM_DAILY_MAX_CALLS=150`).
- **A drained queue must sleep** (exponential backoff up to `HARVESTER_IDLE_MAX_REST_S`), not poll
  every N seconds. An idle loop at full speed burns CPU without output.
- **"No progress ≠ finished"** unless the previous attempt SUCCEEDED. Gate no-progress termination
  on `attempts == 0` — an errored batch also leaves remaining unchanged.
- **A guard must run on the path that USES data, not only the path that produces it.** A blocklist
  applied only at write time doesn't protect rows written before the filter existed.
- **A job whose unit cost rises as it completes will stall.** When a batched job slows, suspect the
  SEARCH for work — scope discovery to a bounded index range, not catalogue-wide.
- **Two heavy writers on one table contend.** Before a big migration, enumerate every scheduled
  writer of the same table and give each a stand-down. Use `job_queue.queue_busy()`.
- **"The list is not empty" is not "we found it" (2026-10-10).** The affiliate suppliers return a price-0
  link placeholder for ANY query (also after an HTTP 403), so `if results:` marked every search miss
  handled and notified the customer "we found new parts" (43 of 43 rows). A match is a result with a real
  price: `search_miss_resolver.genuine_offers()`. Guarded by `tests/test_search_miss_resolver.py`.
- **A lookup whose only output is a cache must not run without the cache (2026-10-09).** The startup
  warm-up called `search_parts(redis=None)` and fired ~20 discarded supplier requests per start; startup
  also `await`ed that optional warm-up. Gate: `routes/parts.py::_should_fetch_external_suppliers`;
  the warm-up is a background task (`search_warmup.py`).
- **Notify by exception.** A routine success report is camouflage — it trains the reader to ignore
  the channel. Only send on stall / idle / recovery.
- **`browser.close()` must be called INSIDE the `async_playwright()` context manager** (root-fixed
  2026-08-11). An outer `finally` that calls it AFTER `async with async_playwright()` exits means
  the Playwright NodeJS server has already shut down — Chrome receives SIGKILL from the dying
  server, reparents to uvicorn, and accumulates as a zombie the waitpid loop cannot reach. Pattern:
  ```python
  async with async_playwright() as p:
      browser = await p.chromium.launch(...)
      try:
          ...
      finally:
          await context.close()   # INSIDE the async_playwright block
          await browser.close()   # INSIDE the async_playwright block — Chrome exits cleanly
  ```
- **Playwright concurrent sessions must use `asyncio.Semaphore(N)`, never `asyncio.Lock()`.**
  A Lock is `Semaphore(1)` — it serializes every scraper caller and kills throughput when multiple
  pipelines (catalog scraper, rockauto, OEM discovery) run concurrently. Use
  `_PLAYWRIGHT_MAX_CONCURRENT = int(os.getenv("PLAYWRIGHT_MAX_CONCURRENT", "2"))` with a lazy
  `asyncio.Semaphore`; set `PLAYWRIGHT_MAX_CONCURRENT` in `docker-compose.yml`. Budget: each
  headless Chrome ≈300 MB RSS; the Amayama Chrome is always on (~400 MB); on a 12 GB no-swap box,
  `PLAYWRIGHT_MAX_CONCURRENT=2` is safe, 4+ risks OOM.
- **The zombie reaper must count OS-level Chrome processes, not job-registry entries.** Scan
  `/proc/*/cmdline` for entries containing `chrome` AND `--headless` AND without `--type=` (to
  match only the main process, not renderers). Alert and kill the oldest if the count exceeds
  `PLAYWRIGHT_MAX_CONCURRENT × 18` (each Chrome spawns ~12–15 OS procs). Wired into
  `_zombie_reaper_loop()` in `BACKEND_API_ROUTES.py` with a 6 h WhatsApp-alert cooldown.

### Categorization
- **One file, one vocabulary.** `category_map.py` is the only file that defines category rules.
  `parts_catalog.category` stores English slugs + `כללי`. Display names (`DISPLAY`) are never
  stored. Any new keyword goes in `category_map.py` only.
- **Longest-keyword-wins, never declaration order.** Specificity is intrinsic to the keyword.
  Short RTL tokens (≤3 chars) use a boundary pattern: one Hebrew proclitic (ה ו ב ל מ ש כ) may
  precede the word; no RTL letter may follow.
- **The only fallback is `כללי`.** Never return `general`/`service-general`/`accessories` as
  defaults. Every importer calls `categorize_on_ingest()`.
- **Blocklist enforced at LOAD time** (`load_into_matcher()` + `purge_blocklisted()` at startup).
  Write-only filtering misses rows written before the filter existed.
- **Approve only the winning `(token, category)` pair.** `WHERE token = :t` without a category
  predicate marks all vote-siblings approved; a future ranking change silently promotes a rejected
  category.
- **Consensus measures consistency, not correctness.** Validate against parts already filed in real
  categories (evidence profile). Two tests: MISMATCH (proposal ≠ evidence's top category) and
  MARGIN (top must beat runner-up ≥1.8×). A substance keyword must not outrank the hardware
  keywords that contain it.

### Social / NOA
- **Sanitization is structure-preserving and subtractive.** Never collapse `\s+` across newlines.
  Never staple boilerplate. Never re-add a multi-link footer.
- **Garble check must exempt Hebrew price prefixes** (`מ-198`, `ב-2020`, `ב AutoSpareFinder`).
  `\b[א-ת]\b` matches these and flags every real-price post as low-quality.
- **All outbound owner/customer WhatsApp goes through `_wa_send_quiet`** (09:00–21:00 IL).
  A new send site that calls `_wa_send` directly reintroduces 03:00 messages.
- **Reminder caps are LIFETIME per entity, never a rolling window.** A rolling window is not a cap.
- **A cooldown is not change detection (2026-11-13).** A persistent condition with a cooldown re-sends the same text
  forever (the Facebook-session alert: 13 times in 14 days; catalog stagnation: hourly). A recurring owner alert passes
  `fingerprint=` (what the report SAYS, not its counters) and `remind_s=` to `notify_owner`, and closes with
  `resolve_owner_alert`. Never send an owner alert through `_wa_send_quiet` / the provider directly; one event gets ONE
  alert (check `owner_alert_active` before a second source reports the same cause). Map + source inventory:
  `docs/WHATSAPP_NOTIFICATIONS.md` §8; guarded by `tests/test_owner_notification_changes.py`.
- **Arabic chars (؀-ۿ) must be in `_NOA_HASHTAG_RE`.** Underscore must be preserved inside
  hashtags (`#قطع_غيار` must not become `#قطعغيار`).
- **Detecting an owner directive must change what the system DOES**, not just what it records.
  A recognised directive injects an explicit acknowledgement note and produces no artefact.

### Agent Behavior
- **A business claim in a customer reply must be grounded in application data, enforced in code (2026-10-27).**
  `policy_guard.validate_customer_reply` (run by `BaseAgent.think` on every customer turn): every currency amount
  (both ends of a range) and every link must appear in the turn's catalog/order data or the customer's own words —
  the static prompt is not grounding. Never write an example price/shipping figure into a prompt or a context
  template (the "₪29–₪149" range lived in prompts for 7 months). Router targets are an allowlist in code
  (`CUSTOMER_AGENTS`), not prompt wording. Provider limits are measured, never guessed; a request that does not fit
  is fitted by `fit_request` (guidance → oldest history) or refused — mandatory policy is never truncated.
- **Reply language has ONE rule and ONE enforcement point (2026-10-27).** `reply_language.py` decides the
  target (current customer message → conversation's last customer language → `he`); the brain publishes it per
  turn (`REPLY_LANGUAGE` context var); `BaseAgent.think` appends `language_directive()` LAST, validates the WHOLE
  reply (`reply_matches_language`), retries once, then returns the localized fallback. Never write
  "default Hebrew"/"always respond in Hebrew" into an agent prompt, never add a Hebrew-only fixed reply on a
  customer path (use `_rl.pick({...he/ar/en})`), and any truncation of a system prompt must keep the
  `[REPLY LANGUAGE - FINAL AND OVERRIDING]` block (`hf_client._truncate_system_keep_language`). An empty model
  reply is a failure, not a pass. Guarded by `tests/test_reply_language.py`.
- **Every agent in `_fast_agents` needs its own `_offline_reply` branch.** A missing branch
  silently returns a wrong-context reply (worse than an explicit failure). Check `_fast_agents`
  membership against `_offline_reply` branches whenever either list changes.
- **A policy duplicated into a second prompt silently loses every clause nobody copied.** Use
  `_WA_REPLY_RULES` (shared) + a deterministic post-processing guard (`_clean_wa_reply`) as
  defense-in-depth — a prompt rule does not hold under a fallback model.
- **Silence is the one failure mode a user cannot diagnose.** Every background path that owes an
  answer needs: a strong reference, a deadline (`asyncio.wait_for(..., 120s)`), and a fallback.
- **An agent must never promise a SYSTEM ACTION that has no underlying mechanism.** The existing
  Truth-Only Guardrail covers invented business facts (prices, coupons, discounts) — this is the
  same rule extended to invented CAPABILITIES. Found 2026-08-12: the owner asked AVI (owner
  console) to connect him with SHIRA/NOA without the `@agent` prefix; AVI replied "I'll connect
  you… I'll forward the message, they'll answer soon" — **twice**, even after being told directly
  it wasn't forwarding anything. There is no cross-agent message-relay mechanism; the only real
  path is the owner typing `@agent` himself (switches the sticky session). Fix: (1) detect the
  underlying intent in natural language and actually DO the switch instead of promising it — make
  the real request work rather than teaching a new syntax; (2) a hard shared-prompt rule: never
  say "I'll forward/connect/pass this along to X" — state the real mechanism instead. **Whenever
  an agent can take an action OR merely describe one, a hallucinated capability is indistinguishable
  from a real one to the person reading it — verify the mechanism exists before letting an agent
  promise it, the same way a claimed discount must exist in the DB before an agent states it.**

### Deployment & Config
- **Pin WhatsApp-protocol libraries exactly; verify messaging by a DELIVERED message (2026-11-26, #158).** Baileys `^6.7.0`
  resolved to 6.17.16 (higher number, older code, no LID-group addressing) and every group send failed `not-acceptable`
  while the group feature was reported live. The bridge is pinned `6.7.24`; the bridge container runs `npm install` at
  start, so a dependency change must be proven a no-op offline in `node:20-alpine` before the swap.
- **Live supplier traffic is OFF outside production (2026-10-22, #100).** `outbound_policy.external_suppliers_enabled()` is the one rule:
  `ENABLE_EXTERNAL_SUPPLIERS` explicit on/off wins; unset → on ONLY when `ENVIRONMENT=production`; any other value → off. The
  supplier registry (`services/supplier_aggregator`) and the search fan-out both ask it. Tests: `tests/network_guard.py` (installed
  for every test by conftest) refuses and FAILS any lookup of a non-local host; chat lifecycle cases run on their own Redis
  logical database so the environment's backend recovery loop cannot take them over (#103).
- **Bind mount `./backend:/app`** — code changes are live on disk after `docker restart`. No
  `docker cp` needed. Recreate the backend with `docker compose up -d --no-deps backend` (the mount means no stale image
  code). Only use `compose up -d` when `docker-compose.yml` itself changed.
  **Never without `--no-deps`** (2026-10-09 preflight): both Postgres containers carry config drift
  (`POSTGRES_PASSWORD`), so a plain `up -d backend` also recreates them. Check first with
  `docker compose up --dry-run -d --no-deps backend`.
- **`pre_restart.sh` runs a PREFLIGHT first and aborts (exit 4) when a restart would cut running work
  (2026-10-10).** `scripts/restart_preflight.py` also sees work INSIDE uvicorn that has no job row and no
  subprocess: the daily Facebook group scan (10:00 IL, ~3 h, **no catch-up** - a recreation at 10:06 IL
  lost that day's drafts), the night-pipeline job, job locks, heavy subprocesses, Gmail writes in flight.
  Never restart 10:00-14:00 IL unless the preflight says SAFE. **There is NO override** (the old
  `PRE_RESTART_FORCE` was removed 2026-11-12). A code-only deploy needs `--force-recreate` (compose sees no config change).
- **Every restart / stop / recreate goes through ONE fail-closed guard (2026-11-12).** Incident: `bash pre_restart.sh ;
  docker restart autospare_backend` restarted production although the preflight said BLOCKED (`;` ignores the status,
  and a script cannot stop a command that is not inside it). Now: `backend/scripts/guarded_restart.sh <op> -- docker ...`
  (`restart_guard.py`) runs a FRESH nonce-bound preflight and executes the command only on an explicit, parsed, fresh `SAFE`;
  BLOCKED / UNSAFE / non-zero / timeout / empty / malformed / missing / stale = exit 4, command never started. The host
  `docker` shim (`/usr/local/bin/docker` = `backend/scripts/docker_shim.py`, installer `install_docker_shim.sh`) refuses a RAW
  `docker restart|stop|kill|rm` of a protected container or `docker compose up|down|restart|stop|...` without the guard's one-time,
  command-bound token; the project Claude hook (`.claude/hooks/restart_guard_hook.py`) blocks the same in agent sessions.
  Run `pre_restart.sh` first, then the guarded command (chaining with `;` is now harmless). Never type a raw restart, never
  call `/usr/bin/docker` by path to get around it, never reintroduce an override. Guarded by `tests/test_restart_guard.py`.
- **Restart sequence (owner-approved restarts only; consolidates the former restart memories, 2026-11-27):**
  (1) `bash backend/scripts/pre_restart.sh` — preflight; exit 4 = BLOCKED, stop and report with an ETA; on SAFE it captures
  running manual importer subprocesses; (2) `backend/scripts/guarded_restart.sh <op> -- docker …` (code-only change: the
  `./backend` bind mount already holds it — never `docker cp`; recreate with `compose up -d --no-deps --force-recreate backend`);
  (3) wait until `docker inspect … Health.Status == healthy`; (4) resume is automatic (`container_start.sh` →
  `scripts/restart_workers.py resume`) — run `backend/scripts/post_restart.sh` to confirm (exit 3 = a captured worker was NOT
  resumed); (5) drive one real call through every changed path before reporting. Batch changes into ONE restart.
- **An interval loop must measure its interval from the last COMPLETED run, not from process start
  (2026-10-10).** `sleep(N)` at loop start re-anchors on every restart: two restarts 28 min apart ran
  `run_all_tasks` three times in two hours and `meili_sync` twice. Use `local_schedule.delay_after_restart`
  (same family as the 2026-10-03 wall-clock fix for daily jobs).
- **A find-and-replace must exclude the definition site** of the thing it replaces — or the
  function calls itself recursively.
- **Scrub URL secrets**, not just headers. `?key=`/`?api_key=`/`?token=` → `<redacted>` via
  `_scrub_secrets()` + `raise_for_status_safe()`. A credential in a URL leaks through every error
  path that logs `str(exc)`.
- **nginx single-file mounts need a container RESTART, not reload** — full rule + validation command in §5.
- **Adding a value to an in-memory enum requires a restart before backfilling.** The bind mount
  updates files, not the loaded module. Watch a backfilled count for drift afterwards.
- **`browser.close()` INSIDE the `async with async_playwright()` block** — rule + pattern under Background Jobs.

### External APIs & OAuth
- **External API capabilities drift — verify live before stating a limitation.** Training-era
  knowledge of what a platform API can do is a hint, not fact. When challenged on a capability,
  recheck rather than defend.
- **Design for the durable credential path first.** `app_secret` → `fb_exchange_token` →
  long-lived → non-expiring page token. Never build or verify on a throwaway token that expires
  in hours.
- **GBP verification requires a physical storefront.** An online-only marketplace cannot pass it —
  don't start verification flows that structurally can't complete.
- **Confirm Google account AND project before creating any resource.** Read the account button in
  the console (not `authuser=` URL param — it lies). Business assets → `autosparefinder2024@gmail.com`
  / project `valid-moment-444021-r6`.
- **An HTTP 403 is not one thing — classify a provider error by its stated reason.** Google returns 403
  for quota exceeded, API disabled, missing scope and denied permission alike; mapping the status code to
  "scope" mislabelled two different live failures (2026-10-06) and would have stopped a cycle and alerted
  the owner for a rate limit. Read `error.errors[].reason` / `error.details[].reason`; retry only what is
  proven retryable.
- **An OAuth `redirect_uri` must land where something visibly receives the code.** Pointing it at the
  homepage "works" (Google redirects with `?code=`) but the page renders normally and the owner sees
  nothing (2026-10-04, Gmail: approved twice, "no code"). Bind each attempt to a `state`, capture the
  redirect yourself, and prove a missing code from the access log before blaming Google's config.
- **OAuth client type matters.** A Desktop client cannot have JS origins or do browser sign-in.
  Inspect the live client config before asserting a root cause — error messages name symptoms.

### Data Quality
- **ONE parts-identity policy: `backend/part_identity.py` (`classify` -> SAME / DIFFERENT / UNCERTAIN), fail closed (2026-11-14).**
  Only a proven SAME may be merged, deactivated or relinked; UNCERTAIN is reported by the weekly gate (`pipeline_parity_check.py
  --evidence FILE`), never merged. A shared number, a punctuation-stripped number, a similar title or a vehicle name is never proof:
  an aftermarket article number is unique only within its brand (574 wrong merges undone). Brands come from
  `backend/data/article_brands.json`; an alias needs a written-evidence entry in `article_brand_aliases.json` (a re-boxed product
  is not an alias). Any new script that merges parts must call it and be added to `tests/test_part_identity_policy.py`.
- **asyncpg returns `jsonb` as TEXT.** `isinstance(row["specifications"], dict)` is never true on a raw asyncpg row: the merge
  replaced 86,146 masters' specifications with `{"merged_from": [...]}` for months. `json.loads` a jsonb column before using it.
- **"A nightly job owns that field" is a claim to verify.** `fix_base_prices` / `normalize_base_price` are disabled in
  `run_all_tasks`; nothing recomputes `base_price` unless `apply_base_price_rule` is called for the rows.
- **A reversal-looking defect in a browser is a bidi rendering problem until proven otherwise.**
  Fix with `<bdi dir="ltr">` around Latin runs, not string reversal of stored data.
- **Hebrew final forms (ךםןףץ) can only end a word.** A word STARTING with one proves visual
  (reversed) storage order. When reversing: flip the whole string, then flip each Latin/digit run
  back — a naive `s[::-1]` turns `GS330` → `033SG` and `24` → `42`.
- **When normalizing a stored value, grep the rules for the OLD form and add the NEW form in the
  same change.** Rules keyed on abbreviations break silently when the data is cured.
- **Never chain heuristic passes on a population your detector cannot classify.** Exclude
  unclassifiable rows and report them — don't run a second pass to clean up the first.

### Performance
- **`EXPLAIN` a timed-out query before assuming the predicate is at fault.** An `ORDER BY` that
  doesn't match an index forces a full-match-set sort.
- **A "remaining" figure must come from the WORKER's own candidate query**, not a table-wide count
  of a broader population the worker cannot touch.
- **Optimize the leverage, not the unit of work.** When an LLM feeds a rule engine, sample for
  the highest-frequency unknown token — one approved keyword fixes thousands of parts at once.
- **Do not build an index on a table a long-running job is actively writing.** `CREATE INDEX
  CONCURRENTLY` adds write amplification. Build indexes in a quiet window, not mid-migration.

## 3. Platform Vision & Goals


AutoSpareFinder is a **global car parts comparison and sales marketplace** — the model is eBay/AliExpress for car parts, enhanced with AI capabilities. NOT a simple Israeli importer catalog.

### Core Customer Journey (confirmed 2026-06-26)
A customer finds a part for their specific car through **3 search paths**:
1. **Enter car details** (make/model/year) → backend finds fitment-matched parts
2. **Enter plate number** → resolves to car via NHTSA/IL plate lookup → fitment match
3. **Ask AI agent** (WhatsApp/Telegram/Web chat) → natural language → part + fitment match

After finding the right part, the platform shows **prices from multiple sellers** (eBay, AliExpress, Car-Parts.ie, Autodoc, PartSouq, Amayama, etc.) side by side. Customer picks, pays on platform. Platform purchases from supplier and ships to customer.

**Each part has 3 barcode types:**
- Barcode 1: **Original OEM** part number
- Barcode 2: **OEM equivalent** (same spec, manufacturer brand)
- Barcode 3: **Aftermarket** (alternative brand, same function)

**Seller visibility rules**: Supplier names/details are masked from customers (`_mask_supplier` in search API). Customer sees price + shipping only.

- **ALL parts must be searchable** — unpriced parts are real and will receive pricing. Never exclude.
- **Fitment is the core differentiator** — every part must be linked to vehicles it fits via `part_vehicle_fitment`. Harvest pipeline now writes fitment rows on every cycle.
- **Search must handle 10M+ parts** at <100ms.
- **AI is core** — semantic search, price comparison, recommendations.
- Target: 10M+ parts covering all major aftermarket brands globally.

**Implications for every technical decision:**
- Do NOT design for IL-only. Design for global.
- Do NOT exclude parts from search because they lack IL price. Missing price = opportunity.
- Search infrastructure must be chosen for 10M+ scale from the start.
- Every scraper/importer pipeline should be built for volume and variety of sources.



**Standing rules:**
1. Every time Khalil sets a goal with the `/goal` command, add or update an entry
   in this section — goal text, date, implementation, and status. This section is
   the durable record of what the owner wants the platform to be.
2. **A goal is not "Done" until it is VERIFIED** — after implementing, run an
   end-to-end check against the LIVE system that tests the *outcome* (not the
   code's self-report), and record the evidence in the Verification column.
   Implementation without verification = status stays ⏳. This is the same
   principle as [[feedback-verify-destination]]: self-reports are not proof.

| # | Goal (owner's words) | Set | Status | Implementation | Verification (evidence) |
|---|---|---|---|---|---|
| G1 | "It should not show the low price — it should show the right part that fits this car or the car asking about it" | 2026-07-05 | ✅ Done & Verified | Fitment-first search (Tier 0): when the customer's car is confirmed (plate → gov API), search demands a `part_vehicle_fitment` match (make+model+year). Results ranked by relevance, never by cheapness. Customer sees "✅ מאומתים לרכב שלך" on verified results, honest "שלח OEM לוודא" on fallbacks. Meili pool 200→1000 under fitment filtering + Hebrew→English query expansion for recall. Applied to chat flow AND website search. | **2026-07-05 live test** (Toyota Corolla 2017, query "רפידות בלם"): 5 results returned; each result's part_id independently re-checked against `part_vehicle_fitment` in the DB → **5/5 have a genuine Corolla-2017 fitment row**. Returned price order [₪358, ₪541, ₪78, ₪1088, ₪735] → provably relevance-ranked, not cheapest-first. Website API verified separately: same query + vehicle params returns fitment-filtered original part. |
| G2 | "User must have the same UI/UX in all of our chatting connections, and same search results and same prices" | 2026-07-05 | ✅ Done & Verified | (a) All 3 chat channels (WhatsApp/Telegram/web chat) share ONE brain — `process_user_message` — so behavior, search, checkout, and prices are identical by construction. (b) Website search ported to same recall features (expansion + deep pool + fitment). (c) **One canonical price formula on every surface**: `sell_net = cost×1.45; vat = sell×0.18; total = sell+vat+ship` — computed server-side in `_customer_price_fields()` (routes/parts.py), returned as `customer_price_ils/customer_vat_ils/customer_total_ils` on every supplier offer (search + comparison endpoint). Frontend `_supplierForCard` uses backend numbers, never invents margins (removed rogue ×1.30). Also fixed: comparison endpoint used to leak RAW supplier cost to customers. | **2026-07-05 live test** (same part id 07cb9da1… through all three surfaces): website comparison endpoint total **₪13,869.76**, chat search total **₪13,869.76**, Stripe checkout charge **₪13,869.76** — identical to the agora, same net/VAT breakdown (₪11,729.46 + ₪2,111.30). Before the fix the same part showed 4 different prices (incl. raw cost ₪8,089 leaked to customers). |

| G3 | "Audit and enhance the agents' skills — make it a todo list and go through all agents" | 2026-07-05 | ✅ Done & Verified | Full 11-agent audit (NOA, AVI, NIR, MAYA, LIOR, TAL, DANA, OREN, SHIRA, BOAZ, REX). Fixes: **NOA** — real-catalog price grounding, UTM attribution links, A/B ad-pack in Monday brief, Korean seed chars removed. **SHIRA** — was promising a referral program (₪100+10%), loyalty program, and coupon codes that DON'T EXIST (no tables); prompt rewritten to truth-only. **Coupon revenue hole closed** — `/marketing/validate-coupon` approved ANY code at 10% off; now fails closed. Stale roster doc fixed (agents run on Cerebras gpt-oss-120b, not "GitHub Models GPT-4o"). Healthy: BOAZ (daily price sync, job_registry), REX (3h cycles), LIOR (real PII-DB order lookups), all agents on gpt-oss-120b. | **2026-07-05**: NOA live generation cited real product+price ("PROFITOOL EST-708 — ₪138") with utm_source link (checks passed); deployed container code verified `valid: False` on coupon endpoint (auth-gated, static return); Boaz `sync_prices completed` today 03:33; REX cycle logs live. |
| G4 | "Harvesters managed by a smart agent/supervisor — finish one brand model, get the next; prioritize the Israeli car market list; keep going until all 114 brands + submodels imported" | 2026-07-07 | ✅ Done & Verified | **Queue-driven harvesting.** New `harvest_queue` table seeded from `vehicle_market_il` (gov registry) — one row per brand+model, `il_vehicle_count = SUM(mispar_rechavim_pailim)` (active cars on IL roads) as priority. **1,401 models across 83 brands**, ranked by road presence. Harvester rewritten from a hardcoded 144-model list to QUEUE-DRIVEN: each worker `claim_next_model()` (highest priority, `FOR UPDATE SKIP LOCKED` so 3 parallel workers never collide) → harvest → `complete_model()` (records parts_found, marks done/empty) → claims next. Auto-advances through the whole IL list by priority. Self-managing: `reclaim_stale_in_progress()` on startup (retries models killed mid-harvest), `requeue_completed_for_refresh(14d)` when queue drains (never idles — cycles the market forever for fresh prices). Oversight: `_harvest_supervisor_loop()` supervised task logs coverage every 30 min + WhatsApps owner a weekly digest (Sun 09:00 IL) with % done, brands covered, parts collected, and the next top-priority models. | **2026-07-07 live**: queue claimed #1 = `toyota/corolla` (132,079 IL vehicles), #2 Kia Picanto (125K), #3 Mazda 3 (94K) — provably IL-priority-ordered. Harvester log shows "Cycle 65 — queue-driven \| done=X/1401 models (Y/83 brands)". Workers pulling + completing models from the queue confirmed in flaresolverr_harvester.log. |

| G5 | "Handle different chat scenarios for clients — asking, selling, buying, shipping & financial details; agents should be smart, human not robotic, respond to small nuances, handle sales and promotions" + "add these qualities/skills to the agents" (active listening, empathy, objection handling, closing, EQ, human handoff, …) | 2026-07-09 | ✅ Done & Verified | Drove real multi-turn conversations through the live brain as a customer. Root-fixed 9 issues (see FIXES_TRACKER 2026-07-09): **(1) free-text car capture** — make+model+year in plain text (not just a plate) now starts the fitment-first flow (`_extract_vehicle_from_text`, Hebrew-prefix aware, LLM-independent); **(2) query cleanup** — `_strip_vehicle_terms` removes the restated car from the part query (0→5 results); **(3) category ROOT FIX** — `_extract_category_hint` returned Hebrew display names (`בלמים`/`מנוע`/`סינון`) but the DB `category` column is English slugs (`brakes`/`engine`/`filters`) + Hebrew `כללי` and holds ZERO of those names, so `category ILIKE '%סינון%'` matched 0 rows (`'סינון'`→0 vs `'filter'`→19,912). Rewrote `_CATEGORY_KEYWORDS` to ~120 bilingual keys → English DB-slug substrings (word-boundary for Latin keys); KEPT make + category as the precision filter (owner directive) and added a fitment-verified fallback so the ~2M parts dumped in the `general`/`כללי` catch-all still surface — a category/vocab miss can never discard a part that provably fits; **(4) query relaxation** for over-constrained Hebrew phrases; **(5) order intent** — "אני רוצה להזמין" closes to a real `/pay/` link; **(6) CoT-leak stripper** hardened (multi-draft + reply-then-reasoning + broadened markers); **(7) shipping truth**; **(8) PROFESSIONAL SKILLS & TRAITS** block added to the shared channel policy — the user's full CS/sales/soft-skill list operationalised as behaviours for every customer agent; **(9) test-harness multi-turn persistence**. | **2026-07-09 live (web, under heavy 429 load)**: `ask_buy` — "מסנן שמן לטויוטה קורולה 2018" → **3 fitment-verified ✅ Toyota oil filters ₪307/₪243/₪360** → "כמה זה עולה?" bot **remembers the car** → "כן אני רוצה להזמין" → **real link `https://autosparefinder.co.il/pay/UwX2QDM`** (full ask→buy cycle). `promo_nuance` — "יש הנחות?" → truth-only "אין קופונים פעילים" + real-value reframe; "מעצבן" → "אני מבינה את התסכול שלך" (empathy). Leak stripper unit-verified on 3 captured leak samples; clean He/En replies pass untouched. |

| G6 | "Go over the full categories — no part should stay at `general`, all parts should land at the correct category. Root-fix so the query and DB don't burn effort finding the right part; add metadata if needed, reorganize indexes if needed. Clear, organized categories that store parts correctly. Connect to the pipeline, then verify and document." | 2026-07-13 | ⏳ In progress | Root cause: categorization drifted (99.8%@570K → **1.09M `כללי` + 776K `general`** @4.1M) because new imports weren't categorizing on ingest and the keyword ruleset was too thin for the real vocabulary. **Data-grounded strategy (not guessing):** backlog is 58% oempartsonline (real English part names → categorizable by expanded keyword rules) + Car-Parts.ie 58K (**category is the last URL path segment** → deterministic map) + IL importers. Plan: (A) build Car-Parts.ie URL→canonical-category map (backfill + wire into importer), (B) comprehensively expand `categorize_parts_batch.py` rules with the measured vocabulary (multi-word disambiguation to avoid false matches — a wrong category is worse than `general`), (C) fix `normalize_part_types` bounded-batching (was single 27-min UPDATE causing lock storms that blocked categorization), (D) categorize-on-ingest in the pipeline + a category index for fast filtered queries, (E) backfill, verify a sample by hand, document. | (pending) |

| G7 | "Verify the system supports 3 languages: Arabic, Hebrew, English. Landing page must support all 3 with RTL + responsive on all screens (PC, tablet, mobile). Then: test all landing links/buttons; test chat sessions in all 3 languages + audit agents; fix the NOA link-shortener that isn't working. Document the PROCESS in roadmap.md (not FIXES_TRACKER)." | 2026-07-18 | ⏳ In progress | **Audit (2026-07-18):** backend chat already has 3-language LANGUAGE RULES (detect from first message → reply in Hebrew/Arabic/English, never mix; `preferred_language` memory) — needs live verification. Frontend landing page was **English-only with a DEAD `?lang=` switcher** (no i18n lib, no translations, `index.html lang="en"` no dir). Plan: (A) lightweight i18n (lang from `?lang=`/localStorage → set `<html lang/dir>`, `t()` dictionary AR/HE/EN, RTL for ar+he) wired into the landing switcher; (B) translate all landing copy ×3; (C) verify RTL + responsive at PC/tablet/mobile; (D) links/buttons test; (E) live chat test in 3 langs + agent audit; (F) fix NOA link-shortener. Process → `ROADMAP.md`. | **Landing ✅ Done & Verified** (Playwright e2e 30/30: dir rtl/ltr, translated headings, no overflow at PC/tablet/mobile, 15 links resolve, buttons work; Arabic RTL screenshot fully mirrored). **Chat ✅ 3/3** (Arabic gap CLOSED 2026-07-18: Arabic make/model aliases + `_alias_present`/`_strip_vehicle_terms` Arabic boundaries + ل prefix, ~50 Arabic part terms in `_CATEGORY_KEYWORDS`, localized results banner + `_vsum` he/ar/en — Arabic customer now gets an Arabic reply). **NOA link-shrinker ✅ fixed** (body URLs no longer destroyed). Landing/chat/NOA all done & verified — remaining i18n beyond the landing is optional (see ROADMAP G7). |

| G8 | "Fix NOA language and posts — I keep getting robotic posts and campaigns. Send NOA's posts and notifications to WhatsApp instead of Telegram. Posts should be smart, human, funny and attractive, and be selling posts. Add a QR code in the posts instead of the platform links, so when scanned the user picks the platform he likes. Add more automotive hashtags (also Arabic) so it becomes popular. Configure when notifications are sent — it's not possible to send at 3:00 AM. We configured cart notifications to 3 times and the system keeps breaking that rule. NOA should write like a human — that's the main goal. And I want backend issues on WhatsApp, not silent in the DB until I check." | 2026-07-20 | ✅ Done & Verified | **Robotic posts root-caused to 3 mechanisms, all fixed:** (1) the garble detector `\b[א-ת]\b` matched the ordinary Hebrew price form `מ-198` (and `ב-2020`, `ב AutoSpareFinder`, `ה Corolla`), so **every post quoting a real price** was judged low-quality → `_repair_low_quality_caption` collapsed `\s+` (destroying all line breaks) and stapled canned boilerplate; the rule now exempts Hebrew one-letter prefixes bound to a number/Latin token (genuine garble still caught) and repair preserves line structure. (2) A fixed **7-line footer** (5 platform links + 2 slogans) was appended to every post → replaced by the QR funnel; ≤1 link line remains. (3) A **static hashtag line** → rotating HE/AR/EN pools (`_noa_hashtag_mix`/`_enrich_hashtags`). **Personality:** system prompt rewritten into an explicit smart/funny/human/selling contract + a ban on writing its own disclosure sentences inline; mirrored into the loop prompts. **QR funnel:** `social/qr_media.py` composes thumbnail(or brand canvas)+QR+scan-strip → content-addressed `thumbs/qr/<sha256>.jpg` in the existing private bucket, served by the existing proxy; QR encodes `/api/v1/go?src=qr_<platform>_w<week>` → `routes/connect.py` tri-lingual (HE/AR/EN) RTL channel picker, no DB access, UTM-tagged. `qrcode>=8.0` baked into the image. **Arabic hashtags:** tag regex extended with `؀-ۿ` (they were being silently dropped) and `_normalize_noa_symbols` no longer strips `_` inside tags (it was mangling `#قطع_غيار`). **WhatsApp routing:** post approvals + Monday brief now go to the owner's WhatsApp; Telegram demoted to opt-in `NOA_TELEGRAM_MIRROR=1`. **Quiet hours:** single enforcement point `_notify_window_open`/`_wa_send_quiet` (09:00–21:00 IL) applied to all 9 owner/admin send sites + the payment-reminder loop; night messages are **queued in Redis and flushed** when the window opens (never dropped); NOA fires at a fixed IL time (`NOA_POST_HOUR_IL`, default 09:30) instead of a container-start-anchored 24h timer. **Reminder caps:** the "3 sends" was a **rolling 3-day window**, so a cart earned 3 more every 3 days forever (one live cart had **48**); now a lifetime cap + 24h minimum gap, and the pending-payment loop (which had **no cap**) capped at 3/order. **Backend errors:** `run_all_tasks` now WhatsApps the owner the failing task names when `tasks_error > 0`, deduped by failing-task-set hash with 24h TTL. | **2026-07-20 live:** two real LLM generations (Instagram/Corolla bearings, TikTok/Sportage pads) → human story opener, mechanic's tip, real catalog price, single plate-search CTA, engagement question, multi-line structure kept, 9 tags across HE+AR+EN with underscores intact, no link-footer. QR **decoded out of the final composed image** (cv2) → exactly `https://autosparefinder.co.il/api/v1/go?src=qr_instagram_w30`; picker page HTTP 200 through the public domain; media served 200 `image/jpeg` 59,963 B. Quiet hours: 03:00/06:00/08:00/21:00/23:00 blocked, 09:00/13:00/20:00 open; a simulated night alert queued to Redis and was recoverable (test entry cleaned up — nothing delivered to the owner). Cart cap live in logs: `Skip cart ba23170a… — lifetime cap reached (48/3 reminders ever sent)`. Backend healthy after rebuild+recreate, no errors from the new modules. |

**Never regress (G8, 2026-07-20):** NOA post sanitization must stay **structure-preserving and subtractive** — never collapse `\s+` across newlines, never staple boilerplate onto every post, never re-add a multi-line link footer. The lone-Hebrew-letter garble check must keep its prefix exemption (`מ-198`, `ב-2020`, `ב AutoSpareFinder`) or every real-price selling post gets mangled again. Hashtag handling must keep Arabic (`؀-ۿ`) in `_NOA_HASHTAG_RE` and must not strip `_` inside tags. **All outbound owner/customer WhatsApp must go through `_wa_send_quiet`** (or explicitly justify `critical=True`) — a new send site that calls `_wa_send` directly reintroduces 03:00 messages. **Reminder caps are LIFETIME, never a rolling window** — a rolling window is not a cap.

**Never regress:** every customer price comes from `backend/price_policy.py` (rule: §2 Pricing & VAT and `docs/PRICE_POLICY.md`) — no client-side or per-channel formula, never a flat ×1.18, never `base_price`. Customer-facing agents may only claim programs/discounts that actually exist in code+DB. Chat agents: free-text car capture + query-strip + fitment-verified fallback must stay (a category/vocab miss must never discard a `part_vehicle_fitment` match); customer replies pass through `_strip_leaked_reasoning` + `_sanitize_internal_pricing_disclosure` (never leak chain-of-thought, draft options, or internal state under 429 fallback).


## 4. Repository & Agent Architecture


The repo was reorganized so the file tree matches how the system actually runs. **The
container only mounts `backend/` → `/app`**, so anything outside `backend/` is host-side
only (docs, archives) and can never affect runtime.

### How the runtime finds moved scripts (READ before moving/renaming any backend file)
The 4 core app modules + all imported library modules stay at `/app` root; standalone
scripts live in subfolders. Imports still work by **bare name** because
`backend/sitecustomize.py` (auto-loaded via `PYTHONPATH=/app` set in `docker-compose.yml`)
appends every script subfolder to `sys.path`. So `import samelet_import_v2` resolves even
though the file is in `importers/`, for uvicorn **and** every `python3 /app/.../X.py`
subprocess. **Rules when touching backend files:**
- Add a new script → drop it in the right subfolder; no path config needed (sitecustomize
  covers imports). Invoke it as `python3 /app/<subfolder>/<name>.py` (or `python3 -m <name>`).
- Move/rename a script → also fix (a) any `python3 /app/<old>` subprocess string, (b) any
  `Path(__file__).parent …` that reaches app-root resources (`state/`, `data/`, sibling
  scripts) — a file one level deep uses `Path(__file__).parent.parent` to reach `/app`.
- A file **imported by the app** (`from X import …` in BACKEND_*/routes/services/agents)
  must stay at `/app` root (or be added to sitecustomize).
- `state/` (the `worker_state` volume) is always `/app/state`; `data/`, `uploads/`,
  `test_images/` are always at `/app`. Never anchor them off a subfolder's `__file__`.

### Creating a NEW file — place it right AND wire it in, in the same change (MANDATORY)

The 2026-07-18 reorg happened because new files had been written to the flat root and left
loosely connected. **Do not repeat that.** A new file is not "done" until it is (a) in the
correct folder and (b) actually reachable/active in the system — never "write to root now,
move/wire later."

**Where each new file goes (decide BEFORE writing it):**
| New file is… | Put it in | And wire it by… |
|---|---|---|
| an importer (writes catalog/prices) | `backend/importers/` | invoke as `python3 /app/importers/<name>.py`; follow the Import Data Standard + SQL patterns; add the top-of-file docstring |
| a site harvester | `backend/harvesters/` | if it should run continuously, register a supervised loop in `BACKEND_API_ROUTES.startup()` via `_supervised_task(...)`; anchor state at `/app/state` (`Path(__file__).resolve().parent.parent`) |
| a playwright/html scraper | `backend/scrapers/` | called by its importer/`catalog_scraper` with the `scrapers/` path |
| a run_/build_/categorize_/backfill_ pipeline or cleanup job | `backend/maintenance/` | add it to `db_update_agent`/`db_cleanup_agent` task list or schedule it; use bounded batches + `SKIP LOCKED` |
| a shared library module (imported by the app) | `backend/` root | just `import <name>` — it's on the path |
| an API route group | `backend/routes/` | **`app.include_router(...)` in `BACKEND_API_ROUTES.py`** — an unregistered router is dead code |
| a supplier/price-sync service | `backend/services/` | wire into the aggregator / sync loop that consumes it |
| a customer-agent skill | `backend/agents/` or `BACKEND_AI_AGENTS.py` | reachable from `process_user_message` (the one shared brain) |
| an ad-hoc test/debug harness | `backend/devtests/` | — |
| a superseded one-off | `backend/legacy/` or `archive/` | — |
| a data dump / fixture | `backend/data/` (runtime) or `archive/data/` (host artifact) | never the repo root |
| a doc | `docs/` (topical) or root (only `CLAUDE.md`/`README.md`/`FIXES_TRACKER.md`/`ROADMAP.md`) | link it from `CLAUDE.md` if agents need it |

**Wiring checklist before closing (an orphan file is a bug):**
1. Placed in the correct folder above — never the flat root as a parking spot.
2. Connected to its trigger: router registered / supervised-task added / scheduler entry /
   caller updated — and invoked with the correct `/app/<subfolder>/…` path.
3. Top-of-file docstring (Script Documentation Standard).
4. **Proven active**, not just present: hit the route, run one cycle, or confirm the loop
   logs — a file that exists but nothing calls is not done.
5. If it makes a public-facing surface, it returns only masked/right-sized data (see the
   Partner API rules) — never raw cost/margin/supplier internals.

### backend/ layout
| Path | Contents |
|---|---|
| `/app/*.py` (33) | **Core + imported library modules** — `BACKEND_API_ROUTES` (uvicorn entrypoint), `BACKEND_AI_AGENTS`, `BACKEND_AUTH_SECURITY`, `BACKEND_DATABASE_MODELS`, `db_update_agent`, `db_cleanup_agent`, `catalog_scraper`, `meili_sync`, `email_templates`, `hf_client`, `resilience`, `watchdog_state`, `distributed_lock`, `currency_rate`, `manufacturer_normalization`, `categories`, `category_map`, `part_type_taxonomy`, `agent_todo_utils`, `invoice_generator`, `external_fitment_providers`, `ai_catalog_builder`, `auto_backup`, `harvest_heartbeat`, `workbook_normalizer`, `oempartsonline_importer`, `opel_car_parts_ie_import`, `run_rex_transport_office_pipeline`, `run_fitment_enrichment_pass`, `run_targeted_external_fitment_pass`, `build_full_car_database`, `clean_manufacturers_registry`, `ebay_fitment_backfill`, `sitecustomize` |
| `/app/importers/` (64) | One-shot & scheduled catalog/price importers (samelet, colmobil, delek, mct, kia/toyota IL, champion, car_parts_ie, rockauto, etc.) |
| `/app/harvesters/` (11) | Site harvesters — `car_parts_ie_flaresolverr_harvester` & `amayama_flaresolverr_harvester` (both supervised from `BACKEND_API_ROUTES`), champion/toyota/kia IL, rockauto, spareto, tecdoc |
| `/app/scrapers/` (15) | Playwright / HTML scrapers (`oem_parts_online_scraper` spawned by `catalog_scraper`, febest, gm/audi/bmw/lr playwright, etc.) |
| `/app/maintenance/` (30) | `run_*/build_*/categorize_*/backfill_*/seed_*` pipeline & cleanup jobs, fitment passes, dedup, vat/margin fixes |
| `/app/devtests/` (6) | Ad-hoc test/debug harnesses (`_*_test.py`, `test_*.py`) — NOT the pytest suite |
| `/app/legacy/` (1) | Superseded one-off scripts kept for reference |
| `/app/routes/` `services/` `social/` `agents/` | App packages (API routes, supplier/price-sync services, whatsapp/telegram providers, agent memory) — unchanged |
| `/app/tests/` | pytest suite (unchanged) |
| `/app/data/` `state/` `uploads/` `test_images/` `alembic*/` `scripts/` | Data files, persistent worker state (volume), uploads, migrations, shell scripts — unchanged |

### repo root layout
| Path | Contents |
|---|---|
| `CLAUDE.md` `README.md` `FIXES_TRACKER.md` `ROADMAP.md` | Canonical docs (kept at root) |
| `docker-compose.yml` `.env` `.gitignore` `requirements.txt` | Config |
| `backend/` `frontend/` `whatsapp-bridge/` `deploy/` `database/` | Services |
| `docs/` | Topical docs (`skills.md`, `phases.md`, `UI_UX.md`, `roadmap.md`, `SUPPLIERS.md`, import guides) + `docs/schema/` DB schema dumps |
| `archive/scripts/` | Host-side one-off dev scripts (fix_/patch/cm_/update_ … — never run by the container) |
| `archive/data/` | Old JSON/xlsx/pdf data dumps + compose backups (host-side artifacts) |

---



> Merged from the former `claude.md`. Where the two disagreed, the **rest of this file wins**
> — it is newer. In particular the OLD claude.md pricing/import-SQL specifics are SUPERSEDED
> and must NOT be reintroduced: VAT is **conditional** (`get_supplier_vat_rate`: 18% LOCAL/IL
> only, 0% foreign — see the Never-regress note under G2), `part_condition` is **lowercase**
> (`'new'`, never `'New'`), and `supplier_parts` upserts use
> **`ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key`** (never
> `(part_id, supplier_id)` in importers). See "MANDATORY: Before Writing Any Importer".

### Web scraping — always use the browser/FlareSolverr path
The server IP is Cloudflare/anti-bot blocked; direct `urllib`/`requests`/`httpx` to external
sites will fail. Use the browser tool / FlareSolverr harvesters. **Two-step pattern:** (1)
extractor scrapes → JSON on disk; (2) a separate importer reads the JSON → Postgres. Internal
calls (localhost, inter-container) may use plain HTTP.

### The two agent layers
**Layer A — AI customer agents** (`BACKEND_AI_AGENTS.py`, all on gpt-oss-120b via the HF Router pinned to DeepInfra →
Groq fallback; Cerebras OFF permanently since 2026-10-27 (owner) — `hf_client.py`; one
shared brain `process_user_message`): AVI (router), NIR (parts/fitment/OEM), MAYA (sales/
pricing), LIOR (orders), TAL (finance/VAT/invoices), DANA (support/returns/warranty), OREN
(security/fraud), SHIRA (marketing), BOAZ (supplier B2B + daily price sync), NOA (social),
REX (scraper coordinator). Full skills → `docs/skills.md`.
**Owner WhatsApp console** (`agents/owner_console.py`, added 2026-07-23): the OWNER's WhatsApp
messages (`OWNER_WHATSAPP_PHONE`) are intercepted in `routes/webhooks.py` BEFORE the customer
brain and routed here — a private ops console. Two-way owner-mode chat with AVI (default) / NOA
(prefix "נועה"/"noa") via a DIRECT `hf_text` call (NOT `get_agent("router_agent")` — that's a
JSON classifier and emits garbage on freeform chat) seeded with a live system-status block +
rolling Redis history. Deterministic commands: `סטטוס`/status, `שאיבה`/harvester, `פוסטים`/posts,
`אשר <id>`/approve (marks approved + publishes via `social/registry.dispatch`), `דחה`/reject,
`עזרה`/help — so the owner acts on NOA's approval notifications by replying. Uses its own
CATALOG-DB session (those tables aren't in the PII DB the webhook passes).
**Email Agent** (`backend/email_agent/`, added 2026-10-04 — full reference `docs/EMAIL_AGENT.md`): reads
the business Gmail mailbox, classifies, resolves context against the DB, applies a response policy and
prepares DRAFTS. **It never sends on its own**: every request passes a six-endpoint allowlist
(`assert_allowed`), `policy.send_allowed()` returns False unconditionally, and `email_agent_messages` has
`CHECK (sendable = false)`. **The ONLY send path (owner decision 2026-10-27, migration 0041):** the owner
types `שלחמייל <ref>` in the WhatsApp console → `owner_flow.approve_and_send` records `send_approved_at` →
`GmailClient.send_approved_draft(OwnerApproval)` → `POST /drafts/send` of that one existing draft
(`messages/send` stays unreachable; DB `CHECK (sent_at IS NULL OR send_approved_at IS NOT NULL)`).
**Owner flow** (`email_agent/owner_flow.py`, run after every armed cycle): important mail (needs a human / has a
draft / customer, order, refund, complaint, payment, security, supplier) → immediate WhatsApp alert via
`notify_owner` category `email` + one `agent_todos` task for the owning agent (LIOR/DANA/TAL/BOAZ/NIR/OREN,
category `email`); everything else → 09:00 IL digest. Console: `מיילים` · `מייל <ref>` · `שלחמייל <ref>` ·
`סגורמייל <ref>` · `משימות מייל` · `סגורמשימה <ref>` (`agents/email_console.py`). The mapped agents do NOT
act on these todos autonomously — they are a visible worklist. Off unless `EMAIL_AGENT_ENABLED=1` and `GMAIL_OAUTH_*` are set.
**LIVE since 2026-10-10** (`EMAIL_AGENT_ENABLED=1`, runtime control armed; Gmail drafts ON since 2026-10-25,
grant `gmail.readonly` + `gmail.compose`; owner flow live 2026-10-27 17:17 IL) - see `docs/EMAIL_AGENT.md` § Live state.
Runtime kill switch (no restart): `email_agent_cli.py halt|arm|control` → `/app/state/email_agent_control.json`;
missing/invalid file = halted (fail closed), and `run-once` refuses while halted. Context is
resolved only by exact DB match; an unauthenticated sender, another account's order or more than one
candidate stays unresolved and goes to a human.
**Supplier lifecycle (NIR → Email Agent → BOAZ → procurement) — DEPLOYED 2026-10-28 20:20 Israel (PII migration 0042,
all five supplier switches OFF; each switch is a separate owner approval; bundle/runbook/rollback in
`candidates/supplier-lifecycle-2026-10-27/` + `/root/asf-deploy-20261028T181942Z/`):** see `docs/SUPPLIER_LIFECYCLE.md`.
One-shot approvals (purchase order, supplier payment) are consumed ATOMICALLY (`supplier_authorization.consume_once`) —
never reintroduce a check-then-mark consume (it let 3 of 5 concurrent payment webhooks through on one approval).
Which code a process runs is proven with `maintenance/runtime_identity_check.py --pid 1 --expect MOD=SHA`, not a disk hash. Never copy candidate files into the bind-mounted
`backend/` as a parking spot — every container start and every lazy import runs whatever is on disk. Separate permissions (`supplier_authorization.py`): a customer payment
never authorizes a supplier order or payment; `אשררכש` / `אשרתשלוםספק` / `אשרספק` / `אשרפניותספקים` are the only
approval sources. BOAZ owns todos under every spelling (`agent_identity.py`) and executes them (`boaz_tasks.py`).
**Layer B — pipeline workers**: `catalog_scraper` (ingest), `db_cleanup_agent` (30s self-heal),
`db_update_agent` (`run_all_tasks` 3×/day at 01:00/03:00/05:00 Israel, `DB_AGENT_RUN_TIMES_IL`), `ai_catalog_builder` (enrichment), `meili_sync` (fixed 02:15/04:15/06:15 IL, `MEILI_SYNC_TIMES_IL`)
(indexing), `run_rex_transport_office_pipeline` (vehicle registry), REX harvest queue,
`services/ebay_price_sync` + `aliexpress_price_sync`, `auto_backup` (24h). Phase order →
`docs/phases.md`.

### Shared infrastructure
- **Memory** (`agents/memory.py`): in-process → Redis → Postgres. Agent-scoped keys are plain;
  cross-agent shared keys are `shared:{key}`. Workers MUST `write_worker_heartbeat()` at the
  start and end of every cycle — it's how agents know a worker is alive.
- **Alerting** (`_health_monitor_loop`, every 5 min): Redis-backed cooldowns survive restarts;
  container-lifetime guard suppresses restart-orphan false alerts (see the FIXES_TRACKER
  "Worker failed / Zombie" root-fix). Never alert on a job whose last activity predates the
  current container.
- **Zombie auto-fix** (health check 5b): a `running` job silent >30 min gets its Redis lock
  cleared, `job_registry` marked terminal, owner alerted once (24h cooldown). No manual
  `redis-cli DEL` needed.
- **Todos** (`agent_todo_utils.py`): read active todos at the start of every cycle.
- **Resilience** (`resilience.py`): wrap all external calls in `@retry_with_backoff`
  (retry 429/503/504; skip 401/403/404).
- **Distributed lock** (`distributed_lock.py`): acquire `autospare:lock:{job_name}` before any
  write-heavy job; never run two instances of the same job at once.
- **Job registry**: `job_registry_start/heartbeat/finish` around every job.

### Golden rules (every task, every session)
1. **Assignment Execution Protocol first** (§1) — a persistent requirement register, dependency-aware (never a bare
   "in order" list), closed only by the per-requirement final report.
2. **Root-fix only** — no patch-as-final; if an emergency guard is needed, mark it temporary
   and land the root fix in the same cycle. Apply the fix in source first, then rebuild/redeploy
   (never a running-container hotfix as the permanent fix).
3. **Verify from real data, not .md files** — read the live container/DB/source; docs can be
   stale. A goal is not done until an end-to-end check against the LIVE system proves the
   *outcome* (see the PLATFORM GOALS "not Done until VERIFIED" rule).
4. **Check breaking points** — auth, payment, data path, API/route contracts — before closing.
   Before wiring any CTA/nav link, confirm the target route exists in the served app (don't
   point at paths that silently fall back to the landing page).
5. **Document every fix** in `FIXES_TRACKER.md` (append at the END: `## YYYY-MM-DD - #N title. Status: …`, next
   global number) and every affected doc (`docs/roadmap.md` / `docs/phases.md` / `docs/PRE_LAUNCH_CHECKLIST.md` /
   topical docs) — §1 protocol step G. Before a task, grep the tracker for the components/issue numbers involved
   (don't read all 10k+ lines); a read-only mission writes no entry (put it in the report).
6. **Never fabricate data** — counts/metrics/statuses come from live queries, never invented.
7. **Clean before you build (owner rule 2026-10-31).** Before adding a feature or changing code,
   clean up the code the change touches, and remove the leftovers it creates or replaces:
   - **Before the change, in the files and functions you touch:** remove dead code, unused imports
     and variables, commented-out blocks, duplicated logic (call the existing function instead of a
     second copy), stale comments and docstrings that no longer match the code, and obsolete
     fallbacks or flags that nothing reads. Check for an existing implementation first (`grep`)
     and extend it — never build a parallel one.
   - **After the change:** delete what it superseded (the old code path, its constants, its config
     entries, its now-wrong tests and docs) and every working leftover — scratch/backup copies
     (`*.backup`, `*.bak`, `*.orig`), debug prints, temp files, test containers/volumes/images,
     stray `__pycache__` in bundles. No orphan file, no second copy, no "kept just in case".
   - **Scope:** the cleanup stays inside what the change touches; anything wider is recorded in
     `FIXES_TRACKER.md` as a follow-up, not done silently. Cleanup is a code change like any other:
     covered by tests, compared against the baseline, and listed in the tracker entry (what was
     removed and why). Never delete data, production files you did not create, or anything you
     have not traced to "unused" with a real check (grep for callers, imports, config, schedules).
8. **Production safety (owner 2026-11-01; "default to execution" in §1 does NOT cover these).** Never
   restart, recreate, stop or cycle a production service, never interrupt an active scan / harvester /
   background job, never make a destructive production data change or a commercial action (supplier
   order, payment, customer charge, real email) without the owner's explicit approval for that operation.
   Keep three stages separate: isolated testing → production read-only verification → production
   deployment; an isolated test never proves a deployment. Record evidence, test results, changed files
   and rollback for every change, and give every requirement and the assignment a status from §1 protocol step I,
   alongside the VERIFIED / ASSUMED / APPLIED / NOT DONE labels of §1 rule 5. Approvals follow §1 step H: finish all
   non-gated work first, then ask in one consolidated message.
9. **Model selection (owner 2026-11-01).** Default model: Claude Sonnet 5.5 at medium effort (project
   `.claude/settings.json`: `model` + `effortLevel`). Escalate to Opus 5.5 only for the part that needs it —
   hard root-cause analysis, cross-system debugging, architectural decisions, or work Sonnet cannot do
   reliably — never just because a task is long or touches many files; hand back to Sonnet for routine
   implementation and verification, passing the findings, decisions and evidence so nothing is
   re-investigated. Never silently substitute a model; say so when a model is unavailable. Save tokens
   without lowering investigation, implementation, test or verification quality. **Mechanism:** the session
   model cannot switch itself — consult Opus through the `advisor` tool (when present) or a fresh `Agent` with
   `model: "opus"` (independent review), per §1 protocol step E.
10. **Two-Attempt Debugging Rule (owner 2026-11-02).** For a debugging problem that needs investigation
    and code changes: (1) at most TWO genuine attempts; (2) each must use a meaningfully different
    diagnostic or fix — never the same command or a parameter tweak without a reason; (3) each records the
    suspected root cause, a targeted fix or concrete diagnostic, a relevant verification test and the
    actual outcome; (4) fixed + relevant tests pass within either attempt → continue normal verification
    and close; (5) two attempts failed or made no meaningful progress → STOP debugging independently and
    escalate to Opus; (6) never start a third independent attempt before escalating; (7) never split the
    same underlying issue into several nominal tasks to reset the count. Not for routine implementation,
    normal testing, documentation or unrelated work.
    **Mechanism (verified 2026-11-02; procedure `.claude/process/DEBUG_ESCALATION.md`, moved from the
    `candidates/maint-safety-2026-11-01/process/` bundle 2026-11-27):**
    the `Agent` tool with `model: "opus"` and NO `subagent_type: "fork"` (a fork ignores the model
    override) starts a fresh Opus agent; its only context is the prompt, so the prompt IS the handoff
    package — build it with `python3 .claude/process/debug_escalation.py handoff` (ledger dir:
    `DEBUG_LEDGER_DIR`, default `./.debug_ledger/`, git-ignored) (attempt ledger, exact errors, files,
    git state, constraints, acceptance criteria). Opus owns the task to completion (root cause, fix,
    regression tests, run them, report commands + results); its final message returns to Claude, who then
    reviews the diff, re-runs the acceptance tests, rejects unsupported PASS claims, verifies integration and
    (where authorized) production behaviour, updates docs/tracker, lists unresolved limits and closes (ledger: `debug_escalation.py resolved`). While
    Opus works, Claude continues the other independent register items (§1 step C) and stays accountable for
    the whole assignment. Handoff is
    NOT automatic: Claude must make the `Agent` call. Escalation grants Opus no extra permission — all
    production-safety rules (rule 8) and approval gates stay in force; never put credentials, secrets or
    private customer data in the package.

### Conflict resolution / confidence tiers (never overwrite higher with lower)
`1.00` official manufacturer/importer · `0.90` OEM cross-reference · `0.85` known aftermarket
(`manufacturer_normalization.py`) · `0.65` marketplace APIs (eBay/AliExpress) · `0.50` scraped web.

### Standard job result JSON
`{"task","status":"ok|error|skipped","scanned","updated","flagged","elapsed_s","errors":[]}`

### Script documentation standard
Every backend script keeps a top-of-file docstring: `Script:` / `Purpose:` / `Process:` steps /
`Data Imported/Modified:` (which tables/fields) / `Data Sources:` (URLs) / `Missing Data
Delegation:` / `Last Updated:`. Update it when you change the script.

---


## 5. Hard Constraints


Goal: our own catalog cannot be scraped the way we scrape others, and no single expensive endpoint can take the box down.

- **DB network isolation (verified secure)**: `postgres_catalog`/`postgres_pii` bind to `127.0.0.1` only; Meilisearch + Redis have NO host port mapping (internal docker network only). Never add a `0.0.0.0` or public port mapping to any data service.
- **Real client IP behind Cloudflare** — nginx MUST restore the true client IP from `CF-Connecting-IP` via `set_real_ip_from <CF ranges>` + `real_ip_header CF-Connecting-IP`. Without it, `$remote_addr`/`X-Real-IP` is the Cloudflare EDGE IP, so every per-IP rate limit keys on the wrong address (real users share buckets → false 429s; attackers get no throttle). The CF ranges are listed in `deploy/nginx.conf`; refresh from https://www.cloudflare.com/ips/ if they change.
- **nginx rate limit** — `limit_req_zone $binary_remote_addr zone=catalog_api rate=20r/s` + `limit_req zone=catalog_api burst=40 nodelay` on `/api/`. 20r/s sustained + 40 burst = generous for real page loads (which fire several calls at once), trips a catalog scraper. Depends on the real-IP fix above to be meaningful.
- **Backend per-endpoint limits still apply** (keyed on the now-correct IP): search 30/min, autocomplete 30/min, plate 20/min, VIN 10/min. Any NEW public catalog-read endpoint must add `check_rate_limit`.
- **Expensive enumeration endpoints MUST cache + single-flight** — `/parts/manufacturers` (and models/categories) run `SELECT DISTINCT` full-scans over 4.18M+ rows. `manufacturers` had NO cache: every hit ran the scan and concurrent hits STAMPEDED (each its own 4M-row scan), taking the box down under load/harvest (2026-07-07 incident). Pattern now: 10-min in-process cache + `asyncio.Lock` single-flight (`MANUFACTURERS_RESPONSE_CACHE` + `_MANUFACTURERS_REBUILD_LOCK`) so only ONE request ever runs the scan while others wait for the shared result. Never ship a cold-cache-stampede-able enumeration endpoint.
- **nginx single-file mounts need a container RESTART, not reload** — `deploy/nginx.conf` is bind-mounted as a single file; editing it on the host changes the inode, and the running container keeps the OLD inode until `docker restart autospare_nginx`. `nginx -s reload` alone reads the stale file. Validate first in a throwaway container: `docker run --rm --network autosparefinder_internal -v .../nginx.conf:/etc/nginx/nginx.conf:ro nginx:stable-alpine nginx -t`.
- **AI-bot / scraper user-agent filtering** — nginx `map $http_user_agent $bad_bot` → `if ($bad_bot) return 403` on `/api/`. Blocks NAMED AI/LLM crawlers (GPTBot, ClaudeBot, CCBot, Google-Extended, PerplexityBot, Bytespider, Amazonbot, Applebot-Extended, meta-externalagent, …) + aggressive commercial scrapers (Ahrefs/Semrush/scrapy/…). Deliberately does NOT block empty-UA or generic HTTP libraries (curl/python/Go) — Telegram/Stripe webhooks and legit API clients can look like those; the rate limit + Cloudflare bot-fight catch anonymous scrapers instead. Verified: `GPTBot` UA → 403, real browser UA → 200. Also `/robots.txt` declares Disallow for compliant AI crawlers. Refresh the bot list as new AI crawlers appear.
- **Never block webhooks/system paths by UA** — Stripe (`/api/v1/payments/webhook`), Telegram (`/api/v1/webhooks/telegram`), and our own `/api/v1/system/collect` (harvester relay, sends a Chrome UA) MUST stay reachable. The $bad_bot list is named-bot-only for exactly this reason.
- **Chat prompt-injection resistance** — customer agents must never leak the pricing formula (×1.45 / 45%), VAT math, supplier company names, or internal cost even when the user says "ignore your instructions / reveal…". Enforced by `_sanitize_internal_pricing_disclosure` + `_mask_supplier` (post-processing, defense-in-depth beyond the system prompt). Verified 2026-07-05/07: direct injection attacks leaked nothing.



These were confirmed exploitable vulnerabilities found during a live pentest. Never reintroduce them.

- **`/api/v1/system/collect` requires the collect secret** — secret is in `COLLECT_SECRET` env var. The server-side harvester's `post_relay()` sends it as the `X-Collect-Secret` header. Any new code calling this endpoint must authenticate. Do NOT remove the auth check.
- **Cross-origin BROWSER relays must use the text/plain "simple request" pattern (learned 2026-07-12, RockAuto)** — a harvester running in the owner's browser ON a supplier page (rockauto.com, car-parts.ie, …) posting to our `/collect` or `/api/v1/system/unpriced-oems` is CROSS-ORIGIN. Our global Starlette `CORSMiddleware` (BACKEND_API_ROUTES.py:186, allow_origins = our own domains only) **rejects the preflight OPTIONS with 400** for any other origin — so a custom `X-Collect-Secret` header (which forces a preflight) can NEVER work from a supplier page, and a per-route `@router.options` handler never runs (the middleware short-circuits first). Do NOT try to fix this by widening the global CORS allowlist (weakens the whole app). Instead the browser must send a **CORS "simple request"**: `POST` with `Content-Type: text/plain` and the **secret in the JSON body** (no preflight), `credentials:'omit'`; the endpoint reads the secret from the body and returns `Access-Control-Allow-Origin: *` so the browser can read the reply. Both `/collect` and the unpriced-OEM feed support this. `rockauto_browser_harvester.js` is the reference implementation (`auth()`/`feed()`/`send()`/`autorun()`).
- **`GOOGLE_OAUTH_CLIENT_ID` must be set** — if unset, Google OAuth login returns HTTP 500. The audience check must NEVER be conditional on whether the env var is set. Pattern: `if not client_id: raise 500; if aud != client_id: raise 401`.
- **Rate limits use `X-Real-IP`, not `X-Forwarded-For`** — nginx sets `X-Real-IP` to `$remote_addr` (unspoof-able). `X-Forwarded-For` is client-controlled and must never be used for rate limiting or IP-based security decisions.
- **Webhook secrets always fail CLOSED** — pattern: `if not secret or header != secret: raise 403`. Never `if secret and header != secret` (passes when secret is unset).
- **Never print OAuth tokens to stdout** — they go to `docker logs` forever. Store tokens in DB or env; never log them.
- **Rate limit return values must be checked** — `allowed = await check_rate_limit(...)` then `if not allowed: raise 429`. Discarding the return value = no rate limit.
- **All new internal-only endpoints** (harvest relay, import triggers, admin actions) must be authenticated. Options: (1) `X-Collect-Secret` style shared secret, (2) `Depends(get_current_admin_user)`, (3) nginx internal-only restriction.
- **`supplier_parts` ON CONFLICT for re-harvest importers must include `price_ils` and `is_available`** — omitting them means price changes and stock-outs are silently discarded.
- **`task_normalize_base_price_batched` formula**: `supplier_parts.price_ils` = ex-VAT cost → `base_price = cost × 1.45`, `importer_price_ils = cost`. Never reverse this.
  **#119 candidate (`candidates/maint-policy-2026-10-31/`, NOT DEPLOYED — until it is, the line above describes the live code):**
  every non-importer `base_price` writer calls `db_update_agent.apply_base_price_rule` (policy net of the cheapest customer-visible
  offer of the part's own class, else of the catalog estimate; see `docs/PRICE_POLICY.md`), and `importer_price_ils` /
  `max_price_ils` are filled only when empty and only from a LOCAL offer.
- **Batched loops with `updated_at=NOW()` must be bounded** — use `cutoff_id = MAX(id) WHERE updated_at > :since` at loop start; add `AND id <= :cutoff_id` to the batch query. Otherwise the loop perpetually refreshes rows back into scope and never terminates.
- **Multithreaded state dicts need a `threading.Lock()`** — any dict shared across threads (harvester `state`, etc.) must protect all read-modify-write operations and file writes with a lock.


## 6. Mandatory Importer Patterns

> **⇒ The full standard now lives in [`docs/IMPORTER_RULES.md`](docs/IMPORTER_RULES.md),
> and it is ENFORCED by `backend/maintenance/audit_importers.py` (exit ≠ 0 on any
> ERROR). Run the audit before shipping an importer AND before triggering any
> import or backfill.** The section below is the quick reference; the doc is
> authoritative and explains which real incident earned each rule.
>
> **The audit PROCESS itself is documented** in `docs/IMPORTER_RULES.md` §10 — how to
> add a rule, how to validate it against reality before fixing anything (every rule
> written that day produced false positives on its first run), and why a check that is
> wrong more often than right is worse than no check. Read it before adding a rule.
>
> Written prose was never enough: on 2026-07-28 an audit of the existing 64
> importers found **146 ERROR-level violations** of rules that had been documented
> here for months — including SEVEN mutually-incompatible category vocabularies all
> writing to the same column. If you add a rule to the doc, add a check to the
> script in the same change.


These bugs recurred multiple times because I wrote from memory instead of checking. Read this section before writing any importer or scraper SQL.

### SQL Pattern 1 — ON CONFLICT for supplier_parts (CRITICAL)
`supplier_parts` uniqueness (since 2026-11-06 19:31 IL; the old `uq_supplier_parts_part_supplier` was dropped):
- `supplier_parts_supplier_id_supplier_sku_key (supplier_id, supplier_sku)` — the one that gets hit on re-import (commercial-offer identity)
- `uq_sp_part_supplier_variant (part_id, supplier_id, normalized part_type) WHERE part_type IS NOT NULL` and `uq_sp_part_supplier_untyped (part_id, supplier_id) WHERE part_type IS NULL` — one offer per supplier+part+variant

**ALWAYS use:**
```sql
ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO UPDATE SET
    price_ils=EXCLUDED.price_ils, is_available=EXCLUDED.is_available, updated_at=NOW()
```
**NEVER use** `ON CONFLICT(part_id, supplier_id)` in importer scripts — this misses the constraint that actually fires.

### SQL Pattern 2 — importer_price_ils in ON CONFLICT UPDATE (CRITICAL)
**ALWAYS use CASE WHEN in UPDATE to preserve existing value:**
```sql
importer_price_ils = CASE WHEN EXCLUDED.importer_price_ils > 0
    THEN EXCLUDED.importer_price_ils
    ELSE parts_catalog.importer_price_ils END
```
**NEVER** use `importer_price_ils = 0` or `importer_price_ils = EXCLUDED.importer_price_ils` without the CASE guard.

### SQL Pattern 3 — part_condition casing
Always lowercase: `'new'`, `'used'`, `'oem'`, `'aftermarket'` etc.
**NEVER** `'New'`, `'OEM'`, `'Used'`.

### SQL Pattern 4 — OEM number matching (normalized)
IL importer PDFs often use no-dash OEMs (`517592B300`), catalog has dashed (`51759-2B300`).
**Always try exact first, then normalized:**
```sql
-- Exact
WHERE oem_number=$1 AND LOWER(manufacturer)=LOWER($2) AND is_active LIMIT 1
-- Normalized fallback
WHERE REPLACE(REPLACE(UPPER(oem_number),' ',''),'-','')=$1 AND LOWER(manufacturer)=LOWER($2) AND is_active LIMIT 1
```

### SQL Pattern 5 — Per-row savepoints in asyncpg
For row-by-row imports, wrap each row in `async with conn.transaction():` inside the outer loop.
This creates a SAVEPOINT so one failed row doesn't abort the whole batch.

### Pattern 6 — Harvester JSON output format
`toyota_il_harvester.py` and similar write `{"parts": [...], "count": N}` (dict wrapper, not list).
Importer must unwrap: `raw = json.loads(f); parts = raw if isinstance(raw, list) else raw.get("parts", [])`

### Price Gap Root Cause (documented 2026-07-02)
The 2.1M OEMPartsOnline parts with 0% IL price exist because:
1. OEMPartsOnline imported parts with no IL prices (US catalog)
2. IL importer imports try to match by exact OEM number — format mismatch creates DUPLICATE entries instead
3. `dedup_catalog_parts` in db_update_agent deduplicates by SKU/name, NOT by normalized OEM number
4. Fix: use normalized OEM matching in all importers + run `fix_oem_price_gaps.sh` after imports
5. Long-term: add `dedup_by_normalized_oem` task to db_cleanup_agent

---


## 7. IL Importer Site Reference

> **Moved (2026-11-27, #160):** the full per-site reference is in [`docs/claude/IL_IMPORTER_SITES.md`](docs/claude/IL_IMPORTER_SITES.md)
> (verbatim except marked amendments). **Trigger: read it BEFORE touching** an IL importer/harvester (Champion, Kia, Toyota/Union Motors,
> Colmobil PDFs, Delek API, samelet, MCT), the car-parts.ie harvester/seeder/supervisor loops, FlareSolverr, or harvest
> tab strategy. The rules below stay loaded every session.

- **Pricing / VAT / import formula**: §2 Pricing & VAT and §6 (VAT 18%, never 0.17 — see FIXES_TRACKER #130).
- **Restarts**: §2 Deployment & Config + §4 golden rule 8 (preflight, owner approval).
- **Monitoring wakeups** are opt-in: §11 "Monitoring wakeups".
- **NIR todos** (`agent_todos` assigned to NIR) are human tasks for the owner (contact importers directly) — see §1 step H "Owner-only keystrokes".
- **Server-side scraping**: external sites are Cloudflare/anti-bot blocked — use FlareSolverr/the browser path (§4).
- **car-parts.ie numeric `maker_id/model_id/car_id` "spares-search" cascade is a DEAD END** — never rebuild it.
- **Price basis per source differs:** Kia IL and Toyota (Union Motors) list prices are **EX-VAT**; Champion Motors and the
  Colmobil PDFs are **incl. VAT** (`cost = price/1.18`). Check the site's basis before any import — a wrong basis is a wrong
  customer price.
- **Harvester relay posts** to `https://autosparefinder.co.il/api/v1/system/collect` (`.co.il`) need a browser User-Agent
  and the collect secret.
- **Never raise harvester parallelism** (`PARALLEL_SESSIONS` / `HARVESTER_PARALLEL_SESSIONS`) without re-measuring load avg,
  postgres CPU and models/10 min before and after (§11).
- **Orphaned DB connections**: keep the `tcp_keepalives_*` + `idle_in_transaction_session_timeout` settings; the watchdog never
  kills current-container connections.

---



## 8. Business Rules


**Rule 1 — All data must flow through the pipeline, not directly to DB in isolation**
Every scraper/importer must write to these 3 tables together (atomically):
- `parts_catalog` — the part record
- `supplier_parts` — the price/availability record (links supplier to part)
- `part_vehicle_fitment` — fitment rows (if vehicle data available)

**Rule 2 — `importer_price_ils` is the computed ex-VAT cost, never 0** — formula: §2 Pricing & VAT
"Import formula"; ON CONFLICT guard: §6 Pattern 2.

**Rule 3 — `part_condition` is lowercase** — §6 Pattern 3.

**Rule 4 — Pipeline ownership (who writes what)**
| Owner | File | Schedule | Responsibility |
|---|---|---|---|
| **REX** | `catalog_scraper.py` | Every 3h | Catalog discovery — scrapes OEM sites, writes parts_catalog + supplier_parts |
| **DB Update Agent** | `db_update_agent.py` | 01:00/03:00/05:00 Israel | Data quality — normalizes names, types, categories, prices, fitment |
| **DB Cleanup Agent** | `db_cleanup_agent.py` | Every 30s | Continuous self-healing — fixes types, OEM numbers, categories, zombie jobs, **importer_price_ils=0 → heal**, **'New'→'new' → heal** |
| **Boaz** | `BACKEND_AI_AGENTS.py` | Daily | Price pipeline — market price drift on supplier_parts |
| **Importers** | `samelet_import_v2.py` etc. | On-demand | IL importer data — must follow Rules 1-3 above |

**DB Cleanup Agent self-healing tasks (added 2026-06-18)**:
- `task_heal_importer_price()` — every 30s (backs off when idle): parts with `max_price_ils > 0`, no importer and no online cost →
  `importer_price_ils` = the cheapest Israeli supplier offer's cost (`base_price` per the #119 rule once deployed, else cost × 1.45)
- `task_heal_part_condition()` — every 30s: finds uppercase `'New'/'OEM'/'Used'` etc., converts to lowercase

These tasks are the **safety net** — even if an importer bug writes wrong data, the cleanup agent will auto-correct it within 30 seconds.

**Rule 5 — Before writing any new importer/scraper, verify it sets**:
- `importer_price_ils` = computed cost (not 0, not None)
- `base_price` = cost × 1.45
- `max_price_ils` = consumer reference price
- `part_condition` = `'new'` (lowercase) for new parts, `'oem'` for OEM parts
- At least one row in `supplier_parts` with `is_available=True` and `price_ils > 0`

---



Every time REX, the scraper, or any importer runs, it MUST collect and store:

| Field | DB Column | Notes |
|---|---|---|
| **Part type** | `parts_catalog.part_type` | Original / OEM / Aftermarket — never blank |
| **Name** | `parts_catalog.name` + `name_he` | English + Hebrew if available |
| **Price** | `supplier_parts.price_ils` + `price_usd` | Always write to supplier_parts with source |
| **Specs** | `parts_catalog.specifications` JSONB | `{"source","source_url","part_brand","price_ils","price_usd","in_stock","oem_ref","discovered_at"}` |
| **Fitment** | `part_vehicle_fitment` rows | `(part_id, manufacturer, model, year_from, year_to)` |

### Pipeline tables to monitor
- `catalog_versions` — every import run result (type, parts added, timestamp)
- `scraper_api_calls` — API call log (eBay, Google Shopping, etc.)
- `supplier_parts` — 2.3M price records by supplier; `is_available` must be true for search
- `search_misses` — real customer searches with 0 results → priority catalog gaps
- `brand_alias_review_queue` — auto-detected brand name variants; dismiss if confidence < 0.9
- `part_cross_reference` — OEM ↔ aftermarket links (25,939 rows from Febest)
- `part_vehicle_fitment` — vehicle compatibility rows (MUST exist for vehicle-filtered search)
- `job_registry` — all agent job runs with heartbeat and status

### Known pipeline bugs fixed
- `cadillac_israel_import.py` — `importer_price_ils=0` hardcoded → fixed, but it still divides by 1.17 (17% VAT): FIXES_TRACKER #130
- `gmc_buick_umi_import.py` — same bug → SQL fix applied
- OEM Parts Online supplier_parts — `is_available=false` on all 36K records → fixed to true
- Seat pads miscategorized as `brakes` → moved to `interior-comfort`
- Hyundai i35 zero fitment → 896 fitment rows added (2012-2017)
- Brand alias queue (11 pending low-confidence) → all dismissed

### What captures fitment today
- `run_brand_discovery()` in catalog_scraper.py → writes fitment if source provides `part["fitment"]` list (fixed 2026-06-17)
- `febest_scraper.py` → full fitment from detail pages (192 catalog pages)
- `post_import_fitment.py` → backfills fitment for IL importer Excel/PDF imports
- `isuzu_excel_import.py` → has native fitment parsing

### Scraper data sources priority
1. **OEM parts online** (oempartsonline.com) — OEM quality, has fitment
2. **Febest** (febest.de) — OEM cross-refs + fitment, 192 pages
3. **Official IL importer sites** — IL prices, partial fitment
4. **eBay** — fallback, broad coverage, no fitment
5. **RockAuto** — fallback, US prices, some fitment


## 9. Feature Modules

> **Moved (2026-11-27, #160):** the full module reference is in [`docs/claude/FEATURE_MODULES.md`](docs/claude/FEATURE_MODULES.md)
> (verbatim except marked amendments). **Trigger: read the matching block BEFORE touching** categorization (`category_map.py`, LLM keyword
> learning, `category_learning`), `warranty_policy.py`, the local embedding model / `embed_policy`, the `dept-*` / gstack design
> skills or the SHIRA/NOA prompt grounding, the Partner API (`/api/public/v1`), NOA social engagement (`social/engagement.py`),
> NIR supplier sourcing, part thumbnails / S3 / `parts_images`, or a Telegram bot/webhook (`routes/webhooks.py`). The hard rules below stay loaded every session.

- **Categories**: `category_map.py` is the only rule file; stored values are English slugs + `כללי`; the only fallback is `כללי`;
  every importer calls `categorize_on_ingest()`. A learned keyword goes live only after owner approval (`אשרמילה`) and the
  evidence gate; never-learn blocklist enforced at write, load AND startup purge.
- **Any background LLM task** needs all three limits: small batch, minimum interval, daily ceiling (env-gated, default off).
- **Warranty**: `warranty_policy.resolve()` in every importer; test supplier-stated with `warranty_policy.is_supplier_stated()`,
  never `== 'supplier'`.
- **Embedding model**: never promote it to autonomous writing on a benchmark alone; per-type owner-approval history first
  (`embed_policy.autowrite_enabled()` refuses by default).
- **gstack design binaries never run on the production host** — only inside the isolated `gstack_sandbox`; after any re-clone/update
  of `gstack/`, re-apply and re-verify the `/health` token-leak patch (issue #1324) before use.
- **Partner API**: every new public endpoint requires `X-API-Key` (`Depends(require_api_key)`), prices via the price policy
  (`_customer_price_fields`), returns only the masked `_shape` schema, and calls `check_rate_limit`; keep `docs/PUBLIC_API.md` in sync.
- **Telegram webhooks** (both bots) must be registered with `secret_token=TELEGRAM_WEBHOOK_SECRET`; the handlers fail closed
  without it.
- **NOA engagement**: replies are owner-approval-gated by default (`NOA_ENGAGEMENT_AUTOREPLY=0`); never invent prices/stock;
  never `:id::uuid` in `text()`.
- **NIR sourcing**: new suppliers are inserted `is_active=FALSE` and go live only on owner approval; never fabricate candidates.
- **Embedding model** runs on ONNX Runtime, never PyTorch (no swap on this host — OOM is a hard kill).
- **gstack host execution** needs an owner-added Bash permission rule; never work around the classifier.
- **Thumbnails**: the supervisor owns the import (never also run `build_part_thumbnails` by hand); `parts_images` has no
  unique `(part_id,url)` index — never `ON CONFLICT` there, cast `$2::varchar`. Customers only ever see the bucket thumbnail (`primary_image`), never a raw supplier image URL; any new
  parts-creating harvester writes `parts_images`; the bucket stays private (no public-access-block, no bucket policy).


## 10. Google Stitch Design Integration

**Installed 2026-08-12.** Security-audited (87 files, Apache-2.0, Google Labs), sandbox-tested. CLEAN.

### What it is
Google Stitch (`stitch.withgoogle.com`) is a generative UI tool — it creates screens from text/image prompts. The `stitch-skills` repo is a collection of Claude Code skills that teach me how to use Stitch's MCP server to generate, manage, and deploy designs.

### Installation paths
- **Skills repo**: `.claude/skills/stitch-skills/` (project-scoped)
- **Brand ground-truth**: `.stitch/DESIGN.md` — synthesized from `brand/*.md`. Load this before EVERY Stitch generation to prevent generic AI designs.
- **Stitch project state**: `.stitch/metadata.json` (created when first Stitch project is used)

### Available skills (invoke with `/` prefix)
| Skill | Use |
|---|---|
| `/stitch::generate-design` | Generate or edit screens from text/image |
| `/stitch::manage-design-system` | Upload DESIGN.md to Stitch, apply to screens |
| `/stitch::upload-to-stitch` | Upload local PNG/HTML/MD to Stitch |
| `/stitch::code-to-design` | Extract frontend → HTML snapshot → upload to Stitch |
| `/stitch::extract-static-html` | Snapshot a running dev server |
| `/stitch::extract-design-md` | Extract DESIGN.md from frontend source |
| `/stitch::react-components` | Convert Stitch screens → React components |
| `/design-md` | Analyze a Stitch project → synthesize DESIGN.md |
| `/enhance-prompt` | Polish vague UI ideas → Stitch-optimized prompts |
| `/stitch-loop` | Autonomous iterative website builder |

### MCP configuration — LIVE ✅ (configured 2026-08-12)
`~/.claude.json` → `projects["/opt/autosparefinder"].mcpServers.stitch`:
```json
{ "type": "http", "url": "https://stitch.googleapis.com/mcp", "headers": { "X-Goog-Api-Key": "..." } }
```
API key stored in `.env` as `STITCH_API_KEY`. 15 Stitch tools verified live:
`create_project`, `get_project`, `list_projects`, `list_screens`, `get_screen`,
`generate_screen_from_text`, `edit_screens`, `generate_variants`, `upload_design_md`,
`create_design_system`, `create_design_system_from_design_md`, `update_design_system`,
`list_design_systems`, `apply_design_system`, `delete_project`.

### Design system rules (never regress)
- **Always load `.stitch/DESIGN.md` before generating** — it contains the full brand palette, typography, and component specs extracted from `brand/*.md`.
- **Dark mode only**: base `#0F1218`, cards `#151B27`, accent `#0EA5E9`
- **Font**: Inter (primary), JetBrains Mono (OEM numbers/codes)
- **RTL**: Hebrew and Arabic must mirror correctly
- **Forbidden in Stitch**: white/light backgrounds, pill buttons, colorful gradients, generic blue (`#1a73e8`)

### Upload script (works today, no MCP needed)
```bash
python3 .claude/skills/stitch-skills/plugins/stitch-design/skills/upload-to-stitch/scripts/upload_to_stitch.py \
  --project-id <PROJECT_ID> \
  --file-path .stitch/DESIGN.md \
  --api-key <OAUTH2_TOKEN> \
  --title "AutoSpareFinder DESIGN.md"
```

---

## 11. Operations Reference


When the user asks "give me a review / review the system / check everything", always query live data and fill in this exact table format:

### Active Processes
| Process | Status | Details |
|---|---|---|
| `uvicorn` | ✅/❌ | CPU% MEM% |
| `run_all_tasks` | ✅/⏳/❌ | Current task, elapsed time |
| `meili_sync` | ✅/⏳/❌ | N/M docs (%), ETA |
| `freesbe_importer` | ✅/⏳/❌ | page N/total |

### Memory
| Container | Used | Limit | % |
|---|---|---|---|
| Backend | X GB | 2 GB | % |
| Meilisearch | X GB | 1.5 GB | % |
| Postgres | X MB | 2 GB | % |
| Redis | X MB | 256 MB | % |

### Catalog Health
| Metric | Count |
|---|---|
| Total active parts | N |
| With IL importer price | N (%) |
| With base_price | N (%) |
| With fitment data | N rows |
| Categorized | N |

### Agent Todos
| Agent | Status | Count |
|---|---|---|
| `db_update_agent` | ✅/⏳ completed | N pending |
| `rex` | ✅ | N pending |
| `db_cleanup_agent` | ⏳/✅ | N pending |
| `scraper` | ⏳/⚠️ | N pending |
| `NIR` | ⏳ human | N manual tasks |

### Job History (today)
| Job | Result | Duration |
|---|---|---|
| last run_all_tasks | ✅/❌ | elapsed |
| last scraper_cycle | ✅/❌ | elapsed |

### Open Issues
List any blockers, errors, or pending decisions.

---

### System Review Commands

```bash
# Memory per container
docker stats --no-stream --format "{{.Name}} {{.MemUsage}} {{.MemPerc}}" 2>/dev/null

# Running processes in backend
docker exec autospare_backend ps aux | grep python | grep -v grep

# Meili progress
docker exec autospare_backend tail -3 /app/state/logs/meili_sync.log 2>/dev/null

# Catalog health
docker exec autospare_backend python3 -c "
import asyncio, asyncpg, os
DB = os.environ.get('DATABASE_URL','').replace('postgresql+asyncpg://','postgresql://')
async def main():
    conn = await asyncpg.connect(DB)
    row = await conn.fetchrow('''
        SELECT
            COUNT(*) FILTER (WHERE is_active) as total,
            COUNT(*) FILTER (WHERE is_active AND importer_price_ils > 0) as with_il_price,
            COUNT(*) FILTER (WHERE is_active AND base_price > 0) as with_base_price
        FROM parts_catalog
    ''')
    print(f'total={row[\"total\"]} il_price={row[\"with_il_price\"]} base={row[\"with_base_price\"]}')
    await conn.close()
asyncio.run(main())
"

# Agent todos
docker exec autospare_backend python3 -c "
import asyncio, asyncpg, os
DB = os.environ.get('DATABASE_URL','').replace('postgresql+asyncpg://','postgresql://')
async def main():
    conn = await asyncpg.connect(DB)
    rows = await conn.fetch(\"SELECT assigned_to_agent, status, COUNT(*) FROM agent_todos GROUP BY 1,2 ORDER BY 1,2\")
    for r in rows: print(f'  {r[0]} {r[1]}: {r[2]}')
    await conn.close()
asyncio.run(main())
"

# Job registry
docker exec autospare_backend python3 -c "
import asyncio, asyncpg, os
DB = os.environ.get('DATABASE_URL','').replace('postgresql+asyncpg://','postgresql://')
async def main():
    conn = await asyncpg.connect(DB)
    rows = await conn.fetch(\"SELECT job_id, status, started_at, last_heartbeat_at FROM job_registry WHERE started_at > NOW()-INTERVAL '24h' ORDER BY started_at DESC LIMIT 10\")
    for r in rows: print(f'  {r[\"job_id\"]} | {r[\"status\"]} | {r[\"last_heartbeat_at\"]}')
    await conn.close()
asyncio.run(main())
"
```

---

### Monitoring wakeups (opt-in, 2026-11-27)
Create a recurring wakeup (CronCreate / ScheduleWakeup, every 30 min) **only when the owner asks for monitoring in this
session**. A wakeup never restarts, recreates or kills anything and never changes production — it reports (owner alerts go
through the existing notification rules). It never replaces the active assignment: re-read the task register after each
wakeup. Checks when requested: `docker stats --no-stream` (any container > 80 % memory → report), backend `RestartCount`
change (→ diagnose OOM, report), harvester/crawler progress, REX + DB agent (`agent_todos`, `job_registry`), catalogue/
categorizer counts, meili_sync progress, `run_all_tasks` heartbeat.

### Architecture Quick Reference

- **Backend container**: `autospare_backend` — uvicorn + supervised background tasks
- **Persistent volume**: `worker_state:/app/state` — survives OOM restarts
- **Meili checkpoint**: `/app/state/meili_sync_checkpoint.json` — resume after crash
- **Freesbe checkpoint**: `/app/state/freesbe_import_progress.json`
- **Worker logs**: `/app/state/logs/`
- **DB**: PostgreSQL via `DATABASE_URL` env var
- **Search**: Meilisearch at `MEILI_URL` (http://meilisearch:7700)

---

### Server Specs (Contabo VPS — upgraded 2026-07-20)

| Resource | Spec |
|---|---|
| **CPU** | **6 vCPUs** — AMD EPYC @ 2.0 GHz (1 thread/core, QEMU/KVM virtualised) |
| **RAM** | **12 GB** (11.68 GiB) — **no swap configured** |
| **Disk** | 145 GB virtual disk (QEMU, SSD-backed by Contabo), 109 GB used / 36 GB free |
| **OS** | Ubuntu 24.04 LTS, kernel 6.8.0-136 |
| **Hosting** | Contabo standard VPS |
| **IP / SSH** | 207.180.217.129, port 22 (corrected 2026-09-18 — verified live via `ip addr show` + `ss -tlnp`; the previous 161.97.158.177:63159 was stale and not reachable) |

**Container memory limits (post-upgrade 2026-07-20)**:
| Container | Limit | Notes |
|---|---|---|
| postgres_catalog | 5120m | Up from 3072m |
| postgres_pii | 1024m | Up from 768m |
| redis | 512m | Up from 256m |
| backend | 4096m | Unchanged |
| meilisearch | 2560m | Up from 1536m |
| frontend / nginx | 128m | Unchanged |

**Postgres tuning (applied 2026-07-20 via docker-compose.yml command)**:
- `shared_buffers=2GB` (was 256MB) · `work_mem=32MB` (was 8MB) · `maintenance_work_mem=256MB` (was 64MB) · `effective_cache_size=8GB` (was 512MB) · `shm_size=512m` (was 256m)

**Capacity reality check** — 11 containers run concurrently (3× Postgres, Meilisearch, Redis, backend, frontend, Nginx, FlareSolverr, WhatsApp bridge, 2× backup). Load average normally sits 8-12 on 6 CPUs (~1.5-2 per core); above 18 is oversubscription. Key constraints:
- No swap → RAM exhaustion = immediate OOM kills, no graceful degradation.
- **Car-parts harvester workers: `HARVESTER_PARALLEL_SESSIONS = 5`** (the `docker-compose.yml` default; `.env` does not set it; the code default is 1 for manual runs; values < 1 or non-numeric fall back to 1, values > 8 are clamped — `_parallel_sessions()`, logged at start-up as `parallel_sessions=N`). History, so nobody "restores" an obsolete number: 2 (2026-07-23, when every worker was a real FlareSolverr Chrome session and 4 pinned flaresolverr at ~577% CPU) → 1 (aaa4fc3, same day: workers became plain-HTTP threads, FlareSolverr only mints the cf_clearance cookie) → 3 → **5** (eb8a96f, 2026-07-23 21:54, after the `synchronous_commit=off` import fix cut postgres_catalog ~176%→92%; verified "all 5 workers producing, postgres ~130%"). Each worker adds DB import load, not browser load: raise above 5 only after re-measuring postgres CPU and load avg during a non-idle (refresh) cycle. Also `INTER_MODEL = 20 s` (env `HARVESTER_INTER_MODEL_S`). **Health (#78):** the harvester writes `/app/state/harvester_status.json` (idle / waiting_clearance / working + last real progress); the 30-min healthcheck (`harvester_health.evaluate`) treats a drained-queue rest as `idle`, and kills only on no request completed for 15 min while working, a rest that overran, or a stale heartbeat — never on log quietness alone (that killed it hourly while resting). **FlareSolverr Chrome LEAK — root-fixed 2026-07-23 (FlareSolverr is no longer the fetch engine):** FlareSolverr orphans LIVE Chrome on `sessions.destroy` and accumulates renderers within a session (leaked chromium are running procs reparented to PID 1, NOT zombies). The harvester used to route EVERY page (~80/model) through FlareSolverr's browser → constant churn → 577% CPU / load 18.7 → DB statement-timeouts. **Fix:** car-parts.ie is server-rendered (proven by the full-catalogue seeder's plain-`urllib` fetches), so the harvester now uses FlareSolverr **ONLY to mint a `cf_clearance` cookie (~2×/hour, `_solve_clearance` → destroys the session immediately)** and fetches every page via **plain `urllib` + that cookie** (`http_get`; re-mints on 403/503). `fs_get` is now a thin shim over `http_get`; the session pool / per-cycle create-destroy / worker `session_id` are all gone; workers are cheap HTTP threads. Verified: FS chrome dropped 40-52→~9 and held, load 21→10, full parts still harvested. **Never reintroduce a per-page FlareSolverr call** — that is the leak. If chrome ever climbs, check `docker exec flaresolverr ps -e | grep -c chrom` (should be a handful, spiking only during a ~2×/hour solve). Env: `HARVESTER_CLEARANCE_TTL_S` (cookie refresh, default 1500s).
- `idle_in_transaction_session_timeout = 30min` (set via ALTER SYSTEM 2026-06-30).
- Watchdog `BLOCKER_S = 2700s` (45 min) — moot for active-backend connections (never killed), relevant only for orphan-detection fallback.
- `DB_AGENT_TASK_TIMEOUT_S = 3600` — per-task timeout inside `run_all_tasks`. Set in `docker-compose.yml`.

---

### Meilisearch Sync

`meili_sync.py` previously had **no automated scheduling** — it ran once manually (2026-06-24) and silently drifted 6 days / ~580K docs behind the catalog. Added `_meili_sync_loop()` in `BACKEND_API_ROUTES.py`, registered at startup via `_supervised_task("meili_sync_loop", ...)`. Runs `python3 /app/meili_sync.py` every **2 hours** in incremental mode (`MEILI_REBUILD=0` env already set, no full rebuild).

**2026-07-02 rewrite — three root-caused bugs in meili_sync.py (do not reintroduce):**
1. **Incremental resume by id-position was broken by design.** Parts get random UUIDv4 ids; the old resume (`offset=total`, `ORDER BY id`) only saw rows sorted past the previous end position — new parts land at *random* id positions and were silently skipped every incremental run. This is what created the 620K-doc gap while the checkpoint claimed complete. Fix: a completed checkpoint (offset==total, no last_id) now triggers **updated_at-based incremental mode** — `WHERE updated_at > (last run start − 1h margin)`. The completed checkpoint's `updated_at` is the run's START time so mid-run changes are re-checked next cycle.
2. **`OFFSET N` pagination is O(N·logN) per batch** — measured ~100s/batch at offset 195K (full pass ≈ 23h). Fix: keyset pagination `WHERE id > $last_id::uuid ORDER BY id LIMIT batch` — PK index scan, constant per batch. Checkpoint stores `last_id` (and `cutoff` if an incremental run is interrupted, so resume keeps the same cutoff).
3. **No single-instance guard** — the 2h supervised loop spawned a sync while a manual catch-up was mid-pass; the two clobbered each other's checkpoint file. Fix: `fcntl.flock` on `/tmp/meili_sync.lock` at entry — a second instance prints a notice and exits 0.

- To check sync status: `docker exec autospare_backend cat /app/state/meili_sync_checkpoint.json` (shows offset, total, updated_at)
- Index doc count vs catalog: query Meilisearch `/indexes/parts/stats` and compare `numberOfDocuments` to `SELECT COUNT(*) FROM parts_catalog WHERE is_active`
- If index is significantly behind and you need an immediate catch-up: `docker exec -d autospare_backend python3 /app/meili_sync.py` (runs incremental sync in background, can take 30-90 min for millions of docs)
- `lookup_oem_spec` task times out at 1800s by design (`DB_AGENT_TASK_TIMEOUT_S` env, default 30 min) — also hits Cerebras/HF API rate limits; this is a pre-existing ceiling, not a new bug.



