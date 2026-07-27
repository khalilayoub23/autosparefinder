---
name: dept-cro
description: Conversion rate optimization for AutoSpareFinder's real search/checkout flow — landing page audits, checkout friction, UX/microcopy review, A/B test design with real statistical rigor, all wired to the real conversion event (create_whatsapp_checkout / web checkout completion), not a proxy metric.
---

# AutoSpareFinder CRO

Adapted from `digital-marketing-pro`'s `cro` skill (635★, verified clean —
including the referenced `sample-size-calculator.py` and
`significance-tester.py` scripts, both pure statistical math, no
network/exec calls). Closes the CRO gap from the original architecture
proposal.

## Real conversion funnel this must respect

Not a generic e-commerce funnel — this platform's actual path is: search
(web/WhatsApp/Telegram) → fitment-verified result → checkout link
(`create_whatsapp_checkout` or web checkout) → payment. Any CRO
recommendation must point at a **real completed-order event** as the
conversion goal, never a proxy like "add to cart" or "clicked search" as
the primary metric.

## Applicable Capabilities (kept from source, real conditions)

- **Landing page audit** (5-second test, above-the-fold, trust signals,
  CTA analysis, mobile audit) — apply to the real landing page, whose
  actual copy/colors/structure are already documented in
  `dept-brand`. Don't propose changes that contradict the
  brand system without flagging it as a brand-guideline change, not just
  a CRO tweak.
- **Checkout optimization** — real, applicable friction points to check:
  guest checkout availability, real shipping-cost transparency (per
  `feedback_no_fake_data` — shown shipping must be measured, never
  estimated), payment method coverage, WhatsApp checkout-link tap-through
  (already flagged and fixed once — `CLAUDE.md` 2026-07-13: reminders
  used to send a bare API path instead of a tappable `https://` link;
  don't reintroduce that class of bug).
- **A/B testing framework** — genuinely reusable statistical rigor (ICE
  scoring, real sample-size/significance calculation via the two vetted
  scripts). Use these calculators for real, don't eyeball significance.

## UX Writing / Microcopy (folded in, not a separate skill)

No source repo we vetted had a dedicated UX-writer skill — rather than
invent one without a real source, this capability lives here, since form/
CTA/error-message copy is inseparable from CRO in practice:

- **Button/CTA copy**: specific over generic ("Get My Free Trial" beats
  "Submit") — for us, "Check Fitment & Price" beats "Search".
  content already established.
- **Form error messages**: specific, positioned near the relevant field,
  never a generic "Error occurred."
- **Empty/loading states**: match the existing brand voice
  (`dept-brand`) — never a bare "No results."
- Every microcopy change still runs through the Truth-Only Guardrail
  below — a "friendly" empty-state message can't imply something false
  ("more results coming soon!" when none are planned).

## What NOT to carry over

- SaaS-specific pricing psychology (plan naming "Starter/Growth/Enterprise",
  tier feature-gating) — this is a marketplace with one pricing formula
  (`cost × 1.45` + conditional VAT), not a tiered-plan product. There is
  no "pricing page" to optimize the way a SaaS pricing page is optimized.
- Any framing that assumes multiple traffic-source funnels feeding into
  a single conversion — check the actual channel (web/WhatsApp/Telegram)
  since each has a different real conversion path.

## Edge Case (kept, genuinely relevant here)

**Low-traffic segments**: many niche vehicle/part combinations will have
low monthly conversion volume individually (same insight as
`dept-seo-programmatic`'s data-quality check — some vehicle/category
combos have very few real fitment-verified parts). For these, skip formal
A/B testing (can't reach significance) and apply audit-based best
practices directly, same as the source material's own low-traffic
guidance — don't force a statistical test where the sample will never
be adequate.

## Process

1. Confirm the conversion event being optimized is a real one (checkout
   completion, not a proxy).
2. Run the standard landing-page or checkout audit against the *real*
   current page/flow, not a hypothetical redesign.
3. For any proposed test, calculate real sample size/duration with the
   vetted scripts before recommending a test — don't propose running a
   test that traffic volume can't actually complete in reasonable time.
4. Flag any change that touches pricing display, shipping claims, or
   program claims through the Truth-Only Guardrail below before shipping.

## Truth-Only Guardrail (MANDATORY)

Any CRO recommendation involving displayed price, shipping cost, or a
trust-badge claim ("100% secure", "fastest shipping") must be real and
verifiable — same rule as every other `dept-*` skill. A CRO "quick win"
that adds an unverified urgency claim ("Only 2 left!") is a truth
violation, not a legitimate optimization, unless the stock number is
real and live.

## Output

Landing-page or checkout audit with severity-rated findings (critical/
high/medium/low) and ICE-scored recommendations; or an A/B test plan with
real calculated sample size/duration, never a plan skipping that step.
