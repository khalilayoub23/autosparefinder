import { create } from 'zustand'
import { chatApi } from '../api/chat'

export const useChatStore = create((set, get) => ({
  conversations: [],
  currentConversationId: null,
  messages: [],
  isTyping: false,
  isLoading: false,
  agentName: 'router_agent',

  loadConversations: async () => {
    const { data } = await chatApi.getConversations()
    set({ conversations: data.conversations || [] })
  },

  selectConversation: async (id) => {
    set({ currentConversationId: id, isLoading: true })
    try {
      const { data } = await chatApi.getMessages(id)
      set({ messages: data.messages || [] })
    } finally {
      set({ isLoading: false })
    }
  },

  newConversation: () => {
    set({ currentConversationId: null, messages: [] })
  },

  sendMessage: async (text, imageFile = null) => {
    const state = get()
    // Optimistic user message
    const tempId = `temp-${Date.now()}`
    const userMsg = { id: tempId, role: 'user', content: text, content_type: 'text', created_at: new Date().toISOString() }
    set({ messages: [...state.messages, userMsg], isTyping: true })

    try {
      let conversationId = state.currentConversationId
      if (imageFile) {
        const { data: fileData } = await chatApi.uploadImage(imageFile)
        text = `[Image: ${fileData.file_id}] ${text}`
      }
      const { data } = await chatApi.sendMessage({ conversation_id: conversationId, message: text })

      const agentMsg = {
        id: data.message_id,
        role: 'assistant',
        agent_name: data.agent,
        content: data.response,
        content_type: 'text',
        created_at: data.created_at,
      }
      set((s) => ({
        messages: [...s.messages.filter((m) => m.id !== tempId), agentMsg],
        currentConversationId: data.conversation_id,
        agentName: data.agent,
        isTyping: false,
      }))

      // Refresh conversations list
      get().loadConversations()
    } catch (err) {
      set((s) => ({
        messages: s.messages.filter((m) => m.id !== tempId),
        isTyping: false,
      }))
      throw err
    }
  },

  deleteConversation: async (id) => {
    await chatApi.deleteConversation(id)
    set((s) => ({
      conversations: s.conversations.filter((c) => c.id !== id),
      currentConversationId: s.currentConversationId === id ? null : s.currentConversationId,
      messages: s.currentConversationId === id ? [] : s.messages,
    }))
  },
}))
