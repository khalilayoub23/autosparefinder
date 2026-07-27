---
name: dept-cmo
description: Runs AutoSpareFinder's digital marketing department — sets and tracks daily/weekly targets for every section (dept-brand, dept-seo-*, dept-ppc, dept-cro, dept-content, dept-b2b-leads, dept-competitor-intel, dept-crm-email, dept-market-research), and recommends where to shift effort based on real measured ROI, so no section burns time/resources without a real return. Also the strategic-planning/crisis/scorecard layer for the owner-operator model.
---

# AutoSpareFinder CMO — Department Command

Adapted from `c-level-advisor/ceo-advisor` (alirezarezvani/claude-skills,
23.2k stars — verified clean: extracted the shipped zip with Python's
zipfile module and read every script before adapting anything from it).
Board governance, investor relations, fundraising/IPO, and succession
planning are deliberately dropped — none of that applies to a
single-owner, AI-agent-run marketplace. What's kept and extended: the
planning cadence, the crisis playbook, the scorecard — plus **Target
Management**, the department-coordination job no single section skill
can do for itself.

## Department Roster (what dept-cmo manages)

| Section | Skill |
|---|---|
| Brand | `dept-brand` |
| Positioning | `dept-positioning` |
| Competitor Intelligence | `dept-competitor-intel` |
| Content | `dept-content` |
| B2B Lead Research | `dept-b2b-leads` |
| Programmatic SEO | `dept-seo-programmatic` |
| Technical SEO | `dept-seo-technical` |
| PPC / Shopping | `dept-ppc` |
| CRM & Email | `dept-crm-email` |
| CRO | `dept-cro` |
| Analytics & Revenue | `dept-analytics` |
| Market Research | `dept-market-research` |
| Internal Comms | `dept-internal-comms` (support function — reporting format, not a profit-driving section, no target below) |
| Keyword Research & Clustering | `dept-keyword-seo` (added 2026-07-27, closing the coverage-audit gaps) |
| GEO/AEO Content Strategy | `dept-geo-content` (added 2026-07-27) |
| Campaign Planning & Launch | `dept-campaign-launch` (added 2026-07-27) |
| SOP Library | `dept-sop-library` (support function, like Internal Comms — no profit target, no target below) |
| Design (landing page, mockups, visual QA) | `dept-design-consultation`, `dept-design-shotgun`, `dept-design-html`, `dept-design-review` — the original 4 skills tested at the start of this whole effort (from `garrytan/gstack`, 124k★, patched — see Security Note below). **Renamed 2026-07-27** to the top-level `dept-*` scheme: copied whole (SKILL.md + sections/ + vendor/ + all supporting files) out of `~/.claude/skills/gstack/design-*` into standalone `~/.claude/skills/dept-design-*` directories, frontmatter `name:` updated, old nested folders deleted. Verified safe first — the 52 hardcoded `~/.claude/skills/gstack/bin/...` references inside `dept-design-review`'s SKILL.md are absolute paths into the untouched shared `gstack/` root (bin/browse/design), not relative paths depending on the skill's own folder name — moving/renaming the skill's own directory doesn't affect them. Re-tested end-to-end after the rename (browse navigate + screenshot, bin/gstack-slug resolution) — that pass declared the rename clean, but it only exercised the SHARED-infra references, not each skill's references to ITS OWN sections/vendor subfolders. **A follow-up full-repo grep rescan (still 2026-07-27) caught 4 real breaks it missed**: `dept-design-consultation/SKILL.md` still pointed its "Read ..." instruction at the deleted `gstack/design-consultation/sections/proposal-and-preview.md`, and `dept-design-html/SKILL.md` (+ its own `.tmpl` source, which would have re-broken it on any future regen) still pointed its Pretext-vendor lookup at the deleted `gstack/design-html/vendor/pretext.js`. Confirmed broken (old paths 404'd) before fixing, repointed all 4 to the new `dept-design-*` locations, then re-ran the exact shell snippets from each skill and confirmed both now resolve to the real files. **Lesson: verifying a rename via the SHARED dependencies is not sufficient — every skill's references to its OWN moved subfolders must be grepped and proven separately.** |

`dept-context` is the shared foundation doc every section reads — not a
managed section either.

**Security note on the Design section**: `garrytan/gstack`'s browse daemon
had a real, publicly-disclosed critical vulnerability (GitHub issue
#1324 — `/health` leaked the auth token to any `chrome-extension://`
origin, letting any other extension or a DNS-rebinding webpage drive a
live shell). **Patched in this sandbox** (`browse/src/server.ts` +
`browse/src/terminal-agent.ts`, rebuilt binary, before/after attack
replay proved it closed) — this only matters if `dept-design-review`'s
underlying `browse` tooling is ever run in headed/connect mode; the
patch must still be in place before any of these 4 skills are trusted
for real use. Re-verify on every rescan (see dept-cmo's own Rescan
Checklist below) — a `git pull` or `./setup` re-run on the gstack
checkout would silently revert the patch.

---

## Target Management (the new capability — read this whole section before running a check-in)

### Why this exists

The owner's explicit concern: sections doing work that burns time/AI-agent
effort without a measurable return. This skill's job is to make that
visible — real targets, real tracking, real "where to shift effort"
calls — never a vibes-based status report.

**This is functionally an OKR system**, re-scaled for a single-owner
business rather than named/structured like a corporate OKR program:
- **Objective** = the section's real purpose (e.g. `dept-seo-programmatic`'s
  objective is "grow real organic/AI-assistant traffic to catalog pages
  without thin-content risk").
- **Key Results** = the daily/weekly targets in the state file below,
  each with a real, queryable definition of "met."
- Reviewed on the same Q1-Q4 cadence as the Strategic Planning Cadence
  further down this file, not a separate corporate ritual — one planning
  rhythm, not two.

### State file

`~/.claude-marketing/dept-targets.json` — read at the start of every
check-in, written at the end. If it doesn't exist, create it from the
starting targets below on first run.

```json
{
  "sections": {
    "dept-seo-programmatic": {
      "daily_target": "0 new pages published without passing quality gates (audit-only most days)",
      "weekly_target": "1 batch (50-100 pages) planned + quality-gate-passed, OR a real audit of an existing batch",
      "unit": "pages passing quality gates",
      "history": []
    },
    "dept-seo-technical": {
      "weekly_target": "1 crawl-budget/index-bloat check given catalog scale",
      "unit": "audit completed",
      "history": []
    },
    "dept-ppc": {
      "daily_target": "spend-vs-budget check if a campaign is live",
      "weekly_target": "1 real ROAS review per active campaign",
      "unit": "campaigns reviewed, real ROAS",
      "history": []
    },
    "dept-cro": {
      "weekly_target": "1 audit (landing page or checkout) OR 1 A/B test stage advanced",
      "unit": "audits/test-stages",
      "history": []
    },
    "dept-content": {
      "weekly_target": "1 piece drafted or revised, grounded in a real catalog fact",
      "unit": "pieces",
      "history": []
    },
    "dept-b2b-leads": {
      "weekly_target": "5-10 real leads researched (not contacted — outreach needs owner approval)",
      "unit": "leads researched",
      "history": []
    },
    "dept-competitor-intel": {
      "weekly_target": "1 battlecard created or refreshed with a live-verified claim",
      "unit": "battlecards",
      "history": []
    },
    "dept-market-research": {
      "weekly_target": "1 finding tied to a real search_misses signal or a sourced external fact",
      "unit": "findings",
      "history": []
    },
    "dept-crm-email": {
      "weekly_target": "0 new sends unless a real trigger exists — this section's job is copy quality, not volume",
      "unit": "N/A — quality gate, not a volume target",
      "history": []
    },
    "dept-analytics": {
      "daily_target": "none — daily is too frequent for real signal at current scale",
      "weekly_target": "1 real scorecard pull (see dept-cmo's own Business Scorecard table)",
      "unit": "scorecard pulls",
      "history": []
    },
    "dept-brand": {
      "weekly_target": "on-demand only — no forced cadence, this is a reference doc not an output pipeline",
      "unit": "N/A",
      "history": []
    },
    "dept-positioning": {
      "weekly_target": "on-demand only — revisit when a real market signal (from dept-competitor-intel or dept-market-research) suggests it's stale",
      "unit": "N/A",
      "history": []
    },
    "dept-keyword-seo": {
      "weekly_target": "1 cluster plan (real search_misses-grounded, or explicitly flagged qualitative-only)",
      "unit": "cluster plans",
      "history": []
    },
    "dept-geo-content": {
      "weekly_target": "on-demand — run the 6-surface AI-visibility baseline monthly, not weekly (low signal-to-effort at higher frequency)",
      "unit": "AI-visibility checks",
      "history": []
    },
    "dept-campaign-launch": {
      "weekly_target": "on-demand only — triggered by an actual campaign/launch, not a forced cadence",
      "unit": "N/A",
      "history": []
    },
    "design-gstack": {
      "weekly_target": "on-demand only — triggered by a real landing-page/mockup/visual-QA need, not a forced cadence",
      "unit": "N/A — covers dept-design-consultation, dept-design-shotgun, dept-design-html, dept-design-review as one tracked group",
      "history": [],
      "security_gate": "Confirm the gstack#1324 patch (see Security Note above) is still present in browse/src/server.ts + terminal-agent.ts before any real use — check via the Rescan Checklist below, not assumed."
    }
  },
  "last_checkin": null,
  "boost_log": []
}
```

**These starting targets are deliberately modest** — this is a
single-owner business with AI agents doing the work, not a staffed
agency. Calibrate up only after a section proves it can hit its target
with real output for 2-3 consecutive weeks; calibrate down (or drop to
"on-demand only," like `dept-brand`/`dept-positioning`) if a section
consistently produces no real signal worth the check-in overhead.

### Check-in Process (daily / weekly, whichever the owner or NOA triggers)

1. **Read the state file.** If a section has no target yet, use the
   defaults above.
2. **For each section with a due target**, verify the ACTUAL output the
   same way that section's own skill already requires — a real query, a
   real live-verified claim, a real audit result. **Never mark a target
   "met" from a self-report** — this is the same rule as
   `feedback_verify_destination`: the section skill's own output must
   show real evidence (e.g. `dept-seo-programmatic`'s quality-gate score,
   `dept-analytics`'s live-queried KPI, `dept-competitor-intel`'s
   "live-verified" vs "could not verify" marker).
3. **Log actual vs target** to the section's `history` array: `{"date":
   ..., "target": ..., "actual": ..., "met": true/false, "evidence":
   "<what was actually checked>"}`.
4. **Run the Boost Decision** (below) using the accumulated history.
5. **Write the updated state file.**
6. **Report** using `dept-internal-comms`'s Weekly Brief format
   (Headlines/Challenges/Looking Ahead) — terse, real numbers only.

### Boost Decision — where to shift effort (the "profit rise" question)

This is a **real ROI comparison**, not a guess. For each section with 2+
weeks of history:

1. **Hit rate**: did it meet its target consistently? A section that
   can't hit a modest target isn't ready to be scaled up regardless of
   theoretical upside.
2. **Real outcome signal**, cross-referenced against `dept-analytics`'s
   live-queried KPIs — not the section's own activity count:
   - `dept-seo-programmatic` → check `dept-analytics`'s AI Assistant +
     organic channel traffic trend for the pages actually published.
   - `dept-ppc` → real ROAS from the campaign itself, never estimated.
   - `dept-cro` → real conversion-rate delta from a completed test,
     never a "should improve" guess.
   - `dept-b2b-leads` → real leads → real outreach → real accounts
     opened (the actual funnel, not the research-count alone).
3. **Effort-to-outcome ratio**: sections showing real, positive,
   growing outcome per unit of effort are boost candidates — recommend
   raising their target and/or dedicating more check-in frequency.
   Sections showing flat or no real outcome after a fair trial period
   (rule of thumb: 4+ weeks of consistently-met targets with no
   measurable KPI movement) are **cut candidates** — recommend reducing
   to on-demand or pausing, not continuing to burn effort on faith.
4. **Never recommend a boost based on activity volume alone** ("we
   published 100 pages!") — only based on a real downstream KPI moving
   in `dept-analytics`. This is the department-level version of the
   Truth-Only Guardrail every section already enforces individually.
5. Log every boost/cut recommendation to the state file's `boost_log`
   with the evidence cited, so the reasoning is auditable later, not
   just a one-time verbal call.

---

## Strategic Planning Cadence (kept, generalized)

```
Q1: Environmental Scan — competitor research (dept-competitor-intel),
    supplier/catalog coverage gaps, search-miss trends
Q2: Strategy Development — positioning check (dept-positioning), resource
    priorities across catalog growth / marketing / B2B
Q3: Planning — what ships next quarter, in priority order
Q4: Review — what worked, what didn't, real numbers only
```

## Crisis Playbook (kept, matches existing incident culture)

Maps directly onto `dept-internal-comms`'s Incident Report format and the
platform's own health-monitor alerting:

- **Level 1** (single task/job failing): handled by the relevant agent's
  own retry/self-heal logic — no escalation needed.
- **Level 2** (customer-facing feature degraded — search slow, payment
  link broken): `_health_monitor_loop` already alerts the owner; this
  skill's job is producing the structured incident report once resolved.
- **Level 3** (platform-wide outage, data integrity risk, security
  incident): owner is alerted immediately (already wired), full incident
  report mandatory before considered closed — see the Truth-Only Guardrail
  below, no incident is "resolved" without a live-system check.

## Business Scorecard (kept, re-scoped to what this business actually tracks)

| Category | Real metrics (query live, never estimate) |
|---|---|
| Catalog health | Total active parts, % with IL price, % with fitment data, % categorized — same table already in `CLAUDE.md`'s System Review format |
| Customer | Search-to-order conversion, WhatsApp/chat response rate, real NPS if ever collected (don't invent one) |
| B2B (once active) | Leads researched (`dept-b2b-leads`) → real outreach → real accounts, not a projected pipeline |
| Financial | Real revenue, real margin (already fixed at 45%+conditional VAT — nothing to "optimize" here, this isn't a SaaS pricing-tier problem) |

## Red Flags (kept — genuinely useful early-warning list, reframed)

- Catalog coverage growth stalling (harvest queue not advancing).
- IL price coverage percentage dropping instead of rising.
- Same bug class recurring (check the Mistake Log before declaring
  something new).
- Customer-facing claim can't be traced to a real number.
- **A section hitting its target every week with zero real KPI movement**
  (new — this is a Target Management red flag specifically: it means the
  target itself is disconnected from real outcomes and needs
  recalibrating, not just the section's effort).

## Explicitly Dropped From the Source Material (do not reintroduce)

Board meeting prep, investor communication cadence, fundraising strategy,
pitch deck structure, cap table management, executive succession
timeline, "YPO/EO peer networking" — none of this applies to a
single-owner platform. If the business model changes (outside investment,
a board forms), revisit and re-add the relevant sections from the source
skill rather than reinventing them.

## Rescan Checklist (run whenever the department roster changes, or periodically)

A "rescan" is a full re-verification pass, not just a new-skill check —
things that were true when a skill was built can silently stop being
true (a patch reverted, a naming drift reintroduced, a new secret
accidentally pasted in). Check all of the following, and don't mark the
rescan complete on a partial pass:

1. **Naming consistency**: grep every `dept-*/SKILL.md` for leftover
   old-style references (`asf-*`, or any prior naming scheme) — this has
   caught real leftovers before (2026-07-27: 5 generic `asf-*` wildcard
   references survived the first rename pass).
2. **No secrets**: grep for API-key/password/PGP-block patterns across
   every skill file — should always return nothing.
3. **Valid frontmatter**: every `SKILL.md` must parse a `---` block with
   `name:` and `description:` present.
4. **The gstack security patch is still live**: `browse/src/server.ts`
   must still reject a spoofed `chrome-extension://` origin (no token
   returned) and reject a spoofed `Host` header (403) — **re-test with
   the actual before/after curl replay**, don't just check the file
   diff, since a rebuild could silently drop the fix even if the source
   still looks patched. This is the single highest-severity thing a
   rescan can miss.
5. **No orphaned cross-references**: if a skill mentions another by
   name, confirm that name still exists in the roster (a rename or
   removal elsewhere can silently break a reference).
6. **Real repo still untouched**: confirm zero footprint in
   `/opt/autosparefinder` — this should be true after every single
   change, not just checked at rescan time, but verify it here too as a
   backstop.

## Truth-Only Guardrail (MANDATORY)

Every number in the scorecard, every claim in a crisis report, every
target/actual/boost recommendation, must come from a live query or a
live-verified claim at the time of writing — this is the platform's own
Golden Rule #3 and #6. This skill's entire value — as department
coordinator as much as executive advisor — is structure and honest
measurement, never a shortcut around verifying against the live system.
A department status report that says every section is "on track"
without real evidence is worse than no report at all.
