import { useState, useEffect } from 'react'
import api from '../api/client'
import {
  LayoutDashboard, Users, Package, TrendingUp, Settings,
  DollarSign, ShoppingBag, BarChart2, Loader2, RefreshCw,
  ToggleLeft, ToggleRight, Truck, PlusCircle, Wand2, ChevronDown,
  ShoppingCart, CheckCircle, Clock, ExternalLink, AlertCircle,
  FileSpreadsheet, Upload, X, Zap, TrendingDown, Percent,
  Trash2, Pencil, Ban, ShieldCheck, Save,
  Globe, Phone, Mail, Key, Star, MapPin, Eye, EyeOff,
  Bot, MessageSquare, Sliders, Cpu, FlaskConical,
} from 'lucide-react'
import toast from 'react-hot-toast'

const STATUS_HE = {
  pending_payment: { label: 'ממתין לתשלום', cls: 'bg-amber-100 text-amber-700' },
  paid:            { label: 'שולם',          cls: 'bg-green-100 text-green-700' },
  processing:      { label: 'בטיפול',        cls: 'bg-blue-100 text-blue-700'  },
  shipped:         { label: 'נשלח',          cls: 'bg-purple-100 text-purple-700' },
  delivered:       { label: 'נמסר',          cls: 'bg-gray-100 text-gray-700'  },
  cancelled:       { label: 'בוטל',          cls: 'bg-red-100 text-red-600'    },
}

function StatCard({ label, value, icon: Icon, color = 'brand', sub }) {
  const colors = {
    brand: 'bg-brand-50 text-brand-600',
    green: 'bg-green-50 text-green-600',
    blue: 'bg-blue-50 text-blue-600',
    purple: 'bg-purple-50 text-purple-600',
    orange: 'bg-orange-50 text-orange-600',
    teal: 'bg-teal-50 text-teal-600',
  }
  return (
    <div className="card p-5 flex items-center gap-4">
      <div className={`w-12 h-12 rounded-xl flex items-center justify-center shrink-0 ${colors[color] || colors.brand}`}>
        <Icon className="w-6 h-6" />
      </div>
      <div>
        <p className="text-sm text-gray-500">{label}</p>
        <p className="text-2xl font-bold text-gray-900">{value ?? <span className="skeleton w-16 h-6 inline-block" />}</p>
        {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
      </div>
    </div>
  )
}

const TABS = [
  { id: 'dashboard', label: 'סקירה', icon: LayoutDashboard },
  { id: 'users',     label: 'משתמשים', icon: Users         },
  { id: 'suppliers', label: 'ספקים', icon: Truck, badge: true },
  { id: 'orders',    label: 'הזמנות', icon: ShoppingBag    },
  { id: 'parts',     label: 'ייבוא חלקים', icon: FileSpreadsheet },
  { id: 'social',    label: 'רשתות חברתיות', icon: Wand2   },
  { id: 'agents',    label: 'סוכני AI',        icon: Bot     },
]

function SupplierFormFields({ f, setF, isCreate }) {
  const [showApiKey, setShowApiKey] = useState(false)
  return (
    <>
      <div>
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">פרטים בסיסיים</p>
        <div className="space-y-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">שם ספק <span className="text-red-500">*</span></label>
            <input className="input-field w-full" value={f.name} onChange={(e) => setF('name', e.target.value)} placeholder="AutoParts Pro" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">מדינה <span className="text-red-500">*</span></label>
              <input className="input-field w-full" value={f.country} onChange={(e) => setF('country', e.target.value)} placeholder="Israel" dir="ltr" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">עדיפות</label>
              <input type="number" className="input-field w-full" value={f.priority} onChange={(e) => setF('priority', Number(e.target.value))} min={0} max={100} dir="ltr" />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">אמינות (0–10)</label>
            <input type="number" step="0.1" min="0" max="10" className="input-field w-full" value={f.reliability_score} onChange={(e) => setF('reliability_score', Number(e.target.value))} dir="ltr" />
          </div>
          {!isCreate && (
            <div className="flex items-center gap-3">
              <label className="text-sm font-medium text-gray-700">פעיל</label>
              <button onClick={() => setF('is_active', !f.is_active)} className={`badge cursor-pointer hover:opacity-80 ${f.is_active ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-600'}`}>
                {f.is_active ? '✓ פעיל' : '✗ לא פעיל'}
              </button>
            </div>
          )}
        </div>
      </div>
      <div>
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">פרטי קשר</p>
        <div className="space-y-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">אימייל קשר</label>
            <input className="input-field w-full" type="email" value={f.contact_email} onChange={(e) => setF('contact_email', e.target.value)} placeholder="supplier@example.com" dir="ltr" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">טלפון קשר</label>
            <input className="input-field w-full" type="tel" value={f.contact_phone} onChange={(e) => setF('contact_phone', e.target.value)} placeholder="+972-X-XXXXXXX" dir="ltr" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">אתר אינטרנט</label>
            <input className="input-field w-full" type="url" value={f.website} onChange={(e) => setF('website', e.target.value)} placeholder="https://supplier.com" dir="ltr" />
          </div>
        </div>
      </div>
      <div>
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">API</p>
        <div className="space-y-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">כתובת API</label>
            <input className="input-field w-full" value={f.api_endpoint} onChange={(e) => setF('api_endpoint', e.target.value)} placeholder="https://api.supplier.com/v1" dir="ltr" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{isCreate ? 'מפתח API' : 'מפתח API חדש (ריק = ללא שינוי)'}</label>
            <div className="relative">
              <input className="input-field w-full pl-10" type={showApiKey ? 'text' : 'password'} value={f.api_key} onChange={(e) => setF('api_key', e.target.value)} placeholder="sk-..." dir="ltr" />
              <button type="button" onClick={() => setShowApiKey((v) => !v)} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600">
                {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>
        </div>
      </div>
      <div>
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">משלוח</p>
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <button onClick={() => setF('supports_express', !f.supports_express)} className={`badge cursor-pointer hover:opacity-80 ${f.supports_express ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'}`}>
              {f.supports_express ? '⚡ אקספרס — פעיל' : 'אקספרס — לא נתמך'}
            </button>
          </div>
          {f.supports_express && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">מוביל</label>
                <input className="input-field w-full" value={f.express_carrier} onChange={(e) => setF('express_carrier', e.target.value)} placeholder="DHL Express" dir="ltr" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">עלות בסיס ($)</label>
                <input type="number" step="0.01" min="0" className="input-field w-full" value={f.express_base_cost_usd} onChange={(e) => setF('express_base_cost_usd', e.target.value)} placeholder="15.00" dir="ltr" />
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

const COUNTRY_ISO = {
  'israel': 'IL', 'usa': 'US', 'united states': 'US', 'germany': 'DE', 'china': 'CN',
  'france': 'FR', 'uk': 'GB', 'united kingdom': 'GB', 'japan': 'JP', 'south korea': 'KR',
  'korea': 'KR', 'italy': 'IT', 'spain': 'ES', 'netherlands': 'NL', 'turkey': 'TR',
  'india': 'IN', 'brazil': 'BR', 'canada': 'CA', 'australia': 'AU', 'russia': 'RU',
  'poland': 'PL', 'czech republic': 'CZ', 'czechia': 'CZ', 'sweden': 'SE', 'taiwan': 'TW',
  'thailand': 'TH', 'malaysia': 'MY', 'indonesia': 'ID', 'mexico': 'MX', 'argentina': 'AR',
  'uae': 'AE', 'united arab emirates': 'AE', 'saudi arabia': 'SA', 'egypt': 'EG',
  'portugal': 'PT', 'austria': 'AT', 'switzerland': 'CH', 'belgium': 'BE', 'denmark': 'DK',
  'finland': 'FI', 'norway': 'NO', 'hungary': 'HU', 'romania': 'RO', 'ukraine': 'UA',
  'singapore': 'SG', 'hong kong': 'HK', 'new zealand': 'NZ', 'south africa': 'ZA',
}

function countryISO(name) {
  if (!name) return name?.slice(0, 2).toUpperCase() || ''
  return COUNTRY_ISO[name.toLowerCase().trim()] || name.slice(0, 2).toUpperCase()
}

function countryFlag(name) {
  if (!name) return null
  const iso = COUNTRY_ISO[name.toLowerCase().trim()]
  if (!iso) return null
  return <img src={`https://flagcdn.com/20x15/${iso.toLowerCase()}.png`} alt={iso} className="inline-block rounded-sm" style={{width:20,height:15}} />
}


export default function Admin() {
  const [tab, setTab] = useState('dashboard')
  const [stats, setStats] = useState(null)
  const [importFile, setImportFile] = useState(null)
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState(null)
  const [users, setUsers] = useState([])
  const [editUser, setEditUser] = useState(null)
  const [editForm, setEditForm] = useState({ full_name: '', email: '', phone: '', role: 'customer', is_verified: false, is_active: true, is_admin: false })
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [createForm, setCreateForm] = useState({ full_name: '', email: '', phone: '', password: '', role: 'customer', is_admin: false, is_verified: true })
  const [userFilters, setUserFilters] = useState({ name: '', email: '', verified: '', role: '', status: '', date: '' })
  const [orders, setOrders] = useState([])
  const [suppliers, setSuppliers] = useState([])
  const [expandedSupplier, setExpandedSupplier] = useState(null)
  const [editSupplier, setEditSupplier] = useState(null)
  const [editSupplierForm, setEditSupplierForm] = useState({})
  const [showCreateSupplier, setShowCreateSupplier] = useState(false)
  const [createSupplierForm, setCreateSupplierForm] = useState({ name: '', country: '', website: '', api_endpoint: '', api_key: '', contact_email: '', contact_phone: '', priority: 0, reliability_score: 5.0, supports_express: false, express_carrier: '', express_base_cost_usd: '' })
  const [supplierFilters, setSupplierFilters] = useState({ name: '', country: '', status: '' })
  const [orderLogFilters, setOrderLogFilters] = useState({ search: '', status: '' })
  const [supplierOrders, setSupplierOrders] = useState([])
  const [supplierOrdersPending, setSupplierOrdersPending] = useState(0)
  const [syncStatus, setSyncStatus] = useState(null)   // price-sync status
  const [syncing, setSyncing] = useState(false)         // manual trigger running
  const [socialContent, setSocialContent] = useState('')
  const [genTopic, setGenTopic] = useState('')
  const [genPlatform, setGenPlatform] = useState('facebook')
  const [generating, setGenerating] = useState(false)
  const [agents, setAgents] = useState([])
  const [agentsAiStatus, setAgentsAiStatus] = useState(null)
  const [editAgent, setEditAgent] = useState(null)
  const [editAgentForm, setEditAgentForm] = useState({})
  const [savingAgent, setSavingAgent] = useState(false)
  const [testingAgent, setTestingAgent] = useState(null)
  const [testMessage, setTestMessage] = useState('')
  const [testResult, setTestResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [salesData, setSalesData] = useState([])
  const [statusFilter, setStatusFilter] = useState('')
  const [updatingStatus, setUpdatingStatus] = useState(null)
  const [categoryStats, setCategoryStats] = useState([])

  useEffect(() => {
    loadDashboard()
  }, [])

  useEffect(() => {
    if (tab === 'users') loadUsers()
    if (tab === 'orders') loadOrders()
    if (tab === 'suppliers') { loadSuppliers(); loadSupplierOrders(); loadSyncStatus() }
    if (tab === 'agents') loadAgents()
  }, [tab])

  const loadDashboard = async () => {
    try {
      const [statsRes, salesRes, catRes] = await Promise.all([
        api.get('/admin/stats'),
        api.get('/admin/analytics/sales'),
        api.get('/parts/categories'),
      ])
      setStats(statsRes.data)
      setSalesData(salesRes.data?.data?.slice(-30) || [])
      // Top 10 categories by part count
      const counts = catRes.data?.counts || {}
      const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 12)
      setCategoryStats(sorted)
    } catch { toast.error('שגיאה בטעינת נתונים') }
  }

  const loadAgents = async () => {
    try {
      const { data } = await api.get('/admin/agents')
      setAgents(data.agents || [])
      setAgentsAiStatus(data)
    } catch { toast.error('שגיאה בטעינת סוכנים') }
  }

  const saveEditAgent = async () => {
    if (!editAgent) return
    setSavingAgent(true)
    try {
      const { data } = await api.put(`/admin/agents/${editAgent.name}`, editAgentForm)
      setAgents((prev) => prev.map((a) => a.name === editAgent.name ? { ...a, ...data } : a))
      setEditAgent(null)
      toast.success('הסוכן עודכן')
    } catch { toast.error('שגיאה בשמירה') }
    finally { setSavingAgent(false) }
  }

  const testAgent = async (agentName) => {
    if (!testMessage.trim()) return
    setTestingAgent(agentName)
    setTestResult(null)
    try {
      const { data } = await api.post(`/admin/agents/${agentName}/test`, { message: testMessage })
      setTestResult(data)
    } catch (e) { setTestResult({ status: 'error', response: e.response?.data?.detail || 'שגיאה' }) }
    finally { setTestingAgent(null) }
  }

  const loadUsers = async () => {
    setLoading(true)
    try { const { data } = await api.get('/admin/users'); setUsers(data.users || []) }
    catch { toast.error('שגיאה') }
    finally { setLoading(false) }
  }

  const loadOrders = async (sf = statusFilter) => {
    setLoading(true)
    try {
      const params = sf ? { status: sf } : {}
      const { data } = await api.get('/admin/orders', { params })
      setOrders(data.orders || [])
    }
    catch { toast.error('שגיאה') }
    finally { setLoading(false) }
  }

  const updateOrderStatus = async (orderId, newStatus) => {
    setUpdatingStatus(orderId)
    try {
      await api.put(`/admin/orders/${orderId}/status`, null, { params: { new_status: newStatus } })
      setOrders((os) => os.map((o) => o.id === orderId ? { ...o, status: newStatus } : o))
      toast.success('סטטוס עודכן')
    } catch { toast.error('שגיאה בעדכון סטטוס') }
    finally { setUpdatingStatus(null) }
  }

  const loadSyncStatus = async () => {
    try {
      const { data } = await api.get('/admin/price-sync/status')
      setSyncStatus(data)
    } catch { /* not an error if never run */ }
  }

  const triggerSync = async () => {
    setSyncing(true)
    try {
      await api.post('/admin/price-sync/run')
      toast.success('סנכרון מחירים הופעל ברקע!')
      // Poll until last_sync timestamp changes
      const before = syncStatus?.last_sync
      let tries = 0
      const poll = setInterval(async () => {
        tries++
        const { data } = await api.get('/admin/price-sync/status').catch(() => ({ data: null }))
        if (data && data.last_sync !== before) {
          setSyncStatus(data)
          setSyncing(false)
          clearInterval(poll)
          toast.success(`✅ עודכנו ${Number(data.message?.match(/updated=(\d+)/)?.[1] || 0).toLocaleString('he-IL')} חלקים`)
        }
        if (tries > 40) { clearInterval(poll); setSyncing(false) }  // 2-min timeout
      }, 3000)
    } catch (e) {
      toast.error('שגיאה בהפעלת הסנכרון')
      setSyncing(false)
    }
  }

  const loadSuppliers = async () => {    setLoading(true)
    try { const { data } = await api.get('/admin/suppliers'); setSuppliers(data.suppliers || []) }
    catch { toast.error('שגיאה') }
    finally { setLoading(false) }
  }

  const loadSupplierOrders = async () => {
    try {
      const { data } = await api.get('/admin/supplier-orders')
      setSupplierOrders(data.supplier_orders || [])
      setSupplierOrdersPending(data.pending_count || 0)
    } catch { /* silent */ }
  }

  const markSupplierOrderDone = async () => {} // no-op: agent handles fulfillment automatically

  const toggleUser = async (userId, currentStatus) => {
    try {
      await api.put(`/admin/users/${userId}`, null, { params: { is_active: !currentStatus } })
      setUsers((u) => u.map((user) => user.id === userId ? { ...user, is_active: !currentStatus } : user))
      toast.success('עודכן')
    } catch { toast.error('שגיאה') }
  }

  const openEditUser = (u) => {
    setEditUser(u)
    setEditForm({
      full_name: u.full_name || '',
      email: u.email || '',
      phone: u.phone || '',
      role: u.role || 'customer',
      is_verified: u.is_verified ?? false,
      is_active: u.is_active !== false,
      is_admin: u.is_admin ?? false,
    })
  }

  const saveEditUser = async () => {
    if (!editUser) return
    try {
      const { data } = await api.put(`/admin/users/${editUser.id}`, editForm)
      setUsers((us) => us.map((x) => x.id === editUser.id ? { ...x, ...data.user } : x))
      setEditUser(null)
      toast.success('פרטי המשתמש עודכנו')
    } catch (e) { toast.error(e?.response?.data?.detail || 'שגיאה בעדכון') }
  }

  const resetLoginFailures = async (userId) => {
    try {
      await api.post(`/admin/users/${userId}/reset-login`)
      setUsers((us) => us.map((x) => x.id === userId ? { ...x, failed_login_count: 0, locked_until: null } : x))
      setEditUser((u) => u ? { ...u, failed_login_count: 0, locked_until: null } : u)
      toast.success('כשלונות כניסה אופסו')
    } catch { toast.error('שגיאה') }
  }

  const submitCreateUser = async () => {
    if (!createForm.full_name.trim() || !createForm.email.trim() || !createForm.phone.trim() || !createForm.password.trim()) {
      toast.error('יש למלא את כל השדות הנחוצים')
      return
    }
    try {
      const { data } = await api.post('/admin/users', createForm)
      setUsers((us) => [data.user, ...us])
      setShowCreateModal(false)
      setCreateForm({ full_name: '', email: '', phone: '', password: '', role: 'customer', is_admin: false, is_verified: true })
      toast.success('המשתמש נוצר בהצלחה')
    } catch (e) { toast.error(e?.response?.data?.detail || 'שגיאה ביצירת משתמש') }
  }

  const deleteUser = async (userId, userName) => {
    if (!window.confirm(`למחוק את המשתמש "${userName}"?\nפעולה זו אינה הפיכה.`)) return
    try {
      await api.delete(`/admin/users/${userId}`)
      setUsers((us) => us.filter((x) => x.id !== userId))
      toast.success('המשתמש נמחק')
    } catch (e) { toast.error(e?.response?.data?.detail || 'שגיאה במחיקה') }
  }

  const toggleAdminRole = async (u) => {
    try {
      await api.put(`/admin/users/${u.id}`, null, { params: { is_admin: !u.is_admin } })
      setUsers((us) => us.map((x) => x.id === u.id ? { ...x, is_admin: !u.is_admin } : x))
      toast.success('עודכן')
    } catch (e) { toast.error(e?.response?.data?.detail || 'שגיאה') }
  }

  const toggleSupplier = async (id, current) => {
    try {
      await api.put(`/admin/suppliers/${id}`, null, { params: { is_active: !current } })
      setSuppliers((s) => s.map((sup) => sup.id === id ? { ...sup, is_active: !current } : sup))
      toast.success('עודכן')
    } catch { toast.error('שגיאה') }
  }

  const openEditSupplier = (s) => {
    setEditSupplier(s)
    setEditSupplierForm({
      name: s.name || '', country: s.country || '', website: s.website || '',
      api_endpoint: s.api_endpoint || '', api_key: '', contact_email: s.contact_email || '',
      contact_phone: s.contact_phone || '', priority: s.priority ?? 0,
      reliability_score: s.reliability_score ?? 5.0, is_active: s.is_active !== false,
      supports_express: s.supports_express ?? false, express_carrier: s.express_carrier || '',
      express_base_cost_usd: s.express_base_cost_usd ?? '',
    })
  }

  const saveEditSupplier = async () => {
    if (!editSupplier) return
    try {
      const body = { ...editSupplierForm }
      if (!body.api_key) delete body.api_key
      if (body.express_base_cost_usd === '') body.express_base_cost_usd = null
      const { data } = await api.put(`/admin/suppliers/${editSupplier.id}`, body)
      setSuppliers((ss) => ss.map((x) => x.id === editSupplier.id ? { ...x, ...data.supplier } : x))
      if (expandedSupplier?.id === editSupplier.id) setExpandedSupplier((p) => ({ ...p, ...data.supplier }))
      setEditSupplier(null)
      toast.success('ספק עודכן')
    } catch (e) { toast.error(e?.response?.data?.detail || 'שגיאה') }
  }

  const submitCreateSupplier = async () => {
    if (!createSupplierForm.name.trim() || !createSupplierForm.country.trim()) {
      toast.error('שם ומדינה הם שדות חובה')
      return
    }
    try {
      const body = { ...createSupplierForm }
      if (!body.api_key) delete body.api_key
      if (body.express_base_cost_usd === '') body.express_base_cost_usd = null
      const { data } = await api.post('/admin/suppliers', body)
      setSuppliers((ss) => [data.supplier, ...ss])
      setShowCreateSupplier(false)
      setCreateSupplierForm({ name: '', country: '', website: '', api_endpoint: '', api_key: '', contact_email: '', contact_phone: '', priority: 0, reliability_score: 5.0, supports_express: false, express_carrier: '', express_base_cost_usd: '' })
      toast.success('ספק נוצר בהצלחה')
    } catch (e) { toast.error(e?.response?.data?.detail || 'שגיאה') }
  }

  const deleteSupplierById = async (id, name) => {
    if (!window.confirm(`למחוק את הספק "${name}"?`)) return
    try {
      await api.delete(`/admin/suppliers/${id}`)
      setSuppliers((ss) => ss.filter((x) => x.id !== id))
      if (expandedSupplier?.id === id) setExpandedSupplier(null)
      toast.success('ספק נמחק')
    } catch (e) { toast.error(e?.response?.data?.detail || 'שגיאה') }
  }

  const syncSupplier = async (id) => {
    try {
      await api.post(`/admin/suppliers/${id}/sync`)
      toast.success('סנכרון התחיל')
    } catch { toast.error('שגיאה') }
  }

  const generateSocial = async () => {
    if (!genTopic.trim()) return
    setGenerating(true)
    try {
      const { data } = await api.post('/admin/social/generate-content', null, { params: { topic: genTopic, platform: genPlatform, tone: 'professional' } })
      setSocialContent(data.content || '')
      toast.success('תוכן נוצר – ממתין לאישורך')
    } catch { toast.error('שגיאה ביצירת תוכן') }
    finally { setGenerating(false) }
  }

  return (
    <>
    <div className="space-y-6">
      <div>
        <h1 className="section-title">לוח ניהול</h1>
        <p className="text-gray-500 mt-1">Auto Spare · עוסק מורשה 060633880</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-200 overflow-x-auto">
        {TABS.map(({ id, label, icon: Icon, badge }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-2 px-5 py-2.5 text-sm font-medium whitespace-nowrap border-b-2 -mb-px transition-colors
              ${tab === id ? 'border-brand-600 text-brand-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
          >
            <Icon className="w-4 h-4" /> {label}
            {badge && supplierOrdersPending > 0 && (
              <span className="inline-flex items-center justify-center w-5 h-5 text-[10px] font-bold bg-red-500 text-white rounded-full">
                {supplierOrdersPending}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Dashboard */}
      {tab === 'dashboard' && (
        <div className="space-y-6">
          {/* Top stat cards — 2 rows of 4 */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard label="משתמשים" value={stats?.total_users} icon={Users} color="blue" />
            <StatCard label="הזמנות" value={stats?.total_orders} icon={ShoppingBag} color="purple" />
            <StatCard label="הזמנות פתוחות" value={stats?.pending_orders} icon={Clock} color="orange" />
            <StatCard label="מוצרים פעילים" value={stats?.total_parts?.toLocaleString('he-IL')} icon={Package} color="brand" />
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard
              label="הכנסות נטו (₪)"
              value={stats?.total_revenue != null ? `₪${Number(stats.total_revenue).toLocaleString('he-IL', {minimumFractionDigits: 0})}` : null}
              icon={DollarSign} color="green"
              sub="לאחר החזרות"
            />
            <StatCard
              label="רווח גולמי (₪)"
              value={stats?.profit_total != null ? `₪${Number(stats.profit_total).toLocaleString('he-IL', {minimumFractionDigits: 0})}` : null}
              icon={TrendingUp} color="teal"
              sub={stats?.margin_pct != null ? `מרווח ${stats.margin_pct}%` : null}
            />
            <StatCard
              label="עלות ספקים (₪)"
              value={stats?.cost_total != null ? `₪${Number(stats.cost_total).toLocaleString('he-IL', {minimumFractionDigits: 0})}` : null}
              icon={TrendingDown} color="purple"
              sub="עלות רכש נטו"
            />
            <StatCard
              label="הזמנה ממוצעת (₪)"
              value={stats?.avg_order_value != null ? `₪${Number(stats.avg_order_value).toLocaleString('he-IL', {minimumFractionDigits: 0})}` : null}
              icon={Percent} color="orange"
              sub="כולל מע״מ + משלוח"
            />
          </div>

          {/* Revenue breakdown row */}
          {stats && (
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
              <div className="card p-4 border-l-4 border-green-400">
                <p className="text-xs text-gray-500 mb-1">הכנסות ברוטו</p>
                <p className="text-xl font-bold text-green-600">₪{Number(stats.gross_revenue || 0).toLocaleString('he-IL', {minimumFractionDigits: 2})}</p>
              </div>
              <div className="card p-4 border-l-4 border-red-300">
                <p className="text-xs text-gray-500 mb-1">החזרות כספיות</p>
                <p className="text-xl font-bold text-red-500">{stats.refunds_total > 0 ? `-₪${Number(stats.refunds_total).toLocaleString('he-IL', {minimumFractionDigits: 2})}` : '₪0'}</p>
              </div>
              <div className="card p-4 border-l-4 border-brand-500">
                <p className="text-xs text-gray-500 mb-1">הכנסות נטו</p>
                <p className="text-xl font-bold text-brand-600">₪{Number(stats.total_revenue || 0).toLocaleString('he-IL', {minimumFractionDigits: 2})}</p>
              </div>
              <div className="card p-4 border-l-4 border-teal-400">
                <p className="text-xs text-gray-500 mb-1">רווח גולמי (מרווח 45% על עלות)</p>
                <p className="text-xl font-bold text-teal-600">₪{Number(stats.profit_total || 0).toLocaleString('he-IL', {minimumFractionDigits: 2})}</p>
                <p className="text-xs text-gray-400 mt-1">= {stats.margin_pct}% מעל עלות הספק</p>
              </div>
            </div>
          )}
          <div className="card p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-bold text-gray-900 flex items-center gap-2"><BarChart2 className="w-5 h-5 text-brand-600" /> הכנסות יומיות</h3>
              <button onClick={loadDashboard} className="btn-ghost text-sm flex items-center gap-1">
                <RefreshCw className="w-4 h-4" /> רענן
              </button>
            </div>
            {salesData.length === 0 ? (
              <p className="text-sm text-gray-400 text-center py-6">אין נתוני מכירות עדיין</p>
            ) : (() => {
              const maxRev = Math.max(...salesData.map((d) => d.revenue || 0), 1)
              const totalOrders = salesData.reduce((a, d) => a + (d.orders || 0), 0)
              const totalRev = salesData.reduce((a, d) => a + (d.revenue || 0), 0)
              return (
                <div>
                  {/* chart */}
                  <div style={{ height: '140px', display: 'flex', alignItems: 'flex-end', gap: '3px', borderBottom: '1px solid #f3f4f6', overflowX: 'auto', paddingBottom: '2px' }}>
                    {salesData.map((d, i) => {
                      const pct = Math.max(((d.revenue || 0) / maxRev) * 100, d.orders > 0 ? 4 : 0)
                      return (
                        <div
                          key={i}
                          title={`${d.date} — ₪${(d.revenue || 0).toFixed(0)} · ${d.orders} הזמנות`}
                          style={{
                            flex: '1 1 0',
                            minWidth: '10px',
                            height: `${pct}%`,
                            backgroundColor: '#fb923c',
                            borderRadius: '3px 3px 0 0',
                            cursor: 'pointer',
                            transition: 'background-color 0.15s',
                            position: 'relative',
                          }}
                          onMouseEnter={e => e.currentTarget.style.backgroundColor = '#ea580c'}
                          onMouseLeave={e => e.currentTarget.style.backgroundColor = '#fb923c'}
                        />
                      )
                    })}
                  </div>
                  {/* x-axis labels */}
                  <div className="flex justify-between mt-1 text-[10px] text-gray-400">
                    <span>{salesData[0]?.date?.slice(5)}</span>
                    <span>{salesData[salesData.length - 1]?.date?.slice(5)}</span>
                  </div>
                  {/* summary */}
                  <div className="mt-3 flex gap-6 text-xs text-gray-500">
                    <span>סה״כ הזמנות: <strong className="text-gray-700">{totalOrders}</strong></span>
                    <span>סה״כ הכנסות: <strong className="text-gray-700">₪{totalRev.toLocaleString('he-IL', {minimumFractionDigits: 2})}</strong></span>
                    <span>ממוצע יומי: <strong className="text-gray-700">₪{(totalRev / salesData.length).toLocaleString('he-IL', {minimumFractionDigits: 0})}</strong></span>
                  </div>
                </div>
              )
            })()}
          </div>

          {/* Orders by status */}
          {stats?.orders_by_status && Object.keys(stats.orders_by_status).length > 0 && (() => {
            const STATUS_LABELS = {
              pending_payment: { label: 'ממתין תשלום', color: 'bg-yellow-400' },
              paid:            { label: 'שולם',           color: 'bg-blue-400' },
              processing:      { label: 'בעיבוד',          color: 'bg-purple-400' },
              supplier_ordered:{ label: 'הוזמן לספק',    color: 'bg-indigo-400' },
              shipped:         { label: 'נשלח',           color: 'bg-cyan-400' },
              delivered:       { label: 'סופק',           color: 'bg-green-500' },
              cancelled:       { label: 'בוטל',           color: 'bg-gray-300' },
              refunded:        { label: 'הוחזר',           color: 'bg-red-400' },
            }
            const entries = Object.entries(stats.orders_by_status).sort((a, b) => b[1] - a[1])
            const total = entries.reduce((s, [, c]) => s + c, 0)
            return (
              <div className="card p-6">
                <h3 className="font-bold text-gray-900 flex items-center gap-2 mb-4">
                  <ShoppingBag className="w-5 h-5 text-brand-600" /> סטטוס הזמנות
                </h3>
                {/* Stacked bar */}
                <div className="flex h-6 rounded-full overflow-hidden mb-4">
                  {entries.map(([status, count]) => (
                    <div
                      key={status}
                      title={`${STATUS_LABELS[status]?.label || status}: ${count}`}
                      className={`${STATUS_LABELS[status]?.color || 'bg-gray-200'} transition-all`}
                      style={{ width: `${(count / total) * 100}%` }}
                    />
                  ))}
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {entries.map(([status, count]) => (
                    <div key={status} className="flex items-center gap-2">
                      <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${STATUS_LABELS[status]?.color || 'bg-gray-300'}`} />
                      <span className="text-xs text-gray-600 truncate">{STATUS_LABELS[status]?.label || status}</span>
                      <span className="text-xs font-bold text-gray-800 mr-auto">{count}</span>
                    </div>
                  ))}
                </div>
              </div>
            )
          })()}

          {/* Category distribution */}
          {categoryStats.length > 0 && (
            <div className="card p-6">
              <h3 className="font-bold text-gray-900 flex items-center gap-2 mb-4">
                <Package className="w-5 h-5 text-brand-600" /> התפלגות קטגוריות ({categoryStats.length})
              </h3>
              <div className="space-y-2">
                {(() => {
                  const maxCount = Math.max(...categoryStats.map(([, c]) => c), 1)
                  return categoryStats.map(([cat, count]) => (
                    <div key={cat} className="flex items-center gap-3">
                      <span className="text-xs text-gray-600 w-36 shrink-0 truncate text-right">{cat}</span>
                      <div className="flex-1 bg-gray-100 rounded-full h-2.5 overflow-hidden">
                        <div
                          className="h-full bg-brand-400 rounded-full transition-all"
                          style={{ width: `${(count / maxCount) * 100}%` }}
                        />
                      </div>
                      <span className="text-xs text-gray-500 w-16 shrink-0 text-left font-mono">{count.toLocaleString()}</span>
                    </div>
                  ))
                })()}
              </div>
              <p className="text-xs text-gray-400 mt-3">
                סה״כ: {categoryStats.reduce((s, [, c]) => s + c, 0).toLocaleString()} חלקים פעילים
              </p>
            </div>
          )}
        </div>
      )}

      {/* Users */}
      {tab === 'users' && (() => {
        const filteredUsers = users.filter((u) => {
          if (userFilters.name && !((u.full_name || '').toLowerCase().includes(userFilters.name.toLowerCase()))) return false
          if (userFilters.email && !(u.email || '').toLowerCase().includes(userFilters.email.toLowerCase())) return false
          if (userFilters.verified === 'yes' && !u.is_verified) return false
          if (userFilters.verified === 'no' && u.is_verified) return false
          if (userFilters.role === 'admin' && !u.is_admin) return false
          if (userFilters.role === 'customer' && u.is_admin) return false
          if (userFilters.status === 'active' && u.is_active === false) return false
          if (userFilters.status === 'blocked' && u.is_active !== false) return false
          if (userFilters.date) {
            const d = new Date(u.created_at)
            const f = new Date(userFilters.date)
            if (d.getFullYear() !== f.getFullYear() || d.getMonth() !== f.getMonth() || d.getDate() !== f.getDate() || d.getHours() !== f.getHours()) return false
          }
          return true
        })
        const uf = userFilters
        const setUF = (k, v) => setUserFilters((f) => ({ ...f, [k]: v }))
        const anyFilter = Object.values(uf).some(Boolean)
        return (
        <div className="card overflow-hidden">
          <div className="p-4 border-b border-gray-100 flex items-center justify-between">
            <h3 className="font-bold text-gray-900">
              משתמשים{anyFilter ? ` (${filteredUsers.length}/${users.length})` : ` (${users.length})`}
            </h3>
            <div className="flex items-center gap-2">
              {anyFilter && (
                <button onClick={() => setUserFilters({ name: '', email: '', verified: '', role: '', status: '', date: '' })} className="text-xs text-gray-400 hover:text-red-500 transition-colors flex items-center gap-1">
                  <X className="w-3 h-3" /> נקה סינון
                </button>
              )}
              <button onClick={() => setShowCreateModal(true)} className="btn-primary text-sm flex items-center gap-1.5 py-1.5 px-3">
                <PlusCircle className="w-4 h-4" />
                משתמש חדש
              </button>
              <button onClick={loadUsers} className="btn-ghost text-sm flex items-center gap-1"><RefreshCw className="w-4 h-4" /></button>
            </div>
          </div>
          {loading ? <div className="flex justify-center p-8"><Loader2 className="w-6 h-6 animate-spin text-brand-600" /></div> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">שם</th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">אימייל</th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">מאומת</th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">תפקיד</th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">סטטוס</th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">תאריך הצטרפות</th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">פעולות</th>
                  </tr>
                  {/* Filter row */}
                  <tr className="border-t border-gray-100">
                    <th className="px-2 py-2">
                      <input className="w-full text-xs border border-gray-200 rounded-lg px-2 py-1 bg-white focus:outline-none focus:border-brand-400" placeholder="חיפוש שם..." value={uf.name} onChange={(e) => setUF('name', e.target.value)} />
                    </th>
                    <th className="px-2 py-2">
                      <input className="w-full text-xs border border-gray-200 rounded-lg px-2 py-1 bg-white focus:outline-none focus:border-brand-400" placeholder="חיפוש אימייל..." value={uf.email} onChange={(e) => setUF('email', e.target.value)} dir="ltr" />
                    </th>
                    <th className="px-2 py-2">
                      <select className="w-full text-xs border border-gray-200 rounded-lg px-2 py-1 bg-white focus:outline-none" value={uf.verified} onChange={(e) => setUF('verified', e.target.value)}>
                        <option value="">הכל</option>
                        <option value="yes">מאומת</option>
                        <option value="no">לא מאומת</option>
                      </select>
                    </th>
                    <th className="px-2 py-2">
                      <select className="w-full text-xs border border-gray-200 rounded-lg px-2 py-1 bg-white focus:outline-none" value={uf.role} onChange={(e) => setUF('role', e.target.value)}>
                        <option value="">הכל</option>
                        <option value="admin">אדמין</option>
                        <option value="customer">משתמש</option>
                      </select>
                    </th>
                    <th className="px-2 py-2">
                      <select className="w-full text-xs border border-gray-200 rounded-lg px-2 py-1 bg-white focus:outline-none" value={uf.status} onChange={(e) => setUF('status', e.target.value)}>
                        <option value="">הכל</option>
                        <option value="active">פעיל</option>
                        <option value="blocked">חסום</option>
                      </select>
                    </th>
                    <th className="px-2 py-2">
                      <input type="datetime-local" className="w-full text-xs border border-gray-200 rounded-lg px-1.5 py-1 bg-white focus:outline-none focus:border-brand-400" title="תאריך ושעה" value={uf.date} onChange={(e) => setUF('date', e.target.value)} />
                    </th>
                    <th className="px-2 py-2" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {filteredUsers.length === 0 ? (
                    <tr><td colSpan={7} className="text-center text-gray-400 py-8 text-sm">לא נמצאו משתמשים</td></tr>
                  ) : filteredUsers.map((u) => (
                    <tr key={u.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3 font-medium text-gray-900">{u.full_name || '—'}</td>
                      <td className="px-4 py-3 text-gray-600" dir="ltr">{u.email}</td>
                      <td className="px-4 py-3">
                        <span className={`badge ${u.is_verified ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>{u.is_verified ? '✓' : '–'}</span>
                      </td>
                      <td className="px-4 py-3">
                        <button
                          onClick={() => toggleAdminRole(u)}
                          title={u.is_admin ? 'הסר הרשאת אדמין' : 'הפוך לאדמין'}
                          className={`badge cursor-pointer hover:opacity-80 transition-opacity ${u.is_admin ? 'bg-purple-100 text-purple-700' : 'bg-gray-100 text-gray-600'}`}
                        >
                          {u.is_admin ? '👑 אדמין' : 'משתמש'}
                        </button>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`badge ${u.is_active !== false ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-600'}`}>
                          {u.is_active !== false ? 'פעיל' : 'חסום'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap">
                        {u.created_at ? (
                          <>
                            <div>{new Date(u.created_at).toLocaleDateString('he-IL')}</div>
                            <div className="text-gray-400">{new Date(u.created_at).toLocaleTimeString('he-IL', { hour: '2-digit', minute: '2-digit' })}</div>
                          </>
                        ) : '—'}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <button onClick={() => openEditUser(u)} title="עדכן פרטים" className="p-1.5 rounded hover:bg-blue-50 text-blue-500 hover:text-blue-700 transition-colors"><Pencil className="w-4 h-4" /></button>
                          <button onClick={() => toggleUser(u.id, u.is_active !== false)} title={u.is_active !== false ? 'חסום משתמש' : 'בטל חסימה'} className={`p-1.5 rounded transition-colors ${u.is_active !== false ? 'hover:bg-orange-50 text-orange-400 hover:text-orange-600' : 'hover:bg-green-50 text-green-500 hover:text-green-700'}`}>
                            {u.is_active !== false ? <Ban className="w-4 h-4" /> : <ShieldCheck className="w-4 h-4" />}
                          </button>
                          <button onClick={() => deleteUser(u.id, u.full_name || u.email)} title="מחק משתמש" className="p-1.5 rounded hover:bg-red-50 text-red-400 hover:text-red-600 transition-colors"><Trash2 className="w-4 h-4" /></button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        )
      })()}

      {/* Orders */}
      {tab === 'orders' && (
        <div className="card overflow-hidden">
          <div className="p-4 border-b border-gray-100 flex flex-wrap items-center justify-between gap-3">
            <h3 className="font-bold text-gray-900">כל ההזמנות ({orders.length})</h3>
            <div className="flex items-center gap-2 flex-wrap">
              <select
                className="input-field text-sm py-1.5 w-44"
                value={statusFilter}
                onChange={(e) => {
                  setStatusFilter(e.target.value)
                  loadOrders(e.target.value)
                }}
              >
                <option value="">כל הסטטוסים</option>
                {Object.entries(STATUS_HE).map(([k, v]) => (
                  <option key={k} value={k}>{v.label}</option>
                ))}
              </select>
              <button onClick={() => loadOrders(statusFilter)} className="btn-ghost text-sm flex items-center gap-1">
                <RefreshCw className="w-4 h-4" />
              </button>
            </div>
          </div>
          {loading ? <div className="flex justify-center p-8"><Loader2 className="w-6 h-6 animate-spin text-brand-600" /></div> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    {['מס׳ הזמנה', 'לקוח', 'סטטוס', 'עדכן סטטוס', 'סכום', 'תאריך'].map((h) => (
                      <th key={h} className="px-3 py-3 text-right text-xs font-medium text-gray-500 whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {orders.length === 0 && (
                    <tr><td colSpan={6} className="text-center py-8 text-gray-400">אין הזמנות</td></tr>
                  )}
                  {orders.map((o) => {
                    const st = STATUS_HE[o.status] || { label: o.status, cls: 'bg-gray-100 text-gray-700' }
                    return (
                      <tr key={o.id} className="hover:bg-gray-50">
                        <td className="px-3 py-3 font-medium text-gray-900 font-mono text-xs" dir="ltr">{o.order_number}</td>
                        <td className="px-3 py-3">
                          <div className="text-xs">
                            <p className="font-medium text-gray-800">{o.user_name}</p>
                            <p className="text-gray-400" dir="ltr">{o.user_email}</p>
                          </div>
                        </td>
                        <td className="px-3 py-3">
                          <span className={`badge text-xs ${st.cls}`}>{st.label}</span>
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex items-center gap-1">
                            <select
                              defaultValue={o.status}
                              disabled={updatingStatus === o.id}
                              onChange={(e) => updateOrderStatus(o.id, e.target.value)}
                              className="text-xs border border-gray-200 rounded-lg px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-brand-400"
                            >
                              {Object.entries(STATUS_HE).map(([k, v]) => (
                                <option key={k} value={k}>{v.label}</option>
                              ))}
                            </select>
                            {updatingStatus === o.id && <Loader2 className="w-3 h-3 animate-spin text-brand-600" />}
                          </div>
                        </td>
                        <td className="px-3 py-3 font-semibold text-brand-600 whitespace-nowrap">₪{Number(o.total).toFixed(2)}</td>
                        <td className="px-3 py-3 text-gray-500 text-xs whitespace-nowrap">{o.created_at ? new Date(o.created_at).toLocaleDateString('he-IL') : ''}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Suppliers */}
      {tab === 'suppliers' && (
        <div className="space-y-6">

          {/* Supplier list */}
          <div className="card overflow-hidden">
            <div className="p-4 border-b border-gray-100 flex items-center justify-between gap-3 flex-wrap">
              <div className="flex items-center gap-2">
                <div className="p-1.5 rounded-lg bg-brand-50">
                  <Zap className="w-4 h-4 text-brand-600" />
                </div>
                <div>
                  <h3 className="font-bold text-gray-900">ספקים ({suppliers.length})</h3>
                  {syncStatus && (
                    <p className="text-xs text-gray-400 mt-0.5 leading-tight">
                      {syncStatus.last_sync
                        ? <>עדכון: {new Date(syncStatus.last_sync).toLocaleString('he-IL')} · הבא בעוד {syncStatus.next_sync_in_h}h</>
                        : 'טרם סונכרן'}
                    </p>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={triggerSync}
                  disabled={syncing}
                  className="btn-primary text-sm flex items-center gap-1.5 py-1.5 px-3"
                >
                  {syncing
                    ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> מסנכרן...</>
                    : <><RefreshCw className="w-3.5 h-3.5" /> סנכרן מחירים</>}
                </button>
                <button onClick={() => setShowCreateSupplier(true)} className="btn-primary text-sm flex items-center gap-1.5 py-1.5 px-3">
                  <PlusCircle className="w-4 h-4" />ספק חדש
                </button>
                <button onClick={loadSuppliers} className="btn-ghost text-sm flex items-center gap-1"><RefreshCw className="w-4 h-4" /></button>
              </div>
            </div>
            {/* Filter bar */}
            <div className="px-4 py-2 border-b border-gray-100 bg-gray-50">
              <div className="flex gap-4 items-center">
                <input
                  className="input-field text-sm py-1.5 w-1/4"
                  placeholder="שם ספק..."
                  value={supplierFilters.name}
                  onChange={(e) => setSupplierFilters((f) => ({ ...f, name: e.target.value }))}
                />
                <input
                  className="input-field text-sm py-1.5 w-1/4"
                  placeholder="מדינה..."
                  value={supplierFilters.country}
                  onChange={(e) => setSupplierFilters((f) => ({ ...f, country: e.target.value }))}
                  dir="ltr"
                />
                <select
                  className="input-field text-sm py-1.5 w-1/4 text-center"
                  value={supplierFilters.status}
                  onChange={(e) => setSupplierFilters((f) => ({ ...f, status: e.target.value }))}
                >
                  <option value="">כל הספקים</option>
                  <option value="active">פעילים</option>
                  <option value="inactive">לא פעילים</option>
                </select>
                {Object.values(supplierFilters).some(Boolean) && (
                  <button onClick={() => setSupplierFilters({ name: '', country: '', status: '' })} className="text-gray-400 hover:text-red-500 shrink-0">
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </div>
            {loading ? <div className="flex justify-center p-8"><Loader2 className="w-6 h-6 animate-spin text-brand-600" /></div> : (() => {
              const filteredSuppliers = suppliers.filter((s) => {
                if (supplierFilters.name && !(s.name || '').toLowerCase().includes(supplierFilters.name.toLowerCase())) return false
                if (supplierFilters.country && !(s.country || '').toLowerCase().includes(supplierFilters.country.toLowerCase())) return false
                if (supplierFilters.status === 'active' && !s.is_active) return false
                if (supplierFilters.status === 'inactive' && s.is_active) return false
                return true
              })
              return (
                <div className="divide-y divide-gray-100">
                  {filteredSuppliers.length === 0 && <p className="text-center text-gray-400 text-sm py-8">לא נמצאו ספקים</p>}
                  {filteredSuppliers.map((s, idx) => {
                    const isExpanded = expandedSupplier?.id === s.id
                    return (
                      <div key={s.id}>
                        {/* Row */}
                        <div className="flex items-center justify-between p-4 hover:bg-gray-50 cursor-pointer" onClick={() => setExpandedSupplier(isExpanded ? null : s)}>
                          <div className="flex items-center gap-3">
                            <span className="text-xs font-mono text-gray-400 w-5 text-center select-none shrink-0">{idx + 1}</span>
                            <div className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 ${s.is_active ? 'bg-brand-50' : 'bg-gray-100'}`}>
                              <Truck className={`w-5 h-5 ${s.is_active ? 'text-brand-600' : 'text-gray-400'}`} />
                            </div>
                            <div>
                              <div className="flex items-center gap-2">
                                <p className="font-medium text-gray-900">{s.name}</p>
                                <ChevronDown className={`w-3.5 h-3.5 text-gray-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
                                {!s.is_active && <span className="badge bg-red-100 text-red-600 text-[10px]">בוטל</span>}
                                {s.supports_express && <span className="badge bg-blue-100 text-blue-600 text-[10px]">אקספרס</span>}
                              </div>
                              <p className="text-xs text-gray-400">עדיפות {s.priority} · ★ {Number(s.reliability_score).toFixed(1)}</p>
                            </div>
                          </div>
                          {/* Country badge */}
                          {s.country && (
                            <div className="hidden sm:flex items-center gap-1.5 px-2 py-1 rounded-lg bg-gray-50 border border-gray-200 text-gray-600 justify-center mx-2">
                              {countryFlag(s.country)}
                              <span className="text-[11px] font-bold tracking-wide">{countryISO(s.country)}</span>
                            </div>
                          )}
                          <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                            <button onClick={() => syncSupplier(s.id)} className="btn-secondary text-xs px-3 py-1.5">סנכרן</button>
                            <button onClick={() => { openEditSupplier(s); }} title="ערוך" className="p-1.5 rounded hover:bg-blue-50 text-blue-500 hover:text-blue-700 transition-colors"><Pencil className="w-4 h-4" /></button>
                            <button onClick={() => deleteSupplierById(s.id, s.name)} title="מחק" className="p-1.5 rounded hover:bg-red-50 text-red-400 hover:text-red-600 transition-colors"><Trash2 className="w-4 h-4" /></button>
                            <button onClick={() => toggleSupplier(s.id, s.is_active)}>
                              {s.is_active ? <ToggleRight className="w-6 h-6 text-green-500" /> : <ToggleLeft className="w-6 h-6 text-gray-400" />}
                            </button>
                          </div>
                        </div>
                        {/* Expanded detail */}
                        {isExpanded && (
                          <div className="bg-gray-50 border-t border-gray-100 px-6 py-4">
                            {/* Header strip with flag */}
                            {s.country && (
                              <div className="flex items-center gap-3 mb-4 pb-3 border-b border-gray-200">
                                <img
                                  src={`https://flagcdn.com/32x24/${(COUNTRY_ISO[s.country.toLowerCase().trim()] || '').toLowerCase()}.png`}
                                  alt={s.country}
                                  className="rounded shadow-sm"
                                  style={{ width: 32, height: 24 }}
                                  onError={(e) => { e.target.style.display = 'none' }}
                                />
                                <div>
                                  <p className="font-semibold text-gray-800 text-sm">{s.name}</p>
                                  <p className="text-xs text-gray-400">{s.country} · {countryISO(s.country)}</p>
                                </div>
                                {s.priority > 0 && <span className="ml-auto text-xs bg-brand-50 text-brand-700 border border-brand-200 px-2 py-0.5 rounded-full">עדיפות {s.priority}</span>}
                              </div>
                            )}
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
                              {/* Contact */}
                              <div className="space-y-2">
                                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">פרטי קשר</p>
                                {s.contact_email && <div className="flex items-center gap-2 text-gray-700"><Mail className="w-3.5 h-3.5 text-gray-400" />{s.contact_email}</div>}
                                {s.contact_phone && <div className="flex items-center gap-2 text-gray-700"><Phone className="w-3.5 h-3.5 text-gray-400" />{s.contact_phone}</div>}
                                {s.website && <div className="flex items-center gap-2"><Globe className="w-3.5 h-3.5 text-gray-400" /><a href={s.website} target="_blank" rel="noreferrer" className="text-brand-600 hover:underline text-xs" dir="ltr">{s.website}</a></div>}
                                {!s.contact_email && !s.contact_phone && !s.website && <p className="text-gray-400 text-xs">לא הוזן פרטי קשר</p>}
                              </div>
                              {/* API */}
                              <div className="space-y-2">
                                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">API</p>
                                {s.api_endpoint ? <div className="flex items-center gap-2"><Key className="w-3.5 h-3.5 text-gray-400" /><span className="font-mono text-xs text-gray-600 break-all" dir="ltr">{s.api_endpoint}</span></div> : <p className="text-gray-400 text-xs">לא נקבע</p>}
                              </div>
                              {/* Shipping */}
                              <div className="space-y-2">
                                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">משלוח</p>
                                {s.supports_express && <div className="flex items-center gap-2"><Zap className="w-3.5 h-3.5 text-blue-400" /><span className="text-gray-700">{s.express_carrier || 'שירות אקספרס'}{s.express_base_cost_usd ? ` · $${s.express_base_cost_usd}` : ''}</span></div>}
                                {s.avg_delivery_days_actual && <div className="flex items-center gap-2"><Clock className="w-3.5 h-3.5 text-gray-400" /><span className="text-gray-700">זמן משלוח ממוצע: {s.avg_delivery_days_actual} ימים</span></div>}
                                {Object.keys(s.shipping_info || {}).length > 0 && <pre className="text-xs bg-white rounded p-2 border border-gray-200 max-h-20 overflow-auto">{JSON.stringify(s.shipping_info, null, 2)}</pre>}
                                {!s.supports_express && !s.avg_delivery_days_actual && Object.keys(s.shipping_info || {}).length === 0 && <p className="text-gray-400 text-xs">לא הוזן פרטי משלוח</p>}
                              </div>
                              {/* Stats */}
                              <div className="space-y-2">
                                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">נתונים</p>
                                <div className="flex items-center gap-2"><Star className="w-3.5 h-3.5 text-amber-400" /><span className="text-gray-700">אמינות: {Number(s.reliability_score).toFixed(1)}/10</span></div>
                                <div className="flex items-center gap-2"><MapPin className="w-3.5 h-3.5 text-gray-400" /><span className="text-gray-700 flex items-center gap-1.5">{countryFlag(s.country)}<span>{s.country}</span><span className="text-xs text-gray-400 font-bold">{countryISO(s.country)}</span></span></div>
                                {s.created_at && <p className="text-xs text-gray-400">נוצר: {new Date(s.created_at).toLocaleDateString('he-IL')}</p>}
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )
            })()}
          </div>
          {/* Supplier purchase tasks */}
          <div className="card overflow-hidden">
            <div className="p-4 border-b border-gray-100 flex items-center justify-between">
              <h3 className="font-bold text-gray-900 flex items-center gap-2">
                <ShoppingCart className="w-5 h-5 text-brand-600" />
                יומן הזמנות ספקים (סוכן אוטומטי)
                {supplierOrdersPending > 0 && (
                  <span className="badge bg-amber-100 text-amber-700">{supplierOrdersPending} דורשות בדיקה</span>
                )}
              </h3>
              <button onClick={loadSupplierOrders} className="btn-ghost text-sm flex items-center gap-1"><RefreshCw className="w-4 h-4" /></button>
            </div>
            {/* Order log filter bar */}
            <div className="px-4 py-2 border-b border-gray-100 bg-gray-50">
              <div className="flex gap-4 items-center">
                <input
                  className="input-field text-sm py-1.5 w-1/4"
                  placeholder="כותרת / שם ספק..."
                  value={orderLogFilters.search}
                  onChange={(e) => setOrderLogFilters((f) => ({ ...f, search: e.target.value }))}
                />
                <select
                  className="input-field text-sm py-1.5 w-1/4 text-center"
                  value={orderLogFilters.status}
                  onChange={(e) => setOrderLogFilters((f) => ({ ...f, status: e.target.value }))}
                >
                  <option value="">כל הסטטוסים</option>
                  <option value="pending">דורש בדיקה</option>
                  <option value="done">בוצע</option>
                </select>
                {Object.values(orderLogFilters).some(Boolean) && (
                  <button onClick={() => setOrderLogFilters({ search: '', status: '' })} className="text-gray-400 hover:text-red-500 shrink-0">
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </div>
            {supplierOrders.length === 0 ? (
              <div className="text-center py-8 text-gray-400">
                <CheckCircle className="w-8 h-8 mx-auto mb-2 text-green-400" />
                <p className="text-sm">אין רשומות הזמנה עדיין</p>
              </div>
            ) : (() => {
              const filteredOrders = supplierOrders.filter((so) => {
                if (orderLogFilters.status === 'pending' && so.is_done) return false
                if (orderLogFilters.status === 'done' && !so.is_done) return false
                if (orderLogFilters.search) {
                  const q = orderLogFilters.search.toLowerCase()
                  const d = so.data || {}
                  if (!(so.title || '').toLowerCase().includes(q) &&
                      !(d.supplier_name || '').toLowerCase().includes(q)) return false
                }
                return true
              })
              return filteredOrders.length === 0
                ? <p className="text-center text-gray-400 text-sm py-8">לא נמצאו רשומות</p>
                : (
              <div className="divide-y divide-gray-100">
                {filteredOrders.map((so) => {
                  const d = so.data || {}
                  const isMissing = !d.supplier_name
                  return (
                    <div key={so.id} className={`p-4 ${!so.is_done ? 'bg-amber-50 border-l-4 border-amber-400' : 'bg-white'}`}>
                      <div className="flex items-start gap-3">
                        <div className="mt-0.5 flex-shrink-0">
                          {so.is_done
                            ? <span className="inline-flex items-center gap-1 text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full"><CheckCircle className="w-3.5 h-3.5" /> בוצע ע״י הסוכן</span>
                            : <span className="inline-flex items-center gap-1 text-xs bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full"><AlertCircle className="w-3.5 h-3.5 animate-pulse" /> דורש בדיקה ידנית</span>
                          }
                        </div>
                        <div className="flex-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            <p className="font-semibold text-gray-900 text-sm">{so.title}</p>
                            {d.supplier_name && (() => {
                              let sup = suppliers.find((s) => s.name?.toLowerCase() === d.supplier_name?.toLowerCase())
                              if (!sup) {
                                const m = d.supplier_name?.match(/(\d+)/)
                                if (m) {
                                  const sorted = [...suppliers].sort((a, b) => (a.priority || 99) - (b.priority || 99))
                                  sup = sorted[parseInt(m[1], 10) - 1]
                                }
                              }
                              const iso = sup?.country ? COUNTRY_ISO[(sup.country || '').toLowerCase().trim()] : null
                              if (!iso) return null
                              return (
                                <img src={`https://flagcdn.com/16x12/${iso.toLowerCase()}.png`} alt={iso} className="rounded-sm inline-block" style={{width:16,height:12}} />
                              )
                            })()}
                          </div>
                          <p className="text-xs text-gray-400 mt-0.5">
                            {new Date(so.created_at).toLocaleString('he-IL')}
                            {so.done_at && ` · טופל ${new Date(so.done_at).toLocaleString('he-IL')}`}
                          </p>
                          {/* Tracking badge (agent-assigned) */}
                          {d.tracking_number && (
                            <div className="mt-2 flex items-center gap-2">
                              <Truck className="w-4 h-4 text-cyan-600" />
                              <span className="text-xs font-mono bg-cyan-50 text-cyan-700 border border-cyan-200 px-2 py-0.5 rounded">{d.carrier} · {d.tracking_number}</span>
                              {(() => {
                                const n = d.tracking_number || ''
                                const rawUrl = d.tracking_url
                                const url = /^1Z[A-Z0-9]{16}$/i.test(n)
                                  ? `https://www.ups.com/track?tracknum=${n}&requester=ST/trackdetails`
                                  : /^\d{12}$/.test(n)
                                  ? `https://www.fedex.com/fedextrack/?trknbr=${n}`
                                  : /^\d{10}$/.test(n)
                                  ? `https://www.dhl.com/en/express/tracking.html?AWB=${n}`
                                  : `https://parcelsapp.com/en/tracking/${n}`
                                return (
                                  <a href={url} target="_blank" rel="noopener noreferrer" className="text-xs text-brand-600 hover:underline flex items-center gap-0.5">
                                    <ExternalLink className="w-3 h-3" /> מעקב
                                  </a>
                                )
                              })()}
                            </div>
                          )}
                            {/* Items table */}
                            {d.items && d.items.length > 0 && (
                              <div className="mt-3 rounded-lg border border-gray-100 overflow-hidden">
                                <table className="w-full text-xs">
                                  <thead className="bg-gray-50">
                                    <tr>
                                      {['חלק', 'SKU ספק', 'כמות', 'עלות יחידא', 'עלות ישירה', 'סה״כ'].map((h) => (
                                        <th key={h} className="px-3 py-2 text-right font-medium text-gray-500">{h}</th>
                                      ))}
                                    </tr>
                                  </thead>
                                  <tbody className="divide-y divide-gray-50">
                                    {d.items.map((it, i) => (
                                      <tr key={i} className="hover:bg-gray-50">
                                        <td className="px-3 py-2 font-medium text-gray-800">{it.part_name}</td>
                                        <td className="px-3 py-2 text-gray-500 font-mono">{it.supplier_sku || it.part_sku || '—'}</td>
                                        <td className="px-3 py-2 text-center font-bold">{it.quantity}</td>
                                        <td className="px-3 py-2 text-gray-600">₪{it.unit_cost_ils?.toFixed(2)}</td>
                                        <td className="px-3 py-2 text-gray-600">₪{it.shipping_ils?.toFixed(2)}</td>
                                        <td className="px-3 py-2 font-semibold text-brand-600">₪{it.item_total_ils?.toFixed(2)}</td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                                <div className="px-3 py-2 bg-gray-50 border-t border-gray-100 flex justify-between items-center">
                                  <span className="text-xs text-gray-500">עלות ספק סה״כ:</span>
                                  <span className="font-bold text-brand-700">₪{Number(d.total_cost_ils || 0).toFixed(2)}</span>
                                </div>
                              </div>
                            )}
                          {d.supplier_website && (
                            <a
                              href={d.supplier_website}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center gap-1 text-xs text-brand-600 hover:underline mt-2"
                            >
                              <ExternalLink className="w-3 h-3" /> {d.supplier_name}
                            </a>
                          )}
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
              )
            })()}
          </div>
        </div>
      )}

      {/* Parts Import */}
      {tab === 'parts' && (
        <div className="space-y-4">
          <div className="card p-6">
            <h3 className="font-bold text-gray-900 mb-1 flex items-center gap-2">
              <FileSpreadsheet className="w-5 h-5 text-brand-600" />
              ייבוא קטלוג חלקים מ-Excel
            </h3>
            <p className="text-sm text-gray-400 mb-5">
              הקובץ חייב לכלול עמודות: <span className="font-mono text-xs bg-gray-100 px-1 rounded">sku / pin</span> ו-<span className="font-mono text-xs bg-gray-100 px-1 rounded">name</span> (שם חלק).
              אפשר גם: category, manufacturer, part_type, description, base_price, compatible_vehicles.
            </p>

            {/* Drop zone */}
            <label
              className={`flex flex-col items-center justify-center border-2 border-dashed rounded-xl p-10 cursor-pointer transition-colors ${
                importFile ? 'border-brand-400 bg-brand-50' : 'border-gray-300 hover:border-brand-400 hover:bg-gray-50'
              }`}
            >
              <input
                type="file"
                accept=".xlsx,.xls"
                className="hidden"
                onChange={(e) => { setImportFile(e.target.files[0] || null); setImportResult(null) }}
              />
              {importFile ? (
                <>
                  <FileSpreadsheet className="w-10 h-10 text-brand-500 mb-2" />
                  <p className="text-sm font-medium text-brand-700">{importFile.name}</p>
                  <p className="text-xs text-gray-400 mt-1">{(importFile.size / 1024).toFixed(1)} KB</p>
                  <button
                    type="button"
                    onClick={(e) => { e.preventDefault(); setImportFile(null); setImportResult(null) }}
                    className="mt-3 text-xs text-red-500 hover:underline flex items-center gap-1"
                  >
                    <X className="w-3 h-3" /> הסר קובץ
                  </button>
                </>
              ) : (
                <>
                  <Upload className="w-10 h-10 text-gray-400 mb-2" />
                  <p className="text-sm text-gray-600">גרור קובץ Excel לכאן או לחץ לבחירה</p>
                  <p className="text-xs text-gray-400 mt-1">.xlsx / .xls</p>
                </>
              )}
            </label>

            <button
              disabled={!importFile || importing}
              onClick={async () => {
                if (!importFile) return
                setImporting(true)
                setImportResult(null)
                try {
                  const fd = new FormData()
                  fd.append('file', importFile)
                  const { data } = await api.post('/admin/parts/import', fd)
                  setImportResult({ ok: true, ...data })
                  toast.success(`נוספו ${data.created} חלקים, עודכנו ${data.updated}`)
                  setImportFile(null)
                } catch (err) {
                  const msg = err.response?.data?.detail || 'שגיאה בייבוא'
                  setImportResult({ ok: false, msg })
                  toast.error(msg)
                } finally {
                  setImporting(false)
                }
              }}
              className="btn-primary w-full mt-4 flex items-center justify-center gap-2"
            >
              {importing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
              {importing ? 'מייבא...' : 'התחל ייבוא'}
            </button>

            {importResult && (
              <div className={`mt-4 rounded-xl p-4 text-sm ${
                importResult.ok ? 'bg-green-50 border border-green-200' : 'bg-red-50 border border-red-200'
              }`}>
                {importResult.ok ? (
                  <>
                    <p className="font-semibold text-green-700 mb-2">✅ הייבוא הושלם בהצלחה</p>
                    <div className="grid grid-cols-3 gap-3 text-center">
                      <div className="bg-white rounded-lg p-3">
                        <p className="text-2xl font-bold text-green-600">{importResult.created}</p>
                        <p className="text-xs text-gray-500">חלקים חדשים</p>
                      </div>
                      <div className="bg-white rounded-lg p-3">
                        <p className="text-2xl font-bold text-blue-600">{importResult.updated}</p>
                        <p className="text-xs text-gray-500">עודכנו</p>
                      </div>
                      <div className="bg-white rounded-lg p-3">
                        <p className="text-2xl font-bold text-gray-400">{importResult.skipped}</p>
                        <p className="text-xs text-gray-500">דולגו</p>
                      </div>
                    </div>
                    {importResult.errors?.length > 0 && (
                      <div className="mt-3">
                        <p className="text-xs font-medium text-red-600 mb-1">שגיאות ({importResult.errors.length}):</p>
                        <ul className="text-xs text-red-500 list-disc list-inside space-y-0.5">
                          {importResult.errors.slice(0, 10).map((e, i) => <li key={i}>{e}</li>)}
                        </ul>
                      </div>
                    )}
                  </>
                ) : (
                  <p className="text-red-700">❌ {importResult.msg}</p>
                )}
              </div>
            )}
          </div>

          {/* Format guide */}
          <div className="card p-5">
            <h4 className="text-sm font-semibold text-gray-700 mb-3">מבנה קובץ Excel לדוגמה</h4>
            <div className="overflow-x-auto">
              <table className="w-full text-xs border-collapse">
                <thead>
                  <tr className="bg-gray-100">
                    {['sku / pin', 'name', 'category', 'manufacturer', 'part_type', 'base_price', 'compatible_vehicles'].map(h => (
                      <th key={h} className="text-left p-2 border border-gray-200 font-mono whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    {['BOS-BP-001', 'רפידות בלם קדמיות', 'בלמים', 'Bosch', 'Aftermarket', '185', 'Toyota Corolla 2018, Honda Civic 2019'].map((v, i) => (
                      <td key={i} className="p-2 border border-gray-200 text-gray-600 whitespace-nowrap">{v}</td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Social media */}
      {tab === 'social' && (
        <div className="space-y-4">
          <div className="card p-6">
            <h3 className="font-bold text-gray-900 mb-4">יצירת תוכן AI לרשתות חברתיות</h3>
            <div className="space-y-4">
              <div className="flex gap-3">
                <input className="input-field flex-1" placeholder="נושא הפוסט... (מבצע על בלמים, טיפ לחורף...)" value={genTopic} onChange={(e) => setGenTopic(e.target.value)} />
                <select className="input-field w-40" value={genPlatform} onChange={(e) => setGenPlatform(e.target.value)}>
                  <option value="facebook">Facebook</option>
                  <option value="instagram">Instagram</option>
                  <option value="tiktok">TikTok</option>
                  <option value="whatsapp">WhatsApp</option>
                </select>
                <button onClick={generateSocial} disabled={generating || !genTopic.trim()} className="btn-primary flex items-center gap-2 whitespace-nowrap">
                  {generating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />}
                  צור תוכן
                </button>
              </div>
              {socialContent && (
                <div className="space-y-3">
                  <div className="bg-gray-50 border border-gray-200 rounded-xl p-4">
                    <p className="text-xs text-orange-600 font-medium mb-2">⚠ ממתין לאישורך לפני פרסום</p>
                    <textarea
                      className="w-full bg-transparent text-sm text-gray-800 resize-none outline-none leading-relaxed"
                      rows={6}
                      value={socialContent}
                      onChange={(e) => setSocialContent(e.target.value)}
                    />
                  </div>
                  <div className="flex gap-3">
                    <button onClick={() => { navigator.clipboard.writeText(socialContent); toast.success('הועתק') }} className="btn-secondary text-sm">העתק</button>
                    <button onClick={() => setSocialContent('')} className="btn-ghost text-sm text-red-500">בטל</button>
                  </div>
                </div>
              )}
              {!socialContent && (
                <p className="text-sm text-gray-400 text-center py-4">כל תוכן מופק על ידי AI ודורש אישור מנהל לפני פרסום</p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Agents management */}
      {tab === 'agents' && (
        <div className="space-y-4">
          {/* Header */}
          <div className="card p-4 flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-brand-50"><Bot className="w-5 h-5 text-brand-600" /></div>
              <div>
                <h3 className="font-bold text-gray-900">סוכני AI ({agents.length})</h3>
                {agentsAiStatus && (
                  <p className="text-xs text-gray-400 mt-0.5">
                    {agentsAiStatus.github_token_set
                      ? <span className="text-green-600">✔ GITHUB_TOKEN פעיל — תגובות AI אמיתיות</span>
                      : <span className="text-amber-600">⚠ אין GITHUB_TOKEN — מצב Mock</span>}
                  </p>
                )}
              </div>
            </div>
            <div className="flex gap-2">
              <div className="hidden sm:flex gap-2">
                {[{k:'customer',label:'לקוח',cls:'bg-blue-50 text-blue-600 border-blue-200'},{k:'admin',label:'ניהול',cls:'bg-purple-50 text-purple-600 border-purple-200'},{k:'internal',label:'פנימי',cls:'bg-gray-50 text-gray-600 border-gray-200'}].map(t => (
                  <span key={t.k} className={`text-xs px-2 py-1 rounded-lg border ${t.cls}`}>{t.label}</span>
                ))}
              </div>
              <button onClick={loadAgents} className="btn-ghost text-sm flex items-center gap-1"><RefreshCw className="w-4 h-4" /></button>
            </div>
          </div>

          {/* Test message bar */}
          <div className="card p-4">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">בדיקת סוכן</p>
            <div className="flex gap-2">
              <input
                className="input-field flex-1 text-sm"
                placeholder="הקלד הודעה לבדיקת הסוכן..."
                value={testMessage}
                onChange={(e) => { setTestMessage(e.target.value); setTestResult(null) }}
              />
            </div>
            {testResult && (
              <div className={`mt-3 p-3 rounded-xl text-sm ${testResult.status === 'ok' ? 'bg-green-50 border border-green-200 text-green-800' : 'bg-red-50 border border-red-200 text-red-700'}`}>
                <p className="text-xs font-semibold mb-1 opacity-60">תגובת הסוכן ({testResult.agent})</p>
                <p className="whitespace-pre-wrap">{testResult.response}</p>
              </div>
            )}
          </div>

          {/* Agent cards grid */}
          {(() => {
            const COLOR_BORDER = { gray:'border-l-gray-400', blue:'border-l-blue-400', green:'border-l-green-400', orange:'border-l-orange-400', yellow:'border-l-yellow-400', pink:'border-l-pink-400', red:'border-l-red-400', purple:'border-l-purple-400', indigo:'border-l-indigo-400', teal:'border-l-teal-400' }
            const COLOR_ICON = { gray:'bg-gray-100 text-gray-500', blue:'bg-blue-100 text-blue-600', green:'bg-green-100 text-green-600', orange:'bg-orange-100 text-orange-600', yellow:'bg-yellow-100 text-yellow-600', pink:'bg-pink-100 text-pink-600', red:'bg-red-100 text-red-600', purple:'bg-purple-100 text-purple-600', indigo:'bg-indigo-100 text-indigo-600', teal:'bg-teal-100 text-teal-600' }
            const TYPE_BADGE = { customer:'bg-blue-50 text-blue-700 border-blue-200', admin:'bg-purple-50 text-purple-700 border-purple-200', internal:'bg-gray-50 text-gray-600 border-gray-200' }
            const TYPE_HE = { customer:'לקוח', admin:'ניהול', internal:'פנימי' }
            return (
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
                {agents.map((a) => (
                  <div key={a.name} className={`card p-4 border-l-4 ${COLOR_BORDER[a.color] || 'border-l-gray-300'} flex flex-col gap-3`}>
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-2.5">
                        <div className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 ${COLOR_ICON[a.color] || 'bg-gray-100 text-gray-500'}`}>
                          <Bot className="w-4.5 h-4.5" />
                        </div>
                        <div>
                          <div className="flex items-center gap-1.5">
                            <p className="font-bold text-gray-900 text-sm">{a.persona}</p>
                            <span className={`text-xs px-1.5 py-0.5 rounded-md border ${TYPE_BADGE[a.type] || TYPE_BADGE.internal}`}>{TYPE_HE[a.type] || a.type}</span>
                          </div>
                          <p className="text-xs text-gray-500 mt-0.5">{a.name_he}</p>
                        </div>
                      </div>
                      {a.enabled === false && <span className="text-xs bg-red-100 text-red-600 px-1.5 py-0.5 rounded-md border border-red-200">מושבת</span>}
                    </div>
                    <p className="text-xs text-gray-500 leading-relaxed">{a.description_he || a.description}</p>
                    <div className="flex items-center gap-2 text-xs text-gray-400">
                      <Cpu className="w-3 h-3" />
                      <span dir="ltr">{a.model}</span>
                      <span className="mx-1">·</span>
                      <Sliders className="w-3 h-3" />
                      <span dir="ltr">{a.temperature}</span>
                    </div>
                    {a.capabilities?.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {a.capabilities.slice(0, 3).map((c) => (
                          <span key={c} className="text-xs bg-gray-50 border border-gray-200 text-gray-500 px-1.5 py-0.5 rounded">{c}</span>
                        ))}
                        {a.capabilities.length > 3 && <span className="text-xs text-gray-400">+{a.capabilities.length - 3}</span>}
                      </div>
                    )}
                    <div className="flex gap-2 mt-auto pt-1">
                      <button
                        onClick={() => { setEditAgent(a); setEditAgentForm({ display_name: a.display_name, persona: a.persona, name_he: a.name_he, description: a.description, description_he: a.description_he, model: a.model, temperature: a.temperature, capabilities: (a.capabilities || []).join(', '), enabled: a.enabled !== false }) }}
                        className="flex-1 btn-secondary text-xs flex items-center justify-center gap-1.5 py-1.5"
                      >
                        <Pencil className="w-3 h-3" /> ערוך
                      </button>
                      <button
                        onClick={() => testAgent(a.name)}
                        disabled={!testMessage.trim() || testingAgent === a.name}
                        className="flex-1 btn-secondary text-xs flex items-center justify-center gap-1.5 py-1.5 disabled:opacity-40"
                      >
                        {testingAgent === a.name ? <Loader2 className="w-3 h-3 animate-spin" /> : <MessageSquare className="w-3 h-3" />}
                        בדוק
                      </button>
                      <button
                        onClick={() => {
                          const enabled = a.enabled !== false
                          api.put(`/admin/agents/${a.name}`, { enabled: !enabled })
                            .then(({ data }) => setAgents((prev) => prev.map((x) => x.name === a.name ? { ...x, ...data } : x)))
                          toast.success(enabled ? 'הסוכן הושבת' : 'הסוכן הופעל')
                        }}
                        className={`px-2 py-1.5 rounded-lg border text-xs transition-colors ${
                          a.enabled !== false
                            ? 'bg-green-50 border-green-200 text-green-700 hover:bg-green-100'
                            : 'bg-red-50 border-red-200 text-red-600 hover:bg-red-100'
                        }`}
                        title={a.enabled !== false ? 'השבת סוכן' : 'הפעל סוכן'}
                      >
                        {a.enabled !== false ? <ToggleRight className="w-4 h-4" /> : <ToggleLeft className="w-4 h-4" />}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )
          })()}
        </div>
      )}
    </div>

    {/* Edit Agent Modal */}
    {editAgent && (
      <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setEditAgent(null)}>
        <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg flex flex-col max-h-[90vh]" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center justify-between p-6 border-b border-gray-100">
            <div>
              <h3 className="font-bold text-lg text-gray-900">עריכת סוכן</h3>
              <p className="text-xs text-gray-400 mt-0.5">{editAgent.name}</p>
            </div>
            <button onClick={() => setEditAgent(null)} className="p-1.5 rounded-full hover:bg-gray-100"><X className="w-5 h-5 text-gray-500" /></button>
          </div>
          <div className="overflow-y-auto p-6 space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">שם תפקיד (עברית)</label>
                <input className="input-field w-full text-sm" value={editAgentForm.name_he || ''} onChange={(e) => setEditAgentForm((f) => ({ ...f, name_he: e.target.value }))} />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">שם פרסונה</label>
                <input className="input-field w-full text-sm" dir="ltr" value={editAgentForm.persona || ''} onChange={(e) => setEditAgentForm((f) => ({ ...f, persona: e.target.value }))} />
              </div>
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">תיאור תפקיד</label>
              <textarea className="input-field w-full text-sm" rows={2} value={editAgentForm.description_he || ''} onChange={(e) => setEditAgentForm((f) => ({ ...f, description_he: e.target.value }))} />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">מודל AI</label>
                <select className="input-field w-full text-sm" dir="ltr" value={editAgentForm.model || 'gpt-4o'} onChange={(e) => setEditAgentForm((f) => ({ ...f, model: e.target.value }))}>
                  <option value="gpt-4o">gpt-4o</option>
                  <option value="gpt-4o-mini">gpt-4o-mini</option>
                  <option value="o1-mini">o1-mini</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">טמפרטורה: {editAgentForm.temperature ?? 0.5}</label>
                <input type="range" min="0" max="1" step="0.05" className="w-full mt-2" value={editAgentForm.temperature ?? 0.5} onChange={(e) => setEditAgentForm((f) => ({ ...f, temperature: parseFloat(e.target.value) }))} />
                <div className="flex justify-between text-xs text-gray-400 mt-1"><span>מדויק</span><span>יצירתי</span></div>
              </div>
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">יכולות (מופרדות בפסיק)</label>
              <input className="input-field w-full text-sm" dir="ltr" placeholder="Part search, Price comparison, ..." value={editAgentForm.capabilities || ''} onChange={(e) => setEditAgentForm((f) => ({ ...f, capabilities: e.target.value }))} />
            </div>
            <div className="flex items-center gap-3 p-3 rounded-xl bg-gray-50 border border-gray-200">
              <span className="text-sm text-gray-600">סוכן פעיל</span>
              <button
                onClick={() => setEditAgentForm((f) => ({ ...f, enabled: !f.enabled }))}
                className={`ml-auto p-1 rounded-lg transition-colors ${editAgentForm.enabled ? 'text-green-600' : 'text-gray-400'}`}
              >
                {editAgentForm.enabled ? <ToggleRight className="w-6 h-6" /> : <ToggleLeft className="w-6 h-6" />}
              </button>
            </div>
          </div>
          <div className="flex gap-3 p-6 border-t border-gray-100">
            <button onClick={saveEditAgent} disabled={savingAgent} className="btn-primary flex-1 flex items-center justify-center gap-2">
              {savingAgent ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              שמור שינויים
            </button>
            <button onClick={() => setEditAgent(null)} className="btn-secondary flex-1">ביטול</button>
          </div>
        </div>
      </div>
    )}

    {/* Create Supplier Modal */}
    {showCreateSupplier && (() => {
      const f = createSupplierForm
      const setF = (k, v) => setCreateSupplierForm((x) => ({ ...x, [k]: v }))
      return (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setShowCreateSupplier(false)}>
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg flex flex-col max-h-[90vh]" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-6 border-b border-gray-100">
              <h3 className="font-bold text-lg text-gray-900">ספק חדש</h3>
              <button onClick={() => setShowCreateSupplier(false)} className="p-1.5 rounded-full hover:bg-gray-100"><X className="w-5 h-5 text-gray-500" /></button>
            </div>
            <div className="overflow-y-auto p-6 space-y-5">
              <SupplierFormFields f={f} setF={setF} isCreate />
            </div>
            <div className="flex gap-3 p-6 border-t border-gray-100">
              <button onClick={submitCreateSupplier} className="btn-primary flex-1 flex items-center justify-center gap-2"><PlusCircle className="w-4 h-4" />צור ספק</button>
              <button onClick={() => setShowCreateSupplier(false)} className="btn-secondary flex-1">ביטול</button>
            </div>
          </div>
        </div>
      )
    })()}

    {/* Edit Supplier Modal */}
    {editSupplier && (() => {
      const f = editSupplierForm
      const setF = (k, v) => setEditSupplierForm((x) => ({ ...x, [k]: v }))
      return (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setEditSupplier(null)}>
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg flex flex-col max-h-[90vh]" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-6 border-b border-gray-100">
              <div>
                <h3 className="font-bold text-lg text-gray-900">עריכת ספק</h3>
                <p className="text-xs text-gray-400 mt-0.5">{editSupplier.name}</p>
              </div>
              <button onClick={() => setEditSupplier(null)} className="p-1.5 rounded-full hover:bg-gray-100"><X className="w-5 h-5 text-gray-500" /></button>
            </div>
            <div className="overflow-y-auto p-6 space-y-5">
              <SupplierFormFields f={f} setF={setF} />
            </div>
            <div className="flex gap-3 p-6 border-t border-gray-100">
              <button onClick={saveEditSupplier} className="btn-primary flex-1 flex items-center justify-center gap-2"><Save className="w-4 h-4" />שמור שינויים</button>
              <button onClick={() => setEditSupplier(null)} className="btn-secondary flex-1">ביטול</button>
            </div>
          </div>
        </div>
      )
    })()}

    {/* Create User Modal */}
    {showCreateModal && (
      <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setShowCreateModal(false)}>
        <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg flex flex-col max-h-[90vh]" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center justify-between p-6 border-b border-gray-100">
            <h3 className="font-bold text-lg text-gray-900">יצירת משתמש חדש</h3>
            <button onClick={() => setShowCreateModal(false)} className="p-1.5 rounded-full hover:bg-gray-100"><X className="w-5 h-5 text-gray-500" /></button>
          </div>
          <div className="overflow-y-auto p-6 space-y-4">
            <div className="grid grid-cols-1 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">שם מלא <span className="text-red-500">*</span></label>
                <input className="input-field w-full" value={createForm.full_name} onChange={(e) => setCreateForm((f) => ({ ...f, full_name: e.target.value }))} placeholder="ישראל ישראלי" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">אימייל <span className="text-red-500">*</span></label>
                <input className="input-field w-full" type="email" value={createForm.email} onChange={(e) => setCreateForm((f) => ({ ...f, email: e.target.value }))} placeholder="user@example.com" dir="ltr" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">טלפון <span className="text-red-500">*</span></label>
                <input className="input-field w-full" type="tel" value={createForm.phone} onChange={(e) => setCreateForm((f) => ({ ...f, phone: e.target.value }))} placeholder="05X-XXXXXXX" dir="ltr" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">סיסמה <span className="text-red-500">*</span></label>
                <input className="input-field w-full" type="password" value={createForm.password} onChange={(e) => setCreateForm((f) => ({ ...f, password: e.target.value }))} placeholder="לפחות 8 תווים" dir="ltr" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">תפקיד</label>
                <select className="input-field w-full" value={createForm.role} onChange={(e) => { const r = e.target.value; setCreateForm((f) => ({ ...f, role: r, is_admin: r === 'admin' })) }}>
                  <option value="customer">לקוח</option>
                  <option value="admin">מנהל</option>
                </select>
              </div>
            </div>
            <div className="flex gap-3 pt-1">
              {[{ key: 'is_admin', label: 'אדמין', on: 'bg-purple-100 text-purple-700', off: 'bg-gray-100 text-gray-500' },
                { key: 'is_verified', label: 'מאומת', on: 'bg-blue-100 text-blue-700', off: 'bg-gray-100 text-gray-500' },
              ].map(({ key, label, on, off }) => (
                <button
                  key={key}
                  onClick={() => setCreateForm((f) => ({ ...f, [key]: !f[key] }))}
                  className={`flex-1 flex items-center justify-center gap-2 p-2.5 rounded-xl border-2 text-sm font-medium transition-all ${
                    createForm[key] ? `${on} border-current` : `${off} border-transparent`
                  }`}
                >
                  <span>{createForm[key] ? '✓' : '✗'}</span> {label}
                </button>
              ))}
            </div>
          </div>
          <div className="flex gap-3 p-6 border-t border-gray-100">
            <button onClick={submitCreateUser} className="btn-primary flex-1 flex items-center justify-center gap-2">
              <PlusCircle className="w-4 h-4" />
              צור משתמש
            </button>
            <button onClick={() => setShowCreateModal(false)} className="btn-secondary flex-1">ביטול</button>
          </div>
        </div>
      </div>
    )}

    {/* Edit User Modal */}
    {editUser && (
      <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setEditUser(null)}>
        <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg flex flex-col max-h-[90vh]" onClick={(e) => e.stopPropagation()}>
          {/* Header */}
          <div className="flex items-center justify-between p-6 border-b border-gray-100">
            <div>
              <h3 className="font-bold text-lg text-gray-900">עדכון פרטי משתמש</h3>
              <p className="text-xs text-gray-400 mt-0.5">נרשם: {editUser.created_at ? new Date(editUser.created_at).toLocaleDateString('he-IL') : '—'}</p>
            </div>
            <button onClick={() => setEditUser(null)} className="p-1.5 rounded-full hover:bg-gray-100"><X className="w-5 h-5 text-gray-500" /></button>
          </div>

          {/* Scrollable body */}
          <div className="overflow-y-auto p-6 space-y-5">

            {/* Personal info */}
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">פרטים אישיים</p>
              <div className="space-y-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">שם מלא</label>
                  <input className="input-field w-full" value={editForm.full_name} onChange={(e) => setEditForm((f) => ({ ...f, full_name: e.target.value }))} placeholder="שם מלא" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">אימייל</label>
                  <input className="input-field w-full" type="email" value={editForm.email} onChange={(e) => setEditForm((f) => ({ ...f, email: e.target.value }))} placeholder="email@example.com" dir="ltr" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">טלפון</label>
                  <input className="input-field w-full" type="tel" value={editForm.phone} onChange={(e) => setEditForm((f) => ({ ...f, phone: e.target.value }))} placeholder="05X-XXXXXXX" dir="ltr" />
                </div>
              </div>
            </div>

            {/* Role */}
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">תפקיד</p>
              <select
                className="input-field w-full"
                value={editForm.role}
                onChange={(e) => {
                  const r = e.target.value
                  setEditForm((f) => ({ ...f, role: r, is_admin: r === 'admin' }))
                }}
              >
                <option value="customer">לקוח</option>
                <option value="admin">מנהל</option>
              </select>
            </div>

            {/* Toggles */}
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">הרשאות וסטטוס</p>
              <div className="grid grid-cols-3 gap-3">
                {[{ key: 'is_active', label: 'פעיל', on: 'bg-green-100 text-green-700', off: 'bg-red-100 text-red-600' },
                  { key: 'is_verified', label: 'מאומת', on: 'bg-blue-100 text-blue-700', off: 'bg-gray-100 text-gray-500' },
                  { key: 'is_admin', label: 'אדמין', on: 'bg-purple-100 text-purple-700', off: 'bg-gray-100 text-gray-500' },
                ].map(({ key, label, on, off }) => (
                  <button
                    key={key}
                    onClick={() => setEditForm((f) => ({ ...f, [key]: !f[key] }))}
                    className={`flex flex-col items-center gap-1.5 p-3 rounded-xl border-2 transition-all cursor-pointer ${
                      editForm[key] ? `${on} border-current` : `${off} border-transparent`
                    }`}
                  >
                    <span className="text-lg">{editForm[key] ? '✓' : '✗'}</span>
                    <span className="text-xs font-medium">{label}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Account info */}
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">מידע חשבון</p>
              <div className="bg-gray-50 rounded-xl p-4 space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-gray-500">מזהה משתמש</span>
                  <span className="font-mono text-xs text-gray-600 dir-ltr" dir="ltr">{editUser.id?.slice(0, 8)}…</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">כשלונות כניסה</span>
                  <div className="flex items-center gap-2">
                    <span className={`font-medium ${editUser.failed_login_count > 0 ? 'text-red-600' : 'text-gray-700'}`}>{editUser.failed_login_count ?? 0}</span>
                    {editUser.failed_login_count > 0 && (
                      <button onClick={() => resetLoginFailures(editUser.id)} className="text-xs text-brand-600 hover:underline">איפוס</button>
                    )}
                  </div>
                </div>
                {editUser.locked_until && (
                  <div className="flex justify-between">
                    <span className="text-gray-500">נעול עד</span>
                    <div className="flex items-center gap-2">
                      <span className="text-red-600 text-xs">{new Date(editUser.locked_until).toLocaleString('he-IL')}</span>
                      <button onClick={() => resetLoginFailures(editUser.id)} className="text-xs text-brand-600 hover:underline">שחרר</button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Footer */}
          <div className="flex gap-3 p-6 border-t border-gray-100">
            <button onClick={saveEditUser} className="btn-primary flex-1 flex items-center justify-center gap-2">
              <Save className="w-4 h-4" />
              שמור שינויים
            </button>
            <button onClick={() => setEditUser(null)} className="btn-secondary flex-1">ביטול</button>
          </div>
        </div>
      </div>
    )}
    </>
  )
}
