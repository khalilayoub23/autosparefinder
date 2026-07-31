# NOA Reconfiguration Plan — Marketing / Sales / Social / Design "Super Agent"

> Owner-requested 2026-07-26. NOA's role expands from "social publisher + engagement" into a
> full marketing/sales/social/design agent. This is the **saved plan + todo list**; build order
> is tracked here. Owner decisions captured: **Design engine = HF image API** (text-to-image via
> the existing Hugging Face account; server has no GPU so all image gen is API-based).
> **Sales scope = BOTH modes** (engage+hand-off AND quote real catalog prices + checkout links).

## Current baseline (already live/built)
- **Publishing:** campaign engine, 2×/day multi-platform posts (peak hours), hashtag mix (HE/AR/EN), QR funnel.
- **Engagement (read+reply):** uniform `social/engagement.py` contract. LIVE: Facebook, Telegram, Discord. Built/awaiting creds: Instagram (Meta review), Reddit. In progress: YouTube, X.
- **Reply cap + hand-off:** ≤`NOA_ENGAGEMENT_MAX_REPLIES` (5) public replies per customer, then hand off to `/api/v1/go` support chat.
- **Owner console (WhatsApp):** `תגובות`/`ענה`/`דלג`, post approvals, `@נועה` guidelines.
- **Design assets:** PIL composition only (QR, thumbnails, brand canvas). **No AI image generation yet.**

## Pillar 1 — Marketing (extend)
- [ ] Performance-aware content: record per-post engagement (likes/comments/reach where the API exposes it) → feed the campaign generator so NOA repeats what works.
- [ ] Promo campaigns tied to REAL catalog deals (cheapest-supplier price via canonical fields; never invented).
- [ ] A/B ad-copy packs (already partly in the Monday brief) surfaced to the owner console.

## Pillar 2 — Sales (BOTH modes — owner decision)
- [ ] Mode A (safe, live): engage → hand off to support chat / plate-search.
- [ ] Mode B (new): NOA may quote **canonical catalog prices** (`_customer_price_fields`: cost×1.45 + conditional VAT) and drop **real checkout/plate-search links** in public replies/posts.
  - MUST follow pricing rules: never leak cost/margin/supplier; VAT conditional; run through `_sanitize_internal_pricing_disclosure`.
  - MUST NOT duplicate/contradict MAYA/LIOR; reuse the same price + checkout backends.
  - Gate per platform/context; default to Mode A, escalate to Mode B when the customer asks price/availability of an identifiable part.

## Pillar 3 — Social handling (extend)
- [x] Add **YouTube** channel to the engagement engine — ✅ **LIVE 2026-07-26** (channel `UCFFUS76VLN9G4JSUhR2whNw` "auto spare finder" under autosparefinder2024; refresh token + Data API v3 enabled; `youtube_configured:True`).
- [ ] ~~X (Twitter)~~ — **DROPPED** (owner 2026-07-26): X is pay-per-use, not free.
- [ ] Unified-inbox analytics in the owner console (counts per platform, pending, response time).
- [ ] Smarter cadence from engagement data (best times per platform).

## Pillar 4 — Design (NEW — HF image API + short video)
- [x] **Short "Coming Soon" video (15s)** — ✅ built + E2E-verified 2026-07-26: `social/video_gen.py` (PIL poster → ffmpeg 15s 1080×1920, CPU-only) + `social/youtube_upload.py` (Shorts via Data API). Test Short live (private) on the channel.
- [ ] Extend video: part-of-the-week / promo clips (part image + price + CTA), trilingual overlays (needs python-bidi), + post to TikTok / IG Reels / FB.
- [ ] `social/design.py`: text-to-image via HF Inference (e.g. FLUX/SDXL) → branded post image / ad creative.
  - Compose with the existing PIL brand canvas (logo, part photo, price strip) so output is on-brand, not raw AI art.
  - Content-addressed cache in the S3 thumbnails bucket (reuse `s3_storage`), like `qr_media`.
  - Guardrails: no fabricated prices/claims baked into the image; no supplier names/logos; alt-text.
- [ ] Wire generated images into NOA's post pipeline (attach as `media_url`) and ad-creative packs.
- [ ] Owner approval of generated designs via the WhatsApp console before publish.

## Build order (owner-set 2026-07-26)
1. **YouTube engagement adapter** — DO NOW (this session). ~~X~~ dropped (not free).
2. Then the NOA role reconfig above, starting with **Design (HF image API)** + **Sales Mode B**, then Marketing performance loop.

## Notes / constraints
- Server: no GPU, ~limited RAM → all AI image gen is API-only (HF).
- Every customer-facing price on EVERY surface = canonical `_customer_price_fields` (never flat VAT, never base_price).
- Keep NOA's truth-only rule: never invent prices, stock, programs, or discounts.

_Last updated: 2026-07-26._
