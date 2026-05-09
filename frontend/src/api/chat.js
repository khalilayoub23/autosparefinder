import api from './client'

export const chatApi = {
  sendMessage: (data) => api.post('/chat/message', data),
  requestHumanHandoff: (data) => api.post('/chat/handoff/request', data),
  submitHandoffFeedback: (data) => api.post('/chat/handoff/feedback', data),
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
  uploadAudio: (file, conversationId = null) => {
    const fd = new FormData()
    fd.append('file', file)
    const config = {
      headers: { 'Content-Type': 'multipart/form-data' },
      params: conversationId ? { conversation_id: conversationId } : undefined,
    }
    return api.post('/chat/upload-audio', fd, config)
  },
}
