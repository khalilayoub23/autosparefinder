import { useState, useEffect } from 'react'
import api from '../api/client'
import {
  Bot, Search, TrendingUp, Package, DollarSign, HeartHandshake,
  ShieldCheck, BadgePercent, Truck, Clapperboard, Compass, Wallet,
  MessagesSquare, Route, Send, Loader2,
  CheckCircle, AlertCircle, ChevronDown, ChevronUp, Zap, Pencil, Save, Eye, EyeOff, Activity, Database, Timer,
} from 'lucide-react'
import toast from 'react-hot-toast'

const AGENT_ICON_MAP = {
  router_agent: Route,
  parts_finder_agent: Search,
  sales_agent: TrendingUp,
  orders_agent: Package,
  finance_agent: Wallet,
  service_agent: MessagesSquare,
  security_agent: ShieldCheck,
  marketing_agent: BadgePercent,
  supplier_manager_agent: Truck,
  social_media_manager_agent: Clapperboard,
}

const COLOR_MAP = {
  gray:   { logoBg: 'bg-gradient-to-br from-slate-500 via-zinc-600 to-gray-700', badge: 'bg-gray-200 text-gray-700', ring: 'ring-gray-200', accent: 'bg-gray-100 text-gray-700' },
  blue:   { logoBg: 'bg-gradient-to-br from-sky-500 via-blue-600 to-indigo-700', badge: 'bg-blue-100 text-blue-700', ring: 'ring-blue-200', accent: 'bg-blue-50 text-blue-700' },
  green:  { logoBg: 'bg-gradient-to-br from-emerald-500 via-green-600 to-teal-700', badge: 'bg-green-100 text-green-700', ring: 'ring-green-200', accent: 'bg-green-50 text-green-700' },
  orange: { logoBg: 'bg-gradient-to-br from-amber-500 via-brand-600 to-red-600', badge: 'bg-brand-100 text-brand-700', ring: 'ring-brand-200', accent: 'bg-brand-50 text-brand-700' },
  yellow: { logoBg: 'bg-gradient-to-br from-yellow-400 via-amber-500 to-brand-600', badge: 'bg-yellow-100 text-yellow-700', ring: 'ring-yellow-200', accent: 'bg-yellow-50 text-yellow-800' },
  pink:   { logoBg: 'bg-gradient-to-br from-rose-500 via-pink-600 to-fuchsia-700', badge: 'bg-pink-100 text-pink-700', ring: 'ring-pink-200', accent: 'bg-pink-50 text-pink-700' },
  red:    { logoBg: 'bg-gradient-to-br from-red-500 via-rose-600 to-red-700', badge: 'bg-red-100 text-red-700', ring: 'ring-red-200', accent: 'bg-red-50 text-red-700' },
  purple: { logoBg: 'bg-gradient-to-br from-brand-500 via-brand-600 to-brand-700', badge: 'bg-brand-100 text-brand-700', ring: 'ring-brand-200', accent: 'bg-brand-50 text-brand-700' },
  indigo: { logoBg: 'bg-gradient-to-br from-indigo-500 via-blue-600 to-cyan-700', badge: 'bg-indigo-100 text-indigo-700', ring: 'ring-indigo-200', accent: 'bg-indigo-50 text-indigo-700' },
  teal:   { logoBg: 'bg-gradient-to-br from-teal-500 via-cyan-600 to-blue-700', badge: 'bg-teal-100 text-teal-700', ring: 'ring-teal-200', accent: 'bg-teal-50 text-teal-700' },
}

const TYPE_LABELS = {
  customer: { label: 'לקוחות', style: 'bg-blue-50 text-blue-600 border border-blue-200' },
  admin:    { label: 'מנהל',   style: 'bg-brand-50 text-brand-600 border border-brand-200' },
  internal: { label: 'פנימי',  style: 'bg-gray-50 text-gray-600 border border-gray-200' },
}

const FALLBACK_TOKEN_PROVIDERS = [
  { provider: 'huggingface', label: 'Hugging Face' },
  { provider: 'cerebras', label: 'Cerebras' },
  { provider: 'gemini', label: 'Google Gemini' },
  { provider: 'groq', label: 'Groq' },
]

function AgentCard({ agent, onSave, isSaving }) {
  const [expanded, setExpanded] = useState(false)
  const [editing, setEditing] = useState(false)
  const [testMsg, setTestMsg] = useState('')
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [editForm, setEditForm] = useState({
    persona: agent.persona || '',
    model: agent.model || '',
    temperature: agent.temperature ?? 0.7,
    system_prompt: agent.system_prompt || '',
    assigned_tasks_text: (agent.assigned_tasks || agent.capabilities || []).join('\n'),
  })

  useEffect(() => {
    setEditForm({
      persona: agent.persona || '',
      model: agent.model || '',
      temperature: agent.temperature ?? 0.7,
      system_prompt: agent.system_prompt || '',
      assigned_tasks_text: (agent.assigned_tasks || agent.capabilities || []).join('\n'),
    })
  }, [agent])

  const colors = COLOR_MAP[agent.color] || COLOR_MAP.gray
  const Icon = AGENT_ICON_MAP[agent.name] || Compass
  const typeInfo = TYPE_LABELS[agent.type] || TYPE_LABELS.internal
  const isInternal = agent.type === 'internal'
  const personaInitial = (agent.persona || 'A').trim().charAt(0).toUpperCase()

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

  const handleSave = async () => {
    const assignedTasks = editForm.assigned_tasks_text
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean)

    await onSave(agent.name, {
      persona: editForm.persona,
      agent_name: editForm.persona,
      model: editForm.model,
      temperature: Number(editForm.temperature),
      system_prompt: editForm.system_prompt,
      assigned_tasks: assignedTasks,
    })
    setEditing(false)
  }

  return (
    <div className={`bg-white/90 rounded-2xl border border-brand-200 shadow-sm shadow-brand-100/60 overflow-hidden ring-1 ${colors.ring} hover:shadow-md transition-shadow`}>
      {/* Header */}
      <div className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className={`relative w-12 h-12 rounded-2xl flex items-center justify-center text-white shadow-md ring-2 ring-white ${colors.logoBg}`}>
              <span className="absolute inset-0 rounded-2xl bg-white/10" />
              <Icon className="relative w-5 h-5 drop-shadow-sm" />
              <span className={`absolute -bottom-1 -left-1 w-5 h-5 rounded-full text-[10px] font-bold flex items-center justify-center border border-white ${colors.accent}`}>
                {personaInitial}
              </span>
            </div>
            <div>
              <div className="flex items-baseline gap-2">
                <h3 className="font-bold text-brand-navy text-base">{agent.persona}</h3>
                <span className="text-xs text-gray-500 font-medium">{agent.name_he}</span>
              </div>
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

        {/* Assigned tasks */}
        <div className="mt-3">
          <p className="text-[11px] text-gray-400 mb-1">משימות משויכות</p>
          <div className="flex flex-wrap gap-1.5">
            {(agent.assigned_tasks || agent.capabilities || []).map((task) => (
              <span key={task} className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-700">
                {task}
              </span>
            ))}
          </div>
        </div>

        {/* Edit controls */}
        <div className="mt-4 border-t border-gray-100 pt-3">
          <button
            type="button"
            onClick={() => setEditing((v) => !v)}
            className="text-sm text-brand-700 hover:text-brand-800 flex items-center gap-1"
          >
            <Pencil className="w-4 h-4" />
            {editing ? 'סגור עריכה' : 'עריכת סוכן'}
          </button>

          {editing && (
            <div className="mt-3 space-y-3 bg-brand-50/80 rounded-xl p-3 border border-brand-200">
              <div>
                <label className="text-xs text-gray-500">שם סוכן (תצוגה)</label>
                <input
                  className="input-field w-full mt-1"
                  value={editForm.persona}
                  onChange={(e) => setEditForm((f) => ({ ...f, persona: e.target.value }))}
                />
              </div>
              <div>
                <label className="text-xs text-gray-500">מודל</label>
                <input
                  className="input-field w-full mt-1"
                  value={editForm.model}
                  onChange={(e) => setEditForm((f) => ({ ...f, model: e.target.value }))}
                  dir="ltr"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500">טמפרטורה</label>
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  max="1.5"
                  className="input-field w-full mt-1"
                  value={editForm.temperature}
                  onChange={(e) => setEditForm((f) => ({ ...f, temperature: e.target.value }))}
                  dir="ltr"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500">System Prompt</label>
                <textarea
                  rows={6}
                  className="input-field w-full mt-1 resize-y"
                  value={editForm.system_prompt}
                  onChange={(e) => setEditForm((f) => ({ ...f, system_prompt: e.target.value }))}
                  dir="ltr"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500">משימות משויכות (שורה לכל משימה)</label>
                <textarea
                  rows={4}
                  className="input-field w-full mt-1 resize-y"
                  value={editForm.assigned_tasks_text}
                  onChange={(e) => setEditForm((f) => ({ ...f, assigned_tasks_text: e.target.value }))}
                />
              </div>
              <button
                type="button"
                disabled={isSaving}
                onClick={handleSave}
                className="btn-primary px-4 py-2 flex items-center gap-2 disabled:opacity-60"
              >
                {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                {isSaving ? 'שומר...' : 'שמור שינויים'}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Test Panel */}
      {!isInternal && (
        <div className="border-t border-gray-100">
          <button
            onClick={() => setExpanded(!expanded)}
            className="w-full flex items-center justify-between px-5 py-3 text-sm text-gray-600 hover:bg-brand-50 transition-colors"
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
  const [runtimeToken, setRuntimeToken] = useState('')
  const [showRuntimeToken, setShowRuntimeToken] = useState(false)
  const [runtimeTokenProvider, setRuntimeTokenProvider] = useState('huggingface')
  const [tokenStatuses, setTokenStatuses] = useState({})
  const [updatingToken, setUpdatingToken] = useState(false)
  const [savingByAgent, setSavingByAgent] = useState({})
  const [usage, setUsage] = useState(null)
  const [agentSearch, setAgentSearch] = useState('')
  const [agentTypeFilter, setAgentTypeFilter] = useState('all')
  const [agentModeFilter, setAgentModeFilter] = useState('all')

  useEffect(() => {
    Promise.all([
      api.get('/admin/agents'),
      api.get('/admin/agents/runtime/tokens').catch(() => ({ data: null })),
      api.get('/admin/agents/usage', { params: { days: 14, limit: 120, memory_limit: 80 } }).catch(() => ({ data: null })),
    ])
      .then(([agentsRes, tokenRes, usageRes]) => {
        setData(agentsRes.data)
        setUsage(usageRes?.data || null)
        const providers = tokenRes?.data?.providers || []
        if (providers.length) {
          const mapped = providers.reduce((acc, p) => {
            if (p?.provider) acc[p.provider] = p
            return acc
          }, {})
          setTokenStatuses(mapped)
          if (!mapped[runtimeTokenProvider]) {
            setRuntimeTokenProvider(providers[0].provider)
          }
        }
      })
      .catch(() => toast.error('שגיאה בטעינת סוכנים'))
      .finally(() => setLoading(false))
  }, [])

  const providerOptions = Object.keys(tokenStatuses).length
    ? Object.values(tokenStatuses)
    : FALLBACK_TOKEN_PROVIDERS
  const selectedProviderStatus = tokenStatuses[runtimeTokenProvider] || null
  const selectedProviderLabel = selectedProviderStatus?.label || providerOptions.find((p) => p.provider === runtimeTokenProvider)?.label || runtimeTokenProvider

  const formatTs = (isoValue) => {
    if (!isoValue) return 'לא עודכן'
    try {
      return new Date(isoValue).toLocaleString('he-IL', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    } catch {
      return 'לא עודכן'
    }
  }

  const updateRuntimeToken = async () => {
    if (!runtimeTokenProvider) {
      toast.error('בחר ספק לפני עדכון טוקן')
      return
    }
    if (!runtimeToken.trim()) {
      toast.error('הכנס טוקן לפני שמירה')
      return
    }
    setUpdatingToken(true)
    try {
      const { data } = await api.put('/admin/agents/runtime/token', {
        provider: runtimeTokenProvider,
        token: runtimeToken.trim(),
        persist: true,
      })
      setTokenStatuses((prev) => ({ ...prev, [runtimeTokenProvider]: data }))
      setRuntimeToken('')
      toast.success(`טוקן עודכן בהצלחה עבור ${selectedProviderLabel}`)
      const [freshAgents, freshTokens, usageRes] = await Promise.all([
        api.get('/admin/agents'),
        api.get('/admin/agents/runtime/tokens').catch(() => ({ data: null })),
        api.get('/admin/agents/usage', { params: { days: 14, limit: 120, memory_limit: 80 } }).catch(() => ({ data: null })),
      ])
      setData(freshAgents.data)
      setUsage(usageRes?.data || usage)
      const providers = freshTokens?.data?.providers || []
      if (providers.length) {
        const mapped = providers.reduce((acc, p) => {
          if (p?.provider) acc[p.provider] = p
          return acc
        }, {})
        setTokenStatuses(mapped)
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'שגיאה בעדכון טוקן')
    } finally {
      setUpdatingToken(false)
    }
  }

  const saveAgent = async (agentName, payload) => {
    setSavingByAgent((s) => ({ ...s, [agentName]: true }))
    try {
      const { data: updated } = await api.put(`/admin/agents/${agentName}`, payload)
      setData((prev) => {
        if (!prev) return prev
        return {
          ...prev,
          agents: (prev.agents || []).map((a) =>
            a.name === agentName
              ? { ...a, ...updated, name: agentName }
              : a
          ),
        }
      })
      toast.success('הסוכן עודכן בהצלחה')
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'שגיאה בעדכון הסוכן')
      throw e
    } finally {
      setSavingByAgent((s) => ({ ...s, [agentName]: false }))
    }
  }

  const agents = data?.agents || []
  const filteredAgents = agents.filter((agent) => {
    const q = agentSearch.trim().toLowerCase()
    const matchesSearch = !q || [agent.persona, agent.name, agent.name_he, agent.description_he]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(q))

    const matchesType = agentTypeFilter === 'all' || agent.type === agentTypeFilter
    const matchesMode = agentModeFilter === 'all' || agent.ai_status === agentModeFilter

    return matchesSearch && matchesType && matchesMode
  })
  const usageSummary = usage?.summary || null
  const usageByAgent = usage?.by_agent || []
  const usageRecent = usage?.recent || []
  const sharedMemoryRows = usage?.shared_memory || []

  const formatUsageTs = (isoValue) => {
    if (!isoValue) return '—'
    try {
      return new Date(isoValue).toLocaleString('he-IL', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    } catch {
      return '—'
    }
  }

  const modelsInUse = data?.models_in_use || []
  const channelModels = data?.channel_models || {}
  const channelModelsText = ['web', 'whatsapp', 'telegram']
    .filter((channel) => channelModels[channel])
    .map((channel) => `${channel}: ${channelModels[channel]}`)
    .join(' | ')

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
        <div className="flex items-center gap-3 mb-2">
          <div className="w-10 h-10 bg-gradient-to-br from-brand-500 to-brand-700 rounded-xl flex items-center justify-center shadow-sm">
            <Bot className="w-5 h-5 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-brand-navy">סוכני AI</h1>
        </div>
        <p className="text-gray-500 mr-13 pr-13">לוח בקרה לניהול, ניטור ובדיקת סוכנים עם חיבור נתונים חי מהמערכת</p>
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
                : 'Mock Mode — CEREBRAS_API_KEY לא מוגדר'}
            </p>
            <p className={`text-xs mt-0.5 ${data.ai_status === 'active' ? 'text-green-600' : 'text-amber-600'}`}>
              {data.ai_status === 'active'
                ? (modelsInUse.length
                  ? `מודלים פעילים בפועל: ${modelsInUse.join(' | ')}`
                  : 'לא זוהו מודלים פעילים כרגע')
                : 'הגדר CEREBRAS_API_KEY ב-backend/.env לקבלת תגובות AI אמיתיות'}
            </p>
            {data.ai_status === 'active' && channelModelsText && (
              <p className="text-[11px] text-green-600 mt-1 break-words">
                מודלי ערוצים: {channelModelsText}
              </p>
            )}
          </div>
        </div>
      )}

      {usageSummary && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3 mb-6">
          <div className="bg-white/90 border border-brand-200 rounded-2xl p-4 shadow-sm shadow-brand-100/60">
            <p className="text-xs text-gray-500">סה״כ ריצות AI (14 ימים)</p>
            <p className="text-2xl font-bold text-brand-navy mt-1">{usageSummary.total_runs ?? 0}</p>
            <div className="flex items-center gap-1 text-xs text-gray-500 mt-2"><Activity className="w-3.5 h-3.5" /> ניטור חי לכל הסוכנים</div>
          </div>
          <div className="bg-white/90 border border-brand-200 rounded-2xl p-4 shadow-sm shadow-brand-100/60">
            <p className="text-xs text-gray-500">אחוז הצלחה</p>
            <p className="text-2xl font-bold text-green-700 mt-1">{usageSummary.success_rate_pct ?? 0}%</p>
            <p className="text-xs text-gray-500 mt-2">{usageSummary.success_runs ?? 0} הצליחו</p>
          </div>
          <div className="bg-white/90 border border-brand-200 rounded-2xl p-4 shadow-sm shadow-brand-100/60">
            <p className="text-xs text-gray-500">זמן תגובה ממוצע</p>
            <p className="text-2xl font-bold text-brand-700 mt-1">{usageSummary.avg_execution_ms ?? 0}ms</p>
            <div className="flex items-center gap-1 text-xs text-gray-500 mt-2"><Timer className="w-3.5 h-3.5" /> latency ממוצע</div>
          </div>
          <div className="bg-white/90 border border-brand-200 rounded-2xl p-4 shadow-sm shadow-brand-100/60">
            <p className="text-xs text-gray-500">פריטי זיכרון משותף</p>
            <p className="text-2xl font-bold text-indigo-700 mt-1">{usageSummary.shared_memory_items ?? 0}</p>
            <div className="flex items-center gap-1 text-xs text-gray-500 mt-2"><Database className="w-3.5 h-3.5" /> shared memory פעיל</div>
          </div>
        </div>
      )}

      {usageSummary && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-5 mb-6">
          <div className="bg-white/90 border border-brand-200 rounded-2xl p-4 shadow-sm shadow-brand-100/60">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-brand-navy">שימוש לפי סוכן</h3>
              <span className="text-xs text-gray-500">{usageByAgent.length} סוכנים פעילים</span>
            </div>
            <div className="space-y-2 max-h-64 overflow-auto pr-1">
              {usageByAgent.slice(0, 10).map((row) => (
                <div key={row.agent_name} className="rounded-xl border border-brand-100 px-3 py-2 flex items-center justify-between gap-2">
                  <div>
                    <p className="text-sm font-medium text-gray-800">{row.agent_name}</p>
                    <p className="text-xs text-gray-500">{row.runs} ריצות • {row.avg_execution_ms}ms ממוצע</p>
                  </div>
                  <div className="text-left">
                    <p className="text-xs text-green-700">{row.success} הצלחות</p>
                    <p className="text-xs text-red-600">{row.errors} שגיאות</p>
                    <p className="text-[11px] text-gray-400 mt-0.5">{formatUsageTs(row.last_used_at)}</p>
                  </div>
                </div>
              ))}
              {!usageByAgent.length && <p className="text-sm text-gray-500">אין נתוני שימוש עדיין.</p>}
            </div>
          </div>

          <div className="bg-white/90 border border-brand-200 rounded-2xl p-4 shadow-sm shadow-brand-100/60">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-brand-navy">זיכרון משותף אחרון</h3>
              <span className="text-xs text-gray-500">{sharedMemoryRows.length} רשומות</span>
            </div>
            <div className="space-y-2 max-h-64 overflow-auto pr-1">
              {sharedMemoryRows.slice(0, 12).map((row) => (
                <div key={row.id} className="rounded-xl border border-brand-100 px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-xs font-semibold text-gray-700">{row.memory_key}</p>
                    <span className="text-[11px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">{row.scope}</span>
                  </div>
                  <p className="text-sm text-gray-700 mt-1 break-words">{row.memory_value}</p>
                  <p className="text-[11px] text-gray-400 mt-1">agent: {row.agent_name || 'system'} • {formatUsageTs(row.updated_at)}</p>
                </div>
              ))}
              {!sharedMemoryRows.length && <p className="text-sm text-gray-500">אין רשומות זיכרון משותף.</p>}
            </div>
          </div>
        </div>
      )}

      {usageSummary && (
        <div className="bg-white/90 border border-brand-200 rounded-2xl p-4 mb-6 shadow-sm shadow-brand-100/60">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-brand-navy">לוג שימוש אחרון</h3>
            <span className="text-xs text-gray-500">{usageRecent.length} אירועים</span>
          </div>
          <div className="space-y-2 max-h-72 overflow-auto pr-1">
            {usageRecent.slice(0, 20).map((row) => (
              <div key={row.id} className="rounded-xl border border-brand-100 px-3 py-2 flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-800 truncate">{row.agent_name} • {row.intent || 'general'}</p>
                  <p className="text-xs text-gray-500 truncate">{row.source || 'web'} • {row.model_used || 'unknown-model'} • {formatUsageTs(row.created_at)}</p>
                </div>
                <div className="text-left shrink-0">
                  <p className={`text-xs font-semibold ${row.success ? 'text-green-700' : 'text-red-600'}`}>{row.success ? 'OK' : 'ERROR'}</p>
                  <p className="text-xs text-gray-500">{row.execution_time_ms ?? 0}ms</p>
                </div>
              </div>
            ))}
            {!usageRecent.length && <p className="text-sm text-gray-500">אין לוגים להצגה.</p>}
          </div>
        </div>
      )}

      {/* Runtime token controls */}
      <div className="bg-white/90 border border-brand-200 rounded-2xl p-5 mb-6 shadow-sm shadow-brand-100/60">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="min-w-[260px]">
            <p className="text-sm font-semibold text-brand-navy">ניהול טוקן AI</p>
            <p className="text-xs text-gray-500 mt-1">ספק נבחר: {selectedProviderLabel}</p>
            <p className="text-xs text-gray-500 mt-1">
              מצב נוכחי: {selectedProviderStatus?.configured ? `מוגדר (${selectedProviderStatus?.token_preview || '****'})` : 'לא מוגדר'}
            </p>
            <p className="text-xs text-gray-500 mt-1">עודכן לאחרונה: {formatTs(selectedProviderStatus?.persisted_updated_at)}</p>
            <p className="text-[11px] text-green-700 mt-2">העדכון נשמר ב-DB ומוחל אוטומטית גם אחרי ריסטארט של ה-Backend (Debug).</p>

            <div className="flex flex-wrap gap-1.5 mt-3">
              {providerOptions.map((provider) => {
                const status = tokenStatuses[provider.provider]
                const configured = Boolean(status?.configured)
                return (
                  <button
                    type="button"
                    key={provider.provider}
                    onClick={() => setRuntimeTokenProvider(provider.provider)}
                    className={`px-2.5 py-1 rounded-full text-xs border transition-colors ${runtimeTokenProvider === provider.provider
                      ? 'border-brand-300 bg-brand-50 text-brand-700'
                      : configured
                        ? 'border-green-200 bg-green-50 text-green-700'
                        : 'border-gray-200 bg-gray-50 text-gray-600'
                    }`}
                  >
                    {provider.label}
                  </button>
                )
              })}
            </div>
          </div>

          <div className="flex items-center gap-2 min-w-[320px] w-full md:w-auto flex-wrap md:flex-nowrap">
            <select
              className="input-field w-full md:w-52"
              value={runtimeTokenProvider}
              onChange={(e) => setRuntimeTokenProvider(e.target.value)}
            >
              {providerOptions.map((p) => (
                <option key={p.provider} value={p.provider}>{p.label}</option>
              ))}
            </select>

            <div className="relative flex-1 min-w-[230px]">
              <input
                type={showRuntimeToken ? 'text' : 'password'}
                className="input-field w-full pl-10"
                placeholder={`${selectedProviderLabel} token חדש`}
                value={runtimeToken}
                onChange={(e) => setRuntimeToken(e.target.value)}
                dir="ltr"
              />
              <button
                type="button"
                aria-label={showRuntimeToken ? 'הסתר טוקן' : 'הצג טוקן'}
                title={showRuntimeToken ? 'הסתר טוקן' : 'הצג טוקן'}
                onClick={() => setShowRuntimeToken((v) => !v)}
                className="absolute left-2 top-1/2 -translate-y-1/2 w-7 h-7 rounded-md flex items-center justify-center text-gray-400 hover:text-brand-600 hover:bg-brand-50 transition-colors"
              >
                {showRuntimeToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>

            <button
              type="button"
              className="btn-primary px-4 py-2 disabled:opacity-60"
              onClick={updateRuntimeToken}
              disabled={updatingToken}
            >
              {updatingToken ? 'מעדכן...' : 'עדכן טוקן'}
            </button>
          </div>
        </div>
      </div>

      {/* Filters */}
      <div className="bg-white/90 border border-brand-200 rounded-2xl p-4 mb-6 shadow-sm shadow-brand-100/60">
        <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
          <h2 className="text-sm font-semibold text-brand-navy">סינון סוכנים</h2>
          <p className="text-xs text-gray-500">{filteredAgents.length} מתוך {agents.length} מוצגים</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="relative">
            <Search className="w-4 h-4 text-gray-400 absolute right-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              className="input-field w-full pr-9"
              placeholder="חיפוש לפי שם, מזהה או תיאור"
              value={agentSearch}
              onChange={(e) => setAgentSearch(e.target.value)}
            />
          </div>

          <select
            className="input-field"
            value={agentTypeFilter}
            onChange={(e) => setAgentTypeFilter(e.target.value)}
          >
            <option value="all">כל הסוגים</option>
            <option value="customer">לקוחות</option>
            <option value="admin">מנהל</option>
            <option value="internal">פנימי</option>
          </select>

          <select
            className="input-field"
            value={agentModeFilter}
            onChange={(e) => setAgentModeFilter(e.target.value)}
          >
            <option value="all">כל המצבים</option>
            <option value="active">AI פעיל</option>
            <option value="mock">Mock Mode</option>
          </select>
        </div>
      </div>

      {/* Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
        {filteredAgents.map((agent) => (
          <AgentCard
            key={agent.name}
            agent={agent}
            onSave={saveAgent}
            isSaving={!!savingByAgent[agent.name]}
          />
        ))}
      </div>

      {!filteredAgents.length && (
        <div className="mt-6 bg-gray-50 border border-gray-200 rounded-2xl p-6 text-center">
          <p className="text-sm text-gray-600">לא נמצאו סוכנים לפי הסינון הנוכחי.</p>
        </div>
      )}
    </div>
  )
}
