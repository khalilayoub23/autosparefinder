---
name: dept-analytics
description: KPI framework, anomaly investigation, and attribution for AutoSpareFinder's marketplace model — including the new GA4 "AI Assistant" channel (added May 2026) and AEO measurement, since customers increasingly ask ChatGPT/Gemini/Claude for part recommendations before ever reaching a search engine.
---

# AutoSpareFinder Analytics & Revenue

Adapted from `digital-marketing-pro`'s `analytics-insights` skill (635★,
verified clean). Fills the "Analytics & Revenue Manager" gap from the
original architecture proposal — re-scoped from generic SaaS/enterprise
KPIs to a marketplace model.

## KPI Framework (marketplace-specific, not SaaS)

| Category | Real metric | Query source, never estimate |
|---|---|---|
| Catalog health | Total active parts, % IL-priced, % fitment-verified, % categorized | Same table already in `CLAUDE.md`'s System Review format — this skill doesn't invent a new one, it's the same numbers viewed as KPIs over time |
| Marketplace liquidity | Search → fitment-verified result rate, search-miss rate (`search_misses` table) | Real query |
| Conversion | Search → checkout-link-created → paid rate | Real query, per channel (web/WhatsApp/Telegram) |
| B2B (once `dept-b2b-leads` produces real accounts) | Leads researched → real outreach → real accounts opened | Never a projected pipeline number |
| Revenue | Real revenue, real margin (fixed at cost×1.45 + conditional VAT — there is no "pricing optimization" experiment to run here, this isn't a SaaS tier problem) | Real query |

## AEO / GA4 AI Assistant channel (new, current, genuinely important)

Google Analytics 4 added a **default "AI Assistant" channel group on 13
May 2026** — when a referrer matches a recognized AI assistant (ChatGPT,
Gemini, Claude, etc.), GA4 auto-categorizes the session under this channel
and sets Medium to `ai-assistant`. This matters here specifically: a
customer who asks an AI assistant "what's the best place to buy brake
pads for a 2018 Corolla" and gets referred to AutoSpareFinder is now
separately measurable traffic — a real, emerging channel worth tracking
as its own line, not merged into "Organic Search" or "Direct" (both are
now misattributions per Google's own guidance).

**Setup check**: confirm the channel group is live in our GA4 property;
if reports look unchanged after the rollout, check Explore reports
filtered by `sessionDefaultChannelGroup = "AI Assistant"`.

**Why this connects to real platform work**: this is the measurement
counterpart to AEO (Answer Engine Optimization) — if AutoSpareFinder's
catalog/content isn't structured for AI assistants to cite confidently
(real schema, real fitment claims, real prices), we're invisible on this
channel regardless of how good our traditional SEO is. Worth cross-
referencing with `dept-seo-programmatic`'s schema work.

## Anomaly Investigation Process

1. Get the specific metric, timeframe, and any known recent change
   (deploy, price-sync run, harvest cycle) — check `job_registry` for
   what actually ran around the anomaly window before speculating.
2. Rule out data-pipeline causes first (a stalled `meili_sync`, a
   categorization drift) before assuming a genuine demand/market shift —
   this platform has a documented history of pipeline drift causing
   metric swings that look like real trends but aren't (see the
   categorization drift entries in `CLAUDE.md`).
3. Only escalate to "real market signal" once pipeline causes are ruled
   out with a live check.

## Truth-Only Guardrail (MANDATORY)

Every number in a report — traffic, conversion, revenue, "top customers,"
anomaly magnitude — comes from a live query at report time. This is the
same Golden Rule #3/#6 every other `dept-*` skill enforces; analytics is
the surface where fabricating a plausible-looking number is easiest and
most damaging, since decisions get made on it directly.

## Output

A dashboard/report spec matching the audience (owner's WhatsApp = terse
table, per `dept-internal-comms`; written report = fuller prose only when
asked), with every figure traceable to the query that produced it.
