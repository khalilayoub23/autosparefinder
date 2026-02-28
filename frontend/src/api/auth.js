import api from './client'

export const authApi = {
  register: (data) => api.post('/auth/register', data),
  login: (data) => api.post('/auth/login', data),
  verify2fa: (data) => api.post('/auth/verify-2fa', data),
  me: () => api.get('/auth/me'),
  logout: () => api.post('/auth/logout'),
  resetPassword: (email) => api.post('/auth/reset-password', { email }),
  resetPasswordConfirm: (token, new_password) => api.post('/auth/reset-password/confirm', { token, new_password }),
  changePassword: (current_password, new_password) => api.post('/auth/change-password', { current_password, new_password }),
  sendCode: () => api.post('/auth/send-2fa'),
  trustedDevices: () => api.get('/auth/trusted-devices'),
  removeTrustedDevice: (id) => api.delete(`/auth/trusted-devices/${id}`),
}
