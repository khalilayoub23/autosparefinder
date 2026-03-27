import { useEffect, useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { paymentsApi, ordersApi } from '../api/orders'
import { useCartStore } from '../stores/cartStore'
import { CheckCircle2, XCircle, Loader2, ShoppingBag, ArrowLeft, FileText } from 'lucide-react'
import toast from 'react-hot-toast'

export default function PaymentSuccess() {
  const [searchParams] = useSearchParams()
  const { clear } = useCartStore()
  const [status, setStatus] = useState('loading') // 'loading' | 'success' | 'error'
  const [orderData, setOrderData] = useState(null)
  const [errorMsg, setErrorMsg] = useState('')
  const [dlLoading, setDlLoading] = useState({}) // orderId -> bool

  const downloadInvoice = async (orderId, orderNumber) => {
    setDlLoading((p) => ({ ...p, [orderId]: true }))
    try {
      const { data } = await ordersApi.invoice(orderId)
      const url = URL.createObjectURL(new Blob([data], { type: 'application/pdf' }))
      const a = document.createElement('a')
      a.href = url
      a.download = `invoice-${orderNumber}.pdf`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      toast.error('שגיאה בהורדת החשבונית')
    } finally {
      setDlLoading((p) => ({ ...p, [orderId]: false }))
    }
  }

  useEffect(() => {
    const sessionId = searchParams.get('session_id')
    if (!sessionId) {
      setStatus('error')
      setErrorMsg('מזהה תשלום חסר בכתובת ה-URL')
      return
    }

    paymentsApi.verifySession(sessionId)
      .then(({ data }) => {
        setOrderData(data)
        setStatus('success')
        clear() // clear cart only after confirmed payment
      })
      .catch((err) => {
        setStatus('error')
        setErrorMsg(err.response?.data?.detail || 'שגיאה באימות התשלום')
      })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  if (status === 'loading') {
    return (
      <div className="max-w-lg mx-auto mt-24 text-center">
        <Loader2 className="w-12 h-12 animate-spin text-brand-600 mx-auto mb-4" />
        <p className="text-gray-600 text-lg">מאמת את התשלום...</p>
        <p className="text-sm text-gray-400 mt-1">אנא המתן מספר שניות</p>
      </div>
    )
  }

  if (status === 'error') {
    return (
      <div className="max-w-lg mx-auto mt-16 card p-10 text-center">
        <XCircle className="w-16 h-16 text-red-500 mx-auto mb-4" />
        <h2 className="text-2xl font-bold text-gray-900 mb-2">אימות התשלום נכשל</h2>
        <p className="text-gray-500 mb-6">{errorMsg}</p>
        <div className="flex gap-3 justify-center">
          <Link to="/cart" className="btn-primary">חזור לסל</Link>
          <Link to="/" className="btn-secondary">דף הבית</Link>
        </div>
      </div>
    )
  }

  const isMulti = orderData?.is_multi && orderData?.orders?.length > 1

  return (
    <div className="max-w-lg mx-auto mt-16 card p-10 text-center">
      <CheckCircle2 className="w-16 h-16 text-green-500 mx-auto mb-4" />
      <h2 className="text-2xl font-bold text-gray-900 mb-2">
        {isMulti ? `תשלום עבור ${orderData.orders.length} הזמנות בוצע!` : 'ההזמנה בוצעה בהצלחה!'}
      </h2>

      {isMulti ? (
        <div className="space-y-1 mb-4">
          {orderData.orders.map((o) => (
            <p key={o.order_id} className="text-gray-600 text-sm">
              <strong className="text-gray-800">{o.order_number}</strong> — ₪{parseFloat(o.amount).toFixed(2)}
            </p>
          ))}
        </div>
      ) : (
        orderData?.order_number && (
          <p className="text-gray-500 mb-1">
            מספר הזמנה: <strong className="text-gray-800">{orderData.order_number}</strong>
          </p>
        )
      )}

      <p className="text-sm text-gray-400 mb-2">תשלום אושר על ידי Stripe</p>
      {orderData?.amount && (
        <p className="text-sm text-gray-500 mb-6">
          סכום ששולם: <strong>₪{parseFloat(orderData.amount).toFixed(2)}</strong>
        </p>
      )}

      <div className="bg-green-50 border border-green-200 rounded-xl p-4 text-sm text-green-700 mb-6 text-right">
        <p className="font-medium mb-1">מה קורה עכשיו?</p>
        <ul className="space-y-1 text-green-600 text-xs list-disc list-inside">
          <li>הספק קיבל הודעה ויטפל בהזמנה</li>
          <li>חשבונית תישלח לאימייל שלך</li>
          <li>תוכל לעקוב אחרי הסטטוס בדף ההזמנות</li>
        </ul>
      </div>

      {/* Invoice download */}
      <div className="mb-6">
        {isMulti ? (
          <div className="space-y-2">
            <p className="text-xs text-gray-400 mb-2">הורד חשבונית לכל הזמנה:</p>
            {orderData.orders.map((o) => (
              <button
                key={o.order_id}
                onClick={() => downloadInvoice(o.order_id, o.order_number)}
                disabled={dlLoading[o.order_id]}
                className="w-full btn-secondary text-sm flex items-center justify-center gap-2"
              >
                {dlLoading[o.order_id]
                  ? <Loader2 className="w-4 h-4 animate-spin" />
                  : <FileText className="w-4 h-4 text-brand-600" />}
                חשבונית — {o.order_number}
              </button>
            ))}
          </div>
        ) : orderData?.order_id && (
          <button
            onClick={() => downloadInvoice(orderData.order_id, orderData.order_number)}
            disabled={dlLoading[orderData.order_id]}
            className="w-full btn-secondary text-sm flex items-center justify-center gap-2"
          >
            {dlLoading[orderData.order_id]
              ? <Loader2 className="w-4 h-4 animate-spin" />
              : <FileText className="w-4 h-4 text-brand-600" />}
            הורד חשבונית PDF
          </button>
        )}
      </div>

      <div className="flex gap-3 justify-center">
        <Link to="/orders" className="btn-primary flex items-center gap-2">
          <ShoppingBag className="w-4 h-4" /> הזמנות שלי
        </Link>
        <Link to="/parts" className="btn-secondary flex items-center gap-2">
          <ArrowLeft className="w-4 h-4" /> המשך קניות
        </Link>
      </div>
    </div>
  )
}
