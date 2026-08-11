---
name: dept-campaign-launch
description: Plans and launches cross-section campaigns (product launches, seasonal pushes) end-to-end — closes Campaign Planning, Budget Allocation, and Campaign Launch orchestration gaps in one skill. Ties dept-cmo's targets to a concrete campaign brief, real UTM tracking, and a real post-mortem, single-brand scoped (not a multi-client agency tool).
---

# AutoSpareFinder Campaign Launch

Adapted from `digital-marketing-pro`'s `campaign-orchestrator` skill
(635★, verified clean). Closes three related gaps from the 2026-07-27
coverage audit at once — Campaign Planning, Budget Allocation, and the
missing "Campaign Launch" step in the Market Research → SEO → Content →
Brand Review → Analytics → **Campaign Launch** workflow.

## Single-brand scope (re-scaled from the source)

The source skill is agency tooling (multi-client budget allocation,
account-based marketing for enterprise sales). **We have one brand.**
Kept: the campaign brief structure, the budget-allocation frameworks
(genuinely useful even at one-brand scale), UTM standardization, and
post-mortem discipline. Dropped: ABM/account-based campaign planning
(not applicable — we don't have named target accounts to run ABM
against; `dept-b2b-leads` is the closer analog for B2B, and it's
research, not enterprise ABM orchestration).

## Campaign Brief (the missing "Campaign Launch" step)

Every campaign — a new vehicle-brand catalog push, a seasonal parts
category promotion, a real product launch (see below) — gets a real
brief before any section starts work:

```markdown
# Campaign Brief — [name/date]

**Objective** (SMART, real number): [e.g. "increase Corolla brake-parts
  search-to-order conversion by X% in 4 weeks" — not a vague goal]
**Audience**: [real segment — B2C by vehicle/language, or B2B via
  dept-b2b-leads]
**Sections involved**: [which dept-* skills contribute, in what order]
**Budget**: [real, from dept-cmo's Target Management — never invented]
**Timeline**: [start/end, key milestones]
**Primary conversion event**: [real — checkout completion, not a proxy]
**UTM plan**: see below
```

## Budget Allocation Frameworks (kept, all three genuinely apply at any scale)

- **70/20/10**: 70% proven channels (whatever's already shown real ROI
  in `dept-analytics`), 20% promising, 10% experimental. Good default
  when there's no strong efficiency data yet.
- **Efficiency-ranked**: allocate by real channel CPA/ROAS from
  `dept-analytics` — use once there's enough real history to rank by.
- **Funnel-weighted**: put budget where the real funnel data
  (`dept-analytics`) shows the biggest drop-off.

Never allocate budget by assumption when real channel performance data
already exists in `dept-analytics` — that's exactly the "vibes not
evidence" mistake this whole department exists to prevent.

## UTM Standardization

Reuse the existing pattern already live in the platform (NOA's posts are
already UTM-tagged per project memory) — don't invent a second taxonomy.
Standard structure: `utm_source` (platform), `utm_medium` (channel type),
`utm_campaign` (this campaign's slug). Consistency here is what makes
`dept-analytics`' attribution real instead of guessed.

## Product Launch (adapted from `marketing-strategy-pmm`'s launch-tier
system, alirezarezvani/claude-skills, 23.2k★ — verified clean, this is
the piece `dept-positioning` deliberately left out)

Not every "launch" needs the same ceremony. Tiered by real scope:

| Tier | Scope (re-scaled for a single-owner business, not an enterprise team) | Prep |
|---|---|---|
| 1 | New vehicle-brand catalog opened, or a major platform feature (e.g. a new language) | 1-2 weeks: brief → sections coordinate → launch → monitor |
| 2 | New category/supplier integration | Few days: brief → 1-2 sections involved |
| 3 | Small catalog addition, minor copy update | Same-day, no formal brief needed |

**Launch checklist** (Tier 1/2): positioning confirmed current
(`dept-positioning`), catalog data quality-gated (`dept-seo-programmatic`
if new pages involved), announcement drafted (`dept-content`/NOA),
UTM plan in place, `dept-analytics` baseline captured *before* launch so
the after-comparison is real.

## Post-Mortem (kept — real discipline, low effort)

After any Tier 1/2 campaign: what was the real objective vs. real
outcome (from `dept-analytics`, not a self-report), what worked, what
didn't, one concrete change for next time. Feed this into `dept-cmo`'s
Boost Decision — a campaign post-mortem is exactly the kind of real
evidence that check-in process needs.

## Truth-Only Guardrail (MANDATORY)

Every claim in a campaign brief or post-mortem (budget, objective number,
outcome) must be real. A post-mortem that reports a "successful launch"
without a live-queried before/after comparison is a self-report, exactly
the pattern `feedback_verify_destination` warns against — this skill's
output isn't complete without that comparison.

## Output

A Campaign Brief (before) or Post-Mortem (after), both feeding directly
into `dept-cmo`'s Target Management history — this skill is how a
cross-section effort gets tracked as one unit instead of getting lost
across five separate section check-ins.
