import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export const useCartStore = create(
  persist(
    (set, get) => ({
      items: [], // [{ partId, supplierPartId, name, manufacturer, price, vat, quantity }]

      addItem: (item) => {
        const existing = get().items.find((i) => i.supplierPartId === item.supplierPartId)
        if (existing) {
          set((s) => ({
            items: s.items.map((i) =>
              i.supplierPartId === item.supplierPartId ? { ...i, quantity: i.quantity + 1 } : i
            ),
          }))
        } else {
          set((s) => ({ items: [...s.items, { ...item, quantity: 1 }] }))
        }
      },

      removeItem: (supplierPartId) => {
        set((s) => ({ items: s.items.filter((i) => i.supplierPartId !== supplierPartId) }))
      },

      updateQty: (supplierPartId, quantity) => {
        if (quantity <= 0) {
          get().removeItem(supplierPartId)
          return
        }
        set((s) => ({
          items: s.items.map((i) => (i.supplierPartId === supplierPartId ? { ...i, quantity } : i)),
        }))
      },

      clear: () => set({ items: [] }),

      totals: () => {
        const items = get().items
        const subtotal = items.reduce((sum, i) => sum + i.price * i.quantity, 0)
        const vat = Math.round(subtotal * 0.17 * 100) / 100
        const shipping = 91
        const total = Math.round((subtotal + vat + shipping) * 100) / 100
        return { subtotal: Math.round(subtotal * 100) / 100, vat, shipping, total, count: items.reduce((s, i) => s + i.quantity, 0) }
      },
    }),
    { name: 'cart-store' }
  )
)
