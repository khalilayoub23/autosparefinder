import { useState, useEffect } from 'react'
import api from '../api/client'
import {
  Bot, Search, TrendingUp, Package, DollarSign, HeartHandshake,
  Shield, Megaphone, Truck, Share2, GitBranch, Send, Loader2,
  CheckCircle, AlertCircle, ChevronDown, ChevronUp, Zap, Info,
} from 'lucide-react'
import toast from 'react-hot-toast'

const ICON_MAP = {
  GitBranch, Search, TrendingUp, Package, DollarSign,
  HeartHandshake, Shield, Megaphone, Truck, Share2, Bot,
}

const COLOR_MAP = {
  gray:   { bg: 'bg-gray-100',   text: 'text-gray-700',   badge: 'bg-gray-200 text-gray-700',    ring: 'ring-gray-200' },
  blue:   { bg: 'bg-blue-100',   text: 'text-blue-700',   badge: 'bg-blue-100 text-blue-700',    ring: 'ring-blue-200' },
  green:  { bg: 'bg-green-100',  text: 'text-green-700',  badge: 'bg-green-100 text-green-700',  ring: 'ring-green-200' },
  orange: { bg: 'bg-orange-100', text: 'text-orange-700', badge: 'bg-orange-100 text-orange-700',ring: 'ring-orange-200' },
  yellow: { bg: 'bg-yellow-100', text: 'text-yellow-700', badge: 'bg-yellow-100 text-yellow-700',ring: 'ring-yellow-200' },
  pink:   { bg: 'bg-pink-100',   text: 'text-pink-700',   badge: 'bg-pink-100 text-pink-700',    ring: 'ring-pink-200' },
  red:    { bg: 'bg-red-100',    text: 'text-red-700',    badge: 'bg-red-100 text-red-700',      ring: 'ring-red-200' },
  purple: { bg: 'bg-purple-100', text: 'text-purple-700', badge: 'bg-purple-100 text-purple-700',ring: 'ring-purple-200' },
  indigo: { bg: 'bg-indigo-100', text: 'text-indigo-700', badge: 'bg-indigo-100 text-indigo-700',ring: 'ring-indigo-200' },
  teal:   { bg: 'bg-teal-100',   text: 'text-teal-700',   badge: 'bg-teal-100 text-teal-700',    ring: 'ring-teal-200' },
}

const TYPE_LABELS = {
  customer: { label: 'לקוחות', style: 'bg-blue-50 text-blue-600 border border-blue-200' },
  admin:    { label: 'מנהל',   style: 'bg-purple-50 text-purple-600 border border-purple-200' },
  internal: { label: 'פנימי',  style: 'bg-gray-50 text-gray-600 border border-gray-200' },
}

function AgentCard({ agent, onTest }) {
  const [expanded, setExpanded] = useState(false)
  const [testMsg, setTestMsg] = useState('')
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)

  const colors = COLOR_MAP[agent.color] || COLOR_MAP.gray
  const Icon = ICON_MAP[agent.icon] || Bot
  const typeInfo = TYPE_LABELS[agent.type] || TYPE_LABELS.internal
  const isInternal = agent.type === 'internal'

  const handleTest = async () => {
    if (!testMsg.trim()) return
    setTesting(true)
    setTestResult(null)
    try {
      const { data } = await api.post(`/admin/agents/${agent.name}/test`, { message: testMsg })
      setTestResult(data)
    } catch (err) {
      setTestResult({ status: 'error', response: err.response?.data?.detail || err.message })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className={`bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden ring-1 ${colors.ring} hover:shadow-md transition-shadow`}>
      {/* Header */}
      <div className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className={`w-11 h-11 rounded-xl flex items-center justify-center ${colors.bg}`}>
              <Icon className={`w-6 h-6 ${colors.text}`} />
            </div>
            <div>
              <h3 className="font-bold text-gray-900 text-sm">{agent.name_he}</h3>
              <p className="text-xs text-gray-400 font-mono">{agent.name}</p>
            </div>
          </div>
          <div className="flex flex-col items-end gap-1.5">
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${typeInfo.style}`}>
              {typeInfo.label}
            </span>
            <div className="flex items-center gap-1.5">
              {agent.ai_status === 'active'
                ? <CheckCircle className="w-3.5 h-3.5 text-green-500" />
                : <AlertCircle className="w-3.5 h-3.5 text-amber-500" />
              }
              <span className={`text-xs font-medium ${agent.ai_status === 'active' ? 'text-green-600' : 'text-amber-600'}`}>
                {agent.ai_status === 'active' ? 'AI פעיל' : 'Mock Mode'}
              </span>
            </div>
          </div>
        </div>

        <p className="mt-3 text-sm text-gray-600 leading-relaxed">{agent.description_he}</p>

        {/* Model + Temp */}
        <div className="flex items-center gap-3 mt-3">
          <span className="flex items-center gap-1 text-xs text-gray-500">
            <Zap className="w-3 h-3" /> {agent.model}
          </span>
          <span className="text-xs text-gray-400">•</span>
          <span className="text-xs text-gray-500">Temp: {agent.temperature}</span>
        </div>

        {/* Capabilities */}
        <div className="flex flex-wrap gap-1.5 mt-3">
          {agent.capabilities.map((cap) => (
            <span key={cap} className={`text-xs px-2 py-0.5 rounded-full ${colors.badge}`}>
              {cap}
            </span>
          ))}
        </div>
      </div>

      {/* Test Panel */}
      {!isInternal && (
        <div className="border-t border-gray-100">
          <button
            onClick={() => setExpanded(!expanded)}
            className="w-full flex items-center justify-between px-5 py-3 text-sm text-gray-600 hover:bg-gray-50 transition-colors"
          >
            <span className="font-medium">בדיקת סוכן</span>
            {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>
          {expanded && (
            <div className="px-5 pb-5 space-y-3 bg-gray-50">
              <textarea
                rows={2}
                value={testMsg}
                onChange={(e) => setTestMsg(e.target.value)}
                placeholder="שלח הודעת בדיקה לסוכן..."
                className="w-full text-sm border border-gray-200 rounded-xl px-3 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-brand-500 bg-white"
                dir="rtl"
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleTest() } }}
              />
              <button
                onClick={handleTest}
                disabled={testing || !testMsg.trim()}
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-50 transition-colors"
              >
                {testing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                {testing ? 'שולח...' : 'שלח'}
              </button>
              {testResult && (
                <div className={`rounded-xl p-3 text-sm ${testResult.status === 'ok' ? 'bg-white border border-green-200' : 'bg-red-50 border border-red-200'}`}>
                  <div className="flex items-center gap-1.5 mb-1.5">
                    {testResult.status === 'ok'
                      ? <CheckCircle className="w-3.5 h-3.5 text-green-600" />
                      : <AlertCircle className="w-3.5 h-3.5 text-red-500" />
                    }
                    <span className={`text-xs font-semibold ${testResult.status === 'ok' ? 'text-green-700' : 'text-red-600'}`}>
                      {testResult.status === 'ok' ? 'תגובה' : 'שגיאה'}
                    </span>
                  </div>
                  <p className="text-gray-700 leading-relaxed whitespace-pre-wrap">{testResult.response}</p>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function Agents() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('all')

  useEffect(() => {
    api.get('/admin/agents')
      .then(({ data }) => setData(data))
      .catch(() => toast.error('שגיאה בטעינת סוכנים'))
      .finally(() => setLoading(false))
  }, [])

  const agents = data?.agents || []
  const filtered = filter === 'all' ? agents : agents.filter((a) => a.type === filter)

  const counts = agents.reduce((acc, a) => { acc[a.type] = (acc[a.type] || 0) + 1; return acc }, {})

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-brand-600" />
      </div>
    )
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-8" dir="rtl">
      {/* Header */}
      <div className="mb-8">
        <div className="flex items-center gap-3 mb-1">
          <div className="w-10 h-10 bg-brand-600 rounded-xl flex items-center justify-center">
            <Bot className="w-5 h-5 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900">סוכני AI</h1>
        </div>
        <p className="text-gray-500 mr-13 pr-13">לוח בקרה לניהול ובדיקת סוכני הבינה המלאכותית של המערכת</p>
      </div>

      {/* Status Banner */}
      {data && (
        <div className={`flex items-center gap-3 p-4 rounded-xl mb-6 ${data.ai_status === 'active' ? 'bg-green-50 border border-green-200' : 'bg-amber-50 border border-amber-200'}`}>
          {data.ai_status === 'active'
            ? <CheckCircle className="w-5 h-5 text-green-600 flex-shrink-0" />
            : <AlertCircle className="w-5 h-5 text-amber-600 flex-shrink-0" />
          }
          <div>
            <p className={`text-sm font-semibold ${data.ai_status === 'active' ? 'text-green-800' : 'text-amber-800'}`}>
              {data.ai_status === 'active'
                ? `AI פעיל — ${data.total} סוכנים טעונים`
                : 'Mock Mode — GITHUB_TOKEN לא מוגדר'}
            </p>
            <p className={`text-xs mt-0.5 ${data.ai_status === 'active' ? 'text-green-600' : 'text-amber-600'}`}>
              {data.ai_status === 'active'
                ? 'כל הסוכנים עובדים עם GitHub Models API (GPT-4o)'
                : 'הגדר GITHUB_TOKEN ב-backend/.env לקבלת תגובות AI אמיתיות'}
            </p>
          </div>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        {[
          { label: 'סה"כ סוכנים', value: agents.length, color: 'text-gray-900' },
          { label: 'לקוחות', value: counts.customer || 0, color: 'text-blue-600' },
          { label: 'מנהל / רקע', value: (counts.admin || 0) + (counts.internal || 0), color: 'text-purple-600' },
        ].map(({ label, value, color }) => (
          <div key={label} className="bg-white border border-gray-200 rounded-xl p-4 text-center">
            <div className={`text-2xl font-bold ${color}`}>{value}</div>
            <div className="text-xs text-gray-500 mt-0.5">{label}</div>
          </div>
        ))}
      </div>

      {/* Filter */}
      <div className="flex gap-2 mb-6">
        {[
          { key: 'all',      label: `הכל (${agents.length})` },
          { key: 'customer', label: `לקוחות (${counts.customer || 0})` },
          { key: 'admin',    label: `מנהל (${counts.admin || 0})` },
          { key: 'internal', label: `פנימי (${counts.internal || 0})` },
        ].map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setFilter(key)}
            className={`px-4 py-2 rounded-xl text-sm font-medium transition-colors
              ${filter === key
                ? 'bg-brand-600 text-white'
                : 'bg-white border border-gray-200 text-gray-600 hover:bg-gray-50'
              }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Info note */}
      <div className="flex items-start gap-2 p-3 bg-blue-50 rounded-xl border border-blue-100 mb-6 text-xs text-blue-700">
        <Info className="w-4 h-4 flex-shrink-0 mt-0.5" />
        <span>
          כל הסוכנים נגישים דרך ה-Chat בממשק הלקוח. סוכן הניתוב מחליט אוטומטית לאיזה סוכן לשלוח כל הודעה.
          סוכני הרקע (ספקים, מדיה חברתית) פועלים אוטומטית ואינם משוחח עם לקוחות.
        </span>
      </div>

      {/* Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
        {filtered.map((agent) => (
          <AgentCard key={agent.name} agent={agent} />
        ))}
      </div>
    </div>
  )
}
