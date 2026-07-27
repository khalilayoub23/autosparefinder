---
name: dept-seo-technical
description: General/technical SEO audit for AutoSpareFinder (crawlability, Core Web Vitals, indexation, structured data, international/hreflang) — distinct from dept-seo-programmatic's catalog-page-generation focus; this is site-wide technical health, with crawl-budget/index-bloat risk framed against our real 4M+ part catalog scale.
---

# AutoSpareFinder Technical SEO Audit

Adapted from `digital-marketing-pro`'s `technical-seo` skill (635★,
verified clean). Closes the last remaining gap from the original
architecture proposal ("Catalog SEO Specialist" / general SEO, as
distinct from `dept-seo-programmatic`'s specific job of planning new
catalog-page generation).

## Why crawl budget and index bloat are a real, not theoretical, risk here

Most sites using this skill worry about crawl budget at maybe tens of
thousands of pages. **This platform has 4M+ active parts and a
`part_vehicle_fitment` table capable of generating enormous URL
combinations** if faceted search/filter parameters aren't controlled.
The source material's crawl-trap warning ("faceted navigation generating
millions of URLs") is not a hypothetical for us — it's the most likely
real failure mode if search/filter URLs are ever made crawlable without
canonicalization.

## Capabilities (kept, prioritized by relevance to this platform)

**Highest priority for us:**
- **Crawlability & crawl traps**: faceted-navigation canonicalization is
  the top risk given catalog scale — verify filter/sort parameters don't
  produce indexable duplicate URLs.
- **Structured data**: `Product` schema for parts (real price/fitment/
  availability — same `_customer_price_fields` data, never separate "SEO
  price" data), `BreadcrumbList` for category navigation,
  `LocalBusiness`/`Organization` for the base site — ties directly into
  `dept-seo-programmatic`'s schema work, don't duplicate effort.
- **International/hreflang**: directly relevant to the trilingual
  (Hebrew/Arabic/English) requirement already tracked as G7 in
  `CLAUDE.md` — verify hreflang tags match the real language-detection
  logic already in chat/landing page, don't propose a separate i18n
  system.
- **Index bloat / indexation**: with 4M+ parts, deciding what's genuinely
  indexable (real fitment-verified, real priced) vs. `noindex` (empty
  categories, unpriced rows) is the same judgment call
  `dept-seo-programmatic`'s quality gates already make — apply
  consistently, don't contradict.

**Standard, still relevant:**
- Core Web Vitals (LCP/INP/CLS), page speed, redirect management, HTTP
  status auditing, HTTPS/security headers, XML sitemap strategy (split at
  50K URLs — relevant at our scale, a single sitemap file cannot cover
  the catalog).

## What to check against real infrastructure, not assume

- **Anti-scraping/rate-limiting** (`CLAUDE.md`'s Anti-Harvest section) —
  the nginx rate limits (20r/s, burst 40) and AI-bot User-Agent blocking
  already in place could affect Googlebot/Bingbot crawling if
  misconfigured. Verify legitimate search-engine crawlers aren't
  accidentally caught by the bot-blocking rules built to stop scrapers —
  this is a real, specific risk of our own defensive measures, not a
  generic technical-SEO checklist item.
- **Meilisearch-backed search pages** — if any are server-rendered and
  crawlable, verify they don't create the faceted-URL explosion risk
  above.

## Process

1. **Crawl-trap check first**, given the scale risk above — before
   anything else, confirm filter/sort/pagination parameters are
   canonicalized or `noindex`ed.
2. Site health snapshot: real Core Web Vitals data (CrUX/PageSpeed), real
   Search Console index coverage — never estimated.
3. Structured data audit — cross-check with `dept-seo-programmatic`'s
   schema work rather than duplicating.
4. Hreflang/international check against the real trilingual
   implementation (G7), not a hypothetical i18n redesign.
5. Verify our own anti-scraping measures aren't blocking legitimate
   search engine crawlers.

## Truth-Only Guardrail (MANDATORY)

Structured data (`Product` schema price/availability) must match
`_customer_price_fields` exactly — a schema/display price mismatch is
both a truth violation and a real Google rich-results policy risk
(mismatched schema data can get rich results revoked).

## Output

A technical SEO audit: crawlability/index-bloat risk assessment first
(given our scale, this is the highest-value section), then Core Web
Vitals, structured data, and international-SEO findings, each with
severity and a fix recommendation — cross-referencing
`dept-seo-programmatic` rather than duplicating its quality-gate logic.
