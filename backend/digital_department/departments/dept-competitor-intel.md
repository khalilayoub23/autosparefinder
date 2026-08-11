---
name: dept-competitor-intel
description: Researches competitor auto-parts marketplace advertising (AutoDoc, RockAuto, eBay Motors, AliExpress car parts, Autodoc IL) to inform NOA's campaign copy and SHIRA's positioning. Uses WebSearch/WebFetch only — no scraping credentials, no third-party service.
---

# AutoSpareFinder Competitor Ads Research

Adapted from the general "competitive-ads-extractor" pattern. The original
depends on scraping ad libraries with browser automation; this version uses
only `WebSearch`/`WebFetch` (tools already available) — no new credentials,
no Facebook Ad Library login, no scraping infrastructure to maintain.

## When to Use

- Before a NOA campaign push, to check what competitors are currently
  advertising for the same part category/season.
- When SHIRA needs positioning research ("how do RockAuto/AutoDoc pitch
  fitment guarantees?").
- Auditing whether a competitor claim we're reacting to is actually true
  (don't assume — verify via a live search, consistent with this project's
  "verify from real data" rule).

## Process

1. **Search, don't assume.** Use `WebSearch`/`WebFetch` against public pages
   (competitor homepages, Google/Facebook ad transparency pages where
   public) — never attempt to log into a competitor's ad account or use
   scraped/leaked credentials.
2. Extract: headline patterns, CTA wording, what pain point they lead with,
   whether they claim fitment guarantees, pricing transparency, shipping
   promises.
3. Compare against **what AutoSpareFinder can truthfully claim** (see
   Truth-Only Guardrail below) — the output should be "here's an angle
   competitors use that we can ALSO truthfully claim," not "here's copy to
   mimic regardless of whether it's true for us."

## Output Format

```markdown
# Competitor Ad Research — [category/date]

## [Competitor Name]
- Headline pattern observed: "..."
- Pain point led with: ...
- Claims made (fitment/price/shipping): ...
- Can we truthfully make a similar claim? [yes — cite the real number/policy] / [no — why not]

## Recommended angle for NOA/SHIRA
[Only angles backed by something AutoSpareFinder can actually deliver]
```

## Battlecard (for a competitor we track on an ongoing basis)

Adapted from the "marketing-strategy-pmm" battlecard template
(alirezarezvani/claude-skills). Use this fuller format for a competitor
worth tracking over time (RockAuto, AutoDoc), not a one-off research pass.

```markdown
COMPETITOR: [Name]
OVERVIEW: [real, verified facts only — founding, size, market — see
           "Can't verify directly" note below if a claim can't be checked]

POSITIONING:
- They say: "[their verified claim, quoted]"
- Reality: [our honest assessment, not a dismissal for its own sake]

STRENGTHS: [what they genuinely do well — credibility requires being fair here]
WEAKNESSES: [where they fall short, verified not assumed]

OUR ADVANTAGES: [only ones we can back with a real feature/number]
WHEN WE WIN: [scenario, honestly assessed]
WHEN WE LOSE: [scenario, honestly assessed — a battlecard that never admits
               a loss scenario isn't credible and won't be trusted by
               whoever uses it]

TALK TRACK:
Objection: "[real objection a customer might raise]"
Response: "[our real, truthful response]"
```

**If a claim about the competitor can't be verified directly** (e.g.
AutoDoc's site returning 403 to direct fetch, as happened 2026-07-26),
write "Could not verify — [reason]" in that field rather than filling it
with a plausible-sounding guess. A battlecard with an honest gap is more
useful than one with a fabricated fact.

## Truth-Only Guardrail (MANDATORY)

This research feeds NOA (social) and SHIRA (marketing) — both are already
bound by the truth-only rule (`CLAUDE.md` Mistake Log, 2026-07-05 and
2026-07-20 entries: NOA/SHIRA must never claim a program, discount, or
price that doesn't exist). **This skill's entire output is disqualified if
it recommends copying a competitor claim without checking whether it's
true for AutoSpareFinder specifically.** Competitor research informs
*angles*, never copy verbatim, and every number in the final
recommendation must be real (pulled from `_customer_price_fields`-derived
prices, real supplier counts, real shipping data — see
`feedback_no_fake_data.md`), not estimated to match a competitor's framing.

## Legal & Ethical (kept from the original, still applies)

- Research and inspiration only — never plagiarize copy or designs.
- Public pages only — no login-gated scraping, no credential reuse.
