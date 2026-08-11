---
name: dept-geo-content
description: Structures AutoSpareFinder's content/schema/entity data so AI answer engines (ChatGPT, Perplexity, Google AI Mode/Overviews, Gemini, Copilot) can accurately cite us — the content-production counterpart to dept-analytics' AI Assistant traffic measurement. Includes current (May-June 2026) Google guidance that debunks common AEO myths.
---

# AutoSpareFinder GEO/AEO Content Strategy

Adapted from `digital-marketing-pro`'s `aeo-geo` skill (635★, verified
clean). Closes the GEO gap from the 2026-07-27 coverage audit —
`dept-analytics` already measures AI-assistant traffic (the GA4 "AI
Assistant" channel); this skill is the production side: making our
content actually citable in the first place.

## Genuinely current, load-bearing facts (kept verbatim — these change
what NOT to waste effort on)

Per Google's own AI Optimization Guide (updated 15 May 2026):

- **"No `llms.txt` file is needed."** Google's official position: you
  don't need new machine-readable files, AI text files, or special
  Markdown to appear in generative AI search. **Do not build or maintain
  an `llms.txt` for AutoSpareFinder** — document any suggestion to do so
  as low-priority with no measurable upside, exactly as Google states.
- **"No special AI-specific schema is needed."** Standard `schema.org`
  markup (already `dept-seo-technical`'s job) is what matters — there is
  no separate "AI schema" to add on top.
- **Eligibility is standard Search eligibility** — a page must be
  indexed and Search-eligible with a snippet to appear in AI features.
  This means `dept-seo-technical`'s crawlability/indexation work is a
  **prerequisite** for AEO, not a separate track.
- **Google AI Mode is a distinct surface from AI Overviews** (crossed
  ~1B MAUs, Gemini 3.5 Flash base model, as of the 19 May 2026 I/O
  announcement) — a program that only checks AI Overviews + ChatGPT +
  Perplexity has a real blind spot; Google AI Mode needs independent
  checking.
- **Opt-out control**: Search Console has a property-level toggle
  (added 3 June 2026) to exclude the site from AI Overviews/AI Mode
  grounding, separate from `Google-Extended` (which controls Gemini
  app training / Vertex AI grounding outside Search). We are not
  opting out — noted here only so nobody accidentally flips it.

## What actually helps (the real, buildable work)

- **Entity consistency**: our NAP-equivalent (business name, real
  supplier-count claims, real coverage numbers) must be identical across
  every surface an AI model might retrieve from — our own site, any
  business listings, social profiles. Inconsistency across surfaces is a
  real, fixable AEO problem; fabricating consistency by picking whichever
  number sounds best is not fixing it, it's the same Truth-Only violation
  every other section guards against.
- **AI-first content formatting**: clear factual statements, real
  definitions, citation-worthy snippets — e.g. "AutoSpareFinder verifies
  part fitment against `part_vehicle_fitment` data before showing a
  result" is a citable, factual claim; generic marketing copy is not.
- **Structured data** (real `Product`/`Organization`/`FAQ` schema) — this
  is `dept-seo-technical`'s job; this skill doesn't duplicate it, it
  informs what content needs schema coverage for AI citability
  specifically.

## Process

1. **Baseline**: pick 10-25 real target queries (from `search_misses` +
   `dept-market-research` findings, not invented), test how AutoSpareFinder
   currently appears (or doesn't) across ChatGPT/Perplexity/Google AI
   Mode/AI Overviews/Gemini/Copilot.
2. **Entity consistency check**: verify brand facts are identical across
   every surface — flag any mismatch as a real fix, don't paper over it.
3. **Content gap**: which real, citable facts about AutoSpareFinder
   (fitment verification, real supplier count, real price methodology)
   aren't currently expressed as clear, structured, citation-worthy
   statements anywhere on the site?
4. **Do NOT recommend `llms.txt` or non-standard AI schema** — flag to
   the requester that this is explicitly a non-priority per Google's own
   guidance, save the effort for the real work above.

## Truth-Only Guardrail (MANDATORY)

Every "fact" optimized for AI citation must be real and verifiable — an
AI engine that confidently cites a false claim about AutoSpareFinder
(wrong price formula, an invented program) is worse than not being cited
at all, since it's now actively spreading misinformation about the
business to every user who asks. This is the highest-stakes surface for
the Truth-Only rule in the whole department.

## Output

An AI-visibility baseline (6-surface check), entity-consistency findings,
and a prioritized list of real facts to make more clearly citable —
never a recommendation involving `llms.txt` or non-standard schema.
