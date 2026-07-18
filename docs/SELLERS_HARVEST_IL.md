# International Seller Harvest Status (verified 2026-07-12)

Goal: harvest **prices + shipping fees** from international sellers that ship to Israel.
This is the VERIFIED state — grounded in the live DB (what each seller has actually
harvested) + browser checks this session. Supersedes the earlier optimistic study,
which had errors (it claimed Autodoc has an IL store — the domain doesn't even exist).

## What each connected seller has ACTUALLY harvested (live DB, 2026-07-12)

| Seller | Parts in DB | Priced | With shipping | Integration | Real status |
|---|---:|---:|---:|---|---|
| **eBay** | 33,144 | 33,144 | 26,628 | Browse **API** | ✅ **Working** — the only real harvester |
| RockAuto | 1 | 1 | 1 | Browser relay | 🟡 Wired this session; needs a scaled run |
| AliExpress | 0 | 0 | 0 | DS **API** (keys valid) | ⚠️ API works but **0 OEM matches** (see below) |
| Autodoc | 0 | 0 | 0 | "API" to dead domain | ❌ `autodoc.co.il` = **NXDOMAIN**; EU-only |
| FCP Euro | 0 | 0 | 0 | affiliate link only | ❌ no price harvest yet |
| ECS Tuning | 0 | 0 | 0 | affiliate link only | ❌ no price harvest yet |
| Summit Racing | 0 | 0 | 0 | affiliate link only | ❌ no price harvest yet |
| PartSouq | 0 | 0 | 0 | affiliate link only | ❌ no price harvest yet |
| Amayama | 0 | 0 | 0 | affiliate link only | ❌ no price harvest yet |

**Key reframe:** every `EXTERNAL_ENABLE_*` flag is already ON and eBay/AliExpress keys
are set — so "enabling" is not the blocker. **Only eBay actually harvests.** The real
gaps are (a) match-rate and (b) building real harvesters for the affiliate stubs.

## Ship-to-Israel verification (browser, this session)

| Seller | Ships to IL? | How verified |
|---|---|---|
| RockAuto | ✅ Yes | Cart quote to Tel Aviv: $57.99 economy (per-order floor) |
| eBay | ✅ Yes | 26,628 parts already carry real IL shipping from the API |
| Amayama | ✅ **Confirmed** | Owner's logged-in account has an **Israel address (+972)**. Ships from Japan+UAE. **Has a Customer API but it's ORDER-TRACKING ONLY** (`GET /api/customers/v1/orders`, `GET /orders/{id}`) — NO product/price/quote endpoint, so price harvest = scraping; shipping calc is CAPTCHA-gated; order placement manual |
| AliExpress | ✅ Yes | Ships IL; but OEM search returns ~0 matches |
| car-parts.ie | ❌ No | Shipping page: EU-only ("we ship from locations within the EU") |
| Autodoc | ❌ No | `autodoc.co.il` doesn't exist; autodoc.eu is EU-only |
| FCP Euro / ECS / Summit / PartSouq | ❓ Verify | Ship international via FedEx/USPS; IL needs a checkout cart-check (not yet done) |

## Why match-rate is the real problem (not "enabling")
Harvesting works by looking up our **OEM numbers** on each seller. That only hits when
the seller indexes listings by OEM:
- **eBay** — listings carry OEM/MPN → ~30% hit → 33K parts. ✅
- **AliExpress** — listings are fuzzy titles, not OEM → **0/20** matched. Needs a
  title+fitment matching strategy, not OEM lookup, to be useful.
- **RockAuto** — US catalog → only ~13%, and only US-market OEMs (our unpriced parts are
  mostly region-specific → near-0 on random batches). Best via browsing US vehicles.

## Recommended sequence
1. **eBay** — already our workhorse; keep it. Widen brand/OEM coverage.
2. **AliExpress** — real API + ships IL + keys valid → **highest-value quick win** IF we
   switch matching from OEM to name+fitment (with a confidence guard to avoid wrong parts).
3. **RockAuto** — run the browser harvester at scale on US-market OEMs.
4. **No-API sellers** (Amayama, PartSouq, FCP, ECS, Summit) — each is a RockAuto-scale
   harvester project; build one at a time after a cart-verify of IL shipping. Amayama &
   PartSouq (OEM, worldwide) are the highest-value of these.
