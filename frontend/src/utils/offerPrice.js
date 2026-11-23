// Customer price display / purchase eligibility rules (2026-11-20).
// Backend contract (routes/parts.py): a VERIFIED offer carries a real `supplier_part_id` and a positive
// `customer_price_ils`. A catalog ESTIMATE is `part.is_estimate` + `part.customer_estimate_ils` with NO offer:
// informational only, never orderable. The frontend never computes a price (see backend/price_policy.py).
import { realSupplierPartId } from './offerClass'

const positive = (v) => {
  const n = Number(v)
  return Number.isFinite(n) && n > 0 ? n : null
}

// An offer may be shown with a price and added to the cart only when the server identified it
// (real supplier_part_id) AND it carries a positive customer price. Zero / NULL / NaN / missing => not purchasable.
export function isPurchasableOffer(offer) {
  if (!offer) return false
  if (!realSupplierPartId(offer.supplier_part_id)) return false
  if (offer.is_estimate) return false
  return positive(offer.subtotal ?? offer.customer_total_ils ?? offer.customer_price_ils) !== null
}

// The estimate to show for a part, or null. Only when the part has no purchasable offer.
export function partEstimate(part, purchasableOffers = []) {
  if (!part || purchasableOffers.length > 0) return null
  if (!part.is_estimate) return null
  const total = positive(part.customer_estimate_ils)
  return total === null ? null : { total }
}
