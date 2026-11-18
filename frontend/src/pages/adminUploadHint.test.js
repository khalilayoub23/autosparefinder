import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const src = readFileSync(resolve(__dirname, 'Admin.jsx'), 'utf8')
const m = src.match(/\[('sku \/ pin'[^\]]*)\]\.map/)

describe('Admin upload format hint', () => {
  it('shows max_price_ils (not base_price) as the example price column header', () => {
    expect(m).not.toBeNull()
    expect(m[1]).toContain("'max_price_ils'")
    expect(m[1]).not.toContain('base_price')
  })
  it('describes the column as retail/reference price, not supplier cost', () => {
    expect(src).toMatch(/data-testid="admin-upload-price-hint"/)
    expect(src).toMatch(/מחיר קמעונאי \/ מחיר ייחוס \(לא עלות רכש מספק\)/)
  })
})
