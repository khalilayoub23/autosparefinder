import { useState, useEffect, useRef } from 'react'
import { useChatStore } from '../stores/chatStore'
import { useAuthStore } from '../stores/authStore'
import {
  Send, PlusCircle, Trash2, MessageSquare, Bot,
  User, Image, Loader2, Star, ChevronRight,
} from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import { he } from 'date-fns/locale'
import toast from 'react-hot-toast'
import { chatApi } from '../api/chat'

const AGENT_LABELS = {
  router_agent:          { label: 'מנהל',         color: 'bg-purple-100 text-purple-700' },
  parts_finder_agent:    { label: 'חיפוש חלקים',  color: 'bg-blue-100 text-blue-700'   },
  sales_agent:           { label: 'מכירות',        color: 'bg-green-100 text-green-700' },
  orders_agent:          { label: 'הזמנות',        color: 'bg-orange-100 text-orange-700'},
  finance_agent:         { label: 'כספים',         color: 'bg-yellow-100 text-yellow-700'},
  service_agent:         { label: 'שירות לקוחות',  color: 'bg-cyan-100 text-cyan-700'   },
  security_agent:        { label: 'אבטחה',         color: 'bg-red-100 text-red-700'      },
  marketing_agent:       { label: 'שיווק',         color: 'bg-pink-100 text-pink-700'   },
  supplier_manager_agent:{ label: 'ספקים',         color: 'bg-gray-100 text-gray-700'   },
  social_media_manager_agent:{ label: 'רשתות חברתיות', color: 'bg-indigo-100 text-indigo-700'},
}

const QUICK_MSGS = [
  'רכבי הוא מאזדה 3 2019 – מה שוויו?',
  'אני צריך פילטר שמן לרכב שלי',
  'מה המחיר של רפידות בלם לטויוטה קורולה?',
  'מה הסטטוס של ההזמנה האחרונה שלי?',
  'יש לי מספר לוחית: 12-345-67',
]

function AgentBadge({ agent }) {
  const info = AGENT_LABELS[agent] || { label: agent, color: 'bg-gray-100 text-gray-700' }
  return <span className={`badge ${info.color} text-xs`}>{info.label}</span>
}

function Message({ msg, onRate }) {
  const isUser = msg.role === 'user'
  const [rated, setRated] = useState(false)
  return (
    <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : 'flex-row'} mb-4`}>
      <div className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center ${isUser ? 'bg-brand-600' : 'bg-white border border-gray-200'}`}>
        {isUser ? <User className="w-4 h-4 text-white" /> : <Bot className="w-4 h-4 text-brand-600" />}
      </div>
      <div className={`flex flex-col max-w-[75%] lg:max-w-[60%] ${isUser ? 'items-end' : 'items-start'}`}>
        {!isUser && msg.agent_name && (
          <div className="flex items-center gap-2 mb-1">
            <AgentBadge agent={msg.agent_name} />
          </div>
        )}
        <div className={isUser ? 'chat-bubble-user' : 'chat-bubble-agent'}>
          <p className="text-sm whitespace-pre-wrap leading-relaxed">{msg.content}</p>
        </div>
        <div className={`flex items-center gap-1 mt-1 ${isUser ? 'flex-row-reverse' : ''}`}>
          <span className="text-xs text-gray-400">
            {msg.created_at ? formatDistanceToNow(new Date(msg.created_at), { addSuffix: true, locale: he }) : ''}
          </span>
          {!isUser && !rated && (
            <div className="flex gap-1 mr-2">
              {[1, 2, 3, 4, 5].map((s) => (
                <button key={s} onClick={() => { setRated(true); onRate(s) }} className="text-gray-300 hover:text-yellow-400">
                  <Star className="w-3 h-3" />
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className="flex gap-3 mb-4">
      <div className="w-8 h-8 rounded-full bg-white border border-gray-200 flex items-center justify-center">
        <Bot className="w-4 h-4 text-brand-600" />
      </div>
      <div className="chat-bubble-agent flex items-center gap-1 py-3 px-4">
        <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
        <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
        <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
      </div>
    </div>
  )
}

export default function Chat() {
  const { user } = useAuthStore()
  const { conversations, messages, currentConversationId, isTyping, sendMessage, loadConversations, selectConversation, newConversation, deleteConversation, agentName } = useChatStore()
  const [input, setInput] = useState('')
  const [imageFile, setImageFile] = useState(null)
  const bottomRef = useRef(null)
  const fileRef = useRef(null)

  useEffect(() => { loadConversations() }, [])
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, isTyping])

  const handleSend = async (text = input) => {
    if (!text.trim() && !imageFile) return
    setInput('')
    setImageFile(null)
    try {
      await sendMessage(text, imageFile)
    } catch {
      toast.error('שגיאה בשליחת ההודעה')
    }
  }

  const handleRate = async (score) => {
    if (!currentConversationId || !agentName) return
    try {
      await chatApi.rateAgent(currentConversationId, agentName, score)
      toast.success('תודה על הדירוג!')
    } catch {}
  }

  return (
    <div className="flex h-[calc(100vh-5rem)] gap-4">
      {/* Sidebar - conversations */}
      <aside className="hidden md:flex flex-col w-72 card overflow-hidden">
        <div className="p-4 border-b border-gray-100 flex items-center justify-between">
          <h3 className="font-semibold text-gray-800">שיחות</h3>
          <button onClick={newConversation} className="btn-primary text-sm px-3 py-1.5 flex items-center gap-1">
            <PlusCircle className="w-4 h-4" /> חדש
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          {conversations.length === 0 && (
            <p className="text-xs text-gray-400 text-center py-8">אין שיחות עדיין</p>
          )}
          {conversations.map((c) => (
            <div
              key={c.id}
              onClick={() => selectConversation(c.id)}
              className={`group flex items-center justify-between p-3 rounded-xl cursor-pointer mb-1 transition-colors ${
                currentConversationId === c.id ? 'bg-brand-50' : 'hover:bg-gray-50'
              }`}
            >
              <div className="flex items-center gap-2 min-w-0">
                <MessageSquare className={`w-4 h-4 flex-shrink-0 ${currentConversationId === c.id ? 'text-brand-600' : 'text-gray-400'}`} />
                <div className="min-w-0">
                  <p className={`text-sm font-medium truncate ${currentConversationId === c.id ? 'text-brand-700' : 'text-gray-800'}`}>
                    {c.title || 'שיחה חדשה'}
                  </p>
                  <p className="text-xs text-gray-400 truncate">{AGENT_LABELS[c.current_agent]?.label || c.current_agent}</p>
                </div>
              </div>
              <button
                onClick={(e) => { e.stopPropagation(); deleteConversation(c.id) }}
                className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-red-100 text-red-400"
              >
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
          ))}
        </div>
      </aside>

      {/* Main chat area */}
      <div className="flex-1 flex flex-col card overflow-hidden">
        {/* Header */}
        <div className="p-4 border-b border-gray-100 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-9 h-9 bg-brand-100 rounded-xl flex items-center justify-center">
              <Bot className="w-5 h-5 text-brand-600" />
            </div>
            <div>
              <h2 className="font-semibold text-gray-900 text-sm">עוזר AI</h2>
              <p className="text-xs text-gray-400">{AGENT_LABELS[agentName]?.label || 'מנהל'} • מוכן לעזור</p>
            </div>
          </div>
          <button onClick={newConversation} className="btn-ghost text-sm flex items-center gap-1">
            <PlusCircle className="w-4 h-4" /> שיחה חדשה
          </button>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4">
          {messages.length === 0 && !isTyping && (
            <div className="flex flex-col items-center justify-center h-full text-center">
              <div className="w-16 h-16 bg-brand-50 rounded-2xl flex items-center justify-center mb-4">
                <Bot className="w-9 h-9 text-brand-500" />
              </div>
              <h3 className="text-lg font-semibold text-gray-900 mb-1">שלום, {user?.full_name?.split(' ')[0]}!</h3>
              <p className="text-gray-500 text-sm mb-6">אני יכול לעזור לך למצוא חלקי רכב, לבדוק הזמנות ועוד.</p>
              <div className="flex flex-wrap gap-2 justify-center max-w-lg">
                {QUICK_MSGS.map((q) => (
                  <button key={q} onClick={() => handleSend(q)} className="btn-secondary text-sm px-4 py-2 rounded-xl">
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m) => <Message key={m.id} msg={m} onRate={handleRate} />)}
          {isTyping && <TypingIndicator />}
          <div ref={bottomRef} />
        </div>

        {/* Image preview */}
        {imageFile && (
          <div className="px-4 py-2 border-t border-gray-100 flex items-center gap-2">
            <img src={URL.createObjectURL(imageFile)} className="h-14 w-14 object-cover rounded-lg" alt="" />
            <button onClick={() => setImageFile(null)} className="text-xs text-red-500 hover:text-red-700">הסר</button>
          </div>
        )}

        {/* Input */}
        <div className="p-4 border-t border-gray-100">
          <div className="flex items-end gap-2">
            <button onClick={() => fileRef.current?.click()} className="btn-ghost p-2.5 flex-shrink-0 text-gray-400 hover:text-brand-600">
              <Image className="w-5 h-5" />
            </button>
            <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={(e) => setImageFile(e.target.files[0])} />
            <textarea
              className="flex-1 input-field resize-none min-h-[44px] max-h-32 py-2.5"
              placeholder="שלח הודעה... (מספר לוחית, שאלה על חלק, הזמנה...)"
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
              }}
              onInput={(e) => {
                e.target.style.height = 'auto'
                e.target.style.height = Math.min(e.target.scrollHeight, 128) + 'px'
              }}
            />
            <button
              onClick={() => handleSend()}
              disabled={!input.trim() && !imageFile || isTyping}
              className="btn-primary p-2.5 flex-shrink-0"
            >
              {isTyping ? <Loader2 className="w-5 h-5 animate-spin" /> : <Send className="w-5 h-5" />}
            </button>
          </div>
          <p className="text-xs text-gray-400 mt-2 text-center">Auto Spare AI • הזמנות מעובדות רק לאחר תשלום מאושר</p>
        </div>
      </div>
    </div>
  )
}
