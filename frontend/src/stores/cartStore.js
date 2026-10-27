import { cartTotals } from '../utils/cartTotals'
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

      totals: () => cartTotals(get().items),
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
