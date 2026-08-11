---
name: dept-keyword-seo
description: Keyword research, search-intent classification, and pillar+spokes content clustering for AutoSpareFinder — closes the Keyword Research and Content Clustering gaps. Honest about a real constraint the source material assumes away: we have no Ahrefs/Semrush/GSC connection, so real volume/difficulty data isn't available yet.
---

# AutoSpareFinder Keyword Research & Content Clustering

Adapted from `digital-marketing-pro`'s `keyword-research` + `keyword-cluster`
skills (635★, verified clean). Closes two of the gaps found in the
2026-07-27 department coverage audit.

## The real constraint the source material assumes away

Both source skills assume a connected keyword tool (Ahrefs `getRelatedKeywords`,
Semrush, SE Ranking, or GSC query mining) for real volume/difficulty
numbers. **We don't have one of these connected.** Per the Truth-Only
Guardrail every other section enforces: **do not fabricate a plausible-
sounding search volume or keyword-difficulty number** — that's exactly
the kind of invented statistic this whole department exists to prevent.

Until a real tool is connected, this skill runs in **fallback mode**:

- **Real internal signal first**: `search_misses` table — what customers
  actually searched for and didn't find. This is real, free, and already
  ours; start every keyword exercise here, not with external guessing.
- **Qualitative research via WebSearch**: surface real question patterns
  (People Also Ask style phrasing) without claiming a volume number for
  them — report "commonly asked" or "appears frequently in search
  results," never a fabricated "1,200 searches/month."
- **Clustering falls back to lexical grouping** (keyword-cluster's own
  documented fallback when no SERP/rank-tracker data exists) — flag this
  explicitly as lower-confidence in any output, exactly as the source
  material itself recommends when skipping the paid-tool step.
- If the owner later connects Search Console (free, and the most
  directly relevant since it's OUR OWN real query data) or a paid tool,
  upgrade this skill's process to use real numbers — note that as a
  concrete, low-cost recommendation rather than silently working around
  the gap forever.

## Process

1. **Real signal**: pull `search_misses` for the topic/category in
   question — this alone often tells us what to prioritize.
2. **Classify intent**: informational / navigational / commercial /
   transactional — for a parts marketplace, "buy [part] for [car]" and
   "[OEM number] price" are the highest-value transactional patterns.
3. **Qualitative expansion**: WebSearch for real question phrasing around
   the topic — no fabricated volume, cite what was actually found.
4. **Cluster into pillar + spokes**: group by vehicle/part-category
   (matches `dept-seo-programmatic`'s own page-generation units — a
   keyword cluster and a programmatic page category should be the same
   grouping, not two independent taxonomies).
5. **Hand off**: clusters feed `dept-content` (for pillar content) and
   `dept-seo-programmatic` (for the underlying catalog pages) — this
   skill plans, it doesn't generate the pages or articles itself.

## Truth-Only Guardrail (MANDATORY)

Never state a specific search volume, keyword difficulty score, or
ranking-competition number unless it came from a real connected tool
(Ahrefs/Semrush/GSC) — if none is connected, say "no volume data
available — connect Search Console or a keyword tool for real numbers"
rather than presenting a plausible guess as data. This is stricter than
the source material's own caution ("volume is an estimate, ranges not
point figures") because we don't even have estimates — only real internal
signal (`search_misses`) and qualitative pattern-matching.

## Output

A cluster plan: pillar topic → spoke keywords (each tagged with real
intent classification and a note on whether it's grounded in
`search_misses` data or qualitative WebSearch only) → recommended content
type per spoke → explicit flag if volume/difficulty data is unavailable.
