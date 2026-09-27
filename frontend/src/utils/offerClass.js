// Offer classification shown to customers (backend: offer_classification.offer_label). Hebrew labels mirror class_label_he.
const LABELS = {
  oem: { label: 'OEM', cls: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
  oe_equivalent: { label: 'שווה ערך ל-OEM', cls: 'border-sky-200 bg-sky-50 text-sky-700' },
  aftermarket: { label: 'חליפי (Aftermarket)', cls: 'border-amber-200 bg-amber-50 text-amber-700' },
  remanufactured: { label: 'משופץ', cls: 'border-slate-200 bg-slate-50 text-slate-600' },
  used: { label: 'משומש', cls: 'border-slate-200 bg-slate-50 text-slate-600' },
}

export function offerClassInfo(partType) {
  const k = String(partType || '').trim().toLowerCase().replace(/[\s-]+/g, '_')
  return LABELS[k === 'oem_equivalent' ? 'oe_equivalent' : k] || null
}

// A real server supplier_part_id, never the client-side `fallback-…` placeholder.
export function realSupplierPartId(id) {
  return id && !String(id).startsWith('fallback-') ? String(id) : undefined
}
