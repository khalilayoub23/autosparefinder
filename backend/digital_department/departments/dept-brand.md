---
name: dept-brand
description: Applies AutoSpareFinder's actual brand colors, typography, and voice to any artifact — landing page, marketing asset, social post, or document. Use whenever brand consistency matters.
---

# AutoSpareFinder Brand Guidelines

Adapted from the general "brand-guidelines" pattern (apply a company's real
identity to any artifact), but every value below is pulled from this
project's own `docs/UI_UX.md` and live-audited rendered output — not
invented. If `docs/UI_UX.md` changes, update this file to match; never let
the two drift (the 2026-07 design-review already caught one drift: the doc
said "Inter" but the site renders Rubik/Heebo — always trust the LIVE
render over the doc when they disagree, and fix the doc).

## Voice
<!-- priority: critical -->

Professional, trustworthy, global auto-parts marketplace — not a hobbyist
shop. Confident but never salesy-exaggerated. Match the tone already
established in the landing page copy ("Find the Right Part. Fast. Easy.
Reliable.") — short declarative sentences, benefit-first, no filler
adjectives ("revolutionary", "game-changing").

Trilingual by default: Hebrew, Arabic, English. Never mix languages within
one asset — detect/match the customer's language per the existing chat
policy (see `docs/skills.md` LANGUAGE RULES), and apply the same rule to
any new marketing asset aimed at a specific-language audience.

## Truth-Only Guardrail (MANDATORY — read before writing any marketing copy)
<!-- priority: critical -->

This is the specific, already-logged failure mode this skill exists to
prevent (see `CLAUDE.md` Mistake Log, 2026-07-05 SHIRA entry): **never
invent a program, discount, or number that isn't real.**

Before any brand asset ships, check the claim against what's actually true:
- **Pricing**: `cost × 1.45` + conditional VAT (18% IL suppliers only, 0%
  foreign) — never a flat rate, never a made-up "sale price." If quoting a
  real number, pull it from a real search result, not an estimate.
- **Programs**: no referral program, no loyalty program, no coupon codes
  exist unless a specific ticket/commit says otherwise — verify against the
  DB/code, not memory, before claiming one exists.
- **Coverage claims** ("1000+ verified suppliers", "millions of parts"):
  these must trace back to a real query result, not a round-number guess.
- If a number can't be verified in the time available, use qualitative
  language ("thousands of parts", "trusted global suppliers") instead of a
  specific false-precision figure.

<!-- 2026-08-15 root-fix: a 2026-08-14 patch moved Voice+Truth-Only Guardrail
     ABOVE Colors/Typography, relying on file POSITION to survive truncation
     — a follow-up audit proved this still silently dropped Truth-Only
     Guardrail (it only bought Voice's first paragraph, budget ran out before
     reaching Guardrail). Position is no longer what protects these sections:
     each is now marked `<!-- priority: critical -->` and the allocator
     (digital_department/context.py) guarantees every CRITICAL section across
     every loaded department is fully included before ANY lower-priority
     section anywhere gets a single character — regardless of file order. -->

## Colors
<!-- priority: low -->

| Token | Value | Usage |
|---|---|---|
| `--blue-primary` | `#2563eb` | CTAs, active states, links |
| `--blue-hover` | `#1d4ed8` | Hover on primary buttons |
| `--blue-highlight` | `#3b82f6` / `#7fb2ff` | Accent text on dark backgrounds |
| `--hero-bg` | `#070e1d` → `#0d1b35` gradient | Hero / dark sections |
| `--footer-bg` | `#021737` | Footer, dark cards |
| `--page-bg` | `#ffffff` | Light section backgrounds |
| `--green-whatsapp` | `#25D366` | WhatsApp CTA only — don't reuse this green elsewhere, it signals "chat" specifically |

## Typography
<!-- priority: low -->

- **Rendered font** (verified live, 2026-07-26 audit): Rubik, Heebo, system-ui — Hebrew-capable, matches the platform's Hebrew/Arabic/English trilingual requirement (G7).
- **Do not default to Inter/Roboto/Open Sans/Poppins** for new marketing assets — those are the "potentially generic" fonts flagged in gstack's own design-review checklist, and more importantly they don't have the Hebrew glyph coverage this platform needs.
- Headline weight: 800 (extrabold). Body: 400–600.

## Applying This Skill

1. Read the target artifact (HTML/copy/image spec).
2. Map its colors/fonts to the tokens above; flag anything that doesn't fit
   an existing token rather than silently inventing a new one.
3. Run every factual/numeric claim through the Truth-Only Guardrail above.
4. If the artifact is customer-facing in a specific language, confirm it
   doesn't mix languages and matches that language's existing chat tone.
