import { useState, useEffect } from 'react'
import { ordersApi, returnsApi, invoicesApi } from '../api/orders'
import { Link } from 'react-router-dom'
import { Package, Truck, CheckCircle, XCircle, Clock, ChevronRight, RotateCcw, FileText, Loader2 } from 'lucide-react'
import { format } from 'date-fns'
import { he } from 'date-fns/locale'
import toast from 'react-hot-toast'

const STATUS_MAP = {
  pending_payment: { label: 'ממתין לתשלום', icon: Clock,       color: 'bg-yellow-100 text-yellow-700' },
  paid:            { label: 'שולם',          icon: CheckCircle, color: 'bg-blue-100 text-blue-700'    },
  processing:      { label: 'בעיבוד',        icon: Clock,       color: 'bg-indigo-100 text-indigo-700'},
  shipped:         { label: 'נשלח',          icon: Truck,       color: 'bg-purple-100 text-purple-700'},
  delivered:       { label: 'נמסר',          icon: CheckCircle, color: 'bg-green-100 text-green-700' },
  cancelled:       { label: 'בוטל',          icon: XCircle,     color: 'bg-red-100 text-red-700'     },
}

function StatusBadge({ status }) {
  const s = STATUS_MAP[status] || { label: status, icon: Clock, color: 'bg-gray-100 text-gray-700' }
  const Icon = s.icon
  return (
    <span className={`badge ${s.color} flex items-center gap-1`}>
      <Icon className="w-3 h-3" /> {s.label}
    </span>
  )
}

function OrderCard({ order, onReturn }) {
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    if (detail) { setExpanded(!expanded); return }
    setLoading(true)
    try {
      const { data } = await ordersApi.getById(order.id)
      setDetail(data)
      setExpanded(true)
    } catch { toast.error('שגיאה בטעינת הזמנה') }
    finally { setLoading(false) }
  }

  return (
    <div className="card overflow-hidden">
      <div className="p-5 flex items-center justify-between cursor-pointer hover:bg-gray-50" onClick={load}>
        <div className="flex items-center gap-4">
          <div className="w-10 h-10 bg-brand-50 rounded-xl flex items-center justify-center">
            <Package className="w-5 h-5 text-brand-600" />
          </div>
          <div>
            <p className="font-semibold text-gray-900">{order.order_number}</p>
            <p className="text-xs text-gray-400">
              {order.created_at ? format(new Date(order.created_at), 'dd/MM/yyyy HH:mm', { locale: he }) : ''}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div className="text-left hidden sm:block">
            <p className="font-bold text-gray-900">₪{Number(order.total).toFixed(2)}</p>
          </div>
          <StatusBadge status={order.status} />
          {loading ? <Loader2 className="w-4 h-4 animate-spin text-gray-400" /> : <ChevronRight className={`w-4 h-4 text-gray-400 transition-transform ${expanded ? 'rotate-90' : ''}`} />}
        </div>
      </div>

      {expanded && detail && (
        <div className="border-t border-gray-100 p-5 bg-gray-50 space-y-4">
          {/* Items */}
          <div className="space-y-2">
            {detail.items?.map((item, i) => (
              <div key={i} className="flex justify-between text-sm">
                <span className="text-gray-700">{item.part_name} <span className="text-gray-400">×{item.quantity}</span></span>
                <span className="font-medium text-gray-900">₪{Number(item.total).toFixed(2)}</span>
              </div>
            ))}
          </div>

          {/* Totals */}
          <div className="border-t border-gray-200 pt-3 space-y-1 text-sm">
            <div className="flex justify-between text-gray-600"><span>סכום ביניים</span><span>₪{Number(detail.subtotal).toFixed(2)}</span></div>
            <div className="flex justify-between text-gray-600"><span>מע״מ 17%</span><span>₪{Number(detail.vat).toFixed(2)}</span></div>
            <div className="flex justify-between text-gray-600"><span>משלוח</span><span>₪{Number(detail.shipping).toFixed(2)}</span></div>
            <div className="flex justify-between font-bold text-gray-900 text-base pt-1 border-t border-gray-200"><span>סה״כ</span><span className="text-brand-600">₪{Number(detail.total).toFixed(2)}</span></div>
          </div>

          {/* Tracking */}
          {detail.tracking_number && (
            <div className="flex items-center gap-2 text-sm text-gray-600">
              <Truck className="w-4 h-4" />
              <span>מעקב: {detail.tracking_number}</span>
              {detail.tracking_url && <a href={detail.tracking_url} target="_blank" rel="noopener noreferrer" className="text-brand-600 hover:underline">עקוב</a>}
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-2 flex-wrap">
            {detail.invoice_number && (
              <button onClick={() => ordersApi.invoice(order.id).then(({ data }) => window.open(data.download_url)).catch(() => toast.error('שגיאה'))} className="btn-secondary text-sm flex items-center gap-1">
                <FileText className="w-4 h-4" /> חשבונית
              </button>
            )}
            {['delivered', 'shipped'].includes(detail.status) && (
              <button onClick={() => onReturn(order.id)} className="btn-secondary text-sm flex items-center gap-1">
                <RotateCcw className="w-4 h-4" /> החזרה
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function ReturnModal({ orderId, onClose }) {
  const [reason, setReason] = useState('')
  const [desc, setDesc] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setLoading(true)
    try {
      await returnsApi.create({ order_id: orderId, reason, description: desc })
      toast.success('בקשת ההחזרה נפתחה בהצלחה')
      onClose()
    } catch { toast.error('שגיאה') }
    finally { setLoading(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
      <div className="card p-6 max-w-md w-full">
        <h3 className="font-bold text-gray-900 mb-4">בקשת החזרה</h3>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">סיבה</label>
            <select className="input-field" value={reason} onChange={(e) => setReason(e.target.value)} required>
              <option value="">בחר סיבה...</option>
              <option value="wrong_part">חלק שגוי</option>
              <option value="defective">פגום / לא עובד</option>
              <option value="not_as_described">לא תואם לתיאור</option>
              <option value="changed_mind">שינוי דעה</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">תיאור (אופציונלי)</label>
            <textarea className="input-field resize-none" rows={3} placeholder="פרטים נוספים..." value={desc} onChange={(e) => setDesc(e.target.value)} />
          </div>
          <div className="flex gap-3">
            <button type="button" onClick={onClose} className="btn-secondary flex-1">ביטול</button>
            <button type="submit" disabled={!reason || loading} className="btn-primary flex-1 flex items-center justify-center gap-2">
              {loading && <Loader2 className="w-4 h-4 animate-spin" />} שלח
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function Orders() {
  const [orders, setOrders] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [returnOrderId, setReturnOrderId] = useState(null)
  const [tab, setTab] = useState('orders') // 'orders' | 'returns'
  const [returns, setReturns] = useState([])

  useEffect(() => {
    ordersApi.getAll().then(({ data }) => setOrders(data.orders || [])).catch(() => toast.error('שגיאה בטעינת הזמנות')).finally(() => setIsLoading(false))
    returnsApi.getAll().then(({ data }) => setReturns(data.returns || [])).catch(() => {})
  }, [])

  return (
    <div className="space-y-6">
      <div>
        <h1 className="section-title">ההזמנות שלי</h1>
        <p className="text-gray-500 mt-1">מעקב אחר הזמנות והחזרות</p>
      </div>

      <div className="flex gap-2 border-b border-gray-200">
        {[
          { id: 'orders', label: `הזמנות (${orders.length})` },
          { id: 'returns', label: `החזרות (${returns.length})` },
        ].map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)} className={`px-5 py-2 text-sm font-medium border-b-2 transition-colors -mb-px ${tab === t.id ? 'border-brand-600 text-brand-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
            {t.label}
          </button>
        ))}
      </div>

      {isLoading && <div className="flex justify-center py-12"><Loader2 className="w-8 h-8 animate-spin text-brand-600" /></div>}

      {!isLoading && tab === 'orders' && (
        <>
          {orders.length === 0 ? (
            <div className="card p-12 text-center">
              <Package className="w-12 h-12 text-gray-300 mx-auto mb-3" />
              <h3 className="font-semibold text-gray-700 mb-1">אין הזמנות עדיין</h3>
              <p className="text-sm text-gray-400 mb-4">מצא חלקים ובצע הזמנה ראשונה</p>
              <Link to="/parts" className="btn-primary">חפש חלקים</Link>
            </div>
          ) : (
            <div className="space-y-3">
              {orders.map((o) => <OrderCard key={o.id} order={o} onReturn={setReturnOrderId} />)}
            </div>
          )}
        </>
      )}

      {!isLoading && tab === 'returns' && (
        <div className="space-y-3">
          {returns.length === 0 ? (
            <div className="card p-12 text-center">
              <RotateCcw className="w-10 h-10 text-gray-300 mx-auto mb-3" />
              <p className="text-sm text-gray-400">אין בקשות החזרה</p>
            </div>
          ) : returns.map((r) => (
            <div key={r.id} className="card p-4">
              <div className="flex justify-between items-start">
                <div>
                  <p className="font-semibold text-gray-900">{r.return_number}</p>
                  <p className="text-sm text-gray-500">{r.reason}</p>
                </div>
                <span className={`badge ${r.status === 'approved' ? 'bg-green-100 text-green-700' : r.status === 'pending' ? 'bg-yellow-100 text-yellow-700' : 'bg-red-100 text-red-700'}`}>
                  {r.status === 'approved' ? 'אושר' : r.status === 'pending' ? 'ממתין' : r.status}
                </span>
              </div>
              {r.refund_amount && <p className="text-sm text-green-600 mt-1 font-medium">זיכוי: ₪{Number(r.refund_amount).toFixed(2)}</p>}
            </div>
          ))}
        </div>
      )}

      {returnOrderId && <ReturnModal orderId={returnOrderId} onClose={() => setReturnOrderId(null)} />}
    </div>
  )
}
