import { useState, useEffect } from 'react'
import api from '../api/client'
import {
  LayoutDashboard, Users, Package, TrendingUp, Settings,
  DollarSign, ShoppingBag, BarChart2, Loader2, RefreshCw,
  ToggleLeft, ToggleRight, Truck, PlusCircle, Wand2,
} from 'lucide-react'
import toast from 'react-hot-toast'

function StatCard({ label, value, icon: Icon, color = 'brand' }) {
  const colors = {
    brand: 'bg-brand-50 text-brand-600',
    green: 'bg-green-50 text-green-600',
    blue: 'bg-blue-50 text-blue-600',
    purple: 'bg-purple-50 text-purple-600',
  }
  return (
    <div className="card p-5 flex items-center gap-4">
      <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${colors[color]}`}>
        <Icon className="w-6 h-6" />
      </div>
      <div>
        <p className="text-sm text-gray-500">{label}</p>
        <p className="text-2xl font-bold text-gray-900">{value ?? <span className="skeleton w-16 h-6 inline-block" />}</p>
      </div>
    </div>
  )
}

const TABS = [
  { id: 'dashboard', label: 'סקירה', icon: LayoutDashboard },
  { id: 'users',     label: 'משתמשים', icon: Users         },
  { id: 'orders',    label: 'הזמנות', icon: ShoppingBag    },
  { id: 'suppliers', label: 'ספקים', icon: Truck           },
  { id: 'social',    label: 'רשתות חברתיות', icon: Wand2   },
]

export default function Admin() {
  const [tab, setTab] = useState('dashboard')
  const [stats, setStats] = useState(null)
  const [users, setUsers] = useState([])
  const [orders, setOrders] = useState([])
  const [suppliers, setSuppliers] = useState([])
  const [socialContent, setSocialContent] = useState('')
  const [genTopic, setGenTopic] = useState('')
  const [genPlatform, setGenPlatform] = useState('facebook')
  const [generating, setGenerating] = useState(false)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    loadDashboard()
  }, [])

  useEffect(() => {
    if (tab === 'users') loadUsers()
    if (tab === 'orders') loadOrders()
    if (tab === 'suppliers') loadSuppliers()
  }, [tab])

  const loadDashboard = async () => {
    try {
      const { data } = await api.get('/admin/stats')
      setStats(data)
    } catch { toast.error('שגיאה בטעינת נתונים') }
  }

  const loadUsers = async () => {
    setLoading(true)
    try { const { data } = await api.get('/admin/users'); setUsers(data.users || []) }
    catch { toast.error('שגיאה') }
    finally { setLoading(false) }
  }

  const loadOrders = async () => {
    setLoading(true)
    try { const { data } = await api.get('/orders'); setOrders(data.orders || []) }
    catch { toast.error('שגיאה') }
    finally { setLoading(false) }
  }

  const loadSuppliers = async () => {
    setLoading(true)
    try { const { data } = await api.get('/admin/suppliers'); setSuppliers(data.suppliers || []) }
    catch { toast.error('שגיאה') }
    finally { setLoading(false) }
  }

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
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-2 px-5 py-2.5 text-sm font-medium whitespace-nowrap border-b-2 -mb-px transition-colors
              ${tab === id ? 'border-brand-600 text-brand-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
          >
            <Icon className="w-4 h-4" /> {label}
          </button>
        ))}
      </div>

      {/* Dashboard */}
      {tab === 'dashboard' && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard label="משתמשים" value={stats?.total_users} icon={Users} color="blue" />
            <StatCard label="הזמנות" value={stats?.total_orders} icon={ShoppingBag} color="purple" />
            <StatCard label="הכנסות (₪)" value={stats?.total_revenue != null ? `₪${Number(stats.total_revenue).toLocaleString()}` : null} icon={DollarSign} color="green" />
            <StatCard label="מוצרים" value={stats?.total_parts} icon={Package} color="brand" />
          </div>
          <div className="card p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-bold text-gray-900">מדדים כלליים</h3>
              <button onClick={loadDashboard} className="btn-ghost text-sm flex items-center gap-1">
                <RefreshCw className="w-4 h-4" /> רענן
              </button>
            </div>
            <p className="text-sm text-gray-400">גרפים ודוחות מפורטים – בקרוב</p>
          </div>
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
          <div className="p-4 border-b border-gray-100">
            <h3 className="font-bold text-gray-900">כל ההזמנות ({orders.length})</h3>
          </div>
          {loading ? <div className="flex justify-center p-8"><Loader2 className="w-6 h-6 animate-spin text-brand-600" /></div> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    {['מס׳ הזמנה', 'סטטוס', 'סכום', 'תאריך'].map((h) => (
                      <th key={h} className="px-4 py-3 text-right text-xs font-medium text-gray-500">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {orders.map((o) => (
                    <tr key={o.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3 font-medium text-gray-900" dir="ltr">{o.order_number}</td>
                      <td className="px-4 py-3"><span className="badge bg-gray-100 text-gray-700">{o.status}</span></td>
                      <td className="px-4 py-3 font-semibold text-brand-600">₪{Number(o.total).toFixed(2)}</td>
                      <td className="px-4 py-3 text-gray-500">{o.created_at ? new Date(o.created_at).toLocaleDateString('he-IL') : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Suppliers */}
      {tab === 'suppliers' && (
        <div className="space-y-4">
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
