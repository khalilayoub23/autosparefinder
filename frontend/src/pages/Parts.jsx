import { useState, useEffect, useRef } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { partsApi } from '../api/parts'
import { useCartStore } from '../stores/cartStore'
import { useVehicleStore } from '../stores/vehicleStore'
import { Search, ShoppingCart, Car, Loader2, ChevronDown, Package, SlidersHorizontal, X, Camera, Mic, MicOff, Hash, CheckCircle, AlertCircle, Truck, Shield, Tag, ChevronRight, Link2, Bot } from 'lucide-react'
import toast from 'react-hot-toast'

function PriceTag({ price, vat, shipping, total }) {
  return (
    <div className="bg-gray-50 rounded-lg p-3 text-sm space-y-1">
      <div className="flex justify-between text-gray-600"><span>מחיר נטו</span><span className="font-medium">₪{price?.toFixed(0)}</span></div>
      <div className="flex justify-between text-gray-500"><span>מע״מ 17%</span><span>₪{vat?.toFixed(0)}</span></div>
      <div className="flex justify-between text-gray-500"><span>משלוח</span><span>₪{shipping?.toFixed(0)}</span></div>
      <div className="flex justify-between font-bold text-gray-900 border-t border-gray-200 pt-1 mt-1">
        <span>סה״כ לתשלום</span>
        <span className="text-brand-600 text-base">₪{total?.toFixed(0)}</span>
      </div>
    </div>
  )
}

const PART_TYPE_COLOR = {
  'Original':    'bg-blue-50 text-blue-700 border-blue-200',
  'OEM':         'bg-blue-50 text-blue-700 border-blue-200',
  'מקורי':       'bg-blue-50 text-blue-700 border-blue-200',
  'Aftermarket': 'bg-amber-50 text-amber-700 border-amber-200',
  'חליפי':       'bg-amber-50 text-amber-700 border-amber-200',
  'Refurbished': 'bg-purple-50 text-purple-700 border-purple-200',
  'משופץ':       'bg-purple-50 text-purple-700 border-purple-200',
  'unknown':     'bg-gray-50 text-gray-500 border-gray-200',
}

const PART_TYPE_LABEL = {
  'Original':    'מקורי',
  'מקורי':       'מקורי',
  'OEM':         'OEM',
  'Aftermarket': 'חליפי',
  'חליפי':       'חליפי',
  'Refurbished': 'משופץ',
  'משופץ':       'משופץ',
  'unknown':     'כללי',
}

// Category → accent colour (left border + header tint)
const CATEGORY_ACCENT = {
  'בלמים':           { border: 'border-l-red-400',    bg: 'bg-red-50',    icon: '🛑', text: 'text-red-700' },
  'מנוע':            { border: 'border-l-orange-400', bg: 'bg-orange-50', icon: '⚙️', text: 'text-orange-700' },
  'מתלה':            { border: 'border-l-yellow-400', bg: 'bg-yellow-50', icon: '🔧', text: 'text-yellow-700' },
  'היגוי':           { border: 'border-l-lime-400',   bg: 'bg-lime-50',   icon: '🎯', text: 'text-lime-700' },
  'תאורה':           { border: 'border-l-sky-400',    bg: 'bg-sky-50',    icon: '💡', text: 'text-sky-700' },
  'מיזוג':           { border: 'border-l-cyan-400',   bg: 'bg-cyan-50',   icon: '❄️', text: 'text-cyan-700' },
  'חשמל רכב':        { border: 'border-l-violet-400', bg: 'bg-violet-50', icon: '⚡', text: 'text-violet-700' },
  'דלק':             { border: 'border-l-emerald-400',bg: 'bg-emerald-50',icon: '⛽', text: 'text-emerald-700' },
  'פחיין ומרכב':     { border: 'border-l-blue-400',   bg: 'bg-blue-50',   icon: '🚗', text: 'text-blue-700' },
  'ריפוד ופנים':     { border: 'border-l-pink-400',   bg: 'bg-pink-50',   icon: '🪑', text: 'text-pink-700' },
  'גלגלים וצמיגים':  { border: 'border-l-stone-400',  bg: 'bg-stone-50',  icon: '🛞', text: 'text-stone-700' },
  'תיבת הילוכים':    { border: 'border-l-teal-400',   bg: 'bg-teal-50',   icon: '🔩', text: 'text-teal-700' },
  'כללי':            { border: 'border-l-gray-300',   bg: 'bg-gray-50',   icon: '📦', text: 'text-gray-600' },
}

function AvailabilityBadge({ availability, deliveryDays }) {
  const inStock = availability === 'in_stock'
  return (
    <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium ${inStock ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-amber-50 text-amber-700 border border-amber-200'}`}>
      {inStock ? <CheckCircle className="w-3 h-3" /> : <Truck className="w-3 h-3" />}
      {inStock ? 'במלאי' : `על הזמנה`}
      {deliveryDays && ` · ${deliveryDays} ימים`}
    </span>
  )
}

function PartCard({ part, onAddToCart }) {
  const suppliers = part.suppliers || (part.pricing ? [part.pricing] : [])

  const handleAddToCart = (supplierPartId, priceData) => {
    // For estimated-price parts there is no supplier row yet — use a
    // deterministic fallback key so the cart can still deduplicate correctly.
    const cartKey = supplierPartId || `fallback-${part.id}`
    onAddToCart({
      partId: part.id,
      supplierPartId: cartKey,
      name: part.name,
      manufacturer: part.manufacturer,
      price: priceData.subtotal ?? priceData.price_no_vat,
      vat: priceData.vat,
      deliveryDays: priceData.estimated_delivery_days ?? null,
      isEstimated: priceData.is_base_price_fallback ?? false,
    })
    toast.success(`${part.name} נוסף לסל 🛒`)
  }

  const typeColor = PART_TYPE_COLOR[part.part_type] || 'bg-gray-50 text-gray-600 border-gray-200'
  const typeLabel = PART_TYPE_LABEL[part.part_type] || part.part_type || 'כללי'
  const accent = CATEGORY_ACCENT[part.category] || CATEGORY_ACCENT['כללי']

  return (
    <div className={`card overflow-hidden hover:shadow-lg transition-all duration-200 border border-gray-100 border-l-4 ${accent.border} flex flex-col h-full`}>
      {/* Coloured category header strip */}
      <div className={`${accent.bg} px-4 pt-3 pb-2`}>
        <div className="flex justify-between items-start gap-2">
          <div className="flex-1 min-w-0">
            <h3 className="font-semibold text-gray-900 leading-snug line-clamp-2 min-h-[2.75rem] text-sm">{part.name}</h3>
            <p className={`text-xs mt-0.5 font-medium ${accent.text}`}>
              <span className="mr-1">{accent.icon}</span>
              {part.category}
            </p>
          </div>
          <span className={`shrink-0 text-xs px-2 py-0.5 rounded-full border font-medium ${typeColor}`}>{typeLabel}</span>
        </div>
        <p className="text-xs text-gray-500 mt-1">{part.manufacturer}</p>
      </div>

      {/* Body */}
      <div className="px-4 pt-2 pb-1 flex flex-col flex-1">
        <p className="text-xs text-gray-400 mb-2 min-h-[1rem]">{part.sku ? `SKU: ${part.sku}` : '\u00a0'}</p>

        {/* Supplier / pricing options */}
        {suppliers.length === 0 ? (
          <div className={`flex-1 flex flex-col items-center justify-center ${accent.bg} rounded-lg px-3 py-4 text-center gap-1`}>
            <Tag className={`w-4 h-4 ${accent.text} opacity-60`} />
            <span className={`text-xs font-medium ${accent.text}`}>מחיר על פניה</span>
            <span className="text-xs text-gray-400">צור קשר לקבלת הצעת מחיר</span>
          </div>
        ) : (
          <div className="space-y-2 flex-1">
            {suppliers.map((s, i) => (
              <div key={i} className={`rounded-xl border px-3 py-2.5 flex flex-col gap-2 ${
                i === 0
                  ? `border-l-2 ${accent.border} border-t border-r border-b border-gray-100 bg-white shadow-sm`
                  : 'border-gray-100 bg-gray-50'
              }`}>
                {/* Row 1: availability + warranty */}
                <div className="flex items-center justify-between gap-1">
                  <AvailabilityBadge availability={s.availability} deliveryDays={s.estimated_delivery_days} />
                  <div className="flex items-center gap-2 text-xs text-gray-500">
                    {s.warranty_months && (
                      <span className="flex items-center gap-1">
                        <Shield className="w-3 h-3" />
                        {s.warranty_months} חודשים
                      </span>
                    )}
                    {i === 0 && !s.is_base_price_fallback && <span className="text-xs font-medium text-green-600">✓ מומלץ</span>}
                    {s.is_base_price_fallback && <span className="text-xs text-amber-500 bg-amber-50 px-1.5 py-0.5 rounded-full border border-amber-200">~ מחיר משוער</span>}
                  </div>
                </div>
                {/* Row 2: total price large + breakdown */}
                <div>
                  <div className={`text-2xl font-bold text-left mb-1 ${accent.text}`}>₪{s.total?.toFixed(0)}</div>
                  <div className="grid grid-cols-3 gap-1 text-center text-xs text-gray-500 bg-gray-50 rounded-lg py-1.5">
                    <div><div className="font-semibold text-gray-700">₪{s.price_no_vat?.toFixed(0)}</div>נטו</div>
                    <div className="border-x border-gray-200"><div className="font-semibold text-gray-700">₪{s.vat?.toFixed(0)}</div>מע״מ</div>
                    <div><div className="font-semibold text-gray-700">₪{s.shipping?.toFixed(0)}</div>משלוח</div>
                  </div>
                </div>
                {/* Row 3: add to cart */}
                <button
                  onClick={() => handleAddToCart(s.supplier_part_id, s)}
                  className={`w-full text-sm flex items-center justify-center gap-2 py-2 rounded-lg font-medium transition-colors ${
                    i === 0
                      ? 'bg-brand-600 hover:bg-brand-700 text-white'
                      : 'bg-white hover:bg-brand-50 text-brand-700 border border-brand-200'
                  }`}
                >
                  <ShoppingCart className="w-4 h-4" /> הוסף לסל
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function Parts() {
  const { addItem } = useCartStore()
  const { vehicles, selectedVehicle, loadVehicles, selectVehicle, addVehicle: storeAddVehicle, removeVehicle } = useVehicleStore()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const PAGE_SIZE = 50

  const [searchMode, setSearchMode] = useState('vehicle')

  const [query, setQuery] = useState(searchParams.get('search') || '')
  const [category, setCategory] = useState('')
  const [categories, setCategories] = useState([])
  const [categoryCounts, setCategoryCounts] = useState({})
  const [parts, setParts] = useState([])
  const [isLoading, setIsLoading] = useState(false)
  const [searched, setSearched] = useState(false)
  const [page, setPage] = useState(0)
  const [totalCount, setTotalCount] = useState(0)

  const [suggestions, setSuggestions] = useState([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const suggestRef = useRef(null)

  const [newPlate, setNewPlate] = useState('')
  const [addingVehicle, setAddingVehicle] = useState(false)

  const [brands, setBrands] = useState([])
  const [manualManufacturer, setManualManufacturer] = useState('')
  const [manualModel, setManualModel] = useState('')
  const [manualYear, setManualYear] = useState('')
  const [sortBy, setSortBy] = useState('availability')
  const [filterAvail, setFilterAvail] = useState('')
  const [filterType, setFilterType] = useState('')

  const getSortParams = (sv) => {
    if (sv === 'price_asc')    return { sort_by: 'price_asc',    sort_dir: 'asc' }
    if (sv === 'price_desc')   return { sort_by: 'price_desc',   sort_dir: 'asc' }
    if (sv === 'name')         return { sort_by: 'name',         sort_dir: 'asc' }
    if (sv === 'availability') return { sort_by: 'availability', sort_dir: 'asc' }
    return                            { sort_by: 'name',         sort_dir: 'asc' }
  }

  const [recentSearches, setRecentSearches] = useState(() => {
    try { return JSON.parse(localStorage.getItem('recentPartSearches') || '[]') } catch { return [] }
  })
  const saveRecentSearch = (q) => {
    if (!q || q.trim().length < 2) return
    const updated = [q.trim(), ...recentSearches.filter((s) => s !== q.trim())].slice(0, 6)
    setRecentSearches(updated)
    localStorage.setItem('recentPartSearches', JSON.stringify(updated))
  }

  const displayParts = [...parts]
    .filter(p => !filterAvail || p.pricing?.availability === filterAvail)
    .filter(p => !filterType || p.part_type === filterType)
    .sort((a, b) => {
      if (sortBy === 'availability') {
        const aStock = a.pricing?.availability === 'in_stock' ? 0 : 1
        const bStock = b.pricing?.availability === 'in_stock' ? 0 : 1
        return aStock - bStock
      }
      return 0
    })

  const inStockCount = parts.filter(p => p.pricing?.availability === 'in_stock').length
  const onOrderCount = parts.filter(p => p.pricing?.availability === 'on_order').length
  const typeCounts = { Original: 0, Aftermarket: 0, Refurbished: 0 }
  parts.forEach(p => { if (typeCounts[p.part_type] !== undefined) typeCounts[p.part_type]++ })

  const urlSearchDone = useRef(false)
  useEffect(() => {
    const urlSearch   = searchParams.get('search')   || ''
    const urlCategory = searchParams.get('category') || ''
    loadVehicles()
    partsApi.categories().then(({ data }) => {
      setCategories(data.categories || [])
      setCategoryCounts(data.counts || {})
    })
    partsApi.brandsWithParts().then(({ data }) => setBrands(data.brands || []))
    if (urlSearch && !urlSearchDone.current) {
      urlSearchDone.current = true
      if (urlCategory) setCategory(urlCategory)
      setTimeout(() => {
        setSearchMode('manual')
        setSearched(true)
        setIsLoading(true)
        partsApi.search(urlSearch, null, urlCategory, 50, 0, 'name', 'asc')
          .then(({ data }) => { setParts(data.parts || []); setTotalCount(data.total || 0) })
          .catch(() => toast.error('שגיאה בטעינת תוצאות'))
          .finally(() => setIsLoading(false))
      }, 100)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (searched && sortBy !== 'availability') search(0)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sortBy])

  useEffect(() => {
    if (searched) search(0)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category])

  const recentSearchesRef = useRef(recentSearches)
  useEffect(() => { recentSearchesRef.current = recentSearches }, [recentSearches])

  useEffect(() => {
    if (query.length < 2) {
      setSuggestions([])
      const active = document.activeElement
      const inputEl = suggestRef.current?.querySelector('input')
      setShowSuggestions(active === inputEl && recentSearchesRef.current.length > 0)
      return
    }
    const timer = setTimeout(() => {
      partsApi.autocomplete(query).then(({ data }) => {
        setSuggestions(data.suggestions || [])
        setShowSuggestions((data.suggestions || []).length > 0)
      }).catch(() => {})
    }, 280)
    return () => clearTimeout(timer)
  }, [query])

  useEffect(() => {
    const handler = (e) => { if (suggestRef.current && !suggestRef.current.contains(e.target)) setShowSuggestions(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const switchMode = (mode) => {
    setSearchMode(mode)
    setParts([])
    setSearched(false)
    setTotalCount(0)
    setPage(0)
  }

  const buildManualQuery = () => {
    const parts = []
    if (manualManufacturer) parts.push(manualManufacturer)
    if (manualModel.trim()) parts.push(manualModel.trim())
    if (manualYear.trim()) parts.push(manualYear.trim())
    if (query.trim()) parts.push(query.trim())
    return parts.join(' ')
  }

  const activeFiltersCount = [manualManufacturer, manualModel, manualYear].filter(Boolean).length

  const clearManual = () => {
    setManualManufacturer('')
    setManualModel('')
    setManualYear('')
  }

  const search = async (pageNum = 0) => {
    const { sort_by, sort_dir } = getSortParams(sortBy)
    let q = query.trim()
    let vehicleId = null

    if (searchMode === 'vehicle') {
      vehicleId = selectedVehicle?.id || null
    } else if (searchMode === 'manual') {
      q = buildManualQuery()
    }

    if (!q && !vehicleId && !category) {
      toast.error('הזן שם חלק, בחר קטגוריה, או בחר רכב')
      return
    }

    setIsLoading(true)
    setSearched(true)
    try {
      const { data } = await partsApi.search(q, vehicleId, category, PAGE_SIZE, pageNum * PAGE_SIZE, sort_by, sort_dir)
      setParts(data.parts || [])
      setTotalCount(data.total || 0)
      setPage(pageNum)
      saveRecentSearch(q)
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'שגיאה בחיפוש')
    } finally {
      setIsLoading(false)
    }
  }

  const addVehicle = async () => {
    if (!newPlate.trim()) { toast.error('הזן לוחית רישוי'); return }
    setAddingVehicle(true)
    try {
      await storeAddVehicle(newPlate.trim().toUpperCase())
      setNewPlate('')
      toast.success('רכב נוסף בהצלחה!')
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'לא הצלחנו לאתר את הרכב')
    } finally {
      setAddingVehicle(false)
    }
  }

  // ─── Photo mode ───────────────────────────────────────────────────────────
  const fileInputRef = useRef(null)
  const [photoFile, setPhotoFile] = useState(null)
  const [photoPreview, setPhotoPreview] = useState(null)
  const [photoLoading, setPhotoLoading] = useState(false)
  const [photoResult, setPhotoResult] = useState(null)
  const [photoCandidates, setPhotoCandidates] = useState([])
  const [photoFallbackMfr, setPhotoFallbackMfr] = useState('')

  const handlePhotoFile = (file) => {
    if (!file) return
    setPhotoFile(file)
    setPhotoResult(null)
    setPhotoCandidates([])
    setPhotoFallbackMfr('')
    photoSearchCache.current = {}
    const reader = new FileReader()
    reader.onload = (e) => setPhotoPreview(e.target.result)
    reader.readAsDataURL(file)
  }

  const photoSearchCache = useRef({})

  const runPhotoPartsSearch = async (candidates, vehicleManufacturer) => {
    if (!candidates || candidates.length === 0) return
    const { sort_by, sort_dir } = getSortParams(sortBy)

    // Return cached result instantly
    const cacheKey = `${candidates[0]}__${vehicleManufacturer}`
    if (photoSearchCache.current[cacheKey]) {
      const c = photoSearchCache.current[cacheKey]
      setQuery(c.query); setParts(c.parts); setTotalCount(c.total); setSearched(true); setPage(0)
      setPhotoFallbackMfr(c.fallbackMfr || '')
      return
    }

    setIsLoading(true)
    try {
      // Fire all candidate queries in parallel (with + without manufacturer filter simultaneously)
      const top = candidates.slice(0, 3) // limit to top 3 to avoid over-fetching
      const withMfr = vehicleManufacturer
        ? top.map(c => partsApi.search(c, null, category, PAGE_SIZE, 0, sort_by, sort_dir, vehicleManufacturer).catch(() => null))
        : []
      const noMfr = top.map(c => partsApi.search(c, null, category, PAGE_SIZE, 0, sort_by, sort_dir, null).catch(() => null))

      const [mfrResults, generalResults] = await Promise.all([
        Promise.all(withMfr),
        Promise.all(noMfr),
      ])

      let foundParts = [], foundTotal = 0, usedQuery = top[0], usedMfr = false

      // Prefer manufacturer-filtered results first
      for (let i = 0; i < top.length; i++) {
        const r = mfrResults[i]?.data
        if (r && (r.parts || []).length > 0) {
          foundParts = r.parts; foundTotal = r.total || 0; usedQuery = top[i]; usedMfr = true; break
        }
      }

      // Fallback to general results
      if (!usedMfr) {
        for (let i = 0; i < top.length; i++) {
          const r = generalResults[i]?.data
          if (r && (r.parts || []).length > 0) {
            foundParts = r.parts; foundTotal = r.total || 0; usedQuery = top[i]; break
          }
        }
        if (vehicleManufacturer && foundParts.length > 0) {
          setPhotoFallbackMfr(vehicleManufacturer)
        } else {
          setPhotoFallbackMfr('')
        }
      } else {
        setPhotoFallbackMfr('')
      }

      // Cache for instant re-use
      photoSearchCache.current[cacheKey] = { query: usedQuery, parts: foundParts, total: foundTotal, fallbackMfr: vehicleManufacturer && !usedMfr ? vehicleManufacturer : '' }

      setQuery(usedQuery); setParts(foundParts); setTotalCount(foundTotal); setSearched(true); setPage(0)
    } finally {
      setIsLoading(false)
    }
  }

  // Re-run parts search when vehicle selection changes (photo mode only)
  useEffect(() => {
    if (searchMode === 'photo' && photoCandidates.length > 0) {
      runPhotoPartsSearch(photoCandidates, selectedVehicle?.manufacturer || '')
    }
  }, [selectedVehicle])

  const handlePhotoSearch = async () => {
    if (!photoFile) return
    setPhotoLoading(true)
    setIsLoading(true)
    try {
      const { data } = await partsApi.identifyFromImage(photoFile)
      setPhotoResult(data)
      if (data.identified_part) {
        const candidates = [
          data.identified_part_en,
          data.identified_part,
          ...(data.possible_names || []),
        ].filter(Boolean)
        setPhotoCandidates(candidates)
        await runPhotoPartsSearch(candidates, selectedVehicle?.manufacturer || '')
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'שגיאה בזיהוי התמונה')
    } finally {
      setPhotoLoading(false)
      setIsLoading(false)
    }
  }

  // ─── Voice mode ───────────────────────────────────────────────────────────
  const recognizerRef = useRef(null)
  const [isListening, setIsListening] = useState(false)
  const [voiceTranscript, setVoiceTranscript] = useState('')
  const [voiceFallbackMfr, setVoiceFallbackMfr] = useState('')
  const voiceSearchCache = useRef({})
  const voiceSupported = typeof window !== 'undefined' && ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window)

  const toggleVoice = () => {
    if (isListening) {
      recognizerRef.current?.stop()
      setIsListening(false)
      return
    }
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SR) { toast.error('הדפדפן לא תומך בזיהוי קול'); return }
    const rec = new SR()
    recognizerRef.current = rec
    rec.lang = 'he-IL'
    rec.interimResults = true
    rec.onresult = (e) => {
      const transcript = Array.from(e.results).map(r => r[0].transcript).join('')
      setVoiceTranscript(transcript)
    }
    rec.onerror = () => { setIsListening(false); toast.error('שגיאה בזיהוי קול') }
    rec.onend = () => {
      setIsListening(false)
      // Auto-clean car name/year from transcript once speech ends
      setVoiceTranscript(prev => cleanVoiceQuery(prev, selectedVehicle))
    }
    rec.start()
    setIsListening(true)
  }

  // Strip car model/year/brand words from voice transcript when a vehicle is selected
  const cleanVoiceQuery = (transcript, vehicle) => {
    if (!vehicle || !transcript) return transcript
    const stopWords = [
      vehicle.manufacturer, vehicle.model, vehicle.nickname,
      String(vehicle.year),
    ].filter(Boolean)
      .flatMap(s => s.toLowerCase().split(/[\s\-\/]+/))
      .filter(w => w.length > 1)
    const words = transcript.split(/\s+/)
    const cleaned = words.filter(w => !stopWords.includes(w.toLowerCase())).join(' ').trim()
    return cleaned || transcript // fallback to original if stripping leaves nothing
  }

  const runVoicePartsSearch = async (q, vehicleManufacturer) => {
    if (!q) return
    const { sort_by, sort_dir } = getSortParams(sortBy)

    // Build candidates: full phrase first, then individual words (longest first)
    const words = q.split(/\s+/).filter(w => w.length > 1)
    const candidates = [q, ...words.filter(w => w !== q)].filter((v, i, a) => a.indexOf(v) === i)

    const cacheKey = `${candidates[0]}__${vehicleManufacturer}`
    if (voiceSearchCache.current[cacheKey]) {
      const c = voiceSearchCache.current[cacheKey]
      setQuery(c.query); setParts(c.parts); setTotalCount(c.total); setSearched(true); setPage(0)
      setVoiceFallbackMfr(c.fallbackMfr || '')
      return
    }
    setIsLoading(true)
    try {
      // Fire all candidates × (with/without mfr) in parallel
      const top = candidates.slice(0, 4)
      const [mfrResults, genResults] = await Promise.all([
        Promise.all(
          vehicleManufacturer
            ? top.map(c => partsApi.search(c, null, category, PAGE_SIZE, 0, sort_by, sort_dir, vehicleManufacturer).catch(() => null))
            : top.map(() => Promise.resolve(null))
        ),
        Promise.all(
          top.map(c => partsApi.search(c, null, category, PAGE_SIZE, 0, sort_by, sort_dir, null).catch(() => null))
        ),
      ])

      let foundParts = [], foundTotal = 0, usedQuery = top[0], usedMfr = false

      // Prefer manufacturer-filtered results
      for (let i = 0; i < top.length; i++) {
        const r = mfrResults[i]?.data
        if (r && (r.parts || []).length > 0) {
          foundParts = r.parts; foundTotal = r.total || 0; usedQuery = top[i]; usedMfr = true; break
        }
      }
      // Fallback to general
      if (!usedMfr) {
        for (let i = 0; i < top.length; i++) {
          const r = genResults[i]?.data
          if (r && (r.parts || []).length > 0) {
            foundParts = r.parts; foundTotal = r.total || 0; usedQuery = top[i]; break
          }
        }
      }

      const fallbackMfr = !usedMfr && vehicleManufacturer && foundParts.length > 0 ? vehicleManufacturer : ''
      voiceSearchCache.current[cacheKey] = { query: usedQuery, parts: foundParts, total: foundTotal, fallbackMfr }
      setQuery(usedQuery); setParts(foundParts); setTotalCount(foundTotal); setSearched(true); setPage(0)
      setVoiceFallbackMfr(fallbackMfr)
    } catch { toast.error('שגיאה בחיפוש') }
    finally { setIsLoading(false) }
  }

  // Re-run voice search when vehicle changes
  useEffect(() => {
    if (searchMode === 'voice' && voiceTranscript.trim()) {
      const cleaned = cleanVoiceQuery(voiceTranscript.trim(), selectedVehicle)
      runVoicePartsSearch(cleaned, selectedVehicle?.manufacturer || '')
    }
  }, [selectedVehicle])

  const handleVoiceSearch = () => {
    const raw = voiceTranscript.trim()
    if (!raw) { toast.error('אמור שם חלק תחילה'); return }
    const cleaned = cleanVoiceQuery(raw, selectedVehicle)
    voiceSearchCache.current = {}
    setVoiceFallbackMfr('')
    runVoicePartsSearch(cleaned, selectedVehicle?.manufacturer || '')
  }

  // ─── VIN mode ─────────────────────────────────────────────────────────────
  const [vinInput, setVinInput] = useState('')
  const [vinLoading, setVinLoading] = useState(false)
  const [vinVehicle, setVinVehicle] = useState(null)
  const [vinPartQuery, setVinPartQuery] = useState('')

  const handleVinSearch = async (partQuery = vinPartQuery, pageNum = 0) => {
    if (vinInput.replace(/\s/g, '').length !== 17) { toast.error('VIN חייב להיות בן 17 תווים'); return }
    setVinLoading(true)
    setIsLoading(true)
    try {
      const { data } = await partsApi.searchByVin(vinInput.trim().toUpperCase(), partQuery, category, PAGE_SIZE, pageNum * PAGE_SIZE)
      setVinVehicle(data.vehicle || null)
      setParts(data.parts || [])
      setTotalCount(data.total || 0)
      setSearched(true)
      setPage(pageNum)
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'VIN לא זוהה')
    } finally {
      setVinLoading(false)
      setIsLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="section-title">חיפוש חלקי חילוף</h1>
        <p className="text-gray-500 mt-1">חפש לפי הרכב שלך או הזן פרטים ידנית</p>
      </div>

      {/* Mode toggle */}
      <div className="card p-1 flex flex-wrap gap-1">
        {[
          { key: 'vehicle', icon: <Car className="w-4 h-4" />, label: 'הרכב שלי' },
          { key: 'manual', icon: <SlidersHorizontal className="w-4 h-4" />, label: 'פרטי רכב' },
          { key: 'photo', icon: <Camera className="w-4 h-4" />, label: 'תמונה' },
          { key: 'voice', icon: <Mic className="w-4 h-4" />, label: 'קול' },
        ].map(({ key, icon, label }) => (
          <button
            key={key}
            onClick={() => switchMode(key)}
            className={`flex-1 min-w-[80px] flex items-center justify-center gap-1.5 py-2.5 px-3 rounded-xl text-sm font-medium transition-all ${
              searchMode === key ? 'bg-brand-600 text-white shadow' : 'text-gray-500 hover:text-gray-800'
            }`}
          >
            {icon}
            {label}
            {key === 'manual' && searchMode === 'manual' && activeFiltersCount > 0 && (
              <span className="bg-white text-brand-600 text-xs rounded-full w-5 h-5 flex items-center justify-center font-bold">
                {activeFiltersCount}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* ── VEHICLE MODE ── */}
      {searchMode === 'vehicle' && (
        <div className="card p-4">
          <div className="flex items-center gap-2 mb-3">
            <Car className="w-5 h-5 text-brand-600" />
            <h3 className="font-semibold text-gray-900">בחר רכב לחיפוש</h3>
          </div>

          {vehicles.length === 0 ? (
            <p className="text-sm text-gray-400 mb-3">אין רכבים שמורים – הוסף רכב למטה</p>
          ) : (
            <div className="flex flex-wrap gap-2 mb-3">
              {vehicles.map((v) => (
                <div key={v.id} className={`flex items-center rounded-xl border transition-all overflow-hidden ${
                  selectedVehicle?.id === v.id
                    ? 'bg-brand-600 border-brand-600'
                    : 'bg-white border-gray-200 hover:border-brand-400'
                }`}>
                  <button
                    onClick={() => selectVehicle(v)}
                    className={`px-4 py-2 text-sm font-medium ${
                      selectedVehicle?.id === v.id ? 'text-white' : 'text-gray-700'
                    }`}
                  >
                    <span>{v.nickname || v.model}</span>
                    <span className="mr-1 text-xs opacity-70">{v.year}</span>
                  </button>
                  <button
                    onClick={async () => {
                      if (!window.confirm(`למחוק את ${v.nickname || v.model}?`)) return
                      await removeVehicle(v.id)
                      toast.success('הרכב הוסר')
                    }}
                    className={`px-2 py-2 border-r transition-colors ${
                      selectedVehicle?.id === v.id
                        ? 'border-brand-500 text-brand-200 hover:text-white hover:bg-brand-700'
                        : 'border-gray-200 text-gray-300 hover:text-red-500 hover:bg-red-50'
                    }`}
                    title="הסר רכב"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))}
              {selectedVehicle && (
                <button
                  onClick={() => selectVehicle(null)}
                  className="px-4 py-2 rounded-xl text-sm border border-gray-200 text-gray-400 hover:border-gray-400 transition-all"
                >
                  נקה בחירה
                </button>
              )}
            </div>
          )}

          {/* Enriched vehicle details */}
          {selectedVehicle && (
            <div className="mb-3 bg-brand-50 border border-brand-100 rounded-xl p-3 text-sm">
              <p className="text-xs text-brand-600 font-semibold mb-2">
                {selectedVehicle.manufacturer} {selectedVehicle.model} {selectedVehicle.year}
              </p>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-1 text-gray-700">
                {selectedVehicle.fuel_type && <div><span className="text-gray-400 text-xs">דלק</span><p className="font-medium">{selectedVehicle.fuel_type}</p></div>}
                {selectedVehicle.color && <div><span className="text-gray-400 text-xs">צבע</span><p className="font-medium">{selectedVehicle.color}</p></div>}
                {selectedVehicle.engine_cc > 0 && <div><span className="text-gray-400 text-xs">נפח מנוע</span><p className="font-medium">{selectedVehicle.engine_cc} cc</p></div>}
                {selectedVehicle.horsepower > 0 && <div><span className="text-gray-400 text-xs">כ״ס</span><p className="font-medium">{selectedVehicle.horsepower}</p></div>}
                {selectedVehicle.front_tire && <div><span className="text-gray-400 text-xs">צמיג קדמי</span><p className="font-medium">{selectedVehicle.front_tire}</p></div>}
                {selectedVehicle.test_expiry_date && <div><span className="text-gray-400 text-xs">תוקף טסט</span><p className="font-medium">{selectedVehicle.test_expiry_date?.split('T')[0]}</p></div>}
              </div>
            </div>
          )}

          {/* Israeli license plate styled input */}
          <div className="flex gap-3 items-center">
            <div className="relative flex rounded-lg overflow-hidden border-2 border-gray-300 shadow-sm flex-1" style={{maxWidth: '260px'}}>
              {/* Yellow plate area */}
              <input
                className="flex-1 bg-orange-400 text-gray-900 font-bold text-xl tracking-[0.2em] text-center uppercase placeholder:text-orange-700 placeholder:font-normal placeholder:text-sm placeholder:tracking-normal focus:outline-none px-3 py-2"
                placeholder="מספר הרכב שלך"
                dir="ltr"
                value={newPlate}
                onChange={(e) => setNewPlate(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && addVehicle()}
                style={{fontFamily: 'monospace'}}
              />
            </div>
            <button onClick={addVehicle} disabled={addingVehicle} className="btn-secondary flex items-center gap-2 whitespace-nowrap">
              {addingVehicle ? <Loader2 className="w-4 h-4 animate-spin" /> : <Car className="w-4 h-4" />}
              הוסף
            </button>
          </div>

          {/* VIN search field */}
          <div className="pt-2 border-t border-gray-100 mt-2">
            <label className="block text-xs text-gray-500 mb-1 flex items-center gap-1">
              <Hash className="w-3 h-3" /> חיפוש לפי VIN (17 תווים)
            </label>
            <div className="flex gap-2">
              <input
                className="input-field flex-1 font-mono text-sm tracking-widest uppercase"
                placeholder="לדוג׳: 1HGCM82633A004352"
                dir="ltr"
                maxLength={17}
                value={vinInput}
                onChange={(e) => setVinInput(e.target.value.toUpperCase())}
                onKeyDown={(e) => e.key === 'Enter' && handleVinSearch()}
              />
              <button onClick={() => handleVinSearch()} disabled={vinLoading} className="btn-secondary flex items-center gap-2 whitespace-nowrap">
                {vinLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                זהה
              </button>
            </div>
            {vinInput.length > 0 && (
              <p className={`text-xs mt-1 ${vinInput.replace(/\s/g, '').length === 17 ? 'text-green-600' : 'text-gray-400'}`}>
                {vinInput.replace(/\s/g, '').length}/17 תווים
              </p>
            )}
            {vinVehicle && (
              <div className="mt-3 bg-brand-50 border border-brand-100 rounded-xl p-3 space-y-2">
                <p className="font-semibold text-brand-700 text-sm">
                  {[vinVehicle.manufacturer, vinVehicle.model, vinVehicle.year].filter(Boolean).join(' ')}
                </p>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1 text-xs text-gray-700">
                  {vinVehicle.fuel_type && <div><span className="text-gray-400">דלק</span><p className="font-medium">{vinVehicle.fuel_type}</p></div>}
                  {vinVehicle.engine_cc > 0 && <div><span className="text-gray-400">נפח מנוע</span><p className="font-medium">{vinVehicle.engine_cc} cc</p></div>}
                  {vinVehicle.transmission && <div><span className="text-gray-400">תיבת הילוכים</span><p className="font-medium">{vinVehicle.transmission}</p></div>}
                  {vinVehicle.body_class && <div><span className="text-gray-400">סוג גוף</span><p className="font-medium">{vinVehicle.body_class}</p></div>}
                  {vinVehicle.country_of_origin && <div><span className="text-gray-400">ייצור</span><p className="font-medium">{vinVehicle.country_of_origin}</p></div>}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── MANUAL MODE ── */}
      {searchMode === 'manual' && (
        <div className="card p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <SlidersHorizontal className="w-5 h-5 text-brand-600" />
              <h3 className="font-semibold text-gray-900">חיפוש לפי פרטי רכב</h3>
            </div>
            {activeFiltersCount > 0 && (
              <button onClick={clearManual} className="flex items-center gap-1 text-xs text-gray-400 hover:text-red-500 transition-colors">
                <X className="w-3 h-3" /> נקה הכל
              </button>
            )}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {/* Manufacturer */}
            <div>
              <label className="block text-xs text-gray-500 mb-1">
                יצרן / מותג
                <span className="mr-1 text-gray-300">({brands.length})</span>
              </label>
              <select
                className="input-field"
                value={manualManufacturer}
                onChange={(e) => setManualManufacturer(e.target.value)}
              >
                <option value="">כל היצרנים</option>
                {brands.some(b => b.has_parts) && (
                  <optgroup label="── יש מלאי ──">
                    {brands
                      .filter(b => b.has_parts)
                      .sort((a, b) => b.parts_count - a.parts_count)
                      .map(b => (
                        <option key={b.name} value={b.name}>
                          {b.name_he ? `${b.name} · ${b.name_he}` : b.name}
                          {' '}({b.parts_count.toLocaleString()} חלקים)
                        </option>
                      ))}
                  </optgroup>
                )}
                {['Europe', 'Asia', 'America'].map(region => {
                  const regionBrands = brands.filter(b => b.region === region && !b.has_parts)
                  if (!regionBrands.length) return null
                  const regionLabel = { Europe: '🇪🇺 אירופה', Asia: '🌏 אסיה', America: '🌎 אמריקה' }[region]
                  return (
                    <optgroup key={region} label={`── ${regionLabel} ──`}>
                      {regionBrands.sort((a, b) => a.name.localeCompare(b.name)).map(b => (
                        <option key={b.name} value={b.name}>
                          {b.name_he ? `${b.name} · ${b.name_he}` : b.name}
                          {b.is_luxury ? ' ✦' : ''}
                        </option>
                      ))}
                    </optgroup>
                  )
                })}
                {brands.filter(b => !b.region && !b.has_parts).length > 0 && (
                  <optgroup label="── אחר ──">
                    {brands.filter(b => !b.region && !b.has_parts).map(b => (
                      <option key={b.name} value={b.name}>{b.name}</option>
                    ))}
                  </optgroup>
                )}
              </select>
            </div>

            {/* Model */}
            <div>
              <label className="block text-xs text-gray-500 mb-1">דגם (אופציונלי)</label>
              <input
                className="input-field"
                placeholder="לדוג׳ GTC4, C-Class..."
                value={manualModel}
                onChange={(e) => setManualModel(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && search()}
              />
            </div>

            {/* Year */}
            <div>
              <label className="block text-xs text-gray-500 mb-1">שנה (אופציונלי)</label>
              <input
                className="input-field"
                placeholder="לדוג׳ 2019"
                type="number"
                min="1990"
                max="2026"
                value={manualYear}
                onChange={(e) => setManualYear(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && search()}
              />
            </div>
          </div>

          {/* Active filter chips */}
          {activeFiltersCount > 0 && (
            <div className="flex flex-wrap gap-2">
              {manualManufacturer && (
                <span className="inline-flex items-center gap-1 bg-brand-100 text-brand-700 text-xs px-2 py-1 rounded-full">
                  {manualManufacturer}
                  <button onClick={() => setManualManufacturer('')}><X className="w-3 h-3" /></button>
                </span>
              )}
              {manualModel && (
                <span className="inline-flex items-center gap-1 bg-brand-100 text-brand-700 text-xs px-2 py-1 rounded-full">
                  {manualModel}
                  <button onClick={() => setManualModel('')}><X className="w-3 h-3" /></button>
                </span>
              )}
              {manualYear && (
                <span className="inline-flex items-center gap-1 bg-brand-100 text-brand-700 text-xs px-2 py-1 rounded-full">
                  {manualYear}
                  <button onClick={() => setManualYear('')}><X className="w-3 h-3" /></button>
                </span>
              )}
              {category && (
                <span className="inline-flex items-center gap-1 bg-purple-100 text-purple-700 text-xs px-2 py-1 rounded-full">
                  {category}
                  <button onClick={() => setCategory('')}><X className="w-3 h-3" /></button>
                </span>
              )}
            </div>
          )}

          {/* VIN search field */}
          <div className="pt-2 border-t border-gray-100 mt-2">
            <label className="block text-xs text-gray-500 mb-1 flex items-center gap-1">
              <Hash className="w-3 h-3" /> חיפוש לפי VIN (17 תווים)
            </label>
            <div className="flex gap-2">
              <input
                className="input-field flex-1 font-mono text-sm tracking-widest uppercase"
                placeholder="לדוג׳: 1HGCM82633A004352"
                dir="ltr"
                maxLength={17}
                value={vinInput}
                onChange={(e) => setVinInput(e.target.value.toUpperCase())}
                onKeyDown={(e) => e.key === 'Enter' && handleVinSearch()}
              />
              <button onClick={() => handleVinSearch()} disabled={vinLoading} className="btn-secondary flex items-center gap-2 whitespace-nowrap">
                {vinLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                זהה
              </button>
            </div>
            {vinInput.length > 0 && (
              <p className={`text-xs mt-1 ${vinInput.replace(/\s/g, '').length === 17 ? 'text-green-600' : 'text-gray-400'}`}>
                {vinInput.replace(/\s/g, '').length}/17 תווים
              </p>
            )}
            {vinVehicle && (
              <div className="mt-3 bg-brand-50 border border-brand-100 rounded-xl p-3 space-y-2">
                <p className="font-semibold text-brand-700 text-sm">
                  {[vinVehicle.manufacturer, vinVehicle.model, vinVehicle.year].filter(Boolean).join(' ')}
                </p>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1 text-xs text-gray-700">
                  {vinVehicle.fuel_type && <div><span className="text-gray-400">דלק</span><p className="font-medium">{vinVehicle.fuel_type}</p></div>}
                  {vinVehicle.engine_cc > 0 && <div><span className="text-gray-400">נפח מנוע</span><p className="font-medium">{vinVehicle.engine_cc} cc</p></div>}
                  {vinVehicle.transmission && <div><span className="text-gray-400">תיבת הילוכים</span><p className="font-medium">{vinVehicle.transmission}</p></div>}
                  {vinVehicle.body_class && <div><span className="text-gray-400">סוג גוף</span><p className="font-medium">{vinVehicle.body_class}</p></div>}
                  {vinVehicle.country_of_origin && <div><span className="text-gray-400">ייצור</span><p className="font-medium">{vinVehicle.country_of_origin}</p></div>}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── PHOTO MODE ── */}
      {searchMode === 'photo' && (
        <div className="card p-4 space-y-4">
          <div className="flex items-center gap-2 mb-1">
            <Camera className="w-5 h-5 text-brand-600" />
            <h3 className="font-semibold text-gray-900">זיהוי חלק מתמונה</h3>
          </div>
          <p className="text-sm text-gray-500">צלם או העלה תמונה של החלק — ה-AI יזהה אותו ויחפש במאגר</p>

          {/* Vehicle picker — selected = filled, others = outlined */}
          {vehicles.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {/* General / no-filter option */}
              <button
                onClick={() => selectVehicle(null)}
                className={`px-4 py-2 rounded-xl text-sm font-medium border-2 transition-all flex items-center gap-2 ${
                  !selectedVehicle
                    ? 'bg-gray-700 border-gray-700 text-white shadow-md'
                    : 'bg-white border-gray-200 text-gray-500 hover:border-gray-400 hover:text-gray-700'
                }`}
              >
                <Search className="w-4 h-4" />
                כללי
              </button>
              {vehicles.map((v) => {
                const isSelected = selectedVehicle?.id === v.id
                return (
                  <button
                    key={v.id}
                    onClick={() => selectVehicle(isSelected ? null : v)}
                    className={`px-4 py-2 rounded-xl text-sm font-medium border-2 transition-all flex items-center gap-2 ${
                      isSelected
                        ? 'bg-brand-600 border-brand-600 text-white shadow-md'
                        : 'bg-white border-gray-200 text-gray-700 hover:border-brand-400 hover:text-brand-700'
                    }`}
                  >
                    <Car className="w-4 h-4" />
                    {v.nickname || v.model}
                    <span className={`text-xs ml-2 ${isSelected ? 'opacity-80' : 'text-gray-400'}`}>{v.year}</span>
                  </button>
                )
              })}
            </div>
          )}

          <div
            className="border-2 border-dashed border-gray-200 rounded-xl p-6 text-center cursor-pointer hover:border-brand-400 transition-colors"
            onClick={() => fileInputRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); handlePhotoFile(e.dataTransfer.files[0]) }}
          >
            {photoPreview ? (
              <img src={photoPreview} alt="preview" className="max-h-48 mx-auto rounded-lg object-contain" />
            ) : (
              <>
                <Camera className="w-10 h-10 text-gray-300 mx-auto mb-2" />
                <p className="text-sm text-gray-400">לחץ לבחירת תמונה או גרור לכאן</p>
                <p className="text-xs text-gray-300 mt-1">PNG, JPG, WEBP · עד 10MB</p>
              </>
            )}
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            capture="environment"
            className="hidden"
            onChange={(e) => handlePhotoFile(e.target.files[0])}
          />

          {photoPreview && (
            <div className="flex gap-2">
              <button onClick={handlePhotoSearch} disabled={photoLoading} className="btn-primary flex-1 flex items-center justify-center gap-2">
                {photoLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                זהה וחפש חלק
              </button>
              <button
                onClick={() => { setPhotoFile(null); setPhotoPreview(null); setPhotoResult(null); setPhotoCandidates([]) }}
                className="btn-secondary px-3"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          )}

          {photoResult && (
            <div className="bg-green-50 border border-green-200 rounded-xl p-3 space-y-2">
              <div className="flex items-center gap-2">
                <CheckCircle className="w-5 h-5 text-green-600 flex-shrink-0" />
                <div>
                  <p className="font-semibold text-green-800">{photoResult.identified_part}</p>
                  {photoResult.identified_part_en && (
                    <p className="text-xs text-green-600">{photoResult.identified_part_en}</p>
                  )}
                </div>
                {photoResult.confidence && (
                  <span className="mr-auto badge bg-green-100 text-green-700">
                    {Math.round(photoResult.confidence * 100)}% ביטחון
                  </span>
                )}
              </div>
              {photoResult.possible_names?.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1">
                  {photoResult.possible_names.map((n, i) => (
                    <span key={i} className="text-xs bg-white border border-green-200 text-green-700 px-2 py-0.5 rounded-full">{n}</span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── VOICE MODE ── */}
      {searchMode === 'voice' && (
        <div className="card p-4 space-y-4">
          <div className="flex items-center gap-2 mb-1">
            <Mic className="w-5 h-5 text-brand-600" />
            <h3 className="font-semibold text-gray-900">חיפוש קולי</h3>
          </div>
          <p className="text-sm text-gray-500">לחץ על המיקרופון ואמור שם החלק שאתה מחפש</p>

          {/* Vehicle picker — same as photo tab */}
          {vehicles.length > 0 && (
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => selectVehicle(null)}
                className={`px-4 py-2 rounded-xl text-sm font-medium border-2 transition-all flex items-center gap-2 ${
                  !selectedVehicle
                    ? 'bg-gray-700 border-gray-700 text-white shadow-md'
                    : 'bg-white border-gray-200 text-gray-500 hover:border-gray-400 hover:text-gray-700'
                }`}
              >
                <Search className="w-4 h-4" />
                כללי
              </button>
              {vehicles.map((v) => {
                const isSelected = selectedVehicle?.id === v.id
                return (
                  <button
                    key={v.id}
                    onClick={() => selectVehicle(isSelected ? null : v)}
                    className={`px-4 py-2 rounded-xl text-sm font-medium border-2 transition-all flex items-center gap-2 ${
                      isSelected
                        ? 'bg-brand-600 border-brand-600 text-white shadow-md'
                        : 'bg-white border-gray-200 text-gray-700 hover:border-brand-400 hover:text-brand-700'
                    }`}
                  >
                    <Car className="w-4 h-4" />
                    {v.nickname || v.model}
                    <span className={`text-xs ml-2 ${isSelected ? 'opacity-80' : 'text-gray-400'}`}>{v.year}</span>
                  </button>
                )
              })}
            </div>
          )}

          {!voiceSupported && (
            <div className="flex items-center gap-2 bg-yellow-50 border border-yellow-200 text-yellow-700 rounded-xl p-3 text-sm">
              <AlertCircle className="w-5 h-5 flex-shrink-0" />
              הדפדפן שלך לא תומך בזיהוי קול. נסה Chrome למחשב או נייד.
            </div>
          )}

          <div className="flex flex-col items-center gap-4 py-4">
            <button
              onClick={toggleVoice}
              disabled={!voiceSupported}
              className={`relative w-20 h-20 rounded-full flex items-center justify-center transition-all shadow-lg disabled:opacity-40 ${
                isListening
                  ? 'bg-red-500 hover:bg-red-600 text-white'
                  : 'bg-brand-600 hover:bg-brand-700 text-white'
              }`}
            >
              {isListening && (
                <span className="absolute inset-0 rounded-full bg-red-400 animate-ping opacity-75" />
              )}
              {isListening ? <MicOff className="w-8 h-8 relative" /> : <Mic className="w-8 h-8 relative" />}
            </button>
            <p className="text-sm text-gray-500">{isListening ? '🔴 מקשיב... לחץ לעצור' : 'לחץ להתחיל'}</p>
          </div>

          {voiceTranscript && (
            <div className="bg-gray-50 border border-gray-200 rounded-xl p-3">
              <p className="text-xs text-gray-400 mb-1">זוהה:</p>
              <p className="font-medium text-gray-800 text-lg">{voiceTranscript}</p>
            </div>
          )}

          <div className="flex gap-2">
            <input
              className="input-field flex-1"
              placeholder="או הקלד שם חלק..."
              value={voiceTranscript}
              onChange={(e) => setVoiceTranscript(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleVoiceSearch()}
            />
            <button onClick={handleVoiceSearch} disabled={isLoading || !voiceTranscript.trim()} className="btn-primary flex items-center gap-2 whitespace-nowrap">
              {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
              חפש
            </button>
          </div>
        </div>
      )}

      {/* Voice: no parts for selected manufacturer */}
      {!isLoading && searchMode === 'voice' && voiceFallbackMfr && (
        <div className="flex flex-col items-center gap-3 py-10 text-center">
          <div className="w-14 h-14 rounded-full bg-amber-100 flex items-center justify-center">
            <AlertCircle className="w-7 h-7 text-amber-500" />
          </div>
          <div>
            <p className="font-semibold text-gray-800 text-base">אין חלקים עבור <span className="text-brand-600">{voiceFallbackMfr}</span> במאגר</p>
            <p className="text-sm text-gray-400 mt-1">בחר יצרן אחר מהרשימה או לחץ &quot;כללי&quot; לצפייה בכל התוצאות</p>
          </div>
        </div>
      )}

      {/* ── SEARCH BAR (manual mode only) ── */}
      {searchMode === 'manual' && (
        <div className="card p-4">
          <div className="flex flex-col sm:flex-row gap-3">
            <div className="relative flex-1" ref={suggestRef}>
              <Search className="absolute right-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
              <input
                className="input-field pr-10"
                placeholder={
                  searchMode === 'vehicle'
                    ? 'שם החלק... (פילטר שמן, רפידות בלם, מצמד...)'
                    : 'שם החלק לחיפוש...'
                }
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { setShowSuggestions(false); search() } }}
                onFocus={() => {
                  if (query.length >= 2 && suggestions.length > 0) setShowSuggestions(true)
                  else if (query.length < 2 && recentSearches.length > 0) setShowSuggestions(true)
                }}
                autoComplete="off"
              />
              {showSuggestions && (query.length >= 2 ? suggestions.length > 0 : recentSearches.length > 0) && (
                <ul className="absolute z-50 right-0 left-0 top-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg max-h-64 overflow-y-auto">
                  {query.length < 2 && recentSearches.length > 0 && (
                    <>
                      <li className="px-3 py-1.5 text-xs text-gray-400 font-medium border-b border-gray-100 flex items-center justify-between">
                        <span>חיפושים אחרונים</span>
                        <button
                          onMouseDown={(e) => {
                            e.preventDefault()
                            setRecentSearches([])
                            localStorage.removeItem('recentPartSearches')
                            setShowSuggestions(false)
                          }}
                          className="text-gray-300 hover:text-red-400 text-xs"
                        >מחק</button>
                      </li>
                      {recentSearches.map((s, i) => (
                        <li
                          key={i}
                          className="flex items-center gap-2 px-3 py-2 hover:bg-brand-50 cursor-pointer border-b border-gray-50 last:border-0"
                          onMouseDown={(e) => { e.preventDefault(); setQuery(s); setShowSuggestions(false); setTimeout(() => search(), 0) }}
                        >
                          <span className="text-gray-300 text-xs">🕐</span>
                          <span className="text-sm text-gray-700">{s}</span>
                        </li>
                      ))}
                    </>
                  )}
                  {query.length >= 2 && suggestions.map((s, i) => (
                    <li
                      key={i}
                      className="flex items-center justify-between px-3 py-2 hover:bg-brand-50 cursor-pointer border-b border-gray-50 last:border-0"
                      onMouseDown={(e) => {
                        e.preventDefault()
                        setQuery(s.name)
                        setShowSuggestions(false)
                        setTimeout(() => search(), 0)
                      }}
                    >
                      <span className="text-sm font-medium text-gray-800 truncate flex-1">{s.name}</span>
                      <span className="text-xs text-gray-400 mr-2 shrink-0">{s.category}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <select className="input-field sm:w-44" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">כל הקטגוריות</option>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c}{categoryCounts[c] ? ` (${categoryCounts[c].toLocaleString()})` : ''}
                </option>
              ))}
            </select>
            <button onClick={() => search(0)} disabled={isLoading} className="btn-primary flex items-center gap-2 whitespace-nowrap">
              {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
              חפש
            </button>
          </div>

          {/^[A-Z0-9]{2,6}[-/]?[A-Z0-9]{3,}$/i.test(query.trim()) && query.trim().length >= 5 && (
            <p className="text-xs text-amber-600 mt-2 flex items-center gap-1">
              <Hash className="w-3 h-3" /> זה נראה כמספר קטלוג (SKU) – החיפוש יכלול גם התאמות מספר בלבד
            </p>
          )}

          {searchMode === 'manual' && activeFiltersCount > 0 && (
            <p className="text-xs text-brand-600 mt-2">
              🔍 מחפש: {buildManualQuery() || query || 'כל החלקים'}
            </p>
          )}
        </div>
      )}

      {/* ── RESULTS ── */}
      {isLoading && (
        <div className="flex justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-brand-600" />
        </div>
      )}

      {!isLoading && searched && parts.length === 0 && (
        <div className="card p-8 text-center">
          <Package className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <h3 className="font-semibold text-gray-700 mb-2">לא נמצאו חלקים</h3>
          <p className="text-sm text-gray-400 mb-5">
            {category && query
              ? `לא נמצאו תוצאות עבור "${query}" בקטגוריה "${category}"`
              : category
              ? `לא נמצאו תוצאות בקטגוריה "${category}"`
              : query
              ? `לא נמצאו תוצאות עבור "${query}"`
              : 'לא נמצאו חלקים עם הפרמטרים הנוכחיים'}
          </p>
          {categories.length > 0 && (
            <div className="mb-5">
              <p className="text-xs text-gray-400 mb-2">נסה לחפש בקטגוריה:</p>
              <div className="flex flex-wrap justify-center gap-2">
                {category && (
                  <button
                    onClick={() => setCategory('')}
                    className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-full bg-red-50 text-red-600 border border-red-200 hover:bg-red-100 transition-colors"
                  >
                    <X className="w-3 h-3" /> הסר פילטר: {category}
                  </button>
                )}
                {categories.filter((c) => c !== category).slice(0, 8).map((c) => (
                  <button
                    key={c}
                    onClick={() => { setCategory(c) }}
                    className="text-xs px-3 py-1.5 rounded-full bg-brand-50 text-brand-700 border border-brand-200 hover:bg-brand-100 transition-colors"
                  >
                    {c}{categoryCounts[c] ? ` (${categoryCounts[c].toLocaleString()})` : ''}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="flex items-center justify-center gap-3 flex-wrap">
            {searchMode === 'manual' && (
              <button onClick={() => switchMode('vehicle')} className="btn-ghost text-sm">
                חפש לפי הרכב שלי
              </button>
            )}
            <button
              onClick={() => navigate(`/chat?msg=${encodeURIComponent(`חפש עבורי: ${query || 'חלקי רכב'}`)}`)}
              className="btn-primary text-sm flex items-center gap-2"
            >
              <Bot className="w-4 h-4" /> שאל את ה-AI
            </button>
          </div>
        </div>
      )}

      {!isLoading && searchMode === 'photo' && photoFallbackMfr && (
        <div className="flex flex-col items-center gap-3 py-10 text-center">
          <div className="w-14 h-14 rounded-full bg-amber-100 flex items-center justify-center">
            <AlertCircle className="w-7 h-7 text-amber-500" />
          </div>
          <div>
            <p className="font-semibold text-gray-800 text-base">אין חלקים עבור <span className="text-brand-600">{photoFallbackMfr}</span> במאגר</p>
            <p className="text-sm text-gray-400 mt-1">בחר יצרן אחר מהרשימה או לחץ &quot;כללי&quot; לצפייה בכל התוצאות</p>
          </div>
        </div>
      )}

      {!isLoading && !photoFallbackMfr && !voiceFallbackMfr && parts.length > 0 && (
        <>
          {/* Results header */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3 flex-wrap">
              <p className="text-sm text-gray-500">
                נמצאו <strong>{totalCount.toLocaleString()}</strong> חלקים
                {totalCount > PAGE_SIZE && ` · עמוד ${page + 1}/${Math.ceil(totalCount / PAGE_SIZE)}`}
              </p>
              {selectedVehicle && searchMode === 'photo' && (() => {
                const mfr = selectedVehicle.manufacturer?.toLowerCase() || ''
                const matchCount = parts.filter(p => p.manufacturer?.toLowerCase().includes(mfr)).length
                return matchCount > 0 ? (
                  <span className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full bg-brand-50 text-brand-700 border border-brand-200 font-medium">
                    <Car className="w-3 h-3" />
                    {matchCount} מתאימים ל{selectedVehicle.manufacturer}
                  </span>
                ) : null
              })()}
              {inStockCount > 0 && (
                <button
                  onClick={() => setFilterAvail(filterAvail === 'in_stock' ? '' : 'in_stock')}
                  className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full border font-medium transition-all ${
                    filterAvail === 'in_stock'
                      ? 'bg-green-600 text-white border-green-600'
                      : 'bg-green-50 text-green-700 border-green-200 hover:bg-green-100'
                  }`}
                >
                  <CheckCircle className="w-3 h-3" />
                  {inStockCount} במלאי
                </button>
              )}
              {onOrderCount > 0 && (
                <button
                  onClick={() => setFilterAvail(filterAvail === 'on_order' ? '' : 'on_order')}
                  className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full border font-medium transition-all ${
                    filterAvail === 'on_order'
                      ? 'bg-amber-600 text-white border-amber-600'
                      : 'bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100'
                  }`}
                >
                  <Truck className="w-3 h-3" />
                  {onOrderCount} על הזמנה
                </button>
              )}
              {[['Original','מקורי','bg-blue-600 text-white border-blue-600','bg-blue-50 text-blue-700 border-blue-200 hover:bg-blue-100'],
                ['Aftermarket','חליפי','bg-amber-600 text-white border-amber-600','bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100'],
                ['Refurbished','משופץ','bg-purple-600 text-white border-purple-600','bg-purple-50 text-purple-700 border-purple-200 hover:bg-purple-100'],
              ].filter(([k]) => typeCounts[k] > 0).map(([key, label, activeClass, inactiveClass]) => (
                <button
                  key={key}
                  onClick={() => setFilterType(filterType === key ? '' : key)}
                  className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full border font-medium transition-all ${
                    filterType === key ? activeClass : inactiveClass
                  }`}
                >
                  {label} ({typeCounts[key]})
                </button>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <button
                title="העתק קישור לחיפוש"
                onClick={() => {
                  const url = `${window.location.origin}/parts?search=${encodeURIComponent(query)}${category ? `&category=${encodeURIComponent(category)}` : ''}`
                  navigator.clipboard.writeText(url).then(() => toast.success('קישור הועתק! 🔗'))
                }}
                className="p-1.5 rounded border border-gray-200 text-gray-400 hover:text-brand-600 hover:border-brand-300 transition-colors"
              >
                <Link2 className="w-4 h-4" />
              </button>
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value)}
                className="input-field text-sm py-1.5 w-auto"
              >
                <option value="availability">מיין: זמינות</option>
                <option value="price_asc">מיין: מחיר ↑</option>
                <option value="price_desc">מיין: מחיר ↓</option>
                <option value="name">מיין: שם</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {displayParts.map((p) => <PartCard key={p.id} part={p} onAddToCart={addItem} />)}
            {displayParts.length === 0 && filterAvail && (
              <div className="col-span-3 py-8 text-center text-gray-400 text-sm">
                אין חלקים עם סטטוס "{filterAvail === 'in_stock' ? 'במלאי' : 'על הזמנה'}" בדף זה
                <button onClick={() => setFilterAvail('')} className="mr-2 text-brand-600 underline">הצג הכל</button>
              </div>
            )}
          </div>
          {totalCount > PAGE_SIZE && (
            <div className="flex items-center justify-center gap-2 pt-2 flex-wrap">
              <button
                disabled={page === 0}
                onClick={() => search(page - 1)}
                className="btn-secondary px-4 py-2 text-sm disabled:opacity-40"
              >
                ← הקודם
              </button>
              {Array.from({ length: Math.min(5, Math.ceil(totalCount / PAGE_SIZE)) }, (_, i) => {
                const totalPages = Math.ceil(totalCount / PAGE_SIZE)
                const start = Math.max(0, Math.min(page - 2, totalPages - 5))
                const pg = start + i
                return pg < totalPages ? (
                  <button
                    key={pg}
                    onClick={() => search(pg)}
                    className={`px-3 py-2 rounded-lg text-sm font-medium border transition-all ${
                      pg === page ? 'bg-brand-600 text-white border-brand-600' : 'bg-white text-gray-700 border-gray-200 hover:border-brand-400'
                    }`}
                  >
                    {pg + 1}
                  </button>
                ) : null
              })}
              <button
                disabled={page >= Math.ceil(totalCount / PAGE_SIZE) - 1}
                onClick={() => search(page + 1)}
                className="btn-secondary px-4 py-2 text-sm disabled:opacity-40"
              >
                הבא →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
