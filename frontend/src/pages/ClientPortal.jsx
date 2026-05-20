import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import { User, Save, Package, Clock, CheckCircle2, ShoppingCart } from 'lucide-react'
import api from '../api/client'
import { ordersApi } from '../api/orders'
import { useAuthStore } from '../stores/authStore'

function StatusPill({ status }) {
  const map = {
    pending_payment: { label: 'ממתין לתשלום', cls: 'bg-yellow-100 text-yellow-700' },
    paid: { label: 'שולם', cls: 'bg-blue-100 text-blue-700' },
    supplier_ordered: { label: 'הוזמן מספק', cls: 'bg-indigo-100 text-indigo-700' },
    shipped: { label: 'נשלח', cls: 'bg-cyan-100 text-cyan-700' },
    delivered: { label: 'נמסר', cls: 'bg-green-100 text-green-700' },
    cancelled: { label: 'בוטל', cls: 'bg-red-100 text-red-700' },
    refunded: { label: 'הוחזר', cls: 'bg-red-100 text-red-700' },
  }
  const val = map[status] || { label: status || 'לא ידוע', cls: 'bg-gray-100 text-gray-700' }
  return <span className={`px-2 py-1 rounded-full text-xs font-semibold ${val.cls}`}>{val.label}</span>
}

export default function ClientPortal() {
  const { user, fetchMe } = useAuthStore()
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [ordersLoading, setOrdersLoading] = useState(true)
  const [orders, setOrders] = useState([])
  const [form, setForm] = useState({
    full_name: '',
    phone: '',
    address_line1: '',
    address_line2: '',
    city: '',
    postal_code: '',
  })

  const loadData = async () => {
    setLoading(true)
    try {
      const [{ data: profileRes }, { data: ordersRes }] = await Promise.all([
        api.get('/profile'),
        ordersApi.getAll(20),
      ])

      setForm({
        full_name: profileRes?.user?.full_name || '',
        phone: profileRes?.user?.phone || '',
        address_line1: profileRes?.profile?.address_line1 || profileRes?.profile?.address || '',
        address_line2: profileRes?.profile?.address_line2 || profileRes?.profile?.apartment || '',
        city: profileRes?.profile?.city || '',
        postal_code: profileRes?.profile?.postal_code || '',
      })

      const rows = Array.isArray(ordersRes?.orders) ? ordersRes.orders : []
      setOrders(rows)
    } catch (e) {
      toast.error('לא ניתן לטעון את נתוני הלקוח')
    } finally {
      setLoading(false)
      setOrdersLoading(false)
    }
  }

  useEffect(() => {
    fetchMe()
    loadData()
  }, [])

  const totals = useMemo(() => {
    const orderCount = orders.length
    const deliveredCount = orders.filter((o) => o.status === 'delivered').length
    const pendingCount = orders.filter((o) => o.status === 'pending_payment').length
    const totalSpent = orders
      .filter((o) => ['paid', 'supplier_ordered', 'shipped', 'delivered'].includes(o.status))
      .reduce((sum, o) => sum + Number(o.total_amount || o.total || 0), 0)
    return { orderCount, deliveredCount, pendingCount, totalSpent }
  }, [orders])

  const saveProfile = async (e) => {
    e.preventDefault()
    setSaving(true)
    try {
      await api.put('/profile', null, {
        params: {
          full_name: form.full_name,
          phone: form.phone || undefined,
          address_line1: form.address_line1,
          address_line2: form.address_line2,
          city: form.city,
          postal_code: form.postal_code,
        },
      })
      await fetchMe()
      toast.success('הפרופיל עודכן בהצלחה')
    } catch (err) {
      const detail = err?.response?.data?.detail
      toast.error(typeof detail === 'string' ? detail : 'שמירת הפרופיל נכשלה')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto p-4 sm:p-6">
        <div className="card p-8 text-center text-gray-500">טוען נתוני לקוח...</div>
      </div>
    )
  }

  return (
    <div className="max-w-6xl mx-auto p-4 sm:p-6 space-y-6" dir="rtl">
      <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="section-title">האזור האישי</h1>
          <p className="text-gray-500">שלום {user?.full_name || 'לקוח'}, כאן תוכל לראות נתונים, הזמנות ולעדכן פרופיל.</p>
        </div>
        <div className="flex items-center gap-2">
          <Link to="/parts" className="btn-secondary inline-flex items-center gap-2"><ShoppingCart className="w-4 h-4" /> חיפוש חלקים</Link>
          <Link to="/orders" className="btn-primary inline-flex items-center gap-2"><Package className="w-4 h-4" /> כל ההזמנות</Link>
        </div>
      </header>

      <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="card p-4"><p className="text-sm text-gray-500">סה״כ הזמנות</p><p className="text-2xl font-bold text-brand-navy mt-1">{totals.orderCount}</p></div>
        <div className="card p-4"><p className="text-sm text-gray-500">נמסרו</p><p className="text-2xl font-bold text-green-700 mt-1">{totals.deliveredCount}</p></div>
        <div className="card p-4"><p className="text-sm text-gray-500">ממתינות לתשלום</p><p className="text-2xl font-bold text-yellow-700 mt-1">{totals.pendingCount}</p></div>
        <div className="card p-4"><p className="text-sm text-gray-500">סה״כ רכישות</p><p className="text-2xl font-bold text-brand-navy mt-1">₪{totals.totalSpent.toFixed(0)}</p></div>
      </section>

      <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="card p-6">
          <div className="flex items-center gap-2 mb-4"><User className="w-5 h-5 text-brand-600" /><h2 className="font-bold text-brand-navy">פרופיל לקוח</h2></div>
          <form className="space-y-3" onSubmit={saveProfile}>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="text-sm text-gray-600">שם מלא</label>
                <input className="input-field mt-1" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
              </div>
              <div>
                <label className="text-sm text-gray-600">אימייל</label>
                <input className="input-field mt-1 bg-gray-50" value={user?.email || ''} readOnly dir="ltr" />
              </div>
              <div>
                <label className="text-sm text-gray-600">טלפון</label>
                <input className="input-field mt-1" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} dir="ltr" />
              </div>
              <div>
                <label className="text-sm text-gray-600">מיקוד</label>
                <input className="input-field mt-1" value={form.postal_code} onChange={(e) => setForm({ ...form, postal_code: e.target.value })} />
              </div>
            </div>

            <div>
              <label className="text-sm text-gray-600">כתובת</label>
              <input className="input-field mt-1" value={form.address_line1} onChange={(e) => setForm({ ...form, address_line1: e.target.value })} />
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="text-sm text-gray-600">דירה / קומה</label>
                <input className="input-field mt-1" value={form.address_line2} onChange={(e) => setForm({ ...form, address_line2: e.target.value })} />
              </div>
              <div>
                <label className="text-sm text-gray-600">עיר</label>
                <input className="input-field mt-1" value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} />
              </div>
            </div>

            <button type="submit" className="btn-primary inline-flex items-center gap-2" disabled={saving}>
              <Save className="w-4 h-4" /> {saving ? 'שומר...' : 'שמירת פרופיל'}
            </button>
          </form>
        </div>

        <div className="card p-6">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2"><Package className="w-5 h-5 text-brand-600" /><h2 className="font-bold text-brand-navy">הזמנות אחרונות</h2></div>
            <Link to="/orders" className="text-brand-600 text-sm hover:underline">לכל ההזמנות</Link>
          </div>

          {ordersLoading ? (
            <p className="text-gray-500">טוען הזמנות...</p>
          ) : orders.length === 0 ? (
            <div className="text-sm text-gray-500 space-y-2">
              <p>אין עדיין הזמנות בחשבון.</p>
              <Link to="/parts" className="text-brand-600 hover:underline">התחל חיפוש חלקים</Link>
            </div>
          ) : (
            <div className="space-y-3 max-h-[420px] overflow-y-auto pr-1">
              {orders.slice(0, 12).map((order) => (
                <div key={order.id} className="border border-gray-100 rounded-xl p-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="font-semibold text-brand-navy">{order.order_number || order.id?.slice(0, 8)}</p>
                    <StatusPill status={order.status} />
                  </div>
                  <div className="flex items-center justify-between mt-2 text-sm text-gray-600">
                    <span className="inline-flex items-center gap-1"><Clock className="w-4 h-4" /> {order.created_at ? new Date(order.created_at).toLocaleDateString('he-IL') : '-'}</span>
                    <span className="font-semibold text-brand-navy">₪{Number(order.total_amount || order.total || 0).toFixed(0)}</span>
                  </div>
                  {order.status === 'delivered' && (
                    <p className="mt-2 text-xs text-green-700 inline-flex items-center gap-1"><CheckCircle2 className="w-3.5 h-3.5" /> ההזמנה סופקה בהצלחה</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
