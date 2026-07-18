# AutoSpareFinder — Product Roadmap
*Last updated: 2026-07-18*
*Vision: Global car parts comparison + sales marketplace (eBay/AliExpress for auto parts + AI)*

---

## Workstream — Multi-language (AR/HE/EN) + landing i18n + chat/NOA (goal G7, 2026-07-18)

> Process log for goal **G7** (per owner: document the *process* here, not FIXES_TRACKER).

**Objective:** the system supports 3 languages — Arabic, Hebrew, English. The landing page
supports all 3 with RTL + responsive on PC/tablet/mobile. Then verify links/buttons, test chat
in all 3 languages, and fix the NOA link-shortener.

**Audit (starting state):**
- Backend chat *had* 3-language LANGUAGE RULES (detect first message → reply in-language) but
  the **structured search-results output was hardcoded Hebrew banner + English labels**, and
  Arabic free-text vehicle capture / LLM steering were weak.
- Frontend landing page was **English-only** with a **dead `?lang=` switcher** (no i18n lib, no
  translations, `index.html lang="en"`, no dir).

**Delivered:**
1. **Landing i18n** — new `frontend/src/i18n.js` (lang from `?lang`/localStorage/browser, sets
   `<html lang>`, ~70-string AR/HE/EN dictionary, `t()` with interpolation). `LandingPage.jsx`
   fully translated, dynamic `dir` on the landing container, working language switcher, and
   `rtl:`/`ltr:` Tailwind logical variants for directional spots (dropdown, hero icon/padding,
   WhatsApp button, step connector, cart badge). `dir` scoped to the landing container so the
   app's other (Hebrew-hardcoded) pages are untouched — **no breaking points**.
   *Verified:* Playwright e2e `backend/devtests/landing_i18n_links_test.py` — **30/30 PASS**:
   dir (rtl he/ar, ltr en), translated headings render, **no horizontal overflow** at desktop
   1280 / tablet 820 / mobile 390, **all 15 links resolve**, 11 buttons enabled, hero search
   navigates to `/parts`. Screenshots confirm a fully-mirrored professional Arabic RTL page.
2. **Chat results formatter** — now language-aware (fit banner + price "incl. VAT" + shipping
   "days" + "months warranty" localized he/ar/en). *Verified* `chat_multilang_test.py`: HE reply
   fully Hebrew, EN reply fully English (**2/3**).
3. **NOA link shortener** — root-caused: `_strip_malformed_links` had an unguarded
   `re.sub("://[^\\s]+")` that **destroyed every URL in a message body** to a dangling "https"
   (only icon-footer links survived) — the "link shrinker not working at all messages" report.
   Removed it (the guarded `(?<!\\w)://` already strips orphans), added a shrink pass that tidies
   real body URLs. *Verified:* valid links survive + shrink (www stripped), malformed `://./`
   `://../` orphan `://` still removed.

**Arabic chat gap — CLOSED (2026-07-18):**
- Added **Arabic make/model aliases** to `_MAKE_ALIAS_MAP` + `_MODEL_LEXICON` (27 makes, 30+
  models) and taught `_alias_present` / `_strip_vehicle_terms` to match Arabic (Arabic-letter
  boundaries + the attached preposition prefix ل/ب/و/ف/ك, e.g. "لتويوتا"). Arabic queries now
  enter the fitment-first flow (`تويوتا كورولا 2018` → toyota/corolla/2018, part query cleanly
  separated).
- Added ~50 **Arabic part terms** to `_CATEGORY_KEYWORDS` (فلتر→filter, فرامل→brake, محرك→engine,
  مساعد→suspension, بطارية→electrical, تكييف→ac …) + the `is_parts_request` recognizer.
- **Vehicle summary localized** (`_vehicle_summary_he(profile, lang)` → he/ar/en).
- **Verified:** `chat_multilang_test.py` → **3/3** — Arabic customer gets an Arabic reply (was
  Hebrew), HE fully Hebrew, EN fully English.

**Open follow-ups (next iteration):**
- Extend i18n beyond the landing to the rest of the SPA (parts/chat/cart) if full trilingual UI
  is wanted (out of G7 scope — landing only).
- Broaden Arabic part-term + model coverage as real Arabic queries arrive (search_misses).

**Next (owner-stated, after G7):** images → server bucket + deploy; add Reddit / Discord / X
channels to NOA.

---

## Current State (2026-06-25)
- **4,013,224** active parts in catalog
- **1,685,700** with IL importer price (42%)
- **2,327,524** unpriced — inventory gaps to fill, NOT dead weight
- **3,679,650** supplier_parts rows (1,046,392 available)
- Meilisearch: 3.5M docs indexed, 14.7GB disk, ~300ms avg response
- Server: 8GB RAM, 87% disk used

---

## Phase 1 — Foundation Fixes (NOW / this week)

| Task | Status | Owner |
|------|--------|-------|
| Fix Meilisearch settings timeout (300s → 1800s + non-fatal) | ✅ Done | Claude |
| Add `supplier_count` + `cheapest_price_ils` to search results | ✅ Done | Claude |
| Add `GET /api/v1/parts/{part_id}/suppliers` comparison endpoint | ✅ Done | Claude |
| Raise supplier fetch cap from 4 → 20 per search result | ✅ Done | Claude |
| Create REX todos for unpriced brand gaps (Volvo/Honda/Hongqi/WEY/MG) | ✅ Done | Claude |
| Fix wrong_margin=12,232 parts | ✅ Done | Claude |
| Fix bad_cond=142K parts (original→oem, refurbished→new) | ✅ Done | Claude |

---

## Phase 2 — Catalog Growth (weeks 1-4)

| Task | Priority | Owner | Notes |
|------|----------|-------|-------|
| REX: Volvo IL prices (Colmobil) — 24,892 gaps | 🔴 High | REX | colmobil.co.il |
| REX: Honda IL prices (Champion Motors) — 18,594 gaps | 🔴 High | REX | champion-motors.co.il |
| REX: Hongqi + WEY IL prices (Shlomo Group) — 22K gaps | 🟠 Med | REX | shlomo.co.il |
| REX: Wire car-parts.ie EUR prices to supplier_parts | 🔴 High | REX | ~300K parts get EUR price row |
| REX: Global SKU enrichment from eBay/RockAuto/Autodoc | 🔴 High | REX | Top 1K search misses first |
| Harvest: Commercial vehicles (Sprinter, Transit, Ducato) | ✅ Done | Harvest | In rotation since 2026-06 |
| Harvest: Pre-2000 models (200-400K genuinely new parts) | 🔄 Running | Harvest | 47 pre-2000 generations added to queue 2026-07-05 (96→143 models) |
| Harvest: All engine variants per model (not just 1) | 🟡 Low | Harvest | 10-20% incremental |

---

## Phase 3 — Search & Performance (weeks 2-6)

| Task | Priority | Notes |
|------|----------|-------|
| Disk cleanup / server upgrade (currently 87% full) | 🔴 Critical | Before more Meili indexing |
| Migrate Meilisearch → Typesense | 🔴 High | 3-5 days. 2-3× more RAM efficient. Handles 10M+ at <50ms |
| OR: Upgrade server to 16-32GB RAM | 🔴 High | Alternative to Typesense migration |
| Add `pg_trgm` + `tsvector` Postgres fallback search | 🟠 Med | Covers parts not in Meili |
| Search warm-up + result caching improvements | 🟡 Low | Already partially done |

---

## Phase 4 — Marketplace Features (weeks 4-10)

| Task | Priority | Notes |
|------|----------|-------|
| **Frontend: Supplier comparison table** (eBay-style "see all offers") | ✅ Done 2026-07-05 | `AllOffersModal` in Parts.jsx — "השווה את כל ההצעות" button on every part card (grouped + flat views), fetches `/parts/{id}/suppliers`, sorted by total, best-price highlight, savings deltas, per-offer add-to-cart |
| **Frontend: Price history chart** per part | 🟠 Med | Store price_ils snapshots in supplier_parts |
| **AI: Semantic search** (pgvector embeddings per part) | 🟠 Med | "Find water pump for 2014 Corolla" natural language |
| **AI: Fitment assistant** — "does this fit my car?" | 🟠 Med | Uses part_vehicle_fitment + NLP |
| **AI: Smart recommendations** — "people also bought" | 🟡 Low | Collaborative filtering on orders |
| **Supplier onboarding flow** — self-service for new suppliers | 🟠 Med | Currently manual |
| **Price alerts** — "notify me when brake pads for my car drop below X" | 🟡 Low | Redis pub/sub |

---

## Phase 5 — Scale (months 3-6)

| Task | Notes |
|------|-------|
| 10M parts target | Commercial vehicles + pre-2000 + all variants + new IL importers |
| Typesense at 10M docs | ~2-3GB RAM, <50ms at scale |
| pgvector AI index on 10M parts | Embed on write, IVFFlat index |
| Postgres partitioning by manufacturer | Reduces query scan at 10M+ |
| PgBouncer connection pooling | For high-concurrency marketplace traffic |
| CDN for part images | Currently images are URLs, cache via CDN |

---

## Key Architecture Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Search engine at 10M | Typesense (migrate from Meili) | 3× RAM efficient, same API style, built-in vector |
| AI search | pgvector in Postgres | No extra service, consistent with existing DB |
| Supplier comparison | Existing supplier_parts table (already wired) | Just needs frontend |
| Part pricing | Always compute from supplier_parts, never hard-code | Prices change daily |

---

## Immediate Next Actions (today)

1. **Khalil (NIR)**: Contact Colmobil (Volvo IL) and Champion Motors (Honda IL) for price list access
2. **REX**: Will auto-start working on the 6 todos created today
3. **Server disk**: Either clean Meilisearch old indexes or expand disk before next harvest run
4. **Frontend**: Wire the new `/api/v1/parts/{part_id}/suppliers` endpoint to product detail page
