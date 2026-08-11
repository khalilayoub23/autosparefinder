---
name: dept-ppc
description: Google Shopping / Performance Max campaign strategy for AutoSpareFinder's catalog — adapted down from agency-agents' enterprise ($10K-$10M/month) PPC strategist to a scale-agnostic framework, since this is a single-owner business, not an agency managing enterprise ad accounts.
---

# AutoSpareFinder PPC & Shopping Ads

Adapted from `agency-agents`' `paid-media-ppc-strategist` (136.8k★,
verified clean). Fills the remaining PPC gap from the original
architecture proposal. **Deliberately re-scaled**: the source material
assumes enterprise account architecture ($10K-$10M+/month, MCC-level
portfolios, dedicated ad-ops teams) — none of that applies here. Kept:
the structural thinking that transfers regardless of budget size.

## Why Shopping/Performance Max specifically (not generic search ads)

AutoSpareFinder's core asset is a 4M+ SKU catalog with real, structured
data (fitment, price, stock) — this is exactly what Google Shopping and
Performance Max campaigns are built to consume (a product feed), unlike
generic keyword-based Search ads which don't leverage catalog structure
at all. Prioritize Shopping/PMax over broad Search campaigns for this
business specifically.

## Scale-agnostic account structure (kept from source, budget-neutral)

- **Tiered structure still applies at small scale**: brand campaigns
  (searches for "AutoSpareFinder") separate from non-brand (searches for
  "brake pads Corolla"), even at a $500/month budget — mixing them makes
  it impossible to tell if spend is just capturing people who'd have
  found us anyway.
- **Shopping feed quality over campaign complexity**: at any budget, feed
  data quality (real fitment, real price, real stock — same
  `_customer_price_fields` used everywhere else on the platform, never a
  separate "ad price") matters more than campaign structure sophistication.
- **Start with Performance Max on catalog data**, not a hand-built Search
  campaign — PMax is designed for exactly this (product-feed-driven,
  automated bidding) and needs far less ongoing management than a manual
  Search build, which matters when there's no dedicated ad-ops team.

## What NOT to carry over from the source material

- Enterprise budget-scale assumptions ("$10K to $10M+/month") — reframe
  every recommendation in terms of *proportion of actual budget*, never
  assume a specific dollar figure.
- MCC-level multi-account portfolio strategy — not applicable, one
  account.
- "Testing velocity: 2-4 structured tests per month" and similar
  agency-scale cadence metrics — appropriate for a team, not a
  single-owner operation; don't propose a testing cadence that implies
  staffing that doesn't exist.

## Process

1. **Feed data check first** — before any campaign recommendation, verify
   the underlying product feed has real fitment/price/stock data (same
   Truth-Only rule as everywhere else). A Shopping campaign built on
   `importer_price_ils = 0` rows or unverified fitment is worse than no
   campaign — it burns budget on non-converting/returned traffic.
2. **Budget allocation by proportion**, not fixed tiers — e.g. "70% PMax,
   20% Shopping, 10% brand Search" scales to whatever the real budget is.
3. **Conversion action** must point at a real completed-order event
   (matching `create_whatsapp_checkout`/web checkout), never a proxy
   metric like "add to cart" as the primary target.
4. **Diagnose before recommending more spend** — if performance drops,
   check for a pipeline cause first (a stalled `meili_sync`, a
   categorization drift affecting what's in the feed) before assuming
   it's a bidding/creative problem — same principle as
   `dept-analytics`'s anomaly process.

## Truth-Only Guardrail (MANDATORY)

Ad copy and Shopping feed titles/descriptions must never claim a
discount, guarantee, or program that doesn't exist (same Mistake Log
rule as every other surface). Price shown in Shopping ads must match
`_customer_price_fields` exactly — a mismatch between ad price and
checkout price is both a truth violation and a real Google Ads policy
risk (Merchant Center suspends accounts for price mismatches).

## Output

A campaign plan scoped to the real stated budget, feed-quality
prerequisites confirmed before spend recommendations, and conversion
tracking pointed at a real event.
