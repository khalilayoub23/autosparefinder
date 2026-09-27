import { describe, it, expect } from 'vitest'
import { offerClassInfo, realSupplierPartId } from './offerClass'

describe('offerClass', () => {
  it('labels the three customer-facing classes distinctly', () => {
    const labels = ['oem', 'oe_equivalent', 'aftermarket'].map((c) => offerClassInfo(c).label)
    expect(new Set(labels).size).toBe(3)
  })
  it('normalizes variants and ignores unknown', () => {
    expect(offerClassInfo('OEM Equivalent').label).toBe('שווה ערך ל-OEM')
    expect(offerClassInfo('oe-equivalent').label).toBe('שווה ערך ל-OEM')
    expect(offerClassInfo(null)).toBeNull()
    expect(offerClassInfo('weird')).toBeNull()
  })
  it('never forwards fallback ids', () => {
    expect(realSupplierPartId('fallback-1-0')).toBeUndefined()
    expect(realSupplierPartId('abc')).toBe('abc')
  })
})
