# AutoSpareFinder — Product Roadmap
*Last updated: 2026-06-25*
*Vision: Global car parts comparison + sales marketplace (eBay/AliExpress for auto parts + AI)*

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
