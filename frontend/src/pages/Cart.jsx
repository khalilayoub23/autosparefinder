import { useState, useEffect, useRef } from 'react'
import { useCartStore } from '../stores/cartStore'
import { useAuthStore } from '../stores/authStore'
import { cartApi, ordersApi, paymentsApi } from '../api/orders'
import api from '../api/client'
import { ShoppingCart, Trash2, Plus, Minus, ArrowLeft, CreditCard, Loader2, CheckCircle2, MapPin, Edit3 } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'

export default function Cart() {
  const { items, setItems, totals } = useCartStore()
  const { user } = useAuthStore()
  const navigate = useNavigate()
  const [isOrdering, setIsOrdering] = useState(false)
  const [address, setAddress] = useState({ street: '', city: '', postal_code: '', country: 'Israel' })
  const [profileAddress, setProfileAddress] = useState(null)
  const [useOtherAddress, setUseOtherAddress] = useState(false)
  const [step, setStep] = useState('cart') // 'cart' | 'address' | 'payment' | 'done'
  const [orderId, setOrderId] = useState(null)
  const [orderNumber, setOrderNumber] = useState(null)
  const [pendingCheckoutTotal, setPendingCheckoutTotal] = useState(null)
  const [selectedSupplierPartIds, setSelectedSupplierPartIds] = useState([])
  const createOrderLockRef = useRef(false)
  const t = totals()

  useEffect(() => {
    const availableIds = items
      .map((i) => i.supplierPartId)
      .filter(Boolean)

    setSelectedSupplierPartIds((prev) => {
      const prevIds = Array.isArray(prev) ? prev : []
      if (availableIds.length === 0) return []
      if (prevIds.length === 0) return [...availableIds]
      const keep = new Set(prevIds)
      return availableIds.filter((id) => keep.has(id))
    })
  }, [items])

  const selectedItems = items.filter((i) => selectedSupplierPartIds.includes(i.supplierPartId))
  const selectedSubtotal = selectedItems.reduce((sum, i) => sum + (Number(i.price) || 0) * (Number(i.quantity) || 0), 0)
  const selectedVat = Math.round(selectedSubtotal * 0.18 * 100) / 100
  const selectedShipping = selectedItems.length > 0
    ? selectedItems.reduce((sum, i) => sum + (i.shippingCost ?? 0), 0) || 91
    : 0
  const selectedTotal = Math.round((selectedSubtotal + selectedVat + selectedShipping) * 100) / 100
  const selectedCount = selectedItems.reduce((sum, i) => sum + (Number(i.quantity) || 0), 0)
  const payableTotal = typeof pendingCheckoutTotal === 'number' ? pendingCheckoutTotal : selectedTotal
  const allSelected = items.length > 0 && selectedItems.length === items.length

  const toggleSelectItem = (supplierPartId) => {
    setSelectedSupplierPartIds((prev) => {
      const ids = Array.isArray(prev) ? prev : []
      return ids.includes(supplierPartId)
        ? ids.filter((id) => id !== supplierPartId)
        : [...ids, supplierPartId]
    })
    resetPendingCheckout()
  }

  const toggleSelectAll = () => {
    if (allSelected) {
      setSelectedSupplierPartIds([])
    } else {
      setSelectedSupplierPartIds(items.map((i) => i.supplierPartId).filter(Boolean))
    }
    resetPendingCheckout()
  }

  const parseApiErrorMessage = (err, fallback) => {
    const data = err?.response?.data
    const detail = data?.detail
    const errorObj = data?.error_obj
    if (typeof detail === 'string') return detail
    if (detail && typeof detail === 'object') {
      if (typeof detail.message === 'string') return detail.message
      if (typeof detail.detail === 'string' && !['price_updated', 'part_unavailable'].includes(detail.detail)) return detail.detail
    }
    if (errorObj && typeof errorObj === 'object') {
      if (typeof errorObj.message === 'string') return errorObj.message
      if (typeof errorObj.detail === 'string' && !['price_updated', 'part_unavailable'].includes(errorObj.detail)) return errorObj.detail
    }
    if (typeof data?.error === 'string') return data.error
    if (typeof data?.message === 'string') return data.message
    return fallback
  }

  const mapServerCartItems = (serverItems = []) =>
    serverItems.map((item) => ({
      partId: item.partId,
      supplierPartId: item.supplierPartId || item.supplier_part_id || item.id,
      serverCartItemId: item.id,
      name: item.name,
      manufacturer: item.supplierName || 'Supplier',
      price: Number(item.price || 0),
      vat: 0,
      quantity: Number(item.quantity || 1),
    }))

  // Load profile address on mount
  useEffect(() => {
    api.get('/profile').then(({ data }) => {
      if (data.profile?.address || data.profile?.city) {
        const pa = {
          street: data.profile.address || '',
          city: data.profile.city || '',
          postal_code: data.profile.postal_code || '',
          country: 'Israel',
        }
        setProfileAddress(pa)
        setAddress(pa) // pre-fill
      }
    }).catch(() => {})

    const syncCart = async () => {
      try {
        const { data } = await cartApi.get()
        const serverItems = data.items || []
        if (serverItems.length === 0 && items.length > 0) {
          let syncAttempts = 0
          let syncSuccess = 0
          // Push local items to server one by one; skip items that fail (e.g. part no longer available)
          for (const item of items) {
            if (!item.partId) continue
            syncAttempts += 1
            try {
              await cartApi.addItem(item.partId, item.quantity)
              syncSuccess += 1
            } catch {
              // skip unavailable
            }
          }
          const refreshed = await cartApi.get()
          const synced = refreshed.data.items || []
          setItems(mapServerCartItems(synced))
          if (items.length > 0 && synced.length === 0 && (syncAttempts === 0 || syncSuccess === 0)) {
            toast.error('הסל המקומי לא סונכרן לשרת. יש להוסיף פריטים מחדש.')
          }
          return
        }
        // Server has items — always use server as source of truth after login
        setItems(mapServerCartItems(serverItems))
      } catch {
        // Keep local persisted cart when API is unavailable.
      }
    }

    syncCart()
  }, [])

  const syncCartFromResponse = (data) => {
    setItems(mapServerCartItems(data.items || []))
  }

  const resetPendingCheckout = () => {
    setOrderId(null)
    setOrderNumber(null)
    setPendingCheckoutTotal(null)
  }

  const handleRemoveItem = async (itemId) => {
    try {
      const { data } = await cartApi.removeItem(itemId)
      syncCartFromResponse(data)
      resetPendingCheckout()
    } catch {
      toast.error('שגיאה בעדכון הסל')
    }
  }

  const handleUpdateQty = async (item, quantity) => {
    try {
      await cartApi.removeItem(item.serverCartItemId)
      if (quantity > 0) {
        const { data } = await cartApi.addItem(item.partId, quantity)
        syncCartFromResponse(data)
      } else {
        const { data } = await cartApi.get()
        syncCartFromResponse(data)
      }
      resetPendingCheckout()
    } catch {
      toast.error('שגיאה בעדכון כמות')
    }
  }

  const handleCreateOrder = async () => {
    if (createOrderLockRef.current) return
    if (!address.street || !address.city) { toast.error('יש למלא כתובת מלאה'); return }
    if (selectedItems.length === 0) { toast.error('יש לבחור לפחות חלק אחד לתשלום'); return }

    createOrderLockRef.current = true
    setIsOrdering(true)
    let redirecting = false
    try {
      let activeOrderId = orderId

      // Create a new order only once. If supplier price changed, retry checkout on the same order.
      if (!activeOrderId) {
        // Ensure server cart is in sync with local cart before checkout
        const { data: cartData } = await cartApi.get()
        let refreshedCartItems = cartData.items || []
        if (refreshedCartItems.length === 0 && items.length > 0) {
          for (const item of items) {
            if (!item.partId) continue
            try { await cartApi.addItem(item.partId, item.quantity) } catch { /* skip unavailable */ }
          }
          const { data: refreshedCart } = await cartApi.get()
          refreshedCartItems = refreshedCart.items || []
        }

        let order
        if (refreshedCartItems.length > 0) {
          const checkoutResponse = await cartApi.checkout(address, selectedSupplierPartIds)
          order = checkoutResponse.data
        } else {
          const fallbackItems = selectedItems
            .filter((item) => item.partId && item.supplierPartId)
            .map((item) => ({
              part_id: item.partId,
              supplier_part_id: item.supplierPartId,
              quantity: Number(item.quantity) || 1,
            }))

          if (fallbackItems.length === 0) {
            throw new Error('לא ניתן ליצור הזמנה: הסל אינו מסונכרן')
          }

          const fallbackResponse = await ordersApi.create({
            items: fallbackItems,
            shipping_address: address,
          })
          order = fallbackResponse.data
        }

        activeOrderId = order.order_id
        setOrderId(order.order_id)
        setOrderNumber(order.order_number)
      }

      // Get real Stripe Checkout URL and redirect
      const { data: checkout } = await paymentsApi.createCheckout(activeOrderId)
      setPendingCheckoutTotal(null)
      if (checkout.checkout_url) {
        redirecting = true
        window.location.href = checkout.checkout_url
      } else {
        throw new Error('לא התקבל קישור לתשלום')
      }
    } catch (err) {
        const detailObj = err.response?.data?.detail_obj || err.response?.data?.error_obj
        if (detailObj?.detail === 'price_updated') {
          const nextTotal = Number(detailObj.new_total)
          if (Number.isFinite(nextTotal)) {
            setPendingCheckoutTotal(nextTotal)
          }
          const amountLabel = Number.isFinite(nextTotal) ? nextTotal.toFixed(2) : '---'
          toast(`מחיר ההזמנה עודכן ל-₪${amountLabel}. לחץ שלם שוב להמשך.`, { icon: '💰', duration: 6000 })
        } else if (detailObj?.detail === 'part_unavailable') {
          resetPendingCheckout()
          toast.error(typeof detailObj?.message === 'string' ? detailObj.message : 'חלק אינו זמין כרגע')
      } else {
        resetPendingCheckout()
        toast.error(parseApiErrorMessage(err, 'שגיאה ביצירת הזמנה'))
      }
    } finally {
      if (!redirecting) {
        setIsOrdering(false)
        createOrderLockRef.current = false
      }
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
          <div className="card p-4 flex items-center justify-between text-sm">
            <label className="flex items-center gap-2 text-gray-700 cursor-pointer">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={toggleSelectAll}
                className="w-4 h-4 accent-brand-600"
              />
              בחר הכל
            </label>
            <span className="text-gray-500">נבחרו {selectedItems.length} מתוך {items.length}</span>
          </div>

          <div className="space-y-3">
            {items.map((item) => (
              <div key={item.supplierPartId} className="card p-4 flex items-center gap-4">
                <input
                  type="checkbox"
                  checked={selectedSupplierPartIds.includes(item.supplierPartId)}
                  onChange={() => toggleSelectItem(item.supplierPartId)}
                  className="w-4 h-4 accent-brand-600 shrink-0"
                  aria-label={`select-${item.supplierPartId}`}
                />
                <div className="flex-1 min-w-0">
                  <p className="font-medium text-gray-900 truncate">{item.name}</p>
                  <p className="text-xs text-gray-500">{item.manufacturer}</p>
                  <p className="text-brand-600 font-semibold mt-1">₪{(item.price * item.quantity).toFixed(2)}</p>
                  {item.isEstimated && (
                    <span className="inline-block text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-full px-2 py-0.5 mt-0.5">~ מחיר משוער</span>
                  )}
                  {item.deliveryDays && (
                    <p className="text-xs text-gray-400 mt-0.5">
                      🚚 אספקה: {item.deliveryDays} ימים
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={() => handleUpdateQty(item, item.quantity - 1)} className="w-7 h-7 rounded-full border border-gray-300 flex items-center justify-center hover:bg-gray-100">
                    <Minus className="w-3 h-3" />
                  </button>
                  <span className="w-6 text-center font-medium text-gray-900">{item.quantity}</span>
                  <button onClick={() => handleUpdateQty(item, item.quantity + 1)} className="w-7 h-7 rounded-full border border-gray-300 flex items-center justify-center hover:bg-gray-100">
                    <Plus className="w-3 h-3" />
                  </button>
                </div>
                <button onClick={() => handleRemoveItem(item.serverCartItemId)} className="p-2 rounded-lg hover:bg-red-50 text-red-400 hover:text-red-600">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
          </div>

          <div className="card p-5 space-y-2 text-sm">
            <div className="flex justify-between text-gray-600"><span>סכום ביניים (נבחר)</span><span>₪{selectedSubtotal.toFixed(2)}</span></div>
            <div className="flex justify-between text-gray-600"><span>מע״מ 18% (נבחר)</span><span>₪{selectedVat.toFixed(2)}</span></div>
            <div className="flex justify-between text-gray-600"><span>משלוח (נבחר)</span><span>₪{selectedShipping.toFixed(2)}</span></div>
            <div className="flex justify-between text-base font-bold text-gray-900 border-t border-gray-200 pt-2">
              <span>סה״כ לתשלום</span><span className="text-brand-600">₪{selectedTotal.toFixed(2)}</span>
            </div>
            {selectedItems.length !== items.length && (
              <div className="flex justify-between text-xs text-gray-400 pt-1 border-t border-gray-100">
                <span>סה״כ כל הסל</span><span>₪{t.total.toFixed(2)}</span>
              </div>
            )}
          </div>

          {/* Delivery estimate notice */}
          {(() => {
            const maxDays = Math.max(...items.map((i) => i.deliveryDays ?? 14))
            const hasLongDelivery = items.some((i) => (i.deliveryDays ?? 0) > 18)
            return (
              <div className={`text-xs rounded-xl px-4 py-3 flex items-center gap-2 ${hasLongDelivery ? 'bg-orange-50 text-orange-700 border border-orange-200' : 'bg-blue-50 text-blue-700 border border-blue-100'}`}>
                <span>🚚</span>
                <span>
                  {hasLongDelivery
                    ? `חלק מהפריטים מיובאים — משלוח עד ${maxDays} ימי עסקים`
                    : `זמן אספקה משוער: ${maxDays} ימי עסקים`
                  }
                </span>
              </div>
            )
          })()}

          <button onClick={() => setStep('address')} disabled={selectedItems.length === 0} className="btn-primary w-full flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed">
            המשך לכתובת משלוח <ArrowLeft className="w-4 h-4" />
          </button>
        </>
      )}

      {step === 'address' && (
        <div className="card p-6 space-y-4">
          <h3 className="font-bold text-gray-900">כתובת משלוח</h3>

          {/* Profile address card */}
          {profileAddress && !useOtherAddress && (
            <div className="bg-blue-50 border border-blue-200 rounded-xl p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-start gap-3">
                  <MapPin className="w-5 h-5 text-blue-500 mt-0.5 shrink-0" />
                  <div>
                    <p className="font-medium text-gray-900">{profileAddress.street}</p>
                    <p className="text-sm text-gray-600">{profileAddress.city}{profileAddress.postal_code ? `, ${profileAddress.postal_code}` : ''}</p>
                  </div>
                </div>
                <button
                  onClick={() => { setUseOtherAddress(true); setAddress({ street: '', city: '', postal_code: '', country: 'Israel' }) }}
                  className="text-xs text-blue-600 hover:text-blue-800 flex items-center gap-1 shrink-0 mt-0.5"
                >
                  <Edit3 className="w-3 h-3" /> שנה כתובת
                </button>
              </div>
            </div>
          )}

          {/* No profile address or user chose other address */}
          {(!profileAddress || useOtherAddress) && (
            <>
              {useOtherAddress && (
                <button
                  onClick={() => { setUseOtherAddress(false); setAddress(profileAddress) }}
                  className="text-xs text-blue-600 hover:text-blue-800 flex items-center gap-1"
                >
                  ← חזור לכתובת המשלוח הרגילה
                </button>
              )}
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
            </>
          )}

          <div className="flex gap-3">
            <button onClick={() => setStep('cart')} className="btn-secondary flex-1">חזור</button>
            <button onClick={() => setStep('payment')} disabled={!address.street || !address.city || selectedItems.length === 0} className="btn-primary flex-1 disabled:opacity-50 disabled:cursor-not-allowed">המשך לתשלום</button>
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
            <div className="flex justify-between"><span>פריטים שנבחרו</span><span>{selectedCount}</span></div>
            <div className="flex justify-between"><span>כתובת</span><span>{address.street}, {address.city}</span></div>
            <div className="flex justify-between font-bold text-gray-900"><span>סה״כ</span><span className="text-brand-600">₪{payableTotal.toFixed(2)}</span></div>
            {typeof pendingCheckoutTotal === 'number' && Math.abs(pendingCheckoutTotal - selectedTotal) > 0.01 && (
              <p className="text-xs text-amber-700">הסכום עודכן לפי מחיר ספק עדכני.</p>
            )}
          </div>
          <div className="flex gap-3">
            <button onClick={() => setStep('address')} className="btn-secondary flex-1">חזור</button>
            <button onClick={handleCreateOrder} disabled={isOrdering} className="btn-primary flex-1 flex items-center justify-center gap-2">
              {isOrdering ? <Loader2 className="w-4 h-4 animate-spin" /> : <CreditCard className="w-4 h-4" />}
              {isOrdering ? 'מעבד...' : `שלם ₪${payableTotal.toFixed(2)}`}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
