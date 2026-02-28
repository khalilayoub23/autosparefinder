import api from './client'

export const chatApi = {
  sendMessage: (data) => api.post('/chat/message', data),
  getConversations: () => api.get('/chat/conversations'),
  getConversation: (id) => api.get(`/chat/conversations/${id}`),
  getMessages: (id) => api.get(`/chat/conversations/${id}/messages`),
  deleteConversation: (id) => api.delete(`/chat/conversations/${id}`),
  rateAgent: (conv_id, agent_name, rating, feedback) =>
    api.post('/chat/rate', null, { params: { conversation_id: conv_id, agent_name, rating, feedback } }),
  uploadImage: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return api.post('/chat/upload-image', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
}
