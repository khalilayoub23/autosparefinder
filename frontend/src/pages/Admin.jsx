import { useState, useEffect } from 'react'
import api from '../api/client'
import {
  LayoutDashboard, Users, Package, TrendingUp, Settings,
  DollarSign, ShoppingBag, BarChart2, Loader2, RefreshCw,
  ToggleLeft, ToggleRight, Truck, PlusCircle, Wand2, ChevronDown,
  ShoppingCart, CheckCircle, Clock, ExternalLink, AlertCircle,
  FileSpreadsheet, Upload, X, Zap, TrendingDown, Percent,
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
]

export default function Admin() {
  const [tab, setTab] = useState('dashboard')
  const [stats, setStats] = useState(null)
  const [importFile, setImportFile] = useState(null)
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState(null)
  const [users, setUsers] = useState([])
  const [orders, setOrders] = useState([])
  const [suppliers, setSuppliers] = useState([])
  const [supplierOrders, setSupplierOrders] = useState([])
  const [supplierOrdersPending, setSupplierOrdersPending] = useState(0)
  const [syncStatus, setSyncStatus] = useState(null)   // price-sync status
  const [syncing, setSyncing] = useState(false)         // manual trigger running
  const [socialContent, setSocialContent] = useState('')
  const [genTopic, setGenTopic] = useState('')
  const [genPlatform, setGenPlatform] = useState('facebook')
  const [generating, setGenerating] = useState(false)
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

  const toggleSupplier = async (id, current) => {
    try {
      await api.put(`/admin/suppliers/${id}`, null, { params: { is_active: !current } })
      setSuppliers((s) => s.map((sup) => sup.id === id ? { ...sup, is_active: !current } : sup))
      toast.success('עודכן')
    } catch { toast.error('שגיאה') }
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
      {tab === 'users' && (
        <div className="card overflow-hidden">
          <div className="p-4 border-b border-gray-100 flex items-center justify-between">
            <h3 className="font-bold text-gray-900">משתמשים ({users.length})</h3>
            <button onClick={loadUsers} className="btn-ghost text-sm flex items-center gap-1"><RefreshCw className="w-4 h-4" /></button>
          </div>
          {loading ? <div className="flex justify-center p-8"><Loader2 className="w-6 h-6 animate-spin text-brand-600" /></div> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    {['שם', 'אימייל', 'מאומת', 'אדמין', 'פעיל', 'פעולות'].map((h) => (
                      <th key={h} className="px-4 py-3 text-right text-xs font-medium text-gray-500">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {users.map((u) => (
                    <tr key={u.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3 font-medium text-gray-900">{u.full_name}</td>
                      <td className="px-4 py-3 text-gray-600 dir-ltr" dir="ltr">{u.email}</td>
                      <td className="px-4 py-3"><span className={`badge ${u.is_verified ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>{u.is_verified ? '✓' : '–'}</span></td>
                      <td className="px-4 py-3"><span className={`badge ${u.is_admin ? 'bg-purple-100 text-purple-700' : 'bg-gray-100 text-gray-500'}`}>{u.is_admin ? 'כן' : 'לא'}</span></td>
                      <td className="px-4 py-3">
                        <button onClick={() => toggleUser(u.id, u.is_active !== false)}>
                          {u.is_active !== false ? <ToggleRight className="w-6 h-6 text-green-500" /> : <ToggleLeft className="w-6 h-6 text-gray-400" />}
                        </button>
                      </td>
                      <td className="px-4 py-3">
                        <button onClick={() => api.put(`/admin/users/${u.id}`, null, { params: { is_admin: !u.is_admin } }).then(() => { setUsers((us) => us.map((x) => x.id === u.id ? { ...x, is_admin: !u.is_admin } : x)); toast.success('עודכן') })} className="text-xs text-brand-600 hover:underline">
                          {u.is_admin ? 'הסר אדמין' : 'הפוך לאדמין'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

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

          {/* ── Price-Sync Widget ────────────────────────────────────────── */}
          <div className="card p-5 flex flex-col sm:flex-row sm:items-center gap-4">
            <div className="flex items-center gap-3 flex-1">
              <div className="p-2.5 rounded-xl bg-brand-50">
                <Zap className="w-6 h-6 text-brand-600" />
              </div>
              <div>
                <p className="font-bold text-gray-900 text-sm">סנכרון מחירים אוטומטי</p>
                {syncStatus ? (
                  <p className="text-xs text-gray-500 mt-0.5">
                    {syncStatus.last_sync
                      ? <>עדכון אחרון: <span className="font-medium text-gray-700">{new Date(syncStatus.last_sync).toLocaleString('he-IL')}</span>
                        {' · '}הבא בעוד: <span className="font-medium text-gray-700">{syncStatus.next_sync_in_h}h</span></>
                      : 'טרם רץ'}
                    {syncStatus.message && (
                      <span className="block mt-0.5 text-gray-400">
                        {syncStatus.message.replace('[Price Sync] ', '')}
                      </span>
                    )}
                  </p>
                ) : (
                  <p className="text-xs text-gray-400">טוען מצב...</p>
                )}
              </div>
            </div>

            {/* Status bars per supplier */}
            <div className="flex gap-3 text-xs">
              {[
                { name: 'AutoParts Pro IL', flag: '🇮🇱', vol: '±2%' },
                { name: 'Global Parts Hub', flag: '🇩🇪', vol: '±4%' },
                { name: 'EastAuto Supply',  flag: '🇨🇳', vol: '±6%' },
              ].map(s => (
                <div key={s.name} className="flex flex-col items-center gap-0.5 bg-gray-50 rounded-lg px-3 py-1.5">
                  <span className="text-base">{s.flag}</span>
                  <span className="text-gray-600 font-medium">{s.name.split(' ')[0]}</span>
                  <span className="text-gray-400">{s.vol}</span>
                </div>
              ))}
            </div>

            <button
              onClick={triggerSync}
              disabled={syncing}
              className="btn-primary text-sm flex items-center gap-2 shrink-0"
            >
              {syncing
                ? <><Loader2 className="w-4 h-4 animate-spin" /> מסנכרן...</>
                : <><RefreshCw className="w-4 h-4" /> סנכרן עכשיו</>}
            </button>
          </div>
          {/* Supplier list */}
          <div className="card overflow-hidden">
            <div className="p-4 border-b border-gray-100 flex items-center justify-between">
              <h3 className="font-bold text-gray-900">ספקים ({suppliers.length})</h3>
              <button onClick={loadSuppliers} className="btn-ghost text-sm flex items-center gap-1"><RefreshCw className="w-4 h-4" /></button>
            </div>
            {loading ? <div className="flex justify-center p-8"><Loader2 className="w-6 h-6 animate-spin text-brand-600" /></div> : (
              <div className="divide-y divide-gray-100">
                {suppliers.map((s) => (
                  <div key={s.id} className="flex items-center justify-between p-4 hover:bg-gray-50">
                    <div className="flex items-center gap-3">
                      <div className="w-9 h-9 bg-gray-100 rounded-xl flex items-center justify-center">
                        <Truck className="w-5 h-5 text-gray-600" />
                      </div>
                      <div>
                        <p className="font-medium text-gray-900">{s.name}</p>
                        <p className="text-xs text-gray-400">{s.country} · עדיפות {s.priority} · ציון {Number(s.reliability_score).toFixed(1)}</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      <button onClick={() => syncSupplier(s.id)} className="btn-secondary text-xs px-3 py-1.5">סנכרן</button>
                      <button onClick={() => toggleSupplier(s.id, s.is_active)}>
                        {s.is_active ? <ToggleRight className="w-6 h-6 text-green-500" /> : <ToggleLeft className="w-6 h-6 text-gray-400" />}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
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
            {supplierOrders.length === 0 ? (
              <div className="text-center py-8 text-gray-400">
                <CheckCircle className="w-8 h-8 mx-auto mb-2 text-green-400" />
                <p className="text-sm">אין רשומות הזמנה עדיין</p>
              </div>
            ) : (
              <div className="divide-y divide-gray-100">
                {supplierOrders.map((so) => {
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
                          <p className="font-semibold text-gray-900 text-sm">{so.title}</p>
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
            )}
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
    </div>
  )
}
