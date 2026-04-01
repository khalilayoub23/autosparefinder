import { useState, useEffect, useRef, useCallback } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { partsApi } from '../api/parts'
import { useCartStore } from '../stores/cartStore'
import { useVehicleStore } from '../stores/vehicleStore'
import { Search, ShoppingCart, Car, Loader2, ChevronDown, Package, SlidersHorizontal, X, Camera, Mic, MicOff, Hash, CheckCircle, AlertCircle, Truck, Shield, Tag, ChevronRight, Link2, Bot, Crop, Pencil, Circle, RotateCcw, Check, MousePointer, ScanLine } from 'lucide-react'
import toast from 'react-hot-toast'

// ── Country flag helper ───────────────────────────────────────────────────────
const COUNTRY_ISO = {
  israel: 'il', 'il': 'il', germany: 'de', 'de': 'de', japan: 'jp', 'jp': 'jp',
  usa: 'us', 'us': 'us', china: 'cn', 'cn': 'cn', korea: 'kr', 'kr': 'kr',
  taiwan: 'tw', 'tw': 'tw', uk: 'gb', 'gb': 'gb', france: 'fr', 'fr': 'fr',
  italy: 'it', 'it': 'it', spain: 'es', 'es': 'es', netherlands: 'nl', 'nl': 'nl',
  turkey: 'tr', 'tr': 'tr', czechia: 'cz', 'cz': 'cz',
}
function CountryFlag({ country }) {
  if (!country) return null
  const iso = COUNTRY_ISO[country.toLowerCase().trim()]
  if (!iso) return null
  return (
    <span className="inline-flex items-center justify-center flex-shrink-0" style={{ width: 18, height: 14 }}>
      <img
        src={`https://flagcdn.com/16x12/${iso}.png`}
        srcSet={`https://flagcdn.com/32x24/${iso}.png 2x`}
        alt={country}
        className="rounded-sm object-cover block"
        style={{ width: 16, height: 12 }}
      />
    </span>
  )
}

// ─── Photo Editor Modal ───────────────────────────────────────────────────────
function PhotoEditorModal({ src, onApply, onClose }) {
  const canvasRef = useRef(null)
  const [tool, setTool] = useState('crop') // 'crop' | 'mark' | 'move'
  const [drawing, setDrawing] = useState(false)
  const [start, setStart] = useState(null)
  const dragIdx = useRef(-1)
  const [crop, setCrop] = useState(null)      // {x,y,w,h} in canvas coords
  const [marks, setMarks] = useState([])      // [{x,y,r}]
  const [imgObj, setImgObj] = useState(null)
  const [scale, setScale] = useState(1)
  const [zoom, setZoom] = useState(1)         // 1 = fit-to-window
  const basescale = useRef(1)                 // fit-to-window scale stored here

  // Load image onto canvas
  useEffect(() => {
    const img = new Image()
    img.onload = () => {
      setImgObj(img)
      const canvas = canvasRef.current
      if (!canvas) return
      const maxW = Math.min(window.innerWidth - 48, 700)
      const maxH = window.innerHeight * 0.55
      const s = Math.min(maxW / img.naturalWidth, maxH / img.naturalHeight, 1)
      basescale.current = s
      setScale(s)
      setZoom(1)
      canvas.width  = img.naturalWidth  * s
      canvas.height = img.naturalHeight * s
      drawCanvas(canvas, img, s, null, [])
    }
    img.src = src
  }, [src])

  const drawCanvas = useCallback((canvas, img, s, cropRect, markList) => {
    if (!canvas || !img) return
    const ctx = canvas.getContext('2d')
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)

    // Dim everything outside crop if crop active
    if (cropRect && cropRect.w > 4 && cropRect.h > 4) {
      ctx.save()
      ctx.fillStyle = 'rgba(0,0,0,0.45)'
      ctx.beginPath()
      ctx.rect(0, 0, canvas.width, canvas.height)
      ctx.rect(cropRect.x, cropRect.y, cropRect.w, cropRect.h)
      ctx.fill('evenodd')
      // Border
      ctx.strokeStyle = '#f97316'
      ctx.lineWidth = 2
      ctx.strokeRect(cropRect.x, cropRect.y, cropRect.w, cropRect.h)
      // Corner handles
      const hs = 8
      ctx.fillStyle = '#f97316'
      ;[
        [cropRect.x, cropRect.y],
        [cropRect.x + cropRect.w - hs, cropRect.y],
        [cropRect.x, cropRect.y + cropRect.h - hs],
        [cropRect.x + cropRect.w - hs, cropRect.y + cropRect.h - hs],
      ].forEach(([rx, ry]) => ctx.fillRect(rx, ry, hs, hs))
      ctx.restore()
    }

    // Draw marks
    ;(markList || []).forEach(({ x, y, r }) => {
      ctx.save()
      ctx.strokeStyle = '#ef4444'
      ctx.lineWidth = 3
      ctx.beginPath()
      ctx.arc(x, y, r, 0, Math.PI * 2)
      ctx.stroke()
      // Arrow pointer
      ctx.beginPath()
      ctx.moveTo(x + r * 0.7, y - r * 0.7)
      ctx.lineTo(x + r * 1.4, y - r * 1.4)
      ctx.stroke()
      ctx.restore()
    })
  }, [])

  // Redraw whenever state changes
  useEffect(() => {
    if (canvasRef.current && imgObj) {
      drawCanvas(canvasRef.current, imgObj, scale, crop, marks)
    }
  }, [crop, marks, imgObj, scale, drawCanvas])

  // Apply zoom: rescale canvas + rescale all mark positions
  const applyZoom = useCallback((newZoom) => {
    if (!imgObj || !canvasRef.current) return
    const clampedZoom = Math.min(Math.max(newZoom, 0.25), 5)
    const oldScale = scale
    const newScale = basescale.current * clampedZoom
    const canvas = canvasRef.current
    canvas.width  = imgObj.naturalWidth  * newScale
    canvas.height = imgObj.naturalHeight * newScale
    // Translate mark positions to new scale
    setMarks(m => m.map(mk => ({
      x: mk.x * (newScale / oldScale),
      y: mk.y * (newScale / oldScale),
      r: mk.r * (newScale / oldScale),
    })))
    if (crop) {
      setCrop(cr => cr ? ({
        x: cr.x * (newScale / oldScale),
        y: cr.y * (newScale / oldScale),
        w: cr.w * (newScale / oldScale),
        h: cr.h * (newScale / oldScale),
      }) : null)
    }
    setScale(newScale)
    setZoom(clampedZoom)
  }, [imgObj, scale, crop])

  const onWheel = useCallback((e) => {
    e.preventDefault()
    const delta = e.deltaY < 0 ? 0.15 : -0.15
    applyZoom(zoom + delta)
  }, [zoom, applyZoom])

  const getPos = (e) => {
    const canvas = canvasRef.current
    const rect = canvas.getBoundingClientRect()
    const clientX = e.touches ? e.touches[0].clientX : e.clientX
    const clientY = e.touches ? e.touches[0].clientY : e.clientY
    return { x: clientX - rect.left, y: clientY - rect.top }
  }

  const onPointerDown = (e) => {
    e.preventDefault()
    const pos = getPos(e)

    // Check if clicking near an existing mark (works in any tool mode)
    const hitIdx = marks.findIndex(({ x, y, r }) => {
      const dx = pos.x - x, dy = pos.y - y
      return Math.sqrt(dx * dx + dy * dy) <= r + 12
    })

    if (hitIdx >= 0) {
      // Drag existing mark
      dragIdx.current = hitIdx
      setDrawing(true)
      setStart(pos)
      return
    }

    // No hit — normal tool behaviour
    dragIdx.current = -1
    setDrawing(true)
    setStart(pos)
    if (tool === 'mark') {
      setMarks(m => [...m, { x: pos.x, y: pos.y, r: 28 }])
    }
  }

  const onPointerMove = (e) => {
    if (!drawing || !start) return
    e.preventDefault()
    const pos = getPos(e)

    if (dragIdx.current >= 0) {
      // Move the dragged mark
      setMarks(m => {
        const copy = [...m]
        copy[dragIdx.current] = { ...copy[dragIdx.current], x: pos.x, y: pos.y }
        return copy
      })
      return
    }

    if (tool === 'crop') {
      const x = Math.min(start.x, pos.x)
      const y = Math.min(start.y, pos.y)
      const w = Math.abs(pos.x - start.x)
      const h = Math.abs(pos.y - start.y)
      setCrop({ x, y, w, h })
    } else if (tool === 'mark') {
      // Update last mark radius as user drags
      const dx = pos.x - start.x, dy = pos.y - start.y
      const r = Math.max(16, Math.sqrt(dx * dx + dy * dy))
      setMarks(m => {
        const copy = [...m]
        if (copy.length > 0) copy[copy.length - 1] = { ...copy[copy.length - 1], r }
        return copy
      })
    }
  }

  const onPointerUp = (e) => {
    e.preventDefault()
    dragIdx.current = -1
    setDrawing(false)
    setStart(null)
  }

  const handleReset = () => {
    setCrop(null)
    setMarks([])
  }

  const handleApply = () => {
    const canvas = document.createElement('canvas')
    if (!imgObj) return

    if (crop && crop.w > 20 && crop.h > 20) {
      // Crop to natural image coordinates
      const sx = (crop.x / scale)
      const sy = (crop.y / scale)
      const sw = (crop.w / scale)
      const sh = (crop.h / scale)
      canvas.width  = sw
      canvas.height = sh
      const ctx = canvas.getContext('2d')
      ctx.drawImage(imgObj, sx, sy, sw, sh, 0, 0, sw, sh)

      // Draw marks relocated to cropped coords
      marks.forEach(({ x, y, r }) => {
        const mx = (x / scale) - sx
        const my = (y / scale) - sy
        ctx.save()
        ctx.strokeStyle = '#ef4444'
        ctx.lineWidth = 3
        ctx.beginPath()
        ctx.arc(mx, my, r / scale, 0, Math.PI * 2)
        ctx.stroke()
        ctx.restore()
      })
    } else {
      // No crop — use original size with annotations
      canvas.width  = imgObj.naturalWidth
      canvas.height = imgObj.naturalHeight
      const ctx = canvas.getContext('2d')
      ctx.drawImage(imgObj, 0, 0)
      marks.forEach(({ x, y, r }) => {
        const mx = x / scale, my = y / scale
        ctx.save()
        ctx.strokeStyle = '#ef4444'
        ctx.lineWidth = 3
        ctx.beginPath()
        ctx.arc(mx, my, r / scale, 0, Math.PI * 2)
        ctx.stroke()
        ctx.restore()
      })
    }

    canvas.toBlob(blob => {
      if (!blob) return
      const file = new File([blob], 'edited.jpg', { type: 'image/jpeg' })
      onApply(file, canvas.toDataURL('image/jpeg', 0.9))
    }, 'image/jpeg', 0.9)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-3" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl flex flex-col overflow-hidden" style={{maxHeight: '95vh'}}>
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
          <div className="flex items-center gap-2">
            <Camera className="w-5 h-5 text-brand-600" />
            <h3 className="font-bold text-gray-900">ערוך תמונה לחיפוש מדויק</h3>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gray-100">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* Toolbar */}
        <div className="flex items-center gap-2 px-4 py-2.5 bg-gray-50 border-b border-gray-100">
          <span className="text-xs text-gray-500 font-medium ml-1">כלי:</span>
          {[
            { id: 'crop', icon: <Crop className="w-4 h-4" />, label: 'חתוך' },
            { id: 'mark', icon: <Circle className="w-4 h-4" />, label: 'סמן חלק' },
            { id: 'move', icon: <MousePointer className="w-4 h-4" />, label: 'הזז' },
          ].map(({ id, icon, label }) => (
            <button
              key={id}
              onClick={() => setTool(id)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                tool === id
                  ? 'bg-brand-600 text-white shadow-sm'
                  : 'bg-white border border-gray-200 text-gray-600 hover:border-brand-400'
              }`}
            >
              {icon}{label}
            </button>
          ))}
          <div className="h-5 w-px bg-gray-200 mx-1" />
          <button
            onClick={handleReset}
            disabled={!crop && marks.length === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm border border-gray-200 text-gray-500 hover:border-red-300 hover:text-red-500 disabled:opacity-40 transition-all"
          >
            <RotateCcw className="w-3.5 h-3.5" />איפוס
          </button>
          <div className="h-5 w-px bg-gray-200 mx-1" />
          {/* Zoom controls */}
          <button
            onClick={() => applyZoom(zoom - 0.25)}
            disabled={zoom <= 0.25}
            className="w-7 h-7 flex items-center justify-center rounded border border-gray-200 text-gray-600 hover:border-brand-400 disabled:opacity-40 text-base font-bold"
            title="הקטן"
          >−</button>
          <button
            onClick={() => applyZoom(1)}
            className="px-2 h-7 rounded border border-gray-200 text-xs text-gray-500 hover:border-brand-400 tabular-nums"
            title="איפוס זום"
          >{Math.round(zoom * 100)}%</button>
          <button
            onClick={() => applyZoom(zoom + 0.25)}
            disabled={zoom >= 5}
            className="w-7 h-7 flex items-center justify-center rounded border border-gray-200 text-gray-600 hover:border-brand-400 disabled:opacity-40 text-base font-bold"
            title="הגדל"
          >+</button>
          <div className="text-xs text-gray-400 mr-auto hidden sm:block">
            {tool === 'crop' ? 'גרור לבחירת אזור' : tool === 'mark' ? 'לחץ / גרור להגדלת עיגול' : 'גרור עיגול קיים למיקום חדש'}
          </div>
        </div>

        {/* Canvas */}
        <div className="flex-1 overflow-auto bg-gray-900 flex items-center justify-center p-4" style={{minHeight: 200}}>
          <canvas
            ref={canvasRef}
            className="touch-none select-none rounded-lg"
            style={{cursor: tool === 'move' ? 'grab' : 'crosshair'}}
            onWheel={onWheel}
            onMouseDown={onPointerDown}
            onMouseMove={onPointerMove}
            onMouseUp={onPointerUp}
            onMouseLeave={onPointerUp}
            onTouchStart={onPointerDown}
            onTouchMove={onPointerMove}
            onTouchEnd={onPointerUp}
          />
        </div>

        {/* Instructions */}
        <div className="px-4 py-2 bg-blue-50 border-t border-blue-100">
          <p className="text-xs text-blue-700">
            {tool === 'crop'
              ? '✂️ גרור מלבן סביב החלק שרוצים לזהות — ה-AI יתמקד רק בו'
              : tool === 'mark'
              ? '🔴 לחץ על החלק ו/או גרור ליצירת עיגול — עוזר ל-AI לזהות מה סימנת'
              : '↕️ גרור עיגול קיים למיקום חדש על-גבי התמונה'}
          </p>
        </div>

        {/* Footer */}
        <div className="flex gap-2 p-4 border-t border-gray-100">
          <button onClick={onClose} className="btn-secondary flex-1">ביטול</button>
          <button
            onClick={handleApply}
            className="btn-primary flex-1 flex items-center justify-center gap-2"
          >
            <Check className="w-4 h-4" />
            {crop && crop.w > 20 ? 'החל חיתוך וחפש' : marks.length > 0 ? 'החל סימון וחפש' : 'חפש ללא עריכה'}
          </button>
        </div>
      </div>
    </div>
  )
}

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

// Category → accent colour (left border color as hex + Tailwind bg/text classes)
// Using hex color for border avoids Tailwind JIT purge / specificity issues with dynamic class names
const CATEGORY_ACCENT = {
  'בלמים':           { color: '#f87171', bg: 'bg-red-50',     text: 'text-red-700',     icon: '🛑' },
  'מנוע':            { color: '#fb923c', bg: 'bg-orange-50',  text: 'text-orange-700',  icon: '⚙️' },
  'מתלה':            { color: '#facc15', bg: 'bg-yellow-50',  text: 'text-yellow-700',  icon: '🔧' },
  'היגוי':           { color: '#a3e635', bg: 'bg-lime-50',    text: 'text-lime-700',    icon: '🎯' },
  'תאורה':           { color: '#38bdf8', bg: 'bg-sky-50',     text: 'text-sky-700',     icon: '💡' },
  'מיזוג':           { color: '#22d3ee', bg: 'bg-cyan-50',    text: 'text-cyan-700',    icon: '❄️' },
  'חשמל רכב':        { color: '#a78bfa', bg: 'bg-violet-50',  text: 'text-violet-700',  icon: '⚡' },
  'דלק':             { color: '#34d399', bg: 'bg-emerald-50', text: 'text-emerald-700', icon: '⛽' },
  'פח ומרכב':        { color: '#60a5fa', bg: 'bg-blue-50',    text: 'text-blue-700',    icon: '🚗' },
  'ריפוד ופנים':     { color: '#f472b6', bg: 'bg-pink-50',    text: 'text-pink-700',    icon: '🪑' },
  'גלגלים וצמיגים':  { color: '#a8a29e', bg: 'bg-stone-50',   text: 'text-stone-700',   icon: '🛞' },
  'תיבת הילוכים':    { color: '#2dd4bf', bg: 'bg-teal-50',    text: 'text-teal-700',    icon: '🔩' },
  'מגבים':           { color: '#94a3b8', bg: 'bg-slate-50',   text: 'text-slate-600',   icon: '🌧️' },
  'שרשראות ורצועות': { color: '#d97706', bg: 'bg-amber-50',   text: 'text-amber-700',   icon: '⛓️' },
  'כללי':            { color: '#d1d5db', bg: 'bg-gray-50',    text: 'text-gray-600',    icon: '📦' },
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

// Transform a new-API supplier row into the shape PartCard expects
function _supplierForCard(sp) {
  const costIls    = parseFloat(sp.price_ils) || 0
  const priceNet   = Math.round(costIls * 1.30)           // 30 % retail margin
  const vatAmount  = Math.round(priceNet * 0.17)
  const ship       = Math.round(parseFloat(sp.shipping_cost_ils) || 91)
  return {
    ...sp,
    supplier_part_id:       sp.supplier_part_id,
    price_no_vat:           priceNet,
    vat:                    vatAmount,
    shipping:               ship,
    subtotal:               priceNet + vatAmount,
    total:                  priceNet + vatAmount + ship,
    availability:           sp.availability || 'on_order',
    estimated_delivery_days: sp.estimated_delivery_days,
    is_base_price_fallback: false,
  }
}

// Express badge
function ExpressBadge({ price, days, cutoff }) {
  if (!price) return null
  return (
    <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-yellow-50 text-yellow-700 border border-yellow-200 font-medium">
      ⚡ אקספרס {days}d · +₪{Math.round(price)}{cutoff ? ` עד ${cutoff}` : ''}
    </span>
  )
}

const TYPE_META = {
  original:    { label: 'מקורי',   color: '#3b82f6', bg: 'bg-blue-50',   text: 'text-blue-700',  icon: '🔵', badge: 'bg-blue-100 text-blue-700 border-blue-300' },
  oem:         { label: 'OEM',     color: '#7c3aed', bg: 'bg-violet-50', text: 'text-violet-700',icon: '🔷', badge: 'bg-violet-100 text-violet-700 border-violet-300' },
  aftermarket: { label: 'חליפי',   color: '#d97706', bg: 'bg-amber-50',  text: 'text-amber-700', icon: '🟡', badge: 'bg-amber-100 text-amber-700 border-amber-300' },
}

function TypeSection({ typeKey, data, onAddToCart }) {
  const meta = TYPE_META[typeKey] || TYPE_META.aftermarket
  if (!data?.part) {
    return (
      <div className={`rounded-2xl border border-dashed border-gray-200 ${meta.bg} p-6 text-center opacity-60`}>
        <div className="text-3xl mb-2">{meta.icon}</div>
        <p className={`text-sm font-medium ${meta.text}`}>{meta.label}</p>
        <p className="text-xs text-gray-400 mt-1">אין תוצאות</p>
      </div>
    )
  }

  const { part, suppliers = [] } = data
  const accent = CATEGORY_ACCENT[part.category] || CATEGORY_ACCENT['כללי']

  return (
    <div className="rounded-2xl border border-gray-100 overflow-hidden shadow-sm hover:shadow-md transition-shadow">
      {/* Type header */}
      <div className={`${meta.bg} px-4 py-2 flex items-center gap-2 border-b border-gray-100`}>
        <span className="text-lg">{meta.icon}</span>
        <span className={`text-sm font-bold ${meta.text}`}>{meta.label}</span>
        {part.is_safety_critical && (
          <span className="ml-auto text-xs bg-red-100 text-red-700 border border-red-200 px-2 py-0.5 rounded-full font-medium">⚠️ בטיחותי</span>
        )}
      </div>

      {/* Part info */}
      <div className={`${accent.bg} px-4 pt-3 pb-2 border-l-4`} style={{ borderLeftColor: accent.color }}>
        <h3 className="font-semibold text-gray-900 text-sm leading-snug line-clamp-2">
          {part.name_he || part.name}
        </h3>
        <p className={`text-xs mt-0.5 font-medium ${accent.text}`}>
          <span className="mr-1">{accent.icon}</span>{part.category}
        </p>
        <div className="flex flex-wrap gap-1 mt-1">
          <span className="text-xs text-gray-500">{part.manufacturer}</span>
          {part.sku && <span className="text-xs text-gray-400">· {part.sku}</span>}
          {part.oem_number && <span className="text-xs text-gray-400">· OEM: {part.oem_number}</span>}
        </div>
      </div>

      {/* Superseded part warning */}
      {part.superseded_by_sku && (
        <div className="mx-4 mt-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700 flex items-center gap-2">
          <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
          <span>חלק זה הוחלף — מספר חדש: <span className="font-mono font-bold">{part.superseded_by_sku}</span></span>
        </div>
      )}

      {/* Supplier offers */}
      <div className="px-4 pb-4 pt-2 space-y-2">
        {suppliers.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-4 text-center gap-1 opacity-60">
            <span className="text-xs text-gray-400">אין הצעות ספק זמינות</span>
          </div>
        ) : (
          suppliers.map((sp, i) => {
            const s = _supplierForCard(sp)
            return (
              <div
                key={sp.supplier_part_id || i}
                className={`rounded-xl border px-3 py-2.5 flex flex-col gap-2 ${
                  i === 0 ? 'bg-white shadow-sm border-gray-200' : 'bg-gray-50 border-gray-100'
                }`}
                style={i === 0 ? { borderLeftColor: meta.color, borderLeftWidth: 3 } : undefined}
              >
                {/* Row 1: supplier label + availability */}
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <span className="flex items-center gap-1 text-xs font-medium text-gray-700 truncate">
                    <CountryFlag country={sp.supplier_country} />
                    {sp.supplier_name || `ספק #${i + 1}`}
                  </span>
                  <div className="flex items-center gap-1 flex-wrap">
                    <AvailabilityBadge availability={sp.availability} deliveryDays={sp.estimated_delivery_days} />
                    {sp.express_available && (
                      <ExpressBadge price={sp.express_price_ils} days={sp.express_delivery_days} cutoff={sp.express_cutoff_time} />
                    )}
                    {sp.warranty_months && (
                      <span className="flex items-center gap-1 text-xs text-gray-500">
                        <Shield className="w-3 h-3" />{sp.warranty_months} חודשים
                      </span>
                    )}
                  </div>
                </div>
                {/* Row 2: price breakdown */}
                <div>
                  <div className={`text-2xl font-bold text-left mb-1 ${accent.text}`}>₪{s.total?.toFixed(0)}</div>
                  <div className="grid grid-cols-3 gap-1 text-center text-xs text-gray-500 bg-gray-50 rounded-lg py-1.5">
                    <div><div className="font-semibold text-gray-700">₪{s.price_no_vat}</div>נטו</div>
                    <div className="border-x border-gray-200"><div className="font-semibold text-gray-700">₪{s.vat}</div>מע״מ</div>
                    <div><div className="font-semibold text-gray-700">₪{s.shipping}</div>משלוח</div>
                  </div>
                </div>
                {/* Row 3: cart button */}
                <button
                  onClick={() => {
                    onAddToCart({
                      partId: part.id,
                      supplierPartId: sp.supplier_part_id || `fallback-${part.id}-${i}`,
                      name: part.name_he || part.name,
                      manufacturer: part.manufacturer,
                      price: s.subtotal,
                      vat: s.vat,
                      deliveryDays: sp.estimated_delivery_days || null,
                      isEstimated: false,
                    })
                    toast.success(`${part.name_he || part.name} נוסף לסל 🛒`)
                  }}
                  className={`w-full text-sm flex items-center justify-center gap-2 py-2 rounded-lg font-medium transition-colors ${
                    i === 0
                      ? 'bg-brand-600 hover:bg-brand-700 text-white'
                      : 'bg-white hover:bg-brand-50 text-brand-700 border border-brand-200'
                  }`}
                >
                  <ShoppingCart className="w-4 h-4" /> הוסף לסל
                </button>
              </div>
            )
          })
        )}
      </div>
    </div>
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
    <div
      className="card overflow-hidden hover:shadow-lg transition-all duration-200 border border-gray-100 border-l-4 flex flex-col h-full"
      style={{ borderLeftColor: accent.color }}
    >
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
              <div
                key={i}
                className={`rounded-xl border px-3 py-2.5 flex flex-col gap-2 ${
                  i === 0 ? 'border-l-2 border-t border-r border-b border-gray-100 bg-white shadow-sm' : 'border-gray-100 bg-gray-50'
                }`}
                style={i === 0 ? { borderLeftColor: accent.color } : undefined}
              >
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

// ─── Vehicle confirm / picker popup ─────────────────────────────────────────
function VehicleConfirmModal({ vehicles, selectedVehicle, onConfirm, onClose }) {
  // If a vehicle is already selected → show quick-confirm screen first
  const [mode, setMode] = useState(selectedVehicle ? 'confirm' : 'pick')
  const [chosen, setChosen] = useState(selectedVehicle?.id ?? '__none__')

  // ── Quick-confirm screen ──────────────────────────────────────────────────
  if (mode === 'confirm' && selectedVehicle) {
    const sv = selectedVehicle
    const details = [sv.fuel_type, sv.color, sv.engine_cc ? `${sv.engine_cc}cc` : null].filter(Boolean).join(' · ')
    return (
      <div
        className="fixed inset-0 z-[9999] flex items-end sm:items-center justify-center p-4"
        style={{ background: 'rgba(0,0,0,0.6)' }}
        onClick={onClose}
      >
        <div
          className="bg-white rounded-3xl w-full max-w-sm shadow-2xl overflow-hidden"
          onClick={e => e.stopPropagation()}
        >
          {/* Top bar */}
          <div className="bg-gradient-to-r from-brand-600 to-brand-700 px-5 pt-5 pb-4 text-white text-center">
            <p className="text-xs font-semibold uppercase tracking-widest text-white/70 mb-1">לאיזה רכב לחפש?</p>
            <h2 className="text-2xl font-bold">{sv.manufacturer} {sv.model}</h2>
            <p className="text-white/80 text-sm mt-0.5">{sv.year}</p>
          </div>

          {/* Car details card */}
          <div className="mx-4 mt-4 bg-brand-50 border border-brand-100 rounded-2xl p-4">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-11 h-11 rounded-xl bg-brand-100 flex items-center justify-center flex-shrink-0">
                <Car className="w-6 h-6 text-brand-600" />
              </div>
              <div className="text-right">
                <p className="font-bold text-gray-900 text-sm">{sv.manufacturer} {sv.model} {sv.year}</p>
                {details && <p className="text-xs text-gray-500 mt-0.5">{details}</p>}
              </div>
            </div>
            {(sv.front_tire || sv.test_expiry_date || sv.horsepower) && (
              <div className="grid grid-cols-3 gap-1.5 text-center">
                {sv.front_tire && (
                  <div className="bg-white rounded-xl p-2">
                    <p className="text-gray-400 text-xs mb-0.5">צמיג</p>
                    <p className="font-semibold text-gray-700 text-xs">{sv.front_tire}</p>
                  </div>
                )}
                {sv.horsepower > 0 && (
                  <div className="bg-white rounded-xl p-2">
                    <p className="text-gray-400 text-xs mb-0.5">כ״ס</p>
                    <p className="font-semibold text-gray-700 text-xs">{sv.horsepower}</p>
                  </div>
                )}
                {sv.test_expiry_date && (
                  <div className="bg-white rounded-xl p-2">
                    <p className="text-gray-400 text-xs mb-0.5">טסט</p>
                    <p className="font-semibold text-gray-700 text-xs">{sv.test_expiry_date?.split('T')[0]?.slice(0,7)}</p>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="p-4 space-y-2.5">
            <button
              onClick={() => onConfirm(sv)}
              className="w-full py-3.5 rounded-2xl bg-brand-600 hover:bg-brand-700 text-white font-bold text-base transition-colors flex items-center justify-center gap-2 shadow-md shadow-brand-200"
            >
              <CheckCircle className="w-5 h-5" />
              כן, חפש עבור רכב זה
            </button>
            {vehicles.length > 1 && (
              <button
                onClick={() => setMode('pick')}
                className="w-full py-2.5 rounded-xl border-2 border-gray-200 text-gray-600 hover:bg-gray-50 hover:border-gray-300 font-medium text-sm transition-all"
              >
                בחר רכב אחר
              </button>
            )}
            <button
              onClick={() => onConfirm(null)}
              className="w-full py-2 text-sm text-gray-400 hover:text-gray-600 transition-colors"
            >
              חפש ללא סינון רכב
            </button>
          </div>
        </div>
      </div>
    )
  }

  // ── Full picker list ──────────────────────────────────────────────────────
  return (
    <div
      className="fixed inset-0 z-[9999] flex items-end sm:items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.6)' }}
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl w-full max-w-md shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="bg-gradient-to-r from-brand-600 to-brand-700 px-5 pt-5 pb-4 text-white text-center rounded-t-2xl">
          <p className="text-xs font-semibold uppercase tracking-widest text-white/70 mb-1">לאיזה רכב לחפש?</p>
          <h2 className="text-2xl font-bold">בחר רכב</h2>
          <p className="text-white/80 text-sm mt-0.5">לצמצום תוצאות החיפוש</p>
        </div>
        {/* Vehicle options */}
        <div className="p-4 space-y-2 max-h-64 overflow-y-auto">
          {vehicles.map(v => (
            <button
              key={v.id}
              onClick={() => setChosen(v.id)}
              className={`w-full flex items-center gap-3 p-3 rounded-xl border-2 text-right transition-all ${
                chosen === v.id ? 'border-brand-500 bg-brand-50' : 'border-gray-100 hover:border-brand-200 bg-white'
              }`}
            >
              <div className={`w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0 ${chosen === v.id ? 'bg-brand-100' : 'bg-gray-100'}`}>
                <Car className={`w-5 h-5 ${chosen === v.id ? 'text-brand-600' : 'text-gray-500'}`} />
              </div>
              <div className="flex-1 min-w-0 text-right">
                <p className="font-semibold text-gray-900 text-sm">{v.manufacturer} {v.model}</p>
                <p className="text-xs text-gray-400 mt-0.5">{v.year}{v.fuel_type ? ` · ${v.fuel_type}` : ''}{v.color ? ` · ${v.color}` : ''}</p>
              </div>
              {chosen === v.id && <CheckCircle className="w-5 h-5 text-brand-600 flex-shrink-0" />}
            </button>
          ))}
          <button
            onClick={() => setChosen('__none__')}
            className={`w-full flex items-center gap-3 p-3 rounded-xl border-2 text-right transition-all ${
              chosen === '__none__' ? 'border-gray-400 bg-gray-50' : 'border-gray-100 hover:border-gray-300 bg-white'
            }`}
          >
            <div className={`w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0 ${chosen === '__none__' ? 'bg-gray-200' : 'bg-gray-100'}`}>
              <Search className="w-5 h-5 text-gray-500" />
            </div>
            <div className="flex-1 text-right">
              <p className="font-semibold text-gray-700 text-sm">ללא סינון רכב</p>
              <p className="text-xs text-gray-400">חפש בכל הרכבים</p>
            </div>
            {chosen === '__none__' && <CheckCircle className="w-5 h-5 text-gray-500 flex-shrink-0" />}
          </button>
        </div>
        <div className="p-4 border-t border-gray-100 flex gap-3">
          <button onClick={onClose} className="flex-1 btn-secondary">ביטול</button>
          <button
            onClick={() => onConfirm(vehicles.find(v => v.id === chosen) || null)}
            className="flex-1 btn-primary"
          >
            אשר בחירה
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── VIN Scanner Modal ──────────────────────────────────────────────────────
function VinScannerModal({ onResult, onClose }) {
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const [error, setError] = useState('')
  const [scanning, setScanning] = useState(false)
  const [detected, setDetected] = useState('')

  useEffect(() => {
    let detector = null
    let animId = null

    const start = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 } }
        })
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          await videoRef.current.play()
        }

        // Try BarcodeDetector API (Chrome/Edge)
        if ('BarcodeDetector' in window) {
          detector = new window.BarcodeDetector({ formats: ['code_128', 'code_39', 'qr_code', 'ean_13'] })
          setScanning(true)
          const scan = async () => {
            if (!videoRef.current || videoRef.current.readyState < 2) { animId = requestAnimationFrame(scan); return }
            try {
              const barcodes = await detector.detect(videoRef.current)
              for (const bc of barcodes) {
                const val = bc.rawValue.replace(/[^A-HJ-NPR-Z0-9]/gi, '').toUpperCase()
                if (val.length === 17) { setDetected(val); return }
              }
            } catch {}
            animId = requestAnimationFrame(scan)
          }
          animId = requestAnimationFrame(scan)
        } else {
          setScanning(false) // manual capture fallback
        }
      } catch (e) {
        setError('לא ניתן לגשת למצלמה: ' + (e.message || ''))
      }
    }
    start()
    return () => {
      cancelAnimationFrame(animId)
      streamRef.current?.getTracks().forEach(t => t.stop())
    }
  }, [])

  const handleConfirm = () => { if (detected) { onResult(detected); onClose() } }

  const handleCapture = () => {
    // Fallback: capture frame and show it; user reads VIN manually
    const canvas = document.createElement('canvas')
    canvas.width = videoRef.current?.videoWidth || 640
    canvas.height = videoRef.current?.videoHeight || 480
    canvas.getContext('2d').drawImage(videoRef.current, 0, 0)
    // Try to parse any text — best effort via BarcodeDetector if available
    if ('BarcodeDetector' in window) {
      new window.BarcodeDetector({ formats: ['code_128', 'code_39'] }).detect(canvas)
        .then(barcodes => {
          for (const bc of barcodes) {
            const val = bc.rawValue.replace(/[^A-HJ-NPR-Z0-9]/gi, '').toUpperCase()
            if (val.length === 17) { setDetected(val) }
          }
        }).catch(() => {})
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80" onClick={onClose}>
      <div className="bg-white rounded-2xl overflow-hidden shadow-2xl w-full max-w-sm mx-4" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 bg-gray-900">
          <div className="flex items-center gap-2 text-white">
            <ScanLine className="w-4 h-4" />
            <span className="text-sm font-semibold">סריקת VIN</span>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Camera view */}
        <div className="relative bg-black aspect-video">
          <video ref={videoRef} className="w-full h-full object-cover" muted playsInline />
          {/* scan guide overlay */}
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div className="border-2 border-brand-400 rounded-lg w-4/5 h-12 opacity-70" />
          </div>
          {scanning && !detected && (
            <div className="absolute bottom-2 left-0 right-0 flex justify-center">
              <span className="bg-black/60 text-white text-xs px-2 py-1 rounded-full animate-pulse">מחפש ברקוד VIN...</span>
            </div>
          )}
        </div>

        {/* Error */}
        {error && <p className="text-red-500 text-xs text-center px-4 py-2">{error}</p>}

        {/* Detected result */}
        {detected ? (
          <div className="p-4 space-y-3">
            <p className="text-xs text-gray-500 text-center">VIN זוהה:</p>
            <p className="font-mono font-bold text-center text-gray-900 tracking-widest text-sm bg-green-50 border border-green-200 rounded-xl py-2 px-3">{detected}</p>
            <div className="flex gap-2">
              <button onClick={() => setDetected('')} className="flex-1 py-2 rounded-xl border border-gray-200 text-sm text-gray-600 hover:bg-gray-50">נסה שוב</button>
              <button onClick={handleConfirm} className="flex-1 py-2 rounded-xl bg-brand-600 text-white text-sm font-semibold hover:bg-brand-700">אשר</button>
            </div>
          </div>
        ) : (
          <div className="p-4 space-y-2">
            <p className="text-xs text-gray-400 text-center">כוון את המצלמה לברקוד ה-VIN</p>
            {!scanning && (
              <button onClick={handleCapture} className="w-full py-2.5 rounded-xl bg-brand-600 text-white text-sm font-semibold hover:bg-brand-700">
                <Camera className="w-4 h-4 inline ml-1" /> צלם לזיהוי
              </button>
            )}
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

  const [searchMode, setSearchMode] = useState('manual')

  const [query, setQuery] = useState(searchParams.get('search') || '')
  const [category, setCategory] = useState('')
  const [categories, setCategories] = useState([])
  const [categoryCounts, setCategoryCounts] = useState({})
  const [parts, setParts] = useState([])          // flat list for photo/legacy search
  const [searchResults, setSearchResults] = useState(null) // grouped: {original,oem,aftermarket}
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
  const [modelOptions, setModelOptions] = useState([])
  const [modelsLoading, setModelsLoading] = useState(false)
  const [manualYear, setManualYear] = useState('')
  const [sortBy, setSortBy] = useState('availability')
  const [filterAvail, setFilterAvail] = useState('')
  const [filterType, setFilterType] = useState('')
  const [perType, setPerType] = useState(4)   // suppliers per type shown in grouped view
  const [showVehiclePicker, setShowVehiclePicker] = useState(false)

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
      const cats = (data.categories || []).slice().sort((a, b) => a.localeCompare(b, 'he'))
      setCategories(cats)
      setCategoryCounts(data.counts || {})
    })
    partsApi.brandsWithParts().then(({ data }) => setBrands(data.brands || []))
    partsApi.models().then(({ data }) => setModelOptions(data.models || []))
    if (urlSearch && !urlSearchDone.current) {
      urlSearchDone.current = true
      if (urlCategory) setCategory(urlCategory)
      setTimeout(() => {
        setSearchMode('manual')
        setSearched(true)
        setIsLoading(true)
        partsApi.search(urlSearch, null, urlCategory)
          .then(({ data }) => {
            if (data.original !== undefined || data.oem !== undefined) {
              setSearchResults({ original: data.original, oem: data.oem, aftermarket: data.aftermarket })
              const flat = [
                ...(data.original?.part  ? [{ ...data.original.part,  suppliers: data.original.suppliers  }] : []),
                ...(data.oem?.part       ? [{ ...data.oem.part,       suppliers: data.oem.suppliers       }] : []),
                ...(data.aftermarket?.part ? [{ ...data.aftermarket.part, suppliers: data.aftermarket.suppliers }] : []),
              ]
              setParts(flat); setTotalCount(flat.length)
            } else {
              setParts(data.parts || []); setTotalCount(data.total || 0)
            }
          })
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

  // Reload model options when manufacturer changes
  useEffect(() => {
    setManualModel('')
    setModelsLoading(true)
    partsApi.models(manualManufacturer || null)
      .then(({ data }) => setModelOptions(data.models || []))
      .catch(() => setModelOptions([]))
      .finally(() => setModelsLoading(false))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manualManufacturer])

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
    setSearchResults(null)
    setSearched(false)
    setTotalCount(0)
    setPage(0)
    if ((mode === 'photo' || mode === 'voice') && vehicles.length > 0) {
      setShowVehiclePicker(true)
    }
  }

  const handleVehiclePickerConfirm = (vehicle) => {
    selectVehicle(vehicle)
    setShowVehiclePicker(false)
  }

  const buildManualQuery = () => query.trim()

  const activeFiltersCount = [manualManufacturer, manualModel, manualYear].filter(Boolean).length

  const clearManual = () => {
    setManualManufacturer('')
    setManualModel('')
    setManualYear('')
  }

  const search = async (pageNum = 0) => {
    const q = buildManualQuery()

    if (!q && !category && !manualManufacturer) {
      toast.error('הזן שם חלק, בחר קטגוריה, או בחר יצרן')
      return
    }

    setIsLoading(true)
    setSearched(true)
    try {
      const { data } = await partsApi.search(q, null, category, perType, manualManufacturer || null, manualModel || null, manualYear ? parseInt(manualYear) : null)
      // New grouped response
      if (data.original !== undefined || data.oem !== undefined || data.aftermarket !== undefined) {
        setSearchResults({ original: data.original, oem: data.oem, aftermarket: data.aftermarket })
        // Also flatten for backwards compat (stats, filters)
        const flat = [
          ...(data.original?.part  ? [{ ...data.original.part,  suppliers: data.original.suppliers  }] : []),
          ...(data.oem?.part       ? [{ ...data.oem.part,       suppliers: data.oem.suppliers       }] : []),
          ...(data.aftermarket?.part ? [{ ...data.aftermarket.part, suppliers: data.aftermarket.suppliers }] : []),
        ]
        setParts(flat)
        setTotalCount(flat.length)
      } else {
        // Fallback for old-style responses
        setSearchResults(null)
        setParts(data.parts || [])
        setTotalCount(data.total || 0)
      }
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
  const [showPhotoEditor, setShowPhotoEditor] = useState(false)

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

  const handleEditorApply = (editedFile, editedPreview) => {
    setPhotoFile(editedFile)
    setPhotoPreview(editedPreview)
    setPhotoResult(null)
    setPhotoCandidates([])
    photoSearchCache.current = {}
    setShowPhotoEditor(false)
  }

  const photoSearchCache = useRef({})

  const runPhotoPartsSearch = async (candidates, vehicleManufacturer) => {
    if (!candidates || candidates.length === 0) return

    // Return cached result instantly
    const cacheKey = `${candidates[0]}__${vehicleManufacturer}`
    if (photoSearchCache.current[cacheKey]) {
      const c = photoSearchCache.current[cacheKey]
      setQuery(c.query); setParts(c.parts); setSearchResults(c.grouped || null)
      setTotalCount(c.total); setSearched(true); setPage(0)
      setPhotoFallbackMfr(c.fallbackMfr || '')
      return
    }

    setIsLoading(true)
    try {
      const top = candidates.slice(0, 5)

      // Fire manufacturer-filtered searches + general searches in parallel
      const [mfrResults, genResults] = await Promise.all([
        vehicleManufacturer
          ? Promise.all(top.map(c => partsApi.search(c, null, category, null, vehicleManufacturer).catch(() => null)))
          : Promise.resolve(top.map(() => null)),
        Promise.all(top.map(c => partsApi.search(c, null, category).catch(() => null))),
      ])

      const flattenGrouped = (r) => [
        ...(r.original?.part    ? [{ ...r.original.part,    suppliers: r.original.suppliers    }] : []),
        ...(r.oem?.part         ? [{ ...r.oem.part,         suppliers: r.oem.suppliers         }] : []),
        ...(r.aftermarket?.part ? [{ ...r.aftermarket.part, suppliers: r.aftermarket.suppliers }] : []),
      ]

      let foundGrouped = null, foundParts = [], foundTotal = 0, usedQuery = top[0], usedMfr = false

      // Prefer manufacturer-filtered results first
      for (let i = 0; i < top.length; i++) {
        const d = mfrResults[i]?.data
        if (!d) continue
        if (d.original !== undefined || d.oem !== undefined) {
          const flat = flattenGrouped(d)
          if (flat.length > 0) {
            foundGrouped = { original: d.original, oem: d.oem, aftermarket: d.aftermarket }
            foundParts = flat; foundTotal = flat.length; usedQuery = top[i]; usedMfr = true; break
          }
        } else if ((d.parts || []).length > 0) {
          foundParts = d.parts; foundTotal = d.total || 0; usedQuery = top[i]; usedMfr = true; break
        }
      }

      // Fallback to general results if manufacturer filter returned nothing.
      // But if a specific vehicle manufacturer is selected, do NOT show wrong-brand
      // parts — keep results empty so the "no parts for [brand]" banner is shown.
      if (!usedMfr && !vehicleManufacturer) {
        for (let i = 0; i < top.length; i++) {
          const d = genResults[i]?.data
          if (!d) continue
          if (d.original !== undefined || d.oem !== undefined) {
            const flat = flattenGrouped(d)
            if (flat.length > 0) {
              foundGrouped = { original: d.original, oem: d.oem, aftermarket: d.aftermarket }
              foundParts = flat; foundTotal = flat.length; usedQuery = top[i]; break
            }
          } else if ((d.parts || []).length > 0) {
            foundParts = d.parts; foundTotal = d.total || 0; usedQuery = top[i]; break
          }
        }
      }

      photoSearchCache.current[cacheKey] = {
        query: usedQuery, parts: foundParts, grouped: foundGrouped,
        total: foundTotal, fallbackMfr: !usedMfr && vehicleManufacturer ? vehicleManufacturer : '',
      }

      setQuery(usedQuery); setParts(foundParts); setSearchResults(foundGrouped)
      setTotalCount(foundTotal); setSearched(true); setPage(0)
      setPhotoFallbackMfr(!usedMfr && vehicleManufacturer ? vehicleManufacturer : '')
    } finally {
      setIsLoading(false)
    }
  }

  // Re-run parts search when vehicle selection changes (if photo search is active)
  useEffect(() => {
    if (photoCandidates.length > 0) {
      runPhotoPartsSearch(photoCandidates, selectedVehicle?.manufacturer || '')
    }
  }, [selectedVehicle])

  const handlePhotoSearch = async () => {
    if (!photoFile) return
    setPhotoLoading(true)
    setIsLoading(true)
    try {
      const { data } = await partsApi.identifyFromImage(photoFile, selectedVehicle || null)
      setPhotoResult(data)
      if (data.identified_part) {
        // Build candidates: start with Hebrew name, then its slash/word components,
        // then possible_names (now Hebrew from updated prompt), then English fallback.
        const hebrewName = data.identified_part       // e.g. "בית מצערת"
        const englishName = data.identified_part_en   // e.g. "Throttle Body"
        const possibleNames = data.possible_names || []

        // Expand Hebrew name: split on "/" and spaces to get sub-terms
        const heExpanded = hebrewName
          ? [hebrewName, ...hebrewName.split(/[\/\s]+/).filter(w => w.length > 1)]
          : []

        // Deduplicate preserving order; Hebrew names first, English last as fallback
        const seen = new Set()
        const candidates = [
          ...heExpanded,
          ...possibleNames,
          englishName,
        ].filter(t => {
          if (!t || seen.has(t)) return false
          seen.add(t); return true
        })

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
    // Accept Hebrew primary + English fallback for mixed-language terms
    rec.lang = 'he-IL'
    rec.interimResults = true
    rec.maxAlternatives = 3
    rec.onresult = (e) => {
      // Pick the alternative with the most content (handles mixed He+En best)
      const getBest = (result) => {
        let best = result[0].transcript
        for (let i = 1; i < result.length; i++) {
          if (result[i].transcript.length > best.length) best = result[i].transcript
        }
        return best
      }
      const transcript = Array.from(e.results).map(r => getBest(r)).join('')
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
      setSearchResults(c.grouped || null)
      setVoiceFallbackMfr(c.fallbackMfr || '')
      return
    }
    setIsLoading(true)
    try {
      // Fire all candidates × (with/without mfr) in parallel — use updated partsApi.search signature
      const top = candidates.slice(0, 4)
      const [mfrResults, genResults] = await Promise.all([
        Promise.all(
          vehicleManufacturer
            ? top.map(c => partsApi.search(c, null, category, null, vehicleManufacturer).catch(() => null))
            : top.map(() => Promise.resolve(null))
        ),
        Promise.all(
          top.map(c => partsApi.search(c, null, category, null, null).catch(() => null))
        ),
      ])

      let foundParts = [], foundTotal = 0, foundGrouped = null, usedQuery = top[0], usedMfr = false

      // Helper: extract flat array from grouped response
      const flattenGrouped = (r) => [
        ...(r.original?.part    ? [{ ...r.original.part,    suppliers: r.original.suppliers    }] : []),
        ...(r.oem?.part         ? [{ ...r.oem.part,         suppliers: r.oem.suppliers         }] : []),
        ...(r.aftermarket?.part ? [{ ...r.aftermarket.part, suppliers: r.aftermarket.suppliers }] : []),
      ]

      // Prefer manufacturer-filtered results
      for (let i = 0; i < top.length; i++) {
        const r = mfrResults[i]?.data
        if (!r) continue
        if (r.original !== undefined || r.oem !== undefined) {
          const flat = flattenGrouped(r)
          if (flat.length > 0) {
            foundGrouped = { original: r.original, oem: r.oem, aftermarket: r.aftermarket }
            foundParts = flat; foundTotal = flat.length; usedQuery = top[i]; usedMfr = true; break
          }
        } else if ((r.parts || []).length > 0) {
          foundParts = r.parts; foundTotal = r.total || 0; usedQuery = top[i]; usedMfr = true; break
        }
      }
      // Fallback to general — only when no vehicle manufacturer is selected.
      // With a selected vehicle, keep results empty so the "no parts for [brand]"
      // banner shows instead of wrong-brand parts.
      if (!usedMfr && !vehicleManufacturer) {
        for (let i = 0; i < top.length; i++) {
          const r = genResults[i]?.data
          if (!r) continue
          if (r.original !== undefined || r.oem !== undefined) {
            const flat = flattenGrouped(r)
            if (flat.length > 0) {
              foundGrouped = { original: r.original, oem: r.oem, aftermarket: r.aftermarket }
              foundParts = flat; foundTotal = flat.length; usedQuery = top[i]; break
            }
          } else if ((r.parts || []).length > 0) {
            foundParts = r.parts; foundTotal = r.total || 0; usedQuery = top[i]; break
          }
        }
      }

      const fallbackMfr = !usedMfr && vehicleManufacturer ? vehicleManufacturer : ''
      voiceSearchCache.current[cacheKey] = { query: usedQuery, parts: foundParts, total: foundTotal, grouped: foundGrouped, fallbackMfr }
      setQuery(usedQuery); setParts(foundParts); setTotalCount(foundTotal); setSearched(true); setPage(0)
      setSearchResults(foundGrouped)
      setVoiceFallbackMfr(fallbackMfr)
    } catch { toast.error('שגיאה בחיפוש') }
    finally { setIsLoading(false) }
  }

  // Re-run voice search when vehicle changes (if voice transcript is active)
  useEffect(() => {
    if (voiceTranscript.trim()) {
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
  const [showVinScanner, setShowVinScanner] = useState(false)

  const handleVinSearch = async (partQuery = vinPartQuery, pageNum = 0, explicitVin) => {
    const vin = (explicitVin || vinInput).replace(/\s/g, '').toUpperCase()
    if (vin.length !== 17) { toast.error('VIN חייב להיות בן 17 תווים'); return }
    setVinLoading(true)
    setIsLoading(true)
    try {
      const { data } = await partsApi.searchByVin(vin, partQuery, category, PAGE_SIZE, pageNum * PAGE_SIZE)
      setVinVehicle(data.vehicle || null)
      const vinParts = data.parts || []
      if (vinParts.length > 0) {
        // Vehicle-id matched parts found
        setParts(vinParts)
        setSearchResults(null)
        setTotalCount(data.total || vinParts.length)
      } else if (data.vehicle?.manufacturer) {
        // Fallback: search by manufacturer + model using grouped search API
        const mfr = data.vehicle.manufacturer
        const q = [data.vehicle.model, partQuery].filter(Boolean).join(' ')
        const { data: gd } = await partsApi.search(q, null, category, perType, mfr)
        if (gd.original !== undefined || gd.oem !== undefined || gd.aftermarket !== undefined) {
          setSearchResults({ original: gd.original, oem: gd.oem, aftermarket: gd.aftermarket })
          const flat = [
            ...(gd.original?.part    ? [{ ...gd.original.part,    suppliers: gd.original.suppliers    }] : []),
            ...(gd.oem?.part         ? [{ ...gd.oem.part,         suppliers: gd.oem.suppliers         }] : []),
            ...(gd.aftermarket?.part ? [{ ...gd.aftermarket.part, suppliers: gd.aftermarket.suppliers }] : []),
          ]
          setParts(flat)
          setTotalCount(flat.length)
        } else {
          setParts([])
          setSearchResults(null)
          setTotalCount(0)
        }
      } else {
        setParts([])
        setSearchResults(null)
        setTotalCount(0)
      }
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
      {showVinScanner && (
        <VinScannerModal
          onResult={(vin) => {
            setVinInput(vin)
            setShowVinScanner(false)
            handleVinSearch(vinPartQuery, 0, vin)
          }}
          onClose={() => setShowVinScanner(false)}
        />
      )}
      {showVehiclePicker && (
        <VehicleConfirmModal
          vehicles={vehicles}
          selectedVehicle={selectedVehicle}
          onConfirm={handleVehiclePickerConfirm}
          onClose={() => setShowVehiclePicker(false)}
        />
      )}
      {showPhotoEditor && photoPreview && (
        <PhotoEditorModal
          src={photoPreview}
          onApply={handleEditorApply}
          onClose={() => setShowPhotoEditor(false)}
        />
      )}

      {/* Page hero */}
      <div className="rounded-2xl bg-gradient-to-br from-brand-600 to-brand-800 px-6 py-5 text-white shadow-lg">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 rounded-xl bg-white/20 flex items-center justify-center flex-shrink-0">
              <Search className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold leading-tight">חיפוש חלקי חילוף</h1>
              <p className="text-white/65 text-xs mt-0.5">חפש לפי רכב · תמונה · קול · VIN</p>
            </div>
          </div>
          {selectedVehicle && (
            <button
              onClick={() => setShowVehiclePicker(true)}
              className="flex items-center gap-2 bg-white/15 hover:bg-white/25 transition-colors rounded-xl px-3 py-2 text-right flex-shrink-0"
            >
              <div className="text-right">
                <p className="text-xs text-white/70 leading-none mb-0.5">הרכב שלי</p>
                <p className="text-sm font-bold leading-none">{selectedVehicle.manufacturer} {selectedVehicle.model} {selectedVehicle.year}</p>
              </div>
              <div className="w-8 h-8 rounded-lg bg-white/20 flex items-center justify-center flex-shrink-0">
                <Car className="w-4 h-4 text-white" />
              </div>
            </button>
          )}
        </div>

      </div>

      {/* ── 4 search blocks — visual order controlled by CSS flex order ── */}
      <div className="flex flex-col gap-6">

      {/* ── BLOCK 1: Free text search ── */}
      <div className="card p-4" style={{order: 1}}>
        <div className="flex items-center gap-2 mb-3">
          <div className="w-8 h-8 rounded-lg bg-brand-100 flex items-center justify-center flex-shrink-0">
            <Search className="w-4 h-4 text-brand-600" />
          </div>
          <h3 className="font-semibold text-gray-900">חיפוש חופשי</h3>
          {activeFiltersCount > 0 && (
            <span className="text-xs bg-brand-100 text-brand-700 px-2 py-0.5 rounded-full font-medium mr-auto">
              {activeFiltersCount} פילטרים פעילים
            </span>
          )}
        </div>
        <div className="flex flex-col sm:flex-row gap-3">
          <div className="relative flex-1" ref={suggestRef}>
            <Search className="absolute right-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
            <input
              className="input-field pr-10"
              placeholder="שם החלק לחיפוש... (רפידות בלם, פילטר שמן, מצמד...)"
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
        {activeFiltersCount > 0 && (
          <p className="text-xs text-brand-600 mt-2">
            🔍 מחפש: {[manualManufacturer, manualModel, manualYear, query].filter(Boolean).join(' ') || 'כל החלקים'}
          </p>
        )}
      </div>

      {/* ── BLOCK 4: Search by plate / VIN ── */}
      <div className="card p-4 space-y-4" style={{order: 4}}>
          {/* Header */}
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-green-100 flex items-center justify-center flex-shrink-0">
              <Car className="w-4 h-4 text-green-600" />
            </div>
            <h3 className="font-semibold text-gray-900">חיפוש לפי רכב (לוחית / VIN)</h3>
          </div>

          {/* ── Search inputs row: plate + VIN ── */}
          <div className="grid grid-cols-2 gap-2">

            {/* Israeli license plate input */}
            <div>
              <label className="block text-[11px] text-gray-500 mb-1 font-medium">מספר רכב</label>
              <div className="flex gap-1.5">
                <div className="relative flex rounded-lg overflow-hidden border border-gray-200 focus-within:border-brand-400 focus-within:ring-1 focus-within:ring-brand-300 transition-colors flex-1 min-w-0">
                  <input
                    className="w-full bg-white text-gray-900 font-mono font-semibold text-sm tracking-[0.15em] text-center uppercase placeholder:text-gray-400 placeholder:font-normal placeholder:text-xs placeholder:tracking-normal focus:outline-none px-2 py-2"
                    placeholder="123-45-678"
                    dir="ltr"
                    value={newPlate}
                    onChange={(e) => setNewPlate(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && addVehicle()}
                  />
                </div>
                <button
                  onClick={addVehicle}
                  disabled={addingVehicle}
                  className="bg-brand-600 hover:bg-brand-700 disabled:opacity-50 text-white px-3 py-2 rounded-lg font-semibold text-xs flex items-center gap-1 transition-colors whitespace-nowrap flex-shrink-0"
                >
                  {addingVehicle ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Car className="w-3.5 h-3.5" />}
                  הוסף
                </button>
              </div>
            </div>

            {/* VIN input */}
            <div>
              <label className="flex items-center gap-1 text-[11px] text-gray-500 mb-1 font-medium">
                VIN
                {vinInput.length > 0 && (
                  <span className={`mr-auto text-[10px] font-semibold ${
                    vinInput.replace(/\s/g, '').length === 17 ? 'text-green-600' : 'text-orange-400'
                  }`}>
                    {vinInput.replace(/\s/g, '').length}/17
                  </span>
                )}
              </label>
              <div className="flex gap-1.5">
                <div className="relative flex-1 min-w-0">
                  <input
                    className="w-full border border-gray-200 rounded-lg bg-white text-gray-900 font-mono text-xs tracking-wider uppercase focus:outline-none focus:ring-2 focus:ring-brand-400 focus:border-transparent px-2 py-2 pl-7"
                    placeholder="1HGCM82633..."
                    dir="ltr"
                    maxLength={17}
                    value={vinInput}
                    onChange={(e) => setVinInput(e.target.value.toUpperCase())}
                    onKeyDown={(e) => e.key === 'Enter' && handleVinSearch()}
                  />
                  <button
                    type="button"
                    title="סרוק ברקוד VIN"
                    onClick={() => setShowVinScanner(true)}
                    className="absolute left-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-brand-600 transition-colors"
                  >
                    <ScanLine className="w-3.5 h-3.5" />
                  </button>
                </div>
                <button
                  onClick={() => handleVinSearch()}
                  disabled={vinLoading}
                  className="bg-brand-600 hover:bg-brand-700 disabled:opacity-50 text-white px-3 py-2 rounded-lg font-semibold text-xs flex items-center gap-1 transition-colors whitespace-nowrap flex-shrink-0"
                >
                  {vinLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
                  זהה
                </button>
              </div>
            </div>
          </div>

          {/* ── VIN result ── */}
          {vinVehicle && (
            <div className="bg-brand-50 border border-brand-100 rounded-xl p-3 space-y-1">
              <p className="font-semibold text-brand-700 text-sm">
                {[vinVehicle.manufacturer, vinVehicle.model, vinVehicle.year].filter(Boolean).join(' ')}
              </p>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-1 text-xs text-gray-700">
                {vinVehicle.fuel_type && <div><span className="text-gray-400">דלק</span><p className="font-medium">{vinVehicle.fuel_type}</p></div>}
                {vinVehicle.engine_cc > 0 && <div><span className="text-gray-400">נפח מנוע</span><p className="font-medium">{vinVehicle.engine_cc} cc</p></div>}
                {vinVehicle.transmission && <div><span className="text-gray-400">תיבת הילוכים</span><p className="font-medium">{vinVehicle.transmission}</p></div>}
                {vinVehicle.body_class && <div><span className="text-gray-400">סוג גוף</span><p className="font-medium">{vinVehicle.body_class}</p></div>}
                {vinVehicle.country_of_origin && <div><span className="text-gray-400">ייצור</span><p className="font-medium">{vinVehicle.country_of_origin}</p></div>}
              </div>
              {/* Part search after VIN decode */}
              <div className="pt-2 flex gap-2">
                <input
                  className="flex-1 border border-gray-200 rounded-lg text-sm px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-400"
                  placeholder="שם חלק לחיפוש (רפידות, פילטר...)"
                  value={vinPartQuery}
                  onChange={(e) => setVinPartQuery(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleVinSearch(vinPartQuery)}
                />
                <button
                  onClick={() => handleVinSearch(vinPartQuery)}
                  disabled={vinLoading}
                  className="bg-brand-600 hover:bg-brand-700 disabled:opacity-50 text-white px-4 py-1.5 rounded-lg text-sm font-semibold flex items-center gap-1 whitespace-nowrap"
                >
                  {vinLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
                  חפש חלק
                </button>
              </div>
            </div>
          )}

          {/* divider */}
          <div className="border-t border-gray-100" />

          {/* ── Selected vehicle details ── */}
          {selectedVehicle && (
            <div className="bg-brand-50 border border-brand-100 rounded-xl p-3">
              <p className="text-xs text-brand-600 font-semibold mb-2">
                {selectedVehicle.manufacturer} {selectedVehicle.model} {selectedVehicle.year}
              </p>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-1 text-sm text-gray-700">
                {selectedVehicle.fuel_type && <div><span className="text-gray-400 text-xs">דלק</span><p className="font-medium">{selectedVehicle.fuel_type}</p></div>}
                {selectedVehicle.color && <div><span className="text-gray-400 text-xs">צבע</span><p className="font-medium">{selectedVehicle.color}</p></div>}
                {selectedVehicle.engine_cc > 0 && <div><span className="text-gray-400 text-xs">נפח מנוע</span><p className="font-medium">{selectedVehicle.engine_cc} cc</p></div>}
                {selectedVehicle.horsepower > 0 && <div><span className="text-gray-400 text-xs">כ״ס</span><p className="font-medium">{selectedVehicle.horsepower}</p></div>}
                {selectedVehicle.front_tire && <div><span className="text-gray-400 text-xs">צמיג קדמי</span><p className="font-medium">{selectedVehicle.front_tire}</p></div>}
                {selectedVehicle.test_expiry_date && <div><span className="text-gray-400 text-xs">תוקף טסט</span><p className="font-medium">{selectedVehicle.test_expiry_date?.split('T')[0]}</p></div>}
              </div>
            </div>
          )}
        </div>

      {/* ── BLOCK 2: Car details (filters) ── */}
      <div className="card p-4 space-y-4" style={{order: 2}}>
          {/* Header */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-brand-100 flex items-center justify-center">
                <SlidersHorizontal className="w-4 h-4 text-brand-600" />
              </div>
              <h3 className="font-semibold text-gray-900">חיפוש לפי פרטי רכב</h3>
            </div>
            {(activeFiltersCount > 0 || category) && (
              <button onClick={() => { clearManual(); setCategory('') }} className="flex items-center gap-1 text-xs text-gray-400 hover:text-red-500 transition-colors">
                <X className="w-3 h-3" /> נקה הכל
              </button>
            )}
          </div>

          {/* Fields: Manufacturer → Model → Year → Part Type (todo #4 order) */}
          <div className="grid grid-cols-1 xs:grid-cols-2 sm:grid-cols-4 gap-3">

            {/* 1. Manufacturer */}
            <div>
              <label className="block text-xs text-gray-500 mb-1.5 font-medium">יצרן</label>
              <select
                className="w-full border border-gray-200 rounded-lg bg-white text-gray-900 text-sm focus:outline-none focus:ring-2 focus:ring-brand-400 focus:border-transparent px-2.5 py-2 transition-colors"
                value={manualManufacturer}
                onChange={(e) => setManualManufacturer(e.target.value)}
              >
                <option value="">כל היצרנים</option>
                {brands
                  .slice()
                  .sort((a, b) => a.name.localeCompare(b.name))
                  .map(b => (
                    <option key={b.name} value={b.name}>
                      {b.name_he ? `${b.name} · ${b.name_he}` : b.name}
                      {b.has_parts ? ` (${b.parts_count.toLocaleString()})` : ''}
                    </option>
                  ))}
              </select>
            </div>

            {/* 2. Model */}
            <div>
              <label className="block text-xs text-gray-500 mb-1.5 font-medium">דגם</label>
              <select
                className="w-full border border-gray-200 rounded-lg bg-white text-gray-900 text-sm focus:outline-none focus:ring-2 focus:ring-brand-400 focus:border-transparent px-2.5 py-2 transition-colors"
                value={manualModel}
                onChange={(e) => setManualModel(e.target.value)}
                disabled={modelsLoading}
              >
                <option value="">{modelsLoading ? 'טוען...' : 'כל הדגמים'}</option>
                {modelOptions.map(m => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>

            {/* 3. Year */}
            <div>
              <label className="block text-xs text-gray-500 mb-1.5 font-medium">שנה</label>
              <select
                className="w-full border border-gray-200 rounded-lg bg-white text-gray-900 text-sm focus:outline-none focus:ring-2 focus:ring-brand-400 focus:border-transparent px-2.5 py-2 transition-colors"
                value={manualYear}
                onChange={(e) => setManualYear(e.target.value)}
              >
                <option value="">כל השנים</option>
                {Array.from({ length: 2026 - 1980 + 1 }, (_, i) => 2026 - i).map(y => (
                  <option key={y} value={y}>{y}</option>
                ))}
              </select>
            </div>

            {/* 4. Part Type / Category */}
            <div>
              <label className="block text-xs text-gray-500 mb-1.5 font-medium">סוג חלק</label>
              <select
                className="w-full border border-gray-200 rounded-lg bg-white text-gray-900 text-sm focus:outline-none focus:ring-2 focus:ring-brand-400 focus:border-transparent px-2.5 py-2 transition-colors"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                <option value="">כל הסוגים</option>
                {categories.map((c) => (
                  <option key={c} value={c}>
                    {c}{categoryCounts[c] ? ` (${categoryCounts[c].toLocaleString()})` : ''}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Active filter chips */}
          {(activeFiltersCount > 0 || category) && (
            <div className="flex flex-wrap gap-2">
              {manualManufacturer && (
                <span className="inline-flex items-center gap-1 bg-brand-100 text-brand-700 text-xs px-2.5 py-1 rounded-full font-medium">
                  {manualManufacturer}
                  <button onClick={() => setManualManufacturer('')}><X className="w-3 h-3" /></button>
                </span>
              )}
              {category && (
                <span className="inline-flex items-center gap-1 bg-brand-100 text-brand-700 text-xs px-2.5 py-1 rounded-full font-medium">
                  {category}
                  <button onClick={() => setCategory('')}><X className="w-3 h-3" /></button>
                </span>
              )}
              {manualModel && (
                <span className="inline-flex items-center gap-1 bg-brand-100 text-brand-700 text-xs px-2.5 py-1 rounded-full font-medium">
                  {manualModel}
                  <button onClick={() => setManualModel('')}><X className="w-3 h-3" /></button>
                </span>
              )}
              {manualYear && (
                <span className="inline-flex items-center gap-1 bg-brand-100 text-brand-700 text-xs px-2.5 py-1 rounded-full font-medium">
                  {manualYear}
                  <button onClick={() => setManualYear('')}><X className="w-3 h-3" /></button>
                </span>
              )}
            </div>
          )}
          {/* Search button for Block 2 */}
          <div className="flex justify-start pt-1">
            <button onClick={() => search(0)} disabled={isLoading} className="btn-primary flex items-center gap-2">
              {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
              חפש לפי פרטי רכב
            </button>
          </div>
        </div>

      {/* ── BLOCK 3: Photo / Voice ── */}
      <div className="bg-white rounded-2xl border border-gray-100 shadow-sm" style={{order: 3}}>
          {/* Header */}
          <div className="flex items-center gap-2 px-4 pt-4">
            <div className="w-8 h-8 rounded-lg bg-purple-100 flex items-center justify-center flex-shrink-0">
              <Camera className="w-4 h-4 text-purple-600" />
            </div>
            <h3 className="font-semibold text-gray-900">חיפוש בתמונה / קול</h3>
          </div>
          {/* Sub-tab bar */}
          <div className="flex border-b border-gray-100 p-1.5 gap-1 mt-3">
            {[
              { key: 'photo', icon: <Camera className="w-4 h-4" />, label: 'תמונה' },
              { key: 'voice', icon: <Mic className="w-4 h-4" />, label: 'קול' },
            ].map(({ key, icon, label }) => {
              const active = (key === 'photo' && photoPreview) || (key === 'voice' && isListening)
              return (
                <button
                  key={key}
                  onClick={() => {
                    if (key === 'voice') toggleVoice()
                    else fileInputRef.current?.click()
                  }}
                  className={`flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-semibold transition-all ${
                    active
                      ? 'bg-brand-600 text-white shadow-sm'
                      : 'text-gray-500 hover:text-brand-600 hover:bg-brand-50'
                  }`}
                >
                  {icon}<span>{label}</span>
                </button>
              )
            })}
            {/* hidden file input still works */}
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              capture="environment"
              className="hidden"
              onChange={(e) => handlePhotoFile(e.target.files[0])}
            />
          </div>

          {/* Photo preview + controls */}
          {photoPreview && (
            <div className="p-4 space-y-3">
              <div className="relative inline-block w-full">
                <img src={photoPreview} alt="preview" className="max-h-48 mx-auto rounded-lg object-contain block" />
                <button
                  onClick={() => setShowPhotoEditor(true)}
                  className="absolute top-2 left-2 bg-black/60 hover:bg-black/80 text-white text-xs px-2.5 py-1.5 rounded-lg flex items-center gap-1.5 transition-colors"
                >
                  <Pencil className="w-3.5 h-3.5" />ערוך
                </button>
              </div>
              {photoResult && (
                <div className="bg-green-50 border border-green-200 rounded-xl p-3 space-y-2">
                  <div className="flex items-center gap-2">
                    <CheckCircle className="w-5 h-5 text-green-600 flex-shrink-0" />
                    <div>
                      <p className="font-semibold text-green-800">{photoResult.identified_part}</p>
                      {photoResult.identified_part_en && <p className="text-xs text-green-600">{photoResult.identified_part_en}</p>}
                    </div>
                    {photoResult.confidence && (
                      <span className="mr-auto badge bg-green-100 text-green-700 flex items-center gap-1">
                        {photoResult.cache_hit && <span title="תוצאה מהמאגר השמור">⚡</span>}
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
              <div className="flex gap-2">
                <button onClick={handlePhotoSearch} disabled={photoLoading} className="btn-primary flex-1 flex items-center justify-center gap-2">
                  {photoLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                  זהה וחפש חלק
                </button>
                <button onClick={() => setShowPhotoEditor(true)} className="btn-secondary px-3 flex items-center gap-1.5">
                  <Crop className="w-4 h-4" /><span className="hidden sm:inline text-sm">ערוך</span>
                </button>
                <button onClick={() => { setPhotoFile(null); setPhotoPreview(null); setPhotoResult(null); setPhotoCandidates([]) }} className="btn-secondary px-3">
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>
          )}

          {/* Voice listening indicator */}
          {(isListening || voiceTranscript) && (
            <div className="p-4 space-y-3">
              <div className="flex flex-col items-center gap-3 py-2">
                <button
                  onClick={toggleVoice}
                  className={`relative w-16 h-16 rounded-full flex items-center justify-center transition-all shadow-lg ${
                    isListening ? 'bg-red-500 hover:bg-red-600 text-white' : 'bg-brand-600 hover:bg-brand-700 text-white'
                  }`}
                >
                  {isListening && <span className="absolute inset-0 rounded-full bg-red-400 animate-ping opacity-75" />}
                  {isListening ? <MicOff className="w-6 h-6 relative" /> : <Mic className="w-6 h-6 relative" />}
                </button>
                <p className="text-sm text-gray-500">{isListening ? '🔴 מקשיב... לחץ לעצור' : 'לחץ להתחיל'}</p>
              </div>
              {voiceTranscript && (
                <div className="bg-gray-50 border border-gray-200 rounded-xl p-3">
                  <p className="text-xs text-gray-400 mb-1">זוהה:</p>
                  <p className="font-medium text-gray-800">{voiceTranscript}</p>
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
                  {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}חפש
                </button>
              </div>
            </div>
          )}
        </div>

      </div>{/* end 4-blocks flex */}

      {/* ── OLD STANDALONE PHOTO MODE (replaced — kept as dead block, never shown) ── */}
      {false && searchMode === 'photo' && (
        <div className="card p-4 space-y-4">
          <div className="flex items-center gap-2 mb-1">
            <Camera className="w-5 h-5 text-brand-600" />
            <h3 className="font-semibold text-gray-900">זיהוי חלק מתמונה</h3>
          </div>
          <p className="text-sm text-gray-500">צלם או העלה תמונה של החלק — ה-AI יזהה אותו ויחפש במאגר</p>

          {/* Vehicle confirmation banner */}
          {vehicles.length > 0 && (
            selectedVehicle ? (
              <div className="flex items-center justify-between bg-brand-50 border border-brand-200 rounded-xl p-3">
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-xl bg-brand-100 flex items-center justify-center flex-shrink-0">
                    <Car className="w-4 h-4 text-brand-600" />
                  </div>
                  <div>
                    <p className="text-xs text-brand-500 font-medium">מחפש עבור</p>
                    <p className="text-sm font-bold text-brand-800">
                      {selectedVehicle.manufacturer} {selectedVehicle.model} {selectedVehicle.year}
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => setShowVehiclePicker(true)}
                  className="text-xs text-brand-600 hover:text-brand-800 font-semibold border border-brand-200 hover:border-brand-400 px-2.5 py-1 rounded-lg transition-colors whitespace-nowrap"
                >
                  שנה רכב
                </button>
              </div>
            ) : (
              <button
                onClick={() => setShowVehiclePicker(true)}
                className="w-full flex items-center gap-3 p-3 rounded-xl border-2 border-dashed border-brand-200 hover:border-brand-400 hover:bg-brand-50 transition-all text-right"
              >
                <div className="w-9 h-9 rounded-xl bg-brand-50 flex items-center justify-center flex-shrink-0">
                  <Car className="w-4 h-4 text-brand-500" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-brand-700">בחר רכב לצמצום החיפוש</p>
                  <p className="text-xs text-brand-400">מומלץ לתוצאות מדויקות יותר</p>
                </div>
              </button>
            )
          )}

          <div
            className="border-2 border-dashed border-gray-200 rounded-xl p-6 text-center cursor-pointer hover:border-brand-400 transition-colors"
            onClick={() => fileInputRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); handlePhotoFile(e.dataTransfer.files[0]) }}
          >
            {photoPreview ? (
              <div className="relative inline-block">
                <img src={photoPreview} alt="preview" className="max-h-48 mx-auto rounded-lg object-contain" />
                <button
                  onClick={(e) => { e.stopPropagation(); setShowPhotoEditor(true) }}
                  className="absolute top-2 left-2 bg-black/60 hover:bg-black/80 text-white text-xs px-2.5 py-1.5 rounded-lg flex items-center gap-1.5 transition-colors"
                >
                  <Pencil className="w-3.5 h-3.5" />
                  ערוך
                </button>
              </div>
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
                onClick={() => setShowPhotoEditor(true)}
                title="ערוך תמונה — חתוך / סמן חלק"
                className="btn-secondary px-3 flex items-center gap-1.5"
              >
                <Crop className="w-4 h-4" />
                <span className="hidden sm:inline text-sm">ערוך</span>
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
                  <span className="mr-auto badge bg-green-100 text-green-700 flex items-center gap-1">
                    {photoResult.cache_hit && <span title="תוצאה מהמאגר השמור">⚡</span>}
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

      {/* ── OLD STANDALONE VOICE MODE (replaced — never shown) ── */}
      {false && searchMode === 'voice' && (
        <div className="card p-4 space-y-4">
          <div className="flex items-center gap-2 mb-1">
            <Mic className="w-5 h-5 text-brand-600" />
            <h3 className="font-semibold text-gray-900">חיפוש קולי</h3>
          </div>
          <p className="text-sm text-gray-500">לחץ על המיקרופון ואמור שם החלק שאתה מחפש</p>

          {/* Vehicle confirmation banner */}
          {vehicles.length > 0 && (
            selectedVehicle ? (
              <div className="flex items-center justify-between bg-rose-50 border border-rose-200 rounded-xl p-3">
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-xl bg-rose-100 flex items-center justify-center flex-shrink-0">
                    <Car className="w-4 h-4 text-rose-600" />
                  </div>
                  <div>
                    <p className="text-xs text-rose-500 font-medium">מכוון לרכב</p>
                    <p className="text-sm font-bold text-rose-800">
                      {selectedVehicle.manufacturer} {selectedVehicle.model} {selectedVehicle.year}
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => setShowVehiclePicker(true)}
                  className="text-xs text-rose-600 hover:text-rose-800 font-semibold border border-rose-200 hover:border-rose-400 px-2.5 py-1 rounded-lg transition-colors whitespace-nowrap"
                >
                  שנה רכב
                </button>
              </div>
            ) : (
              <button
                onClick={() => setShowVehiclePicker(true)}
                className="w-full flex items-center gap-3 p-3 rounded-xl border-2 border-dashed border-rose-200 hover:border-rose-400 hover:bg-rose-50 transition-all text-right"
              >
                <div className="w-9 h-9 rounded-xl bg-rose-50 flex items-center justify-center flex-shrink-0">
                  <Car className="w-4 h-4 text-rose-500" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-rose-700">בחר רכב לצמצום החיפוש</p>
                  <p className="text-xs text-rose-400">מומלץ לתוצאות מדויקות יותר</p>
                </div>
              </button>
            )
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

      {/* Voice / Photo: no parts for selected manufacturer */}
      {!isLoading && voiceFallbackMfr && (
        <div className="flex flex-col items-center gap-3 py-10 text-center">
          <div className="w-14 h-14 rounded-full bg-amber-100 flex items-center justify-center">
            <AlertCircle className="w-7 h-7 text-amber-500" />
          </div>
          <div className="space-y-2">
            <p className="font-semibold text-gray-800 text-base">לא נמצאו חלקים עבור <span className="text-brand-600">{voiceFallbackMfr}</span> במאגר</p>
            <p className="text-sm text-gray-400">ייתכן שהחלק אינו זמין לדגם זה</p>
            <button
              onClick={() => runVoicePartsSearch(query, '')}
              className="mt-2 inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-brand-600 text-white text-sm font-medium hover:bg-brand-700 transition-colors"
            >
              <Search className="w-4 h-4" /> הצג תוצאות מכל היצרנים
            </button>
          </div>
        </div>
      )}

      {/* ── Block 1 search bar has been moved above (after hero) ── */}

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
            {selectedVehicle && (
              <button onClick={() => { setParts([]); setSearchResults(null); setSearched(true); setIsLoading(true); partsApi.search('', selectedVehicle.id, category, perType, null).then(({data}) => { setParts(data.parts || []); setTotalCount(data.total || 0); }).catch(() => {}).finally(() => setIsLoading(false)) }} className="btn-ghost text-sm">
                חפש לפי {selectedVehicle.manufacturer} {selectedVehicle.model}
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

      {!isLoading && photoFallbackMfr && (
        <div className="flex flex-col items-center gap-3 py-10 text-center">
          <div className="w-14 h-14 rounded-full bg-amber-100 flex items-center justify-center">
            <AlertCircle className="w-7 h-7 text-amber-500" />
          </div>
          <div className="space-y-2">
            <p className="font-semibold text-gray-800 text-base">לא נמצאו חלקים עבור <span className="text-brand-600">{photoFallbackMfr}</span> במאגר</p>
            <p className="text-sm text-gray-400">ייתכן שהחלק אינו זמין לדגם זה</p>
            <button
              onClick={() => runPhotoPartsSearch(photoCandidates, '')}
              className="mt-2 inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-brand-600 text-white text-sm font-medium hover:bg-brand-700 transition-colors"
            >
              <Search className="w-4 h-4" /> הצג תוצאות מכל היצרנים
            </button>
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
              {selectedVehicle && parts.length > 0 && (() => {
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
              {searchResults && (
                <div className="flex items-center gap-1 border border-gray-200 rounded-lg overflow-hidden text-sm" title="ספקים לסוג">
                  <button
                    onClick={() => { const n = Math.max(1, perType - 1); setPerType(n); setTimeout(() => search(0), 0) }}
                    className="px-2 py-1.5 bg-gray-50 hover:bg-gray-100 text-gray-600 font-bold leading-none"
                  >−</button>
                  <span className="px-2 py-1 text-gray-700 font-medium select-none">{perType}</span>
                  <button
                    onClick={() => { const n = Math.min(10, perType + 1); setPerType(n); setTimeout(() => search(0), 0) }}
                    className="px-2 py-1.5 bg-gray-50 hover:bg-gray-100 text-gray-600 font-bold leading-none"
                  >+</button>
                  <span className="pr-2 text-xs text-gray-400 hidden sm:inline">ספקים</span>
                </div>
              )}
            </div>
          </div>

          {/* ── Grouped 3-type results (new API) ─────────────────────────── */}
          {searchResults ? (() => {
            const activeKeys = ['original', 'oem', 'aftermarket'].filter(k => searchResults[k]?.part)
            const colClass = activeKeys.length === 1 ? 'max-w-lg mx-auto' : activeKeys.length === 2 ? 'grid grid-cols-1 md:grid-cols-2 gap-6' : 'grid grid-cols-1 lg:grid-cols-3 gap-6'
            return activeKeys.length > 0 ? (
              <div className={colClass}>
                {activeKeys.map((key) => (
                  <TypeSection key={key} typeKey={key} data={searchResults[key]} onAddToCart={addItem} />
                ))}
              </div>
            ) : (
              <div className="text-center py-12 text-gray-400 text-sm">לא נמצאו תוצאות</div>
            )
          })() : (
            <>
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
        </>
      )}
    </div>
  )
}
