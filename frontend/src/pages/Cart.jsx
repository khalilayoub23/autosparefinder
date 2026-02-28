import { useState } from 'react'
import { useCartStore } from '../stores/cartStore'
import { useAuthStore } from '../stores/authStore'
import { paymentsApi, ordersApi } from '../api/orders'
import { ShoppingCart, Trash2, Plus, Minus, ArrowLeft, CreditCard, Loader2, CheckCircle2 } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'

export default function Cart() {
  const { items, removeItem, updateQty, totals, clear } = useCartStore()
  const { user } = useAuthStore()
  const navigate = useNavigate()
  const [isOrdering, setIsOrdering] = useState(false)
  const [address, setAddress] = useState({ street: '', city: '', postal_code: '', country: 'Israel' })
  const [step, setStep] = useState('cart') // 'cart' | 'address' | 'payment' | 'done'
  const [orderId, setOrderId] = useState(null)
  const [orderNumber, setOrderNumber] = useState(null)
  const t = totals()

  const handleCreateOrder = async () => {
    if (!address.street || !address.city) { toast.error('יש למלא כתובת מלאה'); return }
    setIsOrdering(true)
    try {
      const payload = {
        items: items.map((i) => ({ part_id: i.partId, supplier_part_id: i.supplierPartId, quantity: i.quantity })),
        shipping_address: address,
      }
      const { data: order } = await ordersApi.create(payload)
      setOrderId(order.order_id)
      setOrderNumber(order.order_number)

      const { data: intent } = await paymentsApi.createIntent(order.order_id)
      // With real Stripe: load Stripe Elements here
      // For dev: auto-confirm
      await paymentsApi.confirm(intent.payment_intent_id)

      clear()
      setStep('done')
      toast.success('ההזמנה בוצעה בהצלחה!')
    } catch (err) {
      toast.error(err.response?.data?.error || 'שגיאה ביצירת הזמנה')
    } finally {
      setIsOrdering(false)
    }
  }

  if (step === 'done') {
    return (
      <div className="max-w-lg mx-auto mt-16 card p-10 text-center">
        <CheckCircle2 className="w-16 h-16 text-green-500 mx-auto mb-4" />
        <h2 className="text-2xl font-bold text-gray-900 mb-2">הזמנה בוצעה!</h2>
        <p className="text-gray-500 mb-1">מספר הזמנה: <strong>{orderNumber}</strong></p>
        <p className="text-sm text-gray-400 mb-6">קבלה נשלחה לאימייל {user?.email}</p>
        <div className="flex gap-3 justify-center">
          <Link to="/orders" className="btn-primary">הזמנות שלי</Link>
          <Link to="/parts" className="btn-secondary">המשך קניות</Link>
        </div>
      </div>
    )
  }

  if (items.length === 0) {
    return (
      <div className="max-w-lg mx-auto mt-16 card p-12 text-center">
        <ShoppingCart className="w-14 h-14 text-gray-300 mx-auto mb-4" />
        <h2 className="text-xl font-bold text-gray-700 mb-2">הסל ריק</h2>
        <p className="text-sm text-gray-400 mb-6">הוסף חלקים מעמוד החיפוש</p>
        <Link to="/parts" className="btn-primary">חפש חלקים</Link>
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="section-title">סל קניות <span className="text-base font-normal text-gray-400">({t.count} פריטים)</span></h1>
        <div className="flex gap-2">
          {['cart', 'address', 'payment'].map((s, i) => (
            <div key={s} className={`w-3 h-3 rounded-full ${step === s ? 'bg-brand-600' : i < ['cart','address','payment'].indexOf(step) ? 'bg-brand-300' : 'bg-gray-200'}`} />
          ))}
        </div>
      </div>

      {step === 'cart' && (
        <>
          <div className="space-y-3">
            {items.map((item) => (
              <div key={item.supplierPartId} className="card p-4 flex items-center gap-4">
                <div className="flex-1 min-w-0">
                  <p className="font-medium text-gray-900 truncate">{item.name}</p>
                  <p className="text-xs text-gray-500">{item.manufacturer}</p>
                  <p className="text-brand-600 font-semibold mt-1">₪{(item.price * item.quantity).toFixed(2)}</p>
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={() => updateQty(item.supplierPartId, item.quantity - 1)} className="w-7 h-7 rounded-full border border-gray-300 flex items-center justify-center hover:bg-gray-100">
                    <Minus className="w-3 h-3" />
                  </button>
                  <span className="w-6 text-center font-medium text-gray-900">{item.quantity}</span>
                  <button onClick={() => updateQty(item.supplierPartId, item.quantity + 1)} className="w-7 h-7 rounded-full border border-gray-300 flex items-center justify-center hover:bg-gray-100">
                    <Plus className="w-3 h-3" />
                  </button>
                </div>
                <button onClick={() => removeItem(item.supplierPartId)} className="p-2 rounded-lg hover:bg-red-50 text-red-400 hover:text-red-600">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
          </div>

          <div className="card p-5 space-y-2 text-sm">
            <div className="flex justify-between text-gray-600"><span>סכום ביניים</span><span>₪{t.subtotal.toFixed(2)}</span></div>
            <div className="flex justify-between text-gray-600"><span>מע״מ 17%</span><span>₪{t.vat.toFixed(2)}</span></div>
            <div className="flex justify-between text-gray-600"><span>משלוח</span><span>₪{t.shipping.toFixed(2)}</span></div>
            <div className="flex justify-between text-base font-bold text-gray-900 border-t border-gray-200 pt-2">
              <span>סה״כ לתשלום</span><span className="text-brand-600">₪{t.total.toFixed(2)}</span>
            </div>
          </div>

          <button onClick={() => setStep('address')} className="btn-primary w-full flex items-center justify-center gap-2">
            המשך לכתובת משלוח <ArrowLeft className="w-4 h-4" />
          </button>
        </>
      )}

      {step === 'address' && (
        <div className="card p-6 space-y-4">
          <h3 className="font-bold text-gray-900">כתובת משלוח</h3>
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <label className="block text-sm font-medium text-gray-700 mb-1">רחוב ומספר</label>
              <input className="input-field" placeholder="הרצל 55" value={address.street} onChange={(e) => setAddress({ ...address, street: e.target.value })} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">עיר</label>
              <input className="input-field" placeholder="עכו" value={address.city} onChange={(e) => setAddress({ ...address, city: e.target.value })} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">מיקוד</label>
              <input className="input-field" placeholder="24100" value={address.postal_code} onChange={(e) => setAddress({ ...address, postal_code: e.target.value })} />
            </div>
          </div>
          <div className="flex gap-3">
            <button onClick={() => setStep('cart')} className="btn-secondary flex-1">חזור</button>
            <button onClick={() => setStep('payment')} disabled={!address.street || !address.city} className="btn-primary flex-1">המשך לתשלום</button>
          </div>
        </div>
      )}

      {step === 'payment' && (
        <div className="card p-6 space-y-4">
          <h3 className="font-bold text-gray-900">תשלום</h3>
          <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 text-sm text-blue-700">
            <CreditCard className="w-5 h-5 mb-2" />
            <p className="font-medium">תשלום מאובטח</p>
            <p className="text-xs mt-1 text-blue-600">הזמנה תאושר לאחר אימות תשלום בלבד. לא יוזמן שום דבר מהספק לפני כן.</p>
          </div>
          <div className="text-sm text-gray-600 space-y-1">
            <div className="flex justify-between"><span>כתובת</span><span>{address.street}, {address.city}</span></div>
            <div className="flex justify-between font-bold text-gray-900"><span>סה״כ</span><span className="text-brand-600">₪{t.total.toFixed(2)}</span></div>
          </div>
          <div className="flex gap-3">
            <button onClick={() => setStep('address')} className="btn-secondary flex-1">חזור</button>
            <button onClick={handleCreateOrder} disabled={isOrdering} className="btn-primary flex-1 flex items-center justify-center gap-2">
              {isOrdering ? <Loader2 className="w-4 h-4 animate-spin" /> : <CreditCard className="w-4 h-4" />}
              {isOrdering ? 'מעבד...' : `שלם ₪${t.total.toFixed(2)}`}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
