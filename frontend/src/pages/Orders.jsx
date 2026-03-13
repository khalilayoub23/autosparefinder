import { useState, useEffect } from 'react'
import { ordersApi, returnsApi, paymentsApi } from '../api/orders'
import { Link, useNavigate } from 'react-router-dom'
import { Package, Truck, CheckCircle, XCircle, Clock, ChevronRight, RotateCcw, FileText, Loader2, Trash2, Ban, CreditCard, Banknote, ExternalLink, RefreshCw } from 'lucide-react'
import { format } from 'date-fns'
import { he } from 'date-fns/locale'
import toast from 'react-hot-toast'
import { useCartStore } from '../stores/cartStore'
import InvoiceActions from '../components/InvoiceActions'

function buildTrackingUrl(trackingNumber, storedUrl) {
  if (!trackingNumber) return null
  const n = trackingNumber.trim()
  // Always rebuild from tracking number — stored URLs may be stale/wrong domain
  // parcelsapp uses path-based URLs that always pre-fill the search
  if (/^1Z[A-Z0-9]{16}$/i.test(n))   // UPS
    return `https://www.ups.com/track?tracknum=${n}&requester=ST/trackdetails`
  if (/^\d{12}$/.test(n))             // FedEx
    return `https://www.fedex.com/fedextrack/?trknbr=${n}`
  if (/^\d{10}$/.test(n))             // DHL
    return `https://www.dhl.com/en/express/tracking.html?AWB=${n}`
  // Israel Post, AliExpress, EMS, unknown → parcelsapp (path-based, always pre-fills)
  return `https://parcelsapp.com/en/tracking/${n}`
}

const STATUS_MAP = {
  pending_payment:   { label: 'ממתין לתשלום', icon: Clock,       color: 'bg-yellow-100 text-yellow-700' },
  paid:              { label: 'שולם',          icon: CheckCircle, color: 'bg-blue-100 text-blue-700'    },
  processing:        { label: 'בעיבוד',        icon: Clock,       color: 'bg-indigo-100 text-indigo-700'},
  supplier_ordered:  { label: 'הוזמן מספק',    icon: Truck,       color: 'bg-cyan-100 text-cyan-700'   },
  shipped:           { label: 'נשלח',          icon: Truck,       color: 'bg-purple-100 text-purple-700'},
  delivered:         { label: 'נמסר',          icon: CheckCircle, color: 'bg-green-100 text-green-700' },
  cancelled:         { label: 'בוטל',          icon: XCircle,     color: 'bg-red-100 text-red-700'     },
  refunded:          { label: 'הוחזר',         icon: XCircle,     color: 'bg-orange-100 text-orange-700'},
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

const TIMELINE_STEPS = [
  { key: 'pending_payment',  label: 'ממתין לתשלום' },
  { key: 'paid',             label: 'שולם' },
  { key: 'processing',       label: 'בעיבוד' },
  { key: 'supplier_ordered', label: 'הוזמן מספק' },
  { key: 'shipped',          label: 'נשלח' },
  { key: 'delivered',        label: 'נמסר' },
]

function OrderTimeline({ status }) {
  if (['cancelled', 'refunded'].includes(status)) {
    return (
      <div className="flex items-center gap-2 py-2 px-3 bg-red-50 rounded-xl text-sm text-red-600">
        <XCircle className="w-4 h-4 shrink-0" />
        <span>{STATUS_MAP[status]?.label || status}</span>
      </div>
    )
  }
  const activeIdx = TIMELINE_STEPS.findIndex((s) => s.key === status)
  return (
    <div className="flex items-center overflow-x-auto pb-2 gap-0">
      {TIMELINE_STEPS.map((step, i) => {
        const done   = i < activeIdx
        const active = i === activeIdx
        return (
          <div key={step.key} className="flex items-center flex-shrink-0">
            <div className="flex flex-col items-center gap-1">
              <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold border-2 transition-all ${
                done   ? 'bg-brand-500 border-brand-500 text-white' :
                active ? 'bg-white border-brand-500 text-brand-600 ring-2 ring-brand-200' :
                         'bg-white border-gray-200 text-gray-300'
              }`}>
                {done ? '✓' : i + 1}
              </div>
              <span className={`text-xs whitespace-nowrap ${
                active ? 'text-brand-600 font-semibold' : done ? 'text-gray-500' : 'text-gray-300'
              }`}>{step.label}</span>
            </div>
            {i < TIMELINE_STEPS.length - 1 && (
              <div className={`h-0.5 w-6 sm:w-10 mb-4 flex-shrink-0 ${done ? 'bg-brand-400' : 'bg-gray-200'}`} />
            )}
          </div>
        )
      })}
    </div>
  )
}

function OrderCard({ order, onReturn, onDelete, selected, onSelect }) {
  const addItem = useCartStore((s) => s.addItem)
  const navigate = useNavigate()
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [paying, setPaying] = useState(false)

  const handlePay = async (e) => {
    e.stopPropagation()
    setPaying(true)
    try {
      const { data } = await paymentsApi.createCheckout(order.id)
      window.location.href = data.checkout_url
    } catch (err) { toast.error(err.response?.data?.detail || 'שגיאה בתשלום') }
    finally { setPaying(false) }
  }

  const handleCancel = async (e) => {
    e.stopPropagation()
    const wasPaid = ['paid', 'processing', 'supplier_ordered'].includes(order.status)
    if (!window.confirm(wasPaid ? 'לבטל הזמנה זו? תשלום יוחזר לכרטיס האשראי שלך.' : 'לבטל הזמנה זו?')) return
    setCancelling(true)
    try {
      const { data: res } = await ordersApi.cancel(order.id, 'ביטול על ידי לקוח')
      if (res.refund_initiated && res.refund_id) {
        toast.success(`ההזמנה בוטלה. החזר כספי של ₪${Number(res.refund_amount).toFixed(2)} נשלח לכרטיס האשראי שלך.`, { duration: 6000 })
      } else if (res.refund_initiated) {
        toast.success('ההזמנה בוטלה. בקשת ההחזר הכספי נשלחה לטיפול.', { duration: 5000 })
      } else {
        toast.success('ההזמנה בוטלה')
      }
      onDelete(order.id, 'cancelled')
    } catch (err) { toast.error(err.response?.data?.detail || 'שגיאה בביטול') }
    finally { setCancelling(false) }
  }

  const handleDelete = async (e) => {
    e.stopPropagation()
    if (!window.confirm('למחוק הזמנה זו לצמיתות?')) return
    setDeleting(true)
    try {
      await ordersApi.delete(order.id)
      toast.success('ההזמנה נמחקה')
      onDelete(order.id)
    } catch (err) { toast.error(err.response?.data?.detail || 'שגיאה במחיקה') }
    finally { setDeleting(false) }
  }

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
        {/* Left: optional checkbox + icon + order info */}
        <div className="flex items-center gap-3">
          {order.status === 'pending_payment' && (
            <input
              type="checkbox"
              checked={!!selected}
              onChange={() => onSelect(order.id)}
              onClick={(e) => e.stopPropagation()}
              className="w-4 h-4 accent-brand-600 cursor-pointer flex-shrink-0"
            />
          )}
          <div className="w-10 h-10 bg-brand-50 rounded-xl flex items-center justify-center flex-shrink-0">
            <Package className="w-5 h-5 text-brand-600" />
          </div>
          <div>
            <p className="font-semibold text-gray-900">{order.order_number}</p>
            <p className="text-xs text-gray-400">
              {order.created_at ? format(new Date(order.created_at), 'dd/MM/yyyy HH:mm', { locale: he }) : ''}
            </p>
          </div>
        </div>
        {/* Right: amount + pay shortcut + status + chevron */}
        <div className="flex items-center gap-2 sm:gap-3">
          <div className="text-left hidden sm:block">
            <p className="font-bold text-gray-900">₪{Number(order.total).toFixed(2)}</p>
          </div>
          {order.status === 'pending_payment' && (
            <button
              onClick={handlePay}
              disabled={paying}
              className="btn-primary text-xs flex items-center gap-1 py-1.5 px-3 whitespace-nowrap"
            >
              {paying ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CreditCard className="w-3.5 h-3.5" />}
              שלם
            </button>
          )}
          <StatusBadge status={order.status} />
          {loading ? <Loader2 className="w-4 h-4 animate-spin text-gray-400" /> : <ChevronRight className={`w-4 h-4 text-gray-400 transition-transform ${expanded ? 'rotate-90' : ''}`} />}
        </div>
      </div>
      {/* Inline tracking pill — visible without expanding */}
      {order.tracking_number && (
        <div className="px-5 pb-3 flex items-center gap-2 text-xs text-cyan-700">
          <Truck className="w-3.5 h-3.5 flex-shrink-0" />
          <span>מעקב: <strong>{order.tracking_number}</strong></span>
          {(() => {
            const url = buildTrackingUrl(order.tracking_number, order.tracking_url)
            return url ? (
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 underline hover:text-cyan-900 font-medium"
              >
                <ExternalLink className="w-3 h-3" /> עקוב אחר המשלוח
              </a>
            ) : null
          })()}
        </div>
      )}
      {expanded && detail && (
        <div className="border-t border-gray-100 p-5 bg-gray-50 space-y-4">
          {/* Status timeline */}
          <OrderTimeline status={detail.status} />

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
              {(() => {
                const url = buildTrackingUrl(detail.tracking_number, detail.tracking_url)
                return url ? (
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-brand-600 hover:underline font-medium"
                  >
                    <ExternalLink className="w-3.5 h-3.5" /> עקוב אחר המשלוח
                  </a>
                ) : null
              })()}
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-2 flex-wrap">
            {detail.status === 'pending_payment' && (
              <button onClick={handlePay} disabled={paying} className="btn-primary text-sm flex items-center gap-1.5">
                {paying ? <Loader2 className="w-4 h-4 animate-spin" /> : <CreditCard className="w-4 h-4" />} שלם הזמנה
              </button>
            )}
            {['paid', 'confirmed', 'processing', 'supplier_ordered', 'shipped', 'delivered', 'refunded'].includes(detail.status) && (
              <InvoiceActions orderId={order.id} orderNumber={order.order_number} compact />
            )}
            {['delivered', 'shipped'].includes(detail.status) && (
              <button onClick={() => onReturn({ id: order.id, total: order.total })} className="btn-secondary text-sm flex items-center gap-1">
                <RotateCcw className="w-4 h-4" /> החזרה
              </button>
            )}
            {['delivered', 'shipped', 'cancelled', 'refunded'].includes(detail.status) && (
              <button
                onClick={() => {
                  const items = detail.items || []
                  if (!items.length) { toast.error('אין פריטים להזמנה מחדש'); return }
                  items.forEach((item) => addItem({
                    partId: item.part_id,
                    supplierPartId: item.supplier_part_id,
                    name: item.part_name,
                    manufacturer: item.manufacturer || '',
                    price: Number(item.unit_price),
                    vat: 0,
                    quantity: 1,
                  }))
                  toast.success(`הוספו ${items.length} פריטים לסל`, { duration: 3000 })
                  navigate('/cart')
                }}
                className="btn-secondary text-sm flex items-center gap-1 text-brand-600 hover:bg-brand-50 border-brand-200"
              >
                <RefreshCw className="w-4 h-4" /> הזמן מחדש
              </button>
            )}
            {['pending_payment', 'paid', 'processing', 'supplier_ordered'].includes(detail.status) && (
              <button onClick={handleCancel} disabled={cancelling} className="btn-secondary text-sm flex items-center gap-1 text-orange-600 hover:bg-orange-50 border-orange-200">
                {cancelling ? <Loader2 className="w-4 h-4 animate-spin" /> : <Ban className="w-4 h-4" />} בטל הזמנה
              </button>
            )}
            {['pending_payment', 'cancelled'].includes(detail.status) && (
              <button onClick={handleDelete} disabled={deleting} className="btn-secondary text-sm flex items-center gap-1 text-red-600 hover:bg-red-50 border-red-200">
                {deleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />} מחק
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

const RETURN_REASONS = {
  wrong_part:        'חלק שגוי',
  defective:         'פגום / לא עובד',
  not_as_described:  'לא תואם לתיאור',
  changed_mind:      'שינוי דעה',
  damaged_in_transit:'נפגע בשילוח',
  duplicate_order:   'הזמנה כפולה',
}

const RETURN_STATUS_MAP = {
  pending:   { label: 'ממתין לאישור', color: 'bg-yellow-100 text-yellow-700' },
  approved:  { label: 'אושר ✓',       color: 'bg-green-100  text-green-700'  },
  rejected:  { label: 'נדחה',          color: 'bg-red-100    text-red-700'    },
  completed: { label: 'הושלם',         color: 'bg-blue-100   text-blue-700'   },
  cancelled: { label: 'בוטל',          color: 'bg-gray-100   text-gray-500'   },
}

// Policy §3 — reasons that qualify for 100% refund (seller pays return shipping)
const FULL_REFUND_REASONS = new Set(['defective', 'wrong_part', 'damaged_in_transit'])

function ReturnModal({ orderId, orderAmount, onClose, onCreated }) {
  const [reason, setReason] = useState('')
  const [desc, setDesc] = useState('')
  const [loading, setLoading] = useState(false)

  const isFullRefund = FULL_REFUND_REASONS.has(reason)
  const suggestedPct = reason ? (isFullRefund ? 100 : 90) : null
  const estimatedRefund = orderAmount && suggestedPct ? (orderAmount * suggestedPct / 100) : null
  const handlingFee = orderAmount && suggestedPct ? (orderAmount * (100 - suggestedPct) / 100) : null

  const submit = async (e) => {
    e.preventDefault()
    setLoading(true)
    try {
      await returnsApi.create({ order_id: orderId, reason, description: desc })
      toast.success('בקשת ההחזרה נפתחה — נחזור אליך תוך 24 שעות')
      onCreated?.()
      onClose()
    } catch (err) {
      toast.error(err.response?.data?.detail || 'שגיאה בפתיחת בקשת ההחזרה')
    } finally { setLoading(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
      <div className="card p-6 max-w-md w-full">
        <h3 className="font-bold text-gray-900 mb-1">בקשת החזרה</h3>
        <p className="text-sm text-gray-500 mb-4">ניתן להחזיר תוך 14 יום מקבלת המשלוח · נחזור תוך 24 שעות</p>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">סיבת ההחזרה</label>
            <select className="input-field" value={reason} onChange={(e) => setReason(e.target.value)} required>
              <option value="">בחר סיבה...</option>
              {Object.entries(RETURN_REASONS).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>

          {/* Refund preview based on policy */}
          {reason && (
            <div className={`rounded-xl p-3 text-sm border ${isFullRefund ? 'bg-green-50 border-green-200' : 'bg-yellow-50 border-yellow-200'}`}>
              {isFullRefund ? (
                <div className="space-y-0.5">
                  <p className="font-semibold text-green-800">✅ זכאי להחזר מלא 100%</p>
                  {estimatedRefund && <p className="text-green-700">זיכוי משוער: ₪{estimatedRefund.toFixed(2)}</p>}
                  <p className="text-green-600 text-xs">אנחנו מממנים את עלות השילוח החזרה</p>
                </div>
              ) : (
                <div className="space-y-0.5">
                  <p className="font-semibold text-yellow-800">⚠ זכאי להחזר חלקי 90%</p>
                  {estimatedRefund && <p className="text-yellow-700">זיכוי משוער: ₪{estimatedRefund.toFixed(2)}</p>}
                  {handlingFee && handlingFee > 0 && <p className="text-yellow-600 text-xs">דמי טיפול: ₪{handlingFee.toFixed(2)} (10%) · עלות שילוח החזרה בחשבונך</p>}
                </div>
              )}
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">פרטים נוספים (אופציונלי)</label>
            <textarea
              className="input-field resize-none"
              rows={3}
              placeholder="תאר את הבעיה בפירוט כדי לזרז את הטיפול..."
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
            />
          </div>
          <div className="flex gap-3">
            <button type="button" onClick={onClose} className="btn-secondary flex-1">ביטול</button>
            <button type="submit" disabled={!reason || loading} className="btn-primary flex-1 flex items-center justify-center gap-2">
              {loading && <Loader2 className="w-4 h-4 animate-spin" />} שלח בקשה
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
  const [returnOrder, setReturnOrder] = useState(null) // { id, total }
  const [tab, setTab] = useState('all') // 'all' | 'unpaid' | 'refunds' | 'returns'
  const [returns, setReturns] = useState([])
  const [cancellingReturn, setCancellingReturn] = useState(null)
  const [refunds, setRefunds] = useState([])
  const [selectedIds, setSelectedIds] = useState(new Set())
  const [bulkPaying, setBulkPaying] = useState(false)

  useEffect(() => {
    ordersApi.getAll().then(({ data }) => setOrders(data.orders || [])).catch(() => toast.error('שגיאה בטעינת הזמנות')).finally(() => setIsLoading(false))
    const loadReturns = () => returnsApi.getAll().then(({ data }) => setReturns(data.returns || [])).catch(() => {})
    loadReturns()
    paymentsApi.getRefunds().then(({ data }) => setRefunds(data.refunds || [])).catch(() => {})
  }, [])

  const refreshReturns = () =>
    returnsApi.getAll().then(({ data }) => setReturns(data.returns || [])).catch(() => {})

  const handleCancelReturn = async (returnId) => {
    if (!window.confirm('לבטל את בקשת ההחזרה?')) return
    setCancellingReturn(returnId)
    try {
      await returnsApi.cancel(returnId)
      toast.success('בקשת ההחזרה בוטלה')
      refreshReturns()
    } catch (err) {
      toast.error(err.response?.data?.detail || 'שגיאה בביטול')
    } finally { setCancellingReturn(null) }
  }

  const pendingOrders = orders.filter((o) => o.status === 'pending_payment')

  const handleSelect = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleSelectAll = () => {
    if (selectedIds.size === pendingOrders.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(pendingOrders.map((o) => o.id)))
    }
  }

  const selectedTotal = pendingOrders.filter((o) => selectedIds.has(o.id)).reduce((sum, o) => sum + Number(o.total), 0)

  const handleBulkPay = async () => {
    if (selectedIds.size === 0) return
    setBulkPaying(true)
    try {
      const ids = [...selectedIds]
      if (ids.length === 1) {
        const { data } = await paymentsApi.createCheckout(ids[0])
        window.location.href = data.checkout_url
      } else {
        const { data } = await paymentsApi.createMultiCheckout(ids)
        window.location.href = data.checkout_url
      }
    } catch (err) { toast.error(err.response?.data?.detail || 'שגיאה בתשלום') }
    finally { setBulkPaying(false) }
  }

  const handleOrderDelete = (orderId, newStatus = null) => {
    if (newStatus) {
      setOrders((prev) => prev.map((o) => o.id === orderId ? { ...o, status: newStatus } : o))
    } else {
      setOrders((prev) => prev.filter((o) => o.id !== orderId))
      setSelectedIds((prev) => { const next = new Set(prev); next.delete(orderId); return next })
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="section-title">ההזמנות שלי</h1>
        <p className="text-gray-500 mt-1">מעקב אחר הזמנות והחזרות</p>
      </div>

      <div className="flex gap-2 border-b border-gray-200">
        {[
          { id: 'all',     label: `כל ההזמנות (${orders.length})` },
          { id: 'unpaid',  label: `ממתינות לתשלום`, count: pendingOrders.length },
          { id: 'refunds', label: `החזרות כספיות`, count: refunds.length },
          { id: 'returns', label: `החזרת מוצרים (${returns.length})` },
        ].map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`px-5 py-2 text-sm font-medium border-b-2 transition-colors -mb-px flex items-center gap-1.5 ${
              tab === t.id ? 'border-brand-600 text-brand-600' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}>
            {t.label}
            {t.count > 0 && (
              <span className={`text-xs font-bold px-1.5 py-0.5 rounded-full ${
                tab === t.id ? 'bg-amber-500 text-white' : 'bg-amber-100 text-amber-700'
              }`}>{t.count}</span>
            )}
          </button>
        ))}
      </div>

      {isLoading && <div className="flex justify-center py-12"><Loader2 className="w-8 h-8 animate-spin text-brand-600" /></div>}

      {!isLoading && tab === 'all' && (
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
              {/* Bulk-pay banner shown when there are pending orders */}
              {pendingOrders.length > 0 && (
                <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 flex items-center justify-between gap-3 flex-wrap">
                  <div className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      checked={selectedIds.size > 0 && selectedIds.size === pendingOrders.length}
                      ref={(el) => { if (el) el.indeterminate = selectedIds.size > 0 && selectedIds.size < pendingOrders.length }}
                      onChange={handleSelectAll}
                      className="w-4 h-4 accent-brand-600 cursor-pointer"
                    />
                    <span className="text-sm font-medium text-amber-800">
                      {selectedIds.size > 0
                        ? selectedIds.size === 1
                          ? 'נבחרה הזמנה אחת לתשלום'
                          : `נבחרו ${selectedIds.size} הזמנות לתשלום`
                        : pendingOrders.length === 1
                          ? 'הזמנה אחת ממתינה לתשלום'
                          : `${pendingOrders.length} הזמנות ממתינות לתשלום`}
                    </span>
                  </div>
                  {selectedIds.size > 0 && (
                    <button
                      onClick={handleBulkPay}
                      disabled={bulkPaying}
                      className="btn-primary flex items-center gap-2 text-sm"
                    >
                      {bulkPaying ? <Loader2 className="w-4 h-4 animate-spin" /> : <CreditCard className="w-4 h-4" />}
                      שלם {selectedIds.size > 1 ? `${selectedIds.size} הזמנות` : 'הזמנה'} · ₪{selectedTotal.toFixed(2)}
                    </button>
                  )}
                </div>
              )}
              {orders.map((o) => (
                <OrderCard
                  key={o.id}
                  order={o}
                  onReturn={setReturnOrder}
                  onDelete={handleOrderDelete}
                  selected={selectedIds.has(o.id)}
                  onSelect={handleSelect}
                />
              ))}
            </div>
          )}
        </>
      )}

      {!isLoading && tab === 'unpaid' && (
        <div className="space-y-4">
          {pendingOrders.length === 0 ? (
            <div className="card p-12 text-center">
              <CheckCircle className="w-12 h-12 text-green-400 mx-auto mb-3" />
              <h3 className="font-semibold text-gray-700 mb-1">כל ההזמנות שולמו!</h3>
              <p className="text-sm text-gray-400 mb-4">אין הזמנות הממתינות לתשלום</p>
              <Link to="/parts" className="btn-primary">המשך קניות</Link>
            </div>
          ) : (
            <>
              {/* Prominent pay-all banner */}
              <div className="bg-amber-50 border-2 border-amber-300 rounded-xl p-5">
                <div className="flex items-center justify-between gap-4 flex-wrap">
                  <div className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      checked={selectedIds.size > 0 && selectedIds.size === pendingOrders.length}
                      ref={(el) => { if (el) el.indeterminate = selectedIds.size > 0 && selectedIds.size < pendingOrders.length }}
                      onChange={handleSelectAll}
                      className="w-4 h-4 accent-brand-600 cursor-pointer"
                    />
                    <div>
                      <p className="font-semibold text-amber-900">
                        {selectedIds.size > 0
                          ? selectedIds.size === 1
                            ? 'נבחרה הזמנה אחת לתשלום'
                            : `נבחרו ${selectedIds.size} הזמנות לתשלום`
                          : pendingOrders.length === 1
                            ? 'הזמנה אחת ממתינה לתשלום'
                            : `${pendingOrders.length} הזמנות ממתינות לתשלום`}
                      </p>
                      {selectedIds.size === 0 && (
                        <p className="text-xs text-amber-700 mt-0.5">סמן הזמנות ושלם הכל בתשלום אחד</p>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    {selectedIds.size === 0 && (
                      <button
                        onClick={() => setSelectedIds(new Set(pendingOrders.map((o) => o.id)))}
                        className="btn-secondary text-sm"
                      >
                        בחר הכל
                      </button>
                    )}
                    {selectedIds.size > 0 && (
                      <button
                        onClick={handleBulkPay}
                        disabled={bulkPaying}
                        className="btn-primary flex items-center gap-2"
                      >
                        {bulkPaying ? <Loader2 className="w-4 h-4 animate-spin" /> : <CreditCard className="w-4 h-4" />}
                        שלם {selectedIds.size > 1 ? `${selectedIds.size} הזמנות` : 'הזמנה'}
                        {' '}·{' '}
                        ₪{selectedTotal.toFixed(2)}
                      </button>
                    )}
                  </div>
                </div>
              </div>

              {/* Unpaid orders list */}
              <div className="space-y-3">
                {pendingOrders.map((o) => (
                  <OrderCard
                    key={o.id}
                    order={o}
                    onReturn={setReturnOrder}
                    onDelete={handleOrderDelete}
                    selected={selectedIds.has(o.id)}
                    onSelect={handleSelect}
                  />
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {!isLoading && tab === 'refunds' && (
        <div className="space-y-3">
          {refunds.length === 0 ? (
            <div className="card p-12 text-center">
              <Banknote className="w-12 h-12 text-gray-300 mx-auto mb-3" />
              <h3 className="font-semibold text-gray-700 mb-1">אין החזרות כספיות</h3>
              <p className="text-sm text-gray-400">ביטול הזמנה ששולמה יוצר החזר אוטומטי לכרטיס האשראי</p>
            </div>
          ) : refunds.map((r) => (
            <div key={r.id} className="card p-4">
              <div className="flex justify-between items-start gap-4">
                <div className="flex items-start gap-3">
                  <div className="w-9 h-9 rounded-xl bg-green-50 flex items-center justify-center flex-shrink-0">
                    <Banknote className="w-5 h-5 text-green-600" />
                  </div>
                  <div>
                    <p className="font-semibold text-gray-900 text-sm">{r.return_number}</p>
                    <p className="text-xs text-gray-500 mt-0.5">הזמנה: {r.order_number}</p>
                    {r.description && <p className="text-xs text-gray-400 mt-0.5">{r.description}</p>}
                    {r.date && (
                      <p className="text-xs text-gray-400 mt-0.5">
                        {format(new Date(r.date), 'dd/MM/yyyy HH:mm', { locale: he })}
                      </p>
                    )}
                  </div>
                </div>
                <div className="text-left flex-shrink-0">
                  <span className={`badge text-xs ${
                    r.status === 'approved' ? 'bg-green-100 text-green-700' :
                    r.status === 'pending'  ? 'bg-yellow-100 text-yellow-700' :
                    'bg-gray-100 text-gray-600'
                  }`}>
                    {r.status === 'approved' ? 'אושר ✓' : r.status === 'pending' ? 'בטיפול' : r.status}
                  </span>
                  {r.refund_amount && (
                    <p className="font-bold text-green-600 mt-1 text-base">₪{Number(r.refund_amount).toFixed(2)}</p>
                  )}
                  {r.original_amount && r.refund_amount && Number(r.original_amount) !== Number(r.refund_amount) && (
                    <p className="text-xs text-gray-400 line-through">₪{Number(r.original_amount).toFixed(2)}</p>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {!isLoading && tab === 'returns' && (
        <div className="space-y-3">
          {returns.length === 0 ? (
            <div className="card p-12 text-center">
              <RotateCcw className="w-12 h-12 text-gray-300 mx-auto mb-3" />
              <h3 className="font-semibold text-gray-700 mb-1">אין בקשות החזרה</h3>
              <p className="text-sm text-gray-400 mb-4">
                ניתן לפתוח בקשת החזרה מתוך הזמנה שנמסרה או שנשלחה
              </p>
              <button onClick={() => setTab('all')} className="btn-secondary text-sm">
                לכל ההזמנות
              </button>
            </div>
          ) : (
            <>
              <p className="text-xs text-gray-400 px-1">{returns.length} בקשות החזרה</p>
              {returns.map((r) => {
                const st = RETURN_STATUS_MAP[r.status] || { label: r.status, color: 'bg-gray-100 text-gray-600' }
                const reasonLabel = RETURN_REASONS[r.reason] || r.reason
                const isCancelling = cancellingReturn === r.id
                return (
                  <div key={r.id} className="card p-4 space-y-3">
                    {/* Header row */}
                    <div className="flex justify-between items-start gap-3">
                      <div className="flex items-start gap-3">
                        <div className="w-9 h-9 rounded-xl bg-orange-50 flex items-center justify-center flex-shrink-0">
                          <RotateCcw className="w-5 h-5 text-orange-500" />
                        </div>
                        <div>
                          <p className="font-semibold text-gray-900 text-sm">{r.return_number}</p>
                          {r.order_id && (
                            <p className="text-xs text-gray-500 mt-0.5">
                              הזמנה: <span className="text-brand-600 font-medium">{r.order_number || r.order_id?.slice(0, 8)}</span>
                            </p>
                          )}
                          {r.requested_at && (
                            <p className="text-xs text-gray-400 mt-0.5">
                              {format(new Date(r.requested_at), 'dd/MM/yyyy HH:mm', { locale: he })}
                            </p>
                          )}
                        </div>
                      </div>
                      <span className={`badge text-xs flex-shrink-0 ${st.color}`}>{st.label}</span>
                    </div>

                    {/* Reason + description */}
                    <div className="bg-gray-50 rounded-lg px-3 py-2 text-sm">
                      <span className="font-medium text-gray-700">סיבה: </span>
                      <span className="text-gray-600">{reasonLabel}</span>
                      {r.description && (
                        <p className="text-xs text-gray-500 mt-1">{r.description}</p>
                      )}
                    </div>

                    {/* Refund amount */}
                    {r.refund_amount && (
                      <div className="flex items-center gap-2">
                        <Banknote className="w-4 h-4 text-green-500" />
                        <span className="text-sm font-semibold text-green-600">
                          זיכוי מאושר: ₪{Number(r.refund_amount).toFixed(2)}
                        </span>
                      </div>
                    )}

                    {/* Actions */}
                    {r.status === 'pending' && (
                      <button
                        onClick={() => handleCancelReturn(r.id)}
                        disabled={isCancelling}
                        className="btn-secondary text-xs flex items-center gap-1.5 text-red-600 border-red-200 hover:bg-red-50"
                      >
                        {isCancelling
                          ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          : <Ban className="w-3.5 h-3.5" />}
                        בטל בקשה
                      </button>
                    )}
                  </div>
                )
              })}
            </>
          )}
        </div>
      )}

      {returnOrder && (
        <ReturnModal
          orderId={returnOrder.id}
          orderAmount={Number(returnOrder.total)}
          onClose={() => setReturnOrder(null)}
          onCreated={() => { refreshReturns(); setTab('returns') }}
        />
      )}
    </div>
  )
}
