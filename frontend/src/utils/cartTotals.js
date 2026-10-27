// Cart totals — ONE rule shared by the cart store and the cart page (2026-10-27).
// `price` from the API already INCLUDES VAT (canonical customer unit price), so VAT is shown as
// "of which VAT", never added again. Delivery is each offer's known fee (`deliveryFee`), charged ONCE
// per supplier (the max over that supplier's selected lines) — exactly what checkout charges
// (backend routes/orders.py _order_line_pricing). No invented fallback fee.
export const VAT_RATE = 0.18

const round2 = (n) => Math.round((Number(n) || 0) * 100) / 100

export function cartTotals(items) {
  const list = Array.isArray(items) ? items : []
  let subtotal = 0
  let vat = 0
  const feeBySupplier = {}
  for (const i of list) {
    const line = (Number(i.price) || 0) * (Number(i.quantity) || 0)
    subtotal += line
    if (i.isIlSupplier) vat += line - line / (1 + VAT_RATE)
    const fee = Number(i.deliveryFee ?? i.shippingCost ?? 0) || 0
    const key = i.supplierId || i.supplierPartId
    feeBySupplier[key] = Math.max(feeBySupplier[key] || 0, fee)
  }
  const shipping = Object.values(feeBySupplier).reduce((a, b) => a + b, 0)
  return {
    subtotal: round2(subtotal),          // parts, VAT included
    vat: round2(vat),                    // of which VAT (display only)
    shipping: round2(shipping),
    total: round2(subtotal + shipping),
    count: list.reduce((s, i) => s + (Number(i.quantity) || 0), 0),
  }
}
