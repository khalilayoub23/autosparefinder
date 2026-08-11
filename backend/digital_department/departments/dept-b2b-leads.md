---
name: dept-b2b-leads
description: Identifies B2B wholesale/fleet leads (repair shops, fleet managers, dealerships) for AutoSpareFinder's bulk-ordering side — complements NIR's supplier-side sourcing (services/supplier_sourcing.py), which finds SELLERS, not buyers.
---

# AutoSpareFinder B2B Lead Research (buyer side)

Adapted from the general "lead-research-assistant" pattern. Important
distinction from existing platform code: NIR's `supplier_sourcing.py`
already discovers and onboards **sellers/suppliers**. This skill is the
mirror image — finding **wholesale buyers** (independent repair shops,
fleet operators, dealership service departments) who'd want a bulk-ordering
or B2B account, a customer segment the platform doesn't yet actively
prospect for.

## When to Use

- The owner wants to grow B2B/wholesale revenue, not just retail chat/web traffic.
- Identifying repair-shop or fleet accounts in a specific region to reach out to.

## Process

1. **Define the ICP** for this platform specifically: independent garages,
   fleet maintenance managers, small dealership service departments —
   businesses that buy parts recurringly and in volume, not one-off retail
   buyers (the existing customer base).
2. **Research via WebSearch/WebFetch only** — public business listings,
   company sites, public job postings signaling growth (e.g. a garage
   hiring more mechanics = more parts volume). No paid data broker, no
   scraping login-gated directories.
3. **Score fit** (1–10): recurring-purchase likelihood, estimated volume,
   geographic serviceability (real shipping data only — see
   `feedback_no_fake_data.md`, never invent a delivery promise for a region
   we haven't actually verified shipping to).
4. **Output actionable, truthful outreach material** — see guardrail below.

## Output Format

```markdown
# B2B Lead Research — [region/date]

## Lead: [Business Name]
- Type: independent garage / fleet / dealership service
- Estimated volume signal: [specific, sourced reason]
- Fit score: [X/10] — [why]
- Outreach angle: [specific value prop we can ACTUALLY deliver]
- Contact path: [public contact info found, or "none public — needs owner intro"]
```

## Truth-Only Guardrail (MANDATORY)

Every "value prop" offered to a lead must be something AutoSpareFinder
genuinely provides today: real supplier comparison, real fitment
verification, real WhatsApp/chat support — never a bulk-discount tier,
dedicated account manager, or NET-30 terms unless that program actually
exists in the platform (check with the owner before claiming any B2B-specific
program, since none is documented as existing yet). This is the same
failure mode already logged for SHIRA (`CLAUDE.md` Mistake Log,
2026-07-05) — promising a program that isn't real.

## Relationship to Existing Code

This skill is research/output only — it does **not** write to any
`suppliers` or `customers` table, and does not duplicate `NIR`'s
`services/supplier_sourcing.py` (that remains the seller-onboarding path,
gated behind owner approval per its own rules). Any actual outreach
automation built from this skill's output should follow the same
owner-approval-gate pattern NIR already uses, not send anything
automatically.
