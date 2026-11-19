# BDS Suspension (ASAP Network) - reconciliation of the 2,257 imported parts (2026-11-19, Israel time)

Source: ASAP data sheet 130085 (updates), staged `130085_updates_20261118_20261118T222452Z.csv` sha256 `3afb8e95...9c049df9c1`
(2,345 rows). Import 2026-11-18 22:2x UTC via the documented browser relay -> `asap-collect` (stage_only) -> `importers/asap_import.py`.
All numbers below were queried live on 2026-11-19 (catalog DB, ASAP supplier 8237c52f...).

| Check | Result |
|---|---|
| BDS parts in catalog (source asap_network) | 2,349 (2,257 created 2026-11-18, 92 pre-existing) |
| Distinct part numbers | 2,349 of 2,349 - no duplicate part number, no duplicate SKU |
| Offers (ASAP Network) | 2,349 = exactly one per part; 0 typed variants; 0 with price <= 0 |
| Source provenance | manufacturer-approved ASAP brand account (BDS Suspension is one of the 5 approved brands of account 209986) |
| Product identity | SKU/brand/title taken from the manufacturer's own sheet; no merge with any other catalog part happened (policy: SKU alone never proves identity) |
| Names | 2,347 good; **2 truncated at import ("2007", "4 Inch Lift Kit 4") - repaired from the source CSV titles 2026-11-19**; root cause of the truncation NOT found (open) |
| Supplier state | `suppliers.is_active = FALSE` -> `ORDERABLE_OFFER_SQL` / `offer_is_orderable()` exclude every ASAP offer: none is purchasable, none shows a supplier price |
| What a customer sees | a labelled ESTIMATE (`is_estimate=true`), derived by the price policy from `importer_price_ils` = list_price(MSRP)/1.18 x FX. `max_price_ils` = 0 (MSRP never written as an Israeli consumer price) |
| Acquisition-cost evidence | NONE: account `price_level = 0` exposes no dealer/cost tier (`jobber_pricing` exists only in the AGM full sheet and is not a verified purchase price) |
| Availability flag | 2,345 offers `is_available = TRUE` (sheet says Available), 4 not; irrelevant to customers while the supplier is inactive |
| Search index | PostgreSQL active parts vs Meilisearch docs: see FIXES_TRACKER 2026-11-19 |

## Classification (one rule for AGM 73, BDS 2,257 (+92), Fox, Banks, Adams)
* **Verified product, no valid current purchasable offer** - ALL 2,349 BDS parts. The catalog part is genuine (manufacturer data); the
  offer is NOT purchasable and has no acquisition-cost evidence. No offer was deleted: nothing in the data proves an offer false or
  duplicate, and deleting the central parts would destroy genuine manufacturer catalog data.
* **Invalid / duplicate offers: 0** found. **Unresolved: 0 identities**; the unresolved item is commercial, not identity: *how we would
  buy and at what cost* (needs the supplier's dealer price level or a quote) - exact dependency: ASAP `price_level` / BDS dealer terms.
* **Open policy question (not changed by this work):** the estimate shown to customers is MSRP-derived. If the owner wants parts without
  a real acquisition cost to show NO price, the estimate path in `price_policy` must exclude `specifications.source='asap_network'`.
