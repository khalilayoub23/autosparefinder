# Customer price policy — one rule, every channel

**Code:** `backend/price_policy.py` (the ONLY place the rule is defined) · **Guard:**
`backend/tests/test_price_policy_unified.py` · **Owner directive 2026-10-29:** every agent and every customer
channel uses the same policy and shows the same price.

## The rule

| Step | Rule | Function |
|---|---|---|
| Which offer prices a part | the cheapest **customer-visible** offer (available, active supplier, not an internal/test supplier, has a listing URL), preferring offers of the part's own product class (OEM / OE-equivalent / aftermarket) | `VISIBLE_OFFER_SQL` + `price_basis_order_sql()` |
| Which offers can be ordered | every **orderable** offer (available, active supplier, not internal/test) — the compare page lists them all, sorted by what the customer pays; the pre-charge check re-prices the offer the customer chose if it is still orderable | `ORDERABLE_OFFER_SQL` |
| Offer cost | `price_ils`, else `price_usd ×` the live USD→ILS rate | `offer_cost_ils()` |
| Net (before VAT) | `round(cost × 1.45, 2)` — uniform 45% margin, no exceptions | `unit_price()` |
| VAT | `round(net × rate, 2)`; rate **18% for a local (Israeli) supplier, 0% for a foreign one**; unknown supplier → local (VAT is never silently dropped) | `vat_rate()` |
| **Customer unit price** | `net + VAT` — the number shown on search, compare, cart, wishlist, watch, chat, brands pages, NOA posts, partner API | `UnitPrice.total` |
| Delivery | the offer's own delivery fee (`resolve_customer_shipping_fee`: supplier shipping, else the supplier profile), never inside the margin base | `delivery_fee()` |
| Order line | `net × qty`, `VAT × qty` (per-unit VAT × qty, never VAT on the rounded subtotal) | `UnitPrice.line()` |
| Order total | lines + delivery **once per supplier** (the highest fee among that supplier's lines) | `order_totals()` |
| Payment provider amount | ILS → agorot **rounded** (`int(x*100)` truncated ₪29.99 to 2998) | `agorot()` |
| Part with no live offer | an **estimate** with the same rule from the catalog importer cost: `importer_price_ils` (local) → `max_price_ils / 1.18` (IL consumer reference incl. VAT, local) → `online_price_ils` (foreign); flagged `is_estimate` / `is_base_price_fallback` | `catalog_estimate_price()` |

**Never a customer price:** `base_price`, `min_price_ils`, `importer_price_ils`, `online_price_ils`,
`supplier_parts.price_ils` / `price_usd` (internal columns). The website never computes a price — it displays
the backend's `customer_price_ils` / `customer_vat_ils` / `customer_total_ils`.

## Where each surface gets its price (all from `price_policy`)

| Surface | Code |
|---|---|
| Website search / VIN / vehicle search (offers + list price range) | `routes/parts.py` `_customer_price_fields` → `customer_fields`; `_apply_customer_price_range` |
| Part compare (`/api/v1/parts/{id}/suppliers`) incl. the no-offer estimate | `routes/parts.py` |
| Cart, wishlist, price watch | `routes/cart.py`, `routes/parts._current_part_offer` |
| Order creation | `routes/orders._order_line_pricing` |
| Stripe checkout — live re-price before charging (single + multi order) | `routes/payments._live_reprice_order` |
| WhatsApp / Telegram / web chat checkout | `routes/payments.create_whatsapp_checkout` |
| Chat search (all agents, all channels) | `BACKEND_AI_AGENTS` `calculate_customer_price(_from_ils)` → `unit_price` |
| Brands pages | `routes/brands.py` (agent functions) |
| NOA marketing price | `BACKEND_API_ROUTES._noa_real_catalog_fact` |
| Partner API, Eurosender declared value, social designs | `_customer_price_fields` |

## Internal (not customer prices)

Importers set `importer_price_ils` / `base_price` / `max_price_ils` at ingest (docs: CLAUDE.md "Import formula");
admin revenue report and supplier-cost estimation use the policy constants.

### `base_price` (internal reference) — ownership rule (#119, 2026-10-31)

`base_price` is read only for the website search's tie-break order, harvest targeting (eBay / unpriced-OEM feed:
`base_price` empty = no price source) and the admin export. It is never shown to a customer. After ingest it is
always this policy's own **net** price (`unit_price(cost).net`), computed by `db_update_agent.apply_base_price_rule`:

| The part has | `base_price` = | Owner task |
|---|---|---|
| a customer-visible offer of its own product class | net of the cheapest such offer (`VISIBLE_OFFER_SQL`, `price_basis_order_sql`) — the net the customer is shown | `fix_base_prices` |
| no customer-visible offer | net of `catalog_estimate_price(importer, max, online)` — the estimate the customer is shown | `normalize_base_price` |
| only offers of another class, or no input | unchanged (an other-class offer never sets it — owner rule 2026-09-21) | — |

The two populations are disjoint, so the tasks cannot undo each other. The cleanup agent's healers
(`task_normalize_base_price_batched`, `task_recover_priced_inactive`, `task_recover_motorstore_prices`,
`task_heal_importer_price`) call the same function. The cleanup normalizer fills `importer_price_ils` /
`max_price_ils` only when empty and only from a **local** offer: those columns are the estimate's local inputs
(18% VAT), so a foreign cost written there would be priced with Israeli VAT.

## Changing the policy

Change `price_policy.py` only, then run `tests/test_price_policy_unified.py`. A new surface that shows a price
must call `price_policy` (or `_customer_price_fields`); the guard fails on any `1.45` / `1.18` / `0.18` number
in a customer-facing module.

### Writers of `base_price` (2026-11-01)
A writer that has a cost stores `price_policy.base_price_for_cost(cost_ils)` (= `unit_price(cost).net`, 0.0 when there is no
usable cost) — for a NEW part or an EMPTY value only; it never overwrites a non-empty value and never carries its own
`x 1.45`. A writer without a supplier/cost context (an uploaded bare price, an LLM estimate) stores nothing. The maintenance
tasks and the cleanup healers call `apply_base_price_rule`. Full inventory and open items: `docs/BASE_PRICE_WRITERS.md`;
the safety controller around the two tasks: `docs/MAINTENANCE_SAFETY.md`.
