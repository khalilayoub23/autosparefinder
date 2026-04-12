import axios from 'axios'
import toast from 'react-hot-toast'

const API_BASE = '/api/v1'

const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

// Attach access token to every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// Auto-refresh on 401
let isRefreshing = false
let refreshQueue = []

// Endpoints that should NEVER trigger auto-refresh (they ARE the auth endpoints)
const AUTH_ENDPOINTS = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/forgot', '/auth/reset']

function normalizeErrorPayload(error) {
  const data = error?.response?.data
  if (!data || typeof data !== 'object') return

  // Preserve the original structured detail for flows that need machine codes
  // (e.g. price_updated / part_unavailable), but ensure detail is UI-safe text.
  if (data.detail && typeof data.detail === 'object' && !Array.isArray(data.detail)) {
    data.detail_obj = data.detail
    if (typeof data.detail.message === 'string') data.detail = data.detail.message
    else if (typeof data.detail.detail === 'string') data.detail = data.detail.detail
    else data.detail = 'אירעה שגיאה'
  }

  if (data.error && typeof data.error === 'object' && !Array.isArray(data.error)) {
    data.error = data.error.message || data.error.detail || 'אירעה שגיאה'
  }

  if (data.message && typeof data.message === 'object' && !Array.isArray(data.message)) {
    data.message = data.message.message || data.message.detail || 'אירעה שגיאה'
  }
}

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    normalizeErrorPayload(error)
    const original = error.config
    const url = original?.url || ''
    // Don't intercept auth endpoints — let the caller handle the error directly
    const isAuthEndpoint = AUTH_ENDPOINTS.some((e) => url.includes(e))
    if (error.response?.status === 401 && !original._retry && !isAuthEndpoint) {
      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          refreshQueue.push({ resolve, reject })
        }).then((token) => {
          original.headers.Authorization = `Bearer ${token}`
          return api(original)
        })
      }
      original._retry = true
      isRefreshing = true
      try {
        const refresh = localStorage.getItem('refresh_token')
        if (!refresh) throw new Error('No refresh token')
        const { data } = await axios.post(`${API_BASE}/auth/refresh`, { refresh_token: refresh })
        localStorage.setItem('access_token', data.access_token)
        localStorage.setItem('refresh_token', data.refresh_token)
        api.defaults.headers.common.Authorization = `Bearer ${data.access_token}`
        refreshQueue.forEach((p) => p.resolve(data.access_token))
        refreshQueue = []
        original.headers.Authorization = `Bearer ${data.access_token}`
        return api(original)
      } catch {
        refreshQueue.forEach((p) => p.reject())
        refreshQueue = []
        // Clear only the invalid tokens, not the entire localStorage (which would lose cart etc.)
        localStorage.removeItem('access_token')
        localStorage.removeItem('refresh_token')
        // If we failed refreshing while on the payment success flow, redirect to
        // /orders (payment was already confirmed by Stripe webhook) rather than
        // sending the user to /login and losing their session silently.
        if (url.includes('verify-session') || url.includes('payment')) {
          window.location.href = '/orders?payment=done'
        } else {
          window.location.href = '/login'
        }
      } finally {
        isRefreshing = false
      }
    }
    const detail = error.response?.data?.detail
    const detailMsg = typeof detail === 'string' ? detail : detail?.message
    const msg = error.response?.data?.error || detailMsg || error.response?.data?.message || error.message
    const isVerifySessionEndpoint = url.includes('/payments/verify-session')
    if (error.response?.status >= 500 && !isAuthEndpoint && !isVerifySessionEndpoint) {
      toast.error(`שגיאת שרת: ${msg}`)
    }
    return Promise.reject(error)
  }
)

export default api
