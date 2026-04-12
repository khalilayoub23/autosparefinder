import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export const useCartStore = create(
  persist(
    (set, get) => ({
      items: [], // [{ partId, supplierPartId, name, manufacturer, price, vat, quantity }]

      _items: () => Array.isArray(get().items) ? get().items : [],

      setItems: (items) => set({ items: Array.isArray(items) ? items : [] }),

      addItem: (item) => {
        const items = Array.isArray(get().items) ? get().items : []
        const existing = items.find((i) => i.supplierPartId === item.supplierPartId)
        if (existing) {
          set((s) => ({
            items: (Array.isArray(s.items) ? s.items : []).map((i) =>
              i.supplierPartId === item.supplierPartId ? { ...i, quantity: i.quantity + 1 } : i
            ),
          }))
        } else {
          set((s) => ({ items: [...(Array.isArray(s.items) ? s.items : []), { ...item, quantity: 1 }] }))
        }
      },

      removeItem: (supplierPartId) => {
        set((s) => ({ items: (Array.isArray(s.items) ? s.items : []).filter((i) => i.supplierPartId !== supplierPartId) }))
      },

      updateQty: (supplierPartId, quantity) => {
        if (quantity <= 0) {
          get().removeItem(supplierPartId)
          return
        }
        set((s) => ({
          items: (Array.isArray(s.items) ? s.items : []).map((i) => (i.supplierPartId === supplierPartId ? { ...i, quantity } : i)),
        }))
      },

      clear: () => set({ items: [] }),

      totals: () => {
        const items = Array.isArray(get().items) ? get().items : []
        const subtotal = items.reduce((sum, i) => sum + (Number(i.price) || 0) * (Number(i.quantity) || 0), 0)
        const vat = Math.round(subtotal * 0.18 * 100) / 100
        const shipping = items.length > 0
          ? items.reduce((sum, i) => sum + (i.shippingCost ?? 0), 0) || 91
          : 0
        const total = Math.round((subtotal + vat + shipping) * 100) / 100
        return { subtotal: Math.round(subtotal * 100) / 100, vat, shipping, total, count: items.reduce((s, i) => s + (Number(i.quantity) || 0), 0) }
      },
    }),
    {
      name: 'cart-store',
      // Sanitize rehydrated state so items is always an array
      merge: (persisted, current) => ({
        ...current,
        ...persisted,
        items: Array.isArray(persisted?.items) ? persisted.items : [],
      }),
    }
  )
)
