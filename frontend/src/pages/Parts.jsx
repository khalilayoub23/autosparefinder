import { useState, useEffect } from 'react'
import { partsApi } from '../api/parts'
import { vehiclesApi } from '../api/vehicles'
import { useCartStore } from '../stores/cartStore'
import { useVehicleStore } from '../stores/vehicleStore'
import { Search, ShoppingCart, Car, Filter, Loader2, ChevronDown, Star, Package } from 'lucide-react'
import toast from 'react-hot-toast'

function PriceTag({ price, vat, shipping, total }) {
  return (
    <div className="bg-gray-50 rounded-lg p-3 text-sm space-y-1">
      <div className="flex justify-between text-gray-600"><span>מחיר</span><span className="font-medium">₪{price?.toFixed(2)}</span></div>
      <div className="flex justify-between text-gray-600"><span>מע״מ 17%</span><span>₪{vat?.toFixed(2)}</span></div>
      <div className="flex justify-between text-gray-600"><span>משלוח</span><span>₪{shipping?.toFixed(2)}</span></div>
      <div className="flex justify-between font-bold text-gray-900 border-t border-gray-200 pt-1"><span>סה״כ</span><span className="text-brand-600">₪{total?.toFixed(2)}</span></div>
    </div>
  )
}

function PartCard({ part, onAddToCart }) {
  const [expanded, setExpanded] = useState(false)
  const [comparisons, setComparisons] = useState([])
  const [loadingCompare, setLoadingCompare] = useState(false)

  const loadCompare = async () => {
    if (comparisons.length > 0) { setExpanded(!expanded); return }
    setLoadingCompare(true)
    try {
      const { data } = await partsApi.compare(part.id)
      setComparisons(data.comparisons || [])
      setExpanded(true)
    } catch {
      toast.error('לא הצלחנו לטעון מחירים')
    } finally {
      setLoadingCompare(false)
    }
  }

  return (
    <div className="card p-4 hover:shadow-md transition-shadow">
      <div className="flex justify-between items-start mb-2">
        <div>
          <h3 className="font-semibold text-gray-900">{part.name}</h3>
          <p className="text-xs text-gray-500">{part.manufacturer} · {part.category}</p>
        </div>
        <span className="badge bg-blue-50 text-blue-700">{part.part_type}</span>
      </div>
      {part.description && <p className="text-sm text-gray-600 mb-3 line-clamp-2">{part.description}</p>}
      {part.sku && <p className="text-xs text-gray-400 mb-3">SKU: {part.sku}</p>}

      <button onClick={loadCompare} disabled={loadingCompare} className="btn-secondary w-full text-sm flex items-center justify-center gap-2 mb-3">
        {loadingCompare ? <Loader2 className="w-4 h-4 animate-spin" /> : <ChevronDown className={`w-4 h-4 transition-transform ${expanded ? 'rotate-180' : ''}`} />}
        {expanded ? 'הסתר מחירים' : 'הצג מחירים ועלויות'}
      </button>

      {expanded && comparisons.length > 0 && (
        <div className="space-y-3">
          {comparisons.map((c, i) => (
            <div key={i} className="border border-gray-100 rounded-lg p-3">
              <div className="flex justify-between items-center mb-2">
                <div className="text-sm">
                  <span className="font-medium text-gray-700">אפשרות {i + 1}</span>
                  <span className="text-gray-400 mr-2">· אחריות {c.warranty_months} חודש</span>
                  <span className="text-gray-400">· {c.estimated_delivery}</span>
                </div>
                {i === 0 && <span className="badge bg-green-100 text-green-700">מומלץ</span>}
              </div>
              <PriceTag price={c.subtotal} vat={c.vat} shipping={c.shipping} total={c.total} />
              <button
                onClick={() => {
                  onAddToCart({ partId: part.id, supplierPartId: c.supplier_part_id || `sp-${i}`, name: part.name, manufacturer: part.manufacturer, price: c.subtotal, vat: c.vat })
                  toast.success(`${part.name} נוסף לסל`)
                }}
                className="btn-primary w-full text-sm mt-3 flex items-center justify-center gap-2"
              >
                <ShoppingCart className="w-4 h-4" /> הוסף לסל
              </button>
            </div>
          ))}
        </div>
      )}
      {expanded && comparisons.length === 0 && (
        <p className="text-sm text-gray-400 text-center py-2">אין ספקים זמינים כרגע</p>
      )}
    </div>
  )
}

export default function Parts() {
  const { addItem } = useCartStore()
  const { vehicles, selectedVehicle, loadVehicles, selectVehicle } = useVehicleStore()
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('')
  const [categories, setCategories] = useState([])
  const [parts, setParts] = useState([])
  const [isLoading, setIsLoading] = useState(false)
  const [searched, setSearched] = useState(false)
  const [newPlate, setNewPlate] = useState('')
  const [addingVehicle, setAddingVehicle] = useState(false)

  useEffect(() => {
    loadVehicles()
    partsApi.categories().then(({ data }) => setCategories(data.categories || []))
  }, [])

  const search = async () => {
    if (!query.trim() && !selectedVehicle && !category) return
    setIsLoading(true)
    setSearched(true)
    try {
      const { data } = await partsApi.search(query, selectedVehicle?.id, category, 20)
      setParts(data.parts || [])
    } catch {
      toast.error('שגיאה בחיפוש')
      setParts([])
    } finally {
      setIsLoading(false)
    }
  }

  const addVehicle = async () => {
    if (!/^\d{2}-?\d{3}-?\d{2}$/.test(newPlate.replace(/-/g, '').padStart(7, '0')) && newPlate.length < 5) {
      toast.error('לוחית לא תקינה')
      return
    }
    setAddingVehicle(true)
    try {
      const { data } = await vehiclesApi.identify(newPlate)
      toast.success(`✅ ${data.manufacturer} ${data.model} ${data.year}`)
      await loadVehicles()
      setNewPlate('')
    } catch {
      toast.error('לא מצאנו רכב עם לוחית זו')
    } finally {
      setAddingVehicle(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="section-title">חיפוש חלקי חילוף</h1>
        <p className="text-gray-500 mt-1">חפש לפי שם חלק, קטגוריה או רכב</p>
      </div>

      {/* Vehicle selector */}
      <div className="card p-4">
        <div className="flex items-center gap-2 mb-3">
          <Car className="w-5 h-5 text-brand-600" />
          <h3 className="font-semibold text-gray-900">הרכב שלי</h3>
        </div>
        <div className="flex flex-wrap gap-2 mb-3">
          {vehicles.map((v) => (
            <button
              key={v.id}
              onClick={() => selectVehicle(v)}
              className={`px-4 py-2 rounded-xl text-sm font-medium border transition-all ${
                selectedVehicle?.id === v.id ? 'bg-brand-600 text-white border-brand-600' : 'bg-white text-gray-700 border-gray-200 hover:border-brand-400'
              }`}
            >
              <span>{v.nickname || `${v.manufacturer} ${v.model}`}</span>
              <span className="mr-1 text-xs opacity-70">{v.year}</span>
            </button>
          ))}
          <button onClick={() => selectVehicle(null)} className={`px-4 py-2 rounded-xl text-sm border transition-all ${!selectedVehicle ? 'bg-gray-900 text-white border-gray-900' : 'bg-white text-gray-400 border-gray-200 hover:border-gray-400'}`}>
            כל הרכבים
          </button>
        </div>
        <div className="flex gap-2">
          <input className="input-field flex-1" placeholder="הוסף רכב – לוחית רישוי" dir="ltr" value={newPlate} onChange={(e) => setNewPlate(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && addVehicle()} />
          <button onClick={addVehicle} disabled={addingVehicle} className="btn-secondary flex items-center gap-2 whitespace-nowrap">
            {addingVehicle ? <Loader2 className="w-4 h-4 animate-spin" /> : <Car className="w-4 h-4" />}
            הוסף רכב
          </button>
        </div>
      </div>

      {/* Search bar */}
      <div className="card p-4">
        <div className="flex flex-col sm:flex-row gap-3">
          <div className="relative flex-1">
            <Search className="absolute right-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
            <input
              className="input-field pr-10"
              placeholder="חפש חלק... (פילטר שמן, רפידות בלם, מצמד...)"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && search()}
            />
          </div>
          <select className="input-field sm:w-48" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">כל הקטגוריות</option>
            {categories.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <button onClick={search} disabled={isLoading} className="btn-primary flex items-center gap-2 whitespace-nowrap">
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
            חפש
          </button>
        </div>
      </div>

      {/* Results */}
      {isLoading && (
        <div className="flex justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-brand-600" />
        </div>
      )}

      {!isLoading && searched && parts.length === 0 && (
        <div className="card p-12 text-center">
          <Package className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <h3 className="font-semibold text-gray-700 mb-1">לא נמצאו חלקים</h3>
          <p className="text-sm text-gray-400">נסה חיפוש אחר או שאל את ה-AI בצ׳אט</p>
        </div>
      )}

      {!isLoading && parts.length > 0 && (
        <>
          <p className="text-sm text-gray-500">נמצאו <strong>{parts.length}</strong> חלקים</p>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {parts.map((p) => <PartCard key={p.id} part={p} onAddToCart={addItem} />)}
          </div>
        </>
      )}
    </div>
  )
}
