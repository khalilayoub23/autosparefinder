---
name: dept-seo-programmatic
description: Plans and audits SEO pages generated at scale from AutoSpareFinder's real catalog/fitment data (vehicle/engine/OEM/brand/category pages) — with hard quality gates against Google's Scaled Content Abuse policy, since our 4M+ part catalog makes it trivially easy to accidentally generate hundreds of thousands of thin pages.
---

# AutoSpareFinder Programmatic SEO

Adapted from `indranilbanerjee/digital-marketing-pro`'s `programmatic-seo`
skill (635★, verified clean — no install hooks, first-party API domains
only). This was our single biggest identified gap: the user's original
architecture proposal explicitly called for a "Programmatic SEO Engineer"
covering brand pages, vehicle model pages, engine pages, OEM reference
pages, part compatibility pages — and our catalog (4M+ parts,
`part_vehicle_fitment` data) is exactly the kind of structured data source
this is built for.

## Why the quality gates matter here specifically

This is not optional caution — it's the difference between real SEO gain
and a Google manual action against the whole domain. Source data (kept
verbatim, it's specific and current):

- Google's **Scaled Content Abuse** policy (introduced March 2024) saw a
  major enforcement wave in **June 2025** (manual actions targeting
  AI-generated content at scale) and **August 2025** (SpamBrain pattern
  detection enhanced for content farms) — resulting in a 45% reduction in
  low-quality results.
- **Site reputation abuse** enforcement (since Nov 2024) can penalize the
  whole domain, not just the offending pages.

Given our scale, a naive "generate one page per make+model+part-category
combination" approach could produce **hundreds of thousands of pages in
one run** — exactly the scaled-content-abuse risk profile.

## Process (adapted to our real data sources)

1. **Data source assessment**: real row counts from `parts_catalog` +
   `part_vehicle_fitment` (query live, never estimate) — column
   uniqueness, missing values, duplicate detection.
2. **Template planning**: design pages that pass the standalone-value
   test using data we actually have per page — real fitment-verified part
   counts for that vehicle, real price range from `_customer_price_fields`,
   real supplier count — never templated filler text with only the
   vehicle name swapped.
3. **URL pattern**: `/parts/{category}/{make}/{model}` or
   `/oem/{oem_number}` style, lowercase-hyphenated, under 100 chars,
   uniqueness-enforced at generation time.
4. **Quality gates (hard stops, do not bypass)**:

| Metric | Threshold | Action |
|---|---|---|
| Pages without content review | 100+ | Require content audit before publishing |
| Pages without justification | 500+ | **HARD STOP** — explicit owner approval + thin-content audit required |
| Unique content per page | <40% | Flag as thin content |
| Unique content per page | <30% | **HARD STOP** — scaled content abuse risk |
| Word count per page | <300 | Flag for review |

5. **Progressive rollout**: batches of 50-100 pages, monitor indexing 2-4
   weeks before expanding. **Never publish 500+ pages in one run** without
   the audit above, regardless of how confident the data looks.
6. **Internal linking**: hub/spoke by category → make → model, 3-5 related
   parts per page, `BreadcrumbList` schema.

## Safe vs. risky page types for THIS catalog

**Safe at scale** (real, per-page-unique data exists):
- Part-category × verified-vehicle pages, where fitment is real
  (`part_vehicle_fitment` match exists) and price/supplier-count data is
  real per page.
- OEM reference pages with a real cross-reference
  (`part_cross_reference`) and real spec data.

**Penalty risk — do not generate**:
- Vehicle pages with no real fitment-verified parts behind them (empty
  category → thin/fake page).
- "Best [part] for [car]" pages where the only per-page-unique content is
  the swapped vehicle name.
- Any page generated from catalog rows that don't have real supplier
  pricing yet (`importer_price_ils = 0` or no active supplier).

## Truth-Only Guardrail (MANDATORY)

Every generated page's claims (part count, price range, "in stock") must
come from a live query at generation time, matching the same rule as
every other `dept-*` skill. A programmatic page showing a stale or
estimated count is both a truth-only violation AND a thin-content risk —
the two failure modes compound here.

## Output

A Programmatic SEO Score (0-100 per category: Data Quality, Template
Uniqueness, URL Structure, Internal Linking, Thin Content Risk, Index
Management) plus a prioritized action plan and an explicit batch-rollout
plan — never a "generate everything now" recommendation.
