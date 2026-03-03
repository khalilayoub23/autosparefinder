import { create } from 'zustand'
import { chatApi } from '../api/chat'

export const useChatStore = create((set, get) => ({
  conversations: [],
  currentConversationId: null,
  messages: [],
  isTyping: false,
  isLoading: false,
  agentName: 'router_agent',
  // map of conversationId -> ISO string of when it was last marked read
  lastReadAt: {},

  loadConversations: async () => {
    const { data } = await chatApi.getConversations()
    set({ conversations: data.conversations || [] })
  },

  selectConversation: async (id) => {
    // mark as read when selected
    set((s) => ({
      currentConversationId: id,
      isLoading: true,
      lastReadAt: { ...s.lastReadAt, [id]: new Date().toISOString() },
    }))
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
        if (fileData.identified_part) {
          const partInfo = fileData.identified_part_en
            ? `${fileData.identified_part} (${fileData.identified_part_en})`
            : fileData.identified_part
          text = `[זוהה בתמונה: ${partInfo}] ${text}`
        } else {
          text = `[Image: ${fileData.file_id}] ${text}`
        }
      }
      const { data } = await chatApi.sendMessage({ conversation_id: conversationId, message: text })

      // Replace temp message with confirmed user message
      set((s) => ({
        messages: s.messages.map((m) =>
          m.id === tempId
            ? { ...m, id: data.user_message_id }
            : m
        ),
        currentConversationId: data.conversation_id,
        isTyping: true, // keep typing indicator while agent processes in background
      }))

      // Poll for the assistant reply (agent runs as background task on server)
      const convId = data.conversation_id
      const startedAt = Date.now()
      const MAX_WAIT_MS = 45_000
      const POLL_INTERVAL_MS = 1_500
      const knownMsgCount = get().messages.length

      const poll = async () => {
        if (Date.now() - startedAt > MAX_WAIT_MS) {
          set({ isTyping: false })
          return
        }
        try {
          const { data: msgData } = await chatApi.getMessages(convId)
          const allMsgs = msgData.messages || []
          const assistantMsgs = allMsgs.filter((m) => m.role === 'assistant')
          const latestAssistant = assistantMsgs[assistantMsgs.length - 1]

          // New assistant message arrived
          if (latestAssistant && allMsgs.length > knownMsgCount) {
            set({
              messages: allMsgs,
              agentName: latestAssistant.agent_name || 'router_agent',
              isTyping: false,
            })
            get().loadConversations()
            return
          }
        } catch {}
        setTimeout(poll, POLL_INTERVAL_MS)
      }
      setTimeout(poll, POLL_INTERVAL_MS)

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
