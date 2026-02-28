import api from './client'

export const ordersApi = {
  create: (data) => api.post('/orders', data),
  getAll: (limit = 50) => api.get('/orders', { params: { limit } }),
  getById: (id) => api.get(`/orders/${id}`),
  track: (id) => api.get(`/orders/${id}/track`),
  cancel: (id, reason) => api.put(`/orders/${id}/cancel`, { reason }),
  return: (id, reason, description) => api.post(`/orders/${id}/return`, null, { params: { reason, description } }),
  invoice: (id) => api.get(`/orders/${id}/invoice`),
}

export const paymentsApi = {
  createIntent: (order_id) => api.post('/payments/create-intent', null, { params: { order_id } }),
  confirm: (payment_intent_id) => api.post('/payments/confirm', null, { params: { payment_intent_id } }),
  history: () => api.get('/payments/history'),
}

export const returnsApi = {
  create: (data) => api.post('/returns', data),
  getAll: () => api.get('/returns'),
  getById: (id) => api.get(`/returns/${id}`),
  cancel: (id) => api.put(`/returns/${id}/cancel`),
}

export const invoicesApi = {
  getAll: () => api.get('/invoices'),
  getById: (id) => api.get(`/invoices/${id}`),
  download: (id) => api.get(`/invoices/${id}/download`),
  resend: (id, email) => api.post(`/invoices/${id}/resend`, null, { params: { email } }),
}

export const notificationsApi = {
  getAll: () => api.get('/notifications'),
  unreadCount: () => api.get('/notifications/unread-count'),
  markRead: (id) => api.put(`/notifications/${id}/read`),
  markAllRead: () => api.put('/notifications/read-all'),
  delete: (id) => api.delete(`/notifications/${id}`),
}
