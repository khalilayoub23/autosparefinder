// Offer classification shown to customers (backend: offer_classification.offer_label). Hebrew labels mirror class_label_he.
const LABELS = {
  oem: { label: 'OEM', cls: 'chip-success' },
  oe_equivalent: { label: 'שווה ערך ל-OEM', cls: 'chip-info' },
  aftermarket: { label: 'חליפי (Aftermarket)', cls: 'chip-warning' },
  remanufactured: { label: 'משופץ', cls: 'chip-neutral' },
  used: { label: 'משומש', cls: 'chip-neutral' },
}

export function offerClassInfo(partType) {
  const k = String(partType || '').trim().toLowerCase().replace(/[\s-]+/g, '_')
  return LABELS[k === 'oem_equivalent' ? 'oe_equivalent' : k] || null
}

// A real server supplier_part_id, never the client-side `fallback-…` placeholder.
export function realSupplierPartId(id) {
  return id && !String(id).startsWith('fallback-') ? String(id) : undefined
}
