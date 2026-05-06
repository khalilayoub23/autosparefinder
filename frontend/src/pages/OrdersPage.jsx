import React, { useCallback, useEffect, useMemo, useState } from 'react'
import StatCard, { SkeletonStatCard } from '../components/Dashboard/StatCard'
import { FaClock, FaTruck, FaCheckCircle, FaFilePdf, FaSyncAlt } from 'react-icons/fa'
import jsPDF from 'jspdf'
import autoTable from 'jspdf-autotable'
import toast from 'react-hot-toast'
import api from '../api/client'

const STATUS_META = {
  pending_payment: { label: 'ממתין לתשלום', cls: 'bg-amber-100 text-amber-800' },
  paid: { label: 'שולם', cls: 'bg-blue-100 text-blue-700' },
  confirmed: { label: 'אושר', cls: 'bg-blue-100 text-blue-700' },
  processing: { label: 'בעיבוד', cls: 'bg-indigo-100 text-indigo-700' },
  supplier_ordered: { label: 'הוזמן מספק', cls: 'bg-cyan-100 text-cyan-700' },
  shipped: { label: 'נשלח', cls: 'bg-blue-100 text-blue-800' },
  delivered: { label: 'הושלם', cls: 'bg-green-100 text-green-700' },
  cancelled: { label: 'בוטל', cls: 'bg-red-100 text-red-700' },
  refunded: { label: 'הוחזר', cls: 'bg-brand-100 text-brand-700' },
}

const FILTER_OPTIONS = [
  { value: '', label: 'כל הסטטוסים' },
  { value: 'pending_payment', label: STATUS_META.pending_payment.label },
  { value: 'paid', label: STATUS_META.paid.label },
  { value: 'processing', label: STATUS_META.processing.label },
  { value: 'supplier_ordered', label: STATUS_META.supplier_ordered.label },
  { value: 'shipped', label: STATUS_META.shipped.label },
  { value: 'delivered', label: STATUS_META.delivered.label },
  { value: 'cancelled', label: STATUS_META.cancelled.label },
]

const PENDING_STATUSES = new Set(['pending_payment', 'paid', 'confirmed', 'processing', 'supplier_ordered'])

function formatAmount(value) {
  const n = Number(value)
  return Number.isFinite(n) ? n.toFixed(2) : '0.00'
}

function formatDate(value) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString('he-IL')
}

function getStatusMeta(status) {
  return STATUS_META[status] || { label: status || 'לא ידוע', cls: 'bg-slate-100 text-slate-600' }
}

const OrdersPage = () => {
  const [orders, setOrders] = useState([])
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState('')
  const [exportingId, setExportingId] = useState(null)

  const loadOrders = useCallback(async () => {
    setLoading(true)
    try {
      const params = statusFilter ? { status: statusFilter } : {}
      const { data } = await api.get('/admin/orders', { params })
      setOrders(Array.isArray(data?.orders) ? data.orders : [])
    } catch {
      toast.error('שגיאה בטעינת הזמנות')
      setOrders([])
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  useEffect(() => {
    loadOrders()
  }, [loadOrders])

  const stats = useMemo(() => {
    const pending = orders.filter((o) => PENDING_STATUSES.has(o.status)).length
    const shipped = orders.filter((o) => o.status === 'shipped').length
    const completed = orders.filter((o) => o.status === 'delivered').length
    return { pending, shipped, completed }
  }, [orders])

  const exportOrderToPDF = async (order) => {
    try {
      setExportingId(order.id)
      const statusMeta = getStatusMeta(order.status)
      const orderNo = order.order_number || order.id
      const doc = new jsPDF()

      doc.setFontSize(22)
      doc.setTextColor(0, 163, 255)
      doc.text('AUTO SPARE', 10, 20)

      doc.setFontSize(10)
      doc.setTextColor(100, 116, 139)
      doc.text('Spare Finder Admin - Order Export', 10, 28)

      doc.setFontSize(13)
      doc.setTextColor(15, 23, 42)
      doc.text(`Order: #${orderNo}`, 10, 42)

      autoTable(doc, {
        startY: 50,
        head: [['Field', 'Value']],
        body: [
          ['Order Number', String(orderNo)],
          ['Customer', order.user_name || '-'],
          ['Email', order.user_email || '-'],
          ['Date', formatDate(order.created_at) || '-'],
          ['Total (ILS)', `₪${formatAmount(order.total)}`],
          ['Status', statusMeta.label],
        ],
        headStyles: { fillColor: [0, 163, 255] },
        styles: { fontSize: 10 },
      })

      doc.save(`AutoSpare_Order_${orderNo}.pdf`)
      toast.success(`PDF עבור הזמנה #${orderNo} הופק בהצלחה`, {
        position: 'bottom-left',
        style: { borderRadius: '14px', background: '#00A3FF', color: '#fff', fontWeight: 'bold' },
      })
    } catch {
      toast.error('שגיאה בהפקת קובץ ה-PDF', { position: 'bottom-left' })
    } finally {
      setExportingId(null)
    }
  }

  return (
    <div className="space-y-6 lg:space-y-8">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-2xl sm:text-3xl font-black text-brand-navy">הזמנות</h1>
        <div className="flex items-center gap-2">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="input-field !py-2 !text-sm w-44"
          >
            {FILTER_OPTIONS.map((opt) => (
              <option key={opt.value || 'all'} value={opt.value}>{opt.label}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={loadOrders}
            className="btn-secondary !py-2 !px-3 text-sm flex items-center gap-2"
            title="רענן הזמנות"
          >
            <FaSyncAlt className="w-3.5 h-3.5" />
            רענן
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 lg:gap-6">
        {loading ? (
          <>
            <SkeletonStatCard />
            <SkeletonStatCard />
            <SkeletonStatCard />
          </>
        ) : (
          <>
            <StatCard label="ממתין" value={stats.pending} icon={FaClock} colorClass="bg-amber-100 text-amber-600" />
            <StatCard label="נשלח" value={stats.shipped} icon={FaTruck} colorClass="bg-blue-100 text-blue-600" />
            <StatCard label="הושלם" value={stats.completed} icon={FaCheckCircle} colorClass="bg-green-100 text-green-600" />
          </>
        )}
      </div>

      <div className="bg-white rounded-brand border border-brand-border overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-right">
            <thead className="bg-brand-surface border-b border-brand-border">
              <tr>
                <th className="py-3 px-4 sm:px-6 text-xs sm:text-sm font-bold text-slate-500 whitespace-nowrap">מספר הזמנה</th>
                <th className="py-3 px-4 sm:px-6 text-xs sm:text-sm font-bold text-slate-500 whitespace-nowrap">לקוח</th>
                <th className="py-3 px-4 sm:px-6 text-xs sm:text-sm font-bold text-slate-500 whitespace-nowrap">תאריך</th>
                <th className="py-3 px-4 sm:px-6 text-xs sm:text-sm font-bold text-slate-500 text-left whitespace-nowrap">סה״כ</th>
                <th className="py-3 px-4 sm:px-6 text-xs sm:text-sm font-bold text-slate-500 text-center whitespace-nowrap">סטטוס</th>
                <th className="py-3 px-4 sm:px-6 text-xs sm:text-sm font-bold text-slate-500 text-left whitespace-nowrap">פעולות</th>
              </tr>
            </thead>
            <tbody>
              {!loading && orders.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-12 text-center text-slate-400 font-medium">אין הזמנות להצגה</td>
                </tr>
              )}

              {orders.map((order) => {
                const statusMeta = getStatusMeta(order.status)
                const orderNo = order.order_number || order.id
                return (
                  <tr key={order.id} className="hover:bg-brand-surface/70 transition-colors border-b border-brand-border last:border-0">
                    <td className="py-4 px-4 sm:px-6 font-bold text-brand-blue whitespace-nowrap">#{orderNo}</td>
                    <td className="py-4 px-4 sm:px-6">
                      <p className="font-medium text-brand-navy leading-tight">{order.user_name || '-'}</p>
                      <p className="text-xs text-slate-500 mt-0.5" dir="ltr">{order.user_email || '-'}</p>
                    </td>
                    <td className="py-4 px-4 sm:px-6 text-slate-500 text-sm whitespace-nowrap">{formatDate(order.created_at)}</td>
                    <td className="py-4 px-4 sm:px-6 text-left font-bold text-brand-navy whitespace-nowrap">₪{formatAmount(order.total)}</td>
                    <td className="py-4 px-4 sm:px-6 text-center">
                      <span className={`px-3 py-1 rounded-full text-[11px] font-bold whitespace-nowrap ${statusMeta.cls}`}>
                        {statusMeta.label}
                      </span>
                    </td>
                    <td className="py-4 px-4 sm:px-6 text-left">
                      <button
                        type="button"
                        onClick={() => exportOrderToPDF(order)}
                        disabled={exportingId === order.id}
                        className="inline-flex items-center gap-2 px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-600 text-xs font-bold rounded-lg transition-colors border border-slate-200 disabled:opacity-60"
                      >
                        <FaFilePdf className="text-red-500" />
                        {exportingId === order.id ? 'מייצא...' : 'Export'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

export default OrdersPage
