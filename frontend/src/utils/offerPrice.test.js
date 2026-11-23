import { describe, it, expect } from 'vitest'
import { isPurchasableOffer, partEstimate } from './offerPrice'

const good = { supplier_part_id: 'abc-123', subtotal: 120.5 }

describe('isPurchasableOffer', () => {
  it('accepts a verified offer', () => expect(isPurchasableOffer(good)).toBe(true))
  it('rejects the client-side fallback id', () => expect(isPurchasableOffer({ ...good, supplier_part_id: 'fallback-1' })).toBe(false))
  it('rejects a missing supplier_part_id', () => expect(isPurchasableOffer({ subtotal: 10 })).toBe(false))
  it.each([0, null, undefined, NaN, -5, 'x'])('rejects invalid price %s', (v) =>
    expect(isPurchasableOffer({ supplier_part_id: 'abc', subtotal: v })).toBe(false))
  it('rejects an estimate row even with a numeric price', () =>
    expect(isPurchasableOffer({ ...good, is_estimate: true })).toBe(false))
  it('rejects null', () => expect(isPurchasableOffer(null)).toBe(false))
})

describe('partEstimate', () => {
  const est = { is_estimate: true, customer_estimate_ils: 56.46 }
  it('returns the estimate when there is no purchasable offer', () => expect(partEstimate(est, [])).toEqual({ total: 56.46 }))
  it('hides the estimate when a verified offer exists', () => expect(partEstimate(est, [good])).toBeNull())
  it('ignores a non-estimate part', () => expect(partEstimate({ is_estimate: false, customer_estimate_ils: 9 }, [])).toBeNull())
  it.each([0, null, undefined, NaN])('ignores an invalid estimate value %s', (v) =>
    expect(partEstimate({ is_estimate: true, customer_estimate_ils: v }, [])).toBeNull())
})
