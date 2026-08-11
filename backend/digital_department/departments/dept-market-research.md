---
name: dept-market-research
description: Broader automotive market research beyond competitor tracking — new vehicle launches, manufacturer recalls/announcements, parts-demand trends — using WebSearch/WebFetch only, feeding catalog-priority and NOA content decisions with real, sourced information.
---

# AutoSpareFinder Automotive Market Research

Fills the "Automotive Market Research Specialist" gap from the original
architecture proposal. No existing repo covered this specifically (it's
distinct from `dept-competitor-intel`, which tracks competitor
marketplaces, not the automotive industry itself) — built directly,
following the same real-data-only pattern as every other `dept-*` skill.

## When to Use

- Before prioritizing catalog/harvest work for a newly popular vehicle.
- When NOA needs a real, sourced automotive fact for content (not a
  guessed statistic).
- Checking whether a manufacturer recall/announcement creates a real
  parts-demand signal worth reacting to.

## What This Covers (distinct from competitor research)

| Area | Real source to check | Not this skill |
|---|---|---|
| New vehicle launches | Manufacturer press releases, real automotive trade press | Competitor marketplace positioning — see `dept-competitor-intel` |
| Recalls | NHTSA (US), real manufacturer recall notices | Speculating about recall causes without a source |
| Parts demand trends | Real search-miss data (`search_misses` table) first, THEN external trend confirmation | Assuming a trend from a single anecdote |
| Manufacturer announcements | Official manufacturer newsroom pages | Rumor sites / unverified forum posts |

## Process

1. **Check internal signal first**: real `search_misses` data already
   tells us what customers are searching for and not finding — this is a
   stronger, more directly actionable signal than external trend
   research, and it's free (already in our own DB). Start here.
2. **External research only via WebSearch/WebFetch** on official sources
   — manufacturer newsrooms, NHTSA, recognized trade publications. Never
   scrape a login-gated or paywalled source; never invent a "trend"
   without a citable source.
3. **Connect the finding to a real platform action**: does this justify
   reprioritizing the `harvest_queue` (real mechanism, see G4 in
   `CLAUDE.md` — priority-ranked by real IL vehicle-registration counts),
   a NOA content angle, or nothing actionable yet?

## Output Format

```markdown
# Automotive Market Research — [topic/date]

## Finding
[What was found, with the source cited]

## Internal signal check
[What search_misses / harvest_queue data shows, if relevant — real
query, not assumed]

## Recommended action
[Reprioritize harvest_queue for X model | NOA content angle | No action —
insufficient signal yet]
```

## Truth-Only Guardrail (MANDATORY)

Every claim needs a real, citable source — a manufacturer press release,
NHTSA record, or our own live query. "Automotive trends" is exactly the
kind of vague claim that's easy to fabricate plausibly; this skill's
entire value depends on never doing that. If a claim can't be sourced,
say so explicitly rather than presenting a guess as research.

## Relationship to Existing Code

Read-only research — this skill never writes to `harvest_queue` or any
catalog table directly. A recommendation to reprioritize harvest work is
an output for a human (or the relevant existing worker) to act on, not
something this skill executes itself.
