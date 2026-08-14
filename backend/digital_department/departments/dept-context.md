---
name: dept-context
description: The single source-of-truth document for AutoSpareFinder's product, audience, and positioning facts — every other dept-* marketing skill reads this first instead of re-deriving or guessing these facts each time.
---

# AutoSpareFinder Marketing Context

Adapted from the general "marketing-context" pattern (alirezarezvani/claude-skills):
one canonical document every marketing skill reads before starting, so facts
never drift between skills or get re-guessed. Stored at
`.claude/dept-context.md` in this sandbox. **This is a foundation
document, not a strategy skill** — it only records what's true, it doesn't
decide what to do about it.

## Why this exists

Every `dept-*` skill built so far (`dept-brand`,
`dept-competitor-intel`, `dept-content`, `dept-b2b-leads`,
`dept-internal-comms`) independently referenced facts like the pricing
formula, the trilingual rule, and the truth-only guardrail. Centralizing
them here means a future skill reads ONE file instead of five, and a fact
that changes (e.g. a new supplier count) gets updated once.

## Product Overview
<!-- priority: high -->

- **What it is**: global auto-parts marketplace — price/fitment comparison
  across suppliers, not a single-shop storefront. Model: eBay/AliExpress
  for car parts, with AI search/fitment verification layered on top.
- **Business model**: marketplace margin (`cost × 1.45` + conditional VAT:
  18% for IL-based suppliers, 0% for foreign-sourced parts). No
  subscription, no SaaS tiers — every other adapted skill's "pricing tier /
  freemium" framing does **not** apply here.
- **Core differentiator**: verified fitment (`part_vehicle_fitment`
  matching) — see PLATFORM GOALS G1 in `CLAUDE.md`. Competitors like
  RockAuto don't make this claim explicitly on their homepage (verified
  2026-07-26); we can, because it's backed by real data.

## Target Audience — TWO distinct segments, don't conflate them
<!-- priority: high -->

**B2C (primary, existing)**: individual car owners searching by
VIN/plate/part name via web, WhatsApp, or Telegram chat. Trilingual —
Hebrew, Arabic, English (see G7 in `CLAUDE.md`) — detect and match the
customer's language, never mix.

**B2B (secondary, being developed — see `dept-b2b-leads`)**:
independent repair shops, fleet managers, dealership service departments
buying in volume. **Do not claim a B2B-specific program (bulk pricing
tier, NET-30 terms, dedicated account manager) unless it actually exists**
— none is documented as built yet.

## What NOT to claim (ruled out, don't re-propose)
<!-- priority: critical -->

- **Google Business Profile / local SEO for our own listing** — an
  online-only marketplace cannot pass GBP verification (owner decision,
  2026-07-26, logged in `CLAUDE.md`). Any "local SEO" content applies to
  researching *how B2B leads (garages) do their own local marketing*, never
  to us building our own GBP presence.
- **Loyalty program, referral program, coupon codes** — none exist. SHIRA
  was already caught promising these (Mistake Log, 2026-07-05).
- **Flat VAT** — never ×1.18 flat; conditional per `get_supplier_vat_rate`.

## Competitive Set (verified, not assumed)
<!-- priority: normal -->

RockAuto, AutoDoc, eBay Motors, AliExpress car parts — see
`dept-competitor-intel` for the live-verified research process and
current findings.

## Voice & Brand
<!-- priority: low -->

See `dept-brand` for colors/fonts/tone in full. Short version:
professional, trustworthy, benefit-first, no filler adjectives, matches
existing landing-page copy tone.

## Maintenance

Update this file when a fact changes (new real supplier count, new
verified competitor claim, a new program actually ships). Every other
`dept-*` skill should reference this file rather than re-stating these
facts inline, the same way `marketing-context` works as the shared
foundation for its sibling skills in the source library.
