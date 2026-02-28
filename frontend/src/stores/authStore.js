import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { authApi } from '../api/auth'

export const useAuthStore = create(
  persist(
    (set, get) => ({
      user: null,
      accessToken: null,
      refreshToken: null,
      isLoading: false,
      pendingUserId: null, // for 2FA flow

      setTokens: (access, refresh) => {
        localStorage.setItem('access_token', access)
        localStorage.setItem('refresh_token', refresh)
        set({ accessToken: access, refreshToken: refresh })
      },

      login: async (email, password, trustDevice = false) => {
        set({ isLoading: true })
        try {
          const { data, status } = await authApi.login({ email, password, trust_device: trustDevice })
          if (status === 202 || data.requires_2fa) {
            set({ pendingUserId: data.user_id, isLoading: false })
            return { requires2fa: true, userId: data.user_id }
          }
          get().setTokens(data.access_token, data.refresh_token)
          set({ user: data.user, pendingUserId: null })
          return { success: true }
        } finally {
          set({ isLoading: false })
        }
      },

      verify2fa: async (userId, code, trustDevice = false) => {
        set({ isLoading: true })
        try {
          const { data } = await authApi.verify2fa({ user_id: userId, code, trust_device: trustDevice })
          get().setTokens(data.access_token, data.refresh_token)
          set({ user: data.user, pendingUserId: null })
          return { success: true }
        } finally {
          set({ isLoading: false })
        }
      },

      register: async (formData) => {
        set({ isLoading: true })
        try {
          const { data } = await authApi.register(formData)
          return { success: true, data }
        } finally {
          set({ isLoading: false })
        }
      },

      fetchMe: async () => {
        try {
          const { data } = await authApi.me()
          set({ user: data })
        } catch {
          get().logout()
        }
      },

      logout: () => {
        authApi.logout().catch(() => {})
        localStorage.removeItem('access_token')
        localStorage.removeItem('refresh_token')
        set({ user: null, accessToken: null, refreshToken: null, pendingUserId: null })
      },

      isAuthenticated: () => !!get().user && !!localStorage.getItem('access_token'),
    }),
    {
      name: 'auth-store',
      partialize: (state) => ({
        user: state.user,
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
      }),
    }
  )
)
