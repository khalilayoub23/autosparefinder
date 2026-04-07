import { useState, useEffect, useRef } from 'react'
import { useChatStore } from '../stores/chatStore'
import { useAuthStore } from '../stores/authStore'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Send, PlusCircle, Trash2, MessageSquare, Bot,
  User, Image, Loader2, Star, ChevronRight, Search, ShoppingCart,
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
  'אני צריך פילטר שמן לרכב שלי',
  'מה המחיר של רפידות בלם לטויוטה קורולה?',
  'יש לי מספר לוחית: 12-345-67',
  'מה הסטטוס של ההזמנה האחרונה שלי?',
  'חפש מצת לרנו קליאו 2020',
]

function AgentBadge({ agent }) {
  const info = AGENT_LABELS[agent] || { label: agent, color: 'bg-gray-100 text-gray-700' }
  return <span className={`badge ${info.color} text-xs`}>{info.label}</span>
}

/** Render inline markdown: **bold**, [link](url), ₪NNN price highlights */
function InlineMarkdown({ text }) {
  const tokens = text.split(/(\[.+?\]\(https?:\/\/[^\s)]+\)|\*\*.+?\*\*|₪[\d,]+(?:\.\d+)?)/g)
  return (
    <>
      {tokens.map((tok, i) => {
        const link = tok.match(/^\[(.+?)\]\((https?:\/\/[^\s)]+)\)$/)
        if (link) return (
          <a key={i} href={link[2]} target="_blank" rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-brand-600 underline hover:text-brand-800 font-medium">
            {link[1]} ↗
          </a>
        )
        const bold = tok.match(/^\*\*(.+?)\*\*$/)
        if (bold) return <strong key={i} className="font-semibold text-gray-900">{bold[1]}</strong>
        const price = tok.match(/^(₪[\d,]+(?:\.\d+)?)$/)
        if (price) return (
          <span key={i} className="font-semibold text-green-700 bg-green-50 rounded px-0.5">{tok}</span>
        )
        return <span key={i}>{tok}</span>
      })}
    </>
  )
}

/** Render full message content with lists, paragraphs, and inline markdown */
function MessageContent({ content }) {
  const lines = content.split('\n')
  const elements = []
  let listBuffer = []

  const flushList = (key) => {
    if (listBuffer.length === 0) return
    elements.push(
      <ul key={`ul-${key}`} className="my-1 space-y-0.5 pr-3">
        {listBuffer.map((item, idx) => (
          <li key={idx} className="flex gap-1.5 text-sm leading-relaxed">
            <span className="text-brand-500 flex-shrink-0 font-medium">•</span>
            <span><InlineMarkdown text={item} /></span>
          </li>
        ))}
      </ul>
    )
    listBuffer = []
  }

  lines.forEach((line, idx) => {
    const trimmed = line.trim()
    if (!trimmed) {
      flushList(idx)
      elements.push(<div key={`br-${idx}`} className="h-1" />)
      return
    }
    // Numbered list: "1. ..." or "١. ..."
    const numbered = trimmed.match(/^(\d+)\.\s+(.+)/)
    if (numbered) {
      flushList(idx)
      elements.push(
        <div key={idx} className="flex gap-2 text-sm leading-relaxed my-0.5">
          <span className="flex-shrink-0 w-5 h-5 bg-brand-100 text-brand-700 rounded-full text-xs font-bold flex items-center justify-center mt-0.5">
            {numbered[1]}
          </span>
          <span className="flex-1"><InlineMarkdown text={numbered[2]} /></span>
        </div>
      )
      return
    }
    // Bullet list: "• ..." or "- ..."
    const bullet = trimmed.match(/^[•\-\*]\s+(.+)/)
    if (bullet) {
      listBuffer.push(bullet[1])
      return
    }
    flushList(idx)
    elements.push(
      <p key={idx} className="text-sm leading-relaxed">
        <InlineMarkdown text={trimmed} />
      </p>
    )
  })
  flushList('end')

  return <div className="space-y-0.5">{elements}</div>
}

const PARTS_AGENTS = new Set(['parts_finder_agent', 'sales_agent'])

function Message({ msg, onRate, onCatalogSearch }) {
  const isUser = msg.role === 'user'
  const [rated, setRated] = useState(false)
  const isPartsAgent = !isUser && PARTS_AGENTS.has(msg.agent_name)

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
          {isUser
            ? <p className="text-sm whitespace-pre-wrap leading-relaxed">{msg.content}</p>
            : <MessageContent content={msg.content} />
          }
        </div>

        {/* Parts catalog CTA */}
        {isPartsAgent && (
          <button
            onClick={() => onCatalogSearch(msg.content)}
            className="mt-1.5 flex items-center gap-1.5 text-xs text-brand-600 hover:text-brand-800 hover:bg-brand-50 rounded-lg px-2 py-1 transition-colors"
          >
            <Search className="w-3.5 h-3.5" />
            חיפוש בקטלוג
          </button>
        )}

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
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { conversations, messages, currentConversationId, isTyping, sendMessage, loadConversations, selectConversation, newConversation, deleteConversation, agentName, lastReadAt } = useChatStore()

  // helper: is a conversation unread?
  const isUnread = (c) => {
    if (!c.last_message_at) return false
    if (c.id === currentConversationId) return false
    const readAt = lastReadAt[c.id]
    if (!readAt) return c.message_count > 0
    return new Date(c.last_message_at) > new Date(readAt)
  }

  // format conversation timestamp
  const fmtTime = (iso) => {
    if (!iso) return ''
    const d = new Date(iso)
    const now = new Date()
    const sameDay = d.toDateString() === now.toDateString()
    return sameDay
      ? d.toLocaleTimeString('he-IL', { hour: '2-digit', minute: '2-digit' })
      : d.toLocaleDateString('he-IL', { day: '2-digit', month: '2-digit', year: '2-digit' }) + ' ' + d.toLocaleTimeString('he-IL', { hour: '2-digit', minute: '2-digit' })
  }
  const [input, setInput] = useState('')
  const [imageFile, setImageFile] = useState(null)
  const bottomRef = useRef(null)
  const fileRef = useRef(null)
  const autoSentRef = useRef(false)

  useEffect(() => { loadConversations() }, [])
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, isTyping])

  // Auto-send when navigated here with ?msg= (e.g. from Parts no-results AI button)
  useEffect(() => {
    const msg = searchParams.get('msg')
    if (msg && !autoSentRef.current) {
      autoSentRef.current = true
      // Small delay to let conversations load first
      const t = setTimeout(() => handleSend(msg), 800)
      return () => clearTimeout(t)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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

  const handleCatalogSearch = (agentText) => {
    // Extract a plausible search term from agent text (first Hebrew noun phrase / part name)
    const hebrewWords = agentText.match(/[\u05D0-\u05EA][\u05D0-\u05EA\s]{2,25}/g)
    const searchTerm = hebrewWords ? hebrewWords[0].trim() : ''
    navigate(`/parts${searchTerm ? `?search=${encodeURIComponent(searchTerm)}` : ''}`)
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
          {conversations.map((c) => {
            const unread = isUnread(c)
            return (
            <div
              key={c.id}
              onClick={() => selectConversation(c.id)}
              className={`group flex items-start justify-between p-3 rounded-xl cursor-pointer mb-1 transition-colors ${
                currentConversationId === c.id ? 'bg-brand-50 border border-brand-200' : unread ? 'bg-orange-50 border border-orange-200' : 'hover:bg-gray-50 border border-transparent'
              }`}
            >
              <div className="flex items-start gap-2 min-w-0 flex-1">
                <div className="relative flex-shrink-0 mt-0.5">
                  <MessageSquare className={`w-4 h-4 ${
                    currentConversationId === c.id ? 'text-brand-600' : unread ? 'text-orange-500' : 'text-gray-400'
                  }`} />
                  {unread && <span className="absolute -top-1 -right-1 w-2 h-2 bg-orange-500 rounded-full" />}
                </div>
                <div className="min-w-0 flex-1">
                  <p className={`text-sm truncate ${
                    currentConversationId === c.id ? 'font-semibold text-brand-700' : unread ? 'font-semibold text-gray-900' : 'font-medium text-gray-700'
                  }`}>
                    {c.title || 'שיחה חדשה'}
                  </p>
                  <div className="flex items-center justify-between gap-1 mt-0.5">
                    <p className="text-xs text-gray-400 truncate">{AGENT_LABELS[c.current_agent]?.label || c.current_agent}</p>
                    <p className="text-xs text-gray-400 flex-shrink-0 dir-ltr">{fmtTime(c.last_message_at)}</p>
                  </div>
                </div>
              </div>
              <button
                onClick={(e) => { e.stopPropagation(); deleteConversation(c.id) }}
                className="flex-shrink-0 opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-red-100 text-red-400 ml-1 mt-0.5 transition-opacity"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
            )
          })}
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
          {messages.map((m) => <Message key={m.id} msg={m} onRate={handleRate} onCatalogSearch={handleCatalogSearch} />)}
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
