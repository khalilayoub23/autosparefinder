---
name: dept-sop-library
description: Maintains the department's own recurring-operation checklists (the "how" behind dept-cmo's targets) — heavily simplified from the source's multi-client agency tooling, since we have one brand, not a portfolio of clients.
---

# AutoSpareFinder SOP Library

Adapted from `digital-marketing-pro`'s `sop-library` skill (635★,
verified clean). Closes the "Marketing SOPs" gap from the 2026-07-27
coverage audit. **Heavily re-scaled**: the source is agency tooling —
assign SOPs to multiple client brands, track per-brand compliance rates,
escalation paths for account managers. None of that applies here.

## What this actually is for us

A single, simple checklist repository at `~/.claude-marketing/sops/` —
one JSON file per recurring procedure, no multi-brand assignment layer,
no account-manager escalation. The point is consistency across sessions
(so the same section skill does its recurring job the same way whether
Claude, a future NOA integration, or the owner runs it), not managing a
client portfolio.

## SOP Categories (mapped to real department sections)

- `seo` — e.g. "Programmatic page batch review" (ties to
  `dept-seo-programmatic`'s quality gates)
- `content` — e.g. "Blog post publishing checklist"
- `campaign` — e.g. "Tier 1 launch checklist" (mirrors
  `dept-campaign-launch`'s own launch checklist — don't duplicate,
  reference it)
- `reporting` — e.g. "Weekly dept-cmo check-in steps"
- `crm-email` — e.g. "New email template review before wiring to a
  trigger" (ties to `dept-crm-email`'s real-template-inventory rule)

## Process (simplified from source's 12-step multi-brand version)

1. **Create**: define objective, prerequisites, numbered steps with a
   pass/fail quality gate at the key checkpoint, completion criteria.
   Save to `~/.claude-marketing/sops/{category}/{sop-slug}.json`.
2. **List**: enumerate existing SOPs by category — this is mostly what
   gets used, since we're checking "is there already a procedure for
   this" before improvising one each time.
3. **Update**: version the change, note why, keep a simple history array
   — no multi-brand re-review flagging needed (one brand).
4. **Check adherence** (lightweight, not a compliance percentage
   dashboard): did the last 2-3 real executions of this procedure follow
   the steps? Use real evidence from the section's own output (e.g.
   `dept-seo-programmatic`'s quality-gate score log), not a self-report.

## Relationship to dept-cmo

SOPs are the **"how"**; `dept-cmo`'s Target Management is the **"how much/
how often."** A section missing its target repeatedly might mean the SOP
itself is wrong (too slow, missing a step) — check both together rather
than assuming the target alone is the problem.

## Truth-Only Guardrail (MANDATORY)

"Adherence" to an SOP must be checked against real execution evidence
(an actual quality-gate score, an actual query result), never assumed
because the SOP exists. An unused, unenforced SOP provides zero real
consistency benefit — don't let this become a paperwork exercise.

## Output

A simple SOP file (create/update) or an adherence check (real evidence
cited) — kept intentionally lightweight given the scale of the business.
