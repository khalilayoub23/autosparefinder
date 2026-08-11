---
name: dept-positioning
description: Develops and tests AutoSpareFinder's positioning statement using the April Dunford methodology — for when messaging feels generic, inconsistent across channels, or hasn't been validated against real customer language.
---

# AutoSpareFinder Positioning

Adapted from the "marketing-strategy-pmm" positioning framework
(alirezarezvani/claude-skills — April Dunford methodology). The framework
itself is genuinely universal; only the B2B-SaaS-specific parts (economic/
technical buyer personas, ARR targets) are dropped as not applicable to a
consumer/marketplace business.

## Process

1. **List competitive alternatives** — direct (RockAuto, AutoDoc, eBay
   Motors, AliExpress car parts — see `dept-competitor-intel`),
   adjacent (a local mechanic just ordering whatever's on hand), status quo
   (customer drives to 3 physical stores comparing prices themselves).
2. **Isolate unique, real attributes** — not aspirational ones. Verified
   today: fitment-first search (Tier 0 matching against
   `part_vehicle_fitment`), price comparison across suppliers on one
   platform, trilingual native support (He/Ar/En).
3. **Map attributes to customer value** — fitment verification → "I won't
   order the wrong part again." Multi-supplier comparison → "I'm not
   overpaying because I only checked one shop."
4. **Define best-fit customers** — who cares most about NOT getting a
   wrong-fit part shipped (higher cost of a mistake: rare/imported cars,
   time-sensitive repairs) vs. who's purely price-driven.
5. **Test with real customer language** — pull actual phrasing from chat
   logs / `search_misses` rather than inventing it.

## Positioning Statement Template

```
FOR [car owners searching for a specific part across languages/regions]
WHO [can't tell if a cheap listing actually fits their car]
AutoSpareFinder IS A [global auto-parts marketplace]
THAT [verifies fitment before you buy, then compares real supplier prices]
UNLIKE [single-listing marketplaces that show price first, fitment never]
AutoSpareFinder [proves the part fits before asking you to pay]
```

Treat this as a draft, not a final claim — validate against real customer
interviews/chat transcripts before publishing anywhere customer-facing.

## Truth-Only Guardrail (MANDATORY)

Every attribute used in a positioning claim must be something currently
**true and verifiable**, not aspirational roadmap language. "Verified
fitment" is fair to claim (real `part_vehicle_fitment` matching exists,
per G1). "Fastest shipping in the industry" would NOT be fair unless a
real, measured comparison backs it (see `feedback_no_fake_data` — shipping
data must be measured, never invented).

## Output

A positioning statement + a one-line audit of which words are backed by a
real, checkable fact vs. which are still aspirational and need
verification before use.
