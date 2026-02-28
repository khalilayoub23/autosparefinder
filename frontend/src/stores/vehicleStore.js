import { create } from 'zustand'
import { vehiclesApi } from '../api/vehicles'

export const useVehicleStore = create((set, get) => ({
  vehicles: [],
  selectedVehicle: null,
  isLoading: false,

  loadVehicles: async () => {
    set({ isLoading: true })
    try {
      const { data } = await vehiclesApi.myVehicles()
      const list = data.vehicles || []
      set({ vehicles: list, selectedVehicle: list.find((v) => v.is_primary) || list[0] || null })
    } finally {
      set({ isLoading: false })
    }
  },

  addVehicle: async (plate, nickname) => {
    const { data } = await vehiclesApi.addVehicle(plate, nickname)
    await get().loadVehicles()
    return data
  },

  selectVehicle: (vehicle) => set({ selectedVehicle: vehicle }),

  removeVehicle: async (id) => {
    await vehiclesApi.deleteVehicle(id)
    set((s) => ({
      vehicles: s.vehicles.filter((v) => v.id !== id),
      selectedVehicle: s.selectedVehicle?.id === id ? null : s.selectedVehicle,
    }))
  },

  setPrimary: async (id) => {
    await vehiclesApi.setPrimary(id)
    set((s) => ({
      vehicles: s.vehicles.map((v) => ({ ...v, is_primary: v.id === id })),
      selectedVehicle: s.vehicles.find((v) => v.id === id) || s.selectedVehicle,
    }))
  },
}))
