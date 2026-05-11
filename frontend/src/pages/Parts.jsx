import { useState, useEffect, useRef, useCallback, useDeferredValue } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { partsApi } from '../api/parts'
import { vehiclesApi } from '../api/vehicles'
import { cartApi } from '../api/orders'
import { useCartStore } from '../stores/cartStore'
import { useVehicleStore } from '../stores/vehicleStore'
import { useAuthStore } from '../stores/authStore'
import { partFamilyImageSrc } from '../components/partFamilyVisuals'
import {
  buildActiveVehicleFilterOrder,
  createCategoryFilterTransition,
  createManufacturerFilterTransition,
  createModelFilterTransition,
  createSubModelFilterTransition,
  createYearFilterTransition,
  getSubModelPlaceholder,
  getYearPlaceholder,
} from './partsFilterState'
import { Search, ShoppingCart, Car, Loader2, ChevronDown, Package, SlidersHorizontal, X, Camera, Mic, MicOff, Hash, CheckCircle, AlertCircle, Truck, Shield, Tag, ChevronRight, Link2, Bot, Crop, Pencil, Circle, RotateCcw, Check, MousePointer, ScanLine } from 'lucide-react'
import toast from 'react-hot-toast'

const MANUFACTURER_LOGO_SIZE = 34
const MANUFACTURER_CHIP_LOGO_SIZE = 32
const PART_FAMILY_MENU_IMAGE_WIDTH = 52
const PART_FAMILY_MENU_IMAGE_HEIGHT = 34
const PART_FAMILY_TRIGGER_IMAGE_WIDTH = 44
const PART_FAMILY_TRIGGER_IMAGE_HEIGHT = 28
const PART_FAMILY_CHIP_IMAGE_WIDTH = 34
const PART_FAMILY_CHIP_IMAGE_HEIGHT = 24
const FILTER_SELECT_CLASS = 'w-full h-12 sm:h-11 border border-gray-200 rounded-xl bg-white text-brand-navy text-[15px] sm:text-sm focus:outline-none focus:ring-2 focus:ring-brand-400 focus:border-transparent px-3 transition-colors disabled:bg-gray-50 disabled:text-gray-400'
const FILTER_MENU_TRIGGER_CLASS = 'w-full h-12 sm:h-11 border border-gray-200 rounded-xl bg-white text-brand-navy text-[15px] sm:text-sm focus:outline-none focus:ring-2 focus:ring-brand-400 focus:border-transparent px-3 transition-colors flex items-center justify-between'

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
            <h3 className="font-bold text-brand-navy">ערוך תמונה לחיפוש מדויק</h3>
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
      <div className="flex justify-between text-gray-500"><span>מע״מ 18%</span><span>₪{vat?.toFixed(0)}</span></div>
      <div className="flex justify-between text-gray-500"><span>משלוח</span><span>₪{shipping?.toFixed(0)}</span></div>
      <div className="flex justify-between font-bold text-brand-navy border-t border-gray-200 pt-1 mt-1">
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
  'Refurbished': 'bg-brand-50 text-brand-700 border-brand-200',
  'משופץ':       'bg-brand-50 text-brand-700 border-brand-200',
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
  'מנוע':            { color: '#fb923c', bg: 'bg-brand-50',  text: 'text-brand-700',  icon: '⚙️' },
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
  const vatAmount  = Math.round(priceNet * 0.18)
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

function normalizeBrandDisplay(name) {
  if (!name) return ''
  return name
    .replace(/\b(spare\s*parts?|auto\s*parts?|parts?)\b/gi, '')
    .replace(/\bחלפים\b/g, '')
    .replace(/\bמותג\b/g, '')
    .replace(/[\-_/]+/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

function logoForManufacturer(name, logoMap = {}) {
  if (!name) return ''
  const key = normalizeBrandDisplay(name)
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  if (!key) return ''
  const primary = key.split(' ')[0]

  const remoteByKey = {
    'alfa romeo': 'https://www.carlogos.org/car-logos/alfa-romeo-logo.png',
    alpine: 'https://www.carlogos.org/car-logos/alpine-logo.png',
    aion: 'https://www.carlogos.org/car-logos/gac-logo.png',
    byd: 'https://www.carlogos.org/car-logos/byd-logo.png',
    buick: 'https://www.carlogos.org/car-logos/buick-logo.png',
    chery: 'https://www.carlogos.org/car-logos/chery-logo.png',
    chrysler: 'https://www.carlogos.org/car-logos/chrysler-logo.png',
    cupra: 'https://www.carlogos.org/car-logos/seat-logo.png',
    dacia: 'https://www.carlogos.org/car-logos/dacia-logo.png',
    daihatsu: 'https://www.carlogos.org/car-logos/daihatsu-logo.png',
    datsun: 'https://www.carlogos.org/car-logos/datsun-logo.png',
    dodge: 'https://www.carlogos.org/car-logos/dodge-logo.png',
    'ds automobiles': 'https://www.carlogos.org/car-logos/ds-logo.png',
    geely: 'https://www.carlogos.org/car-logos/geely-logo.png',
    genesis: 'https://www.carlogos.org/car-logos/genesis-logo.png',
    gmc: 'https://www.carlogos.org/car-logos/gmc-logo.png',
    gwm: 'https://www.carlogos.org/car-logos/great-wall-logo.png',
    haval: 'https://www.carlogos.org/car-logos/haval-logo.png',
    holden: 'https://www.carlogos.org/car-logos/holden-logo.png',
    infiniti: 'https://www.carlogos.org/car-logos/infiniti-logo.png',
    jaecoo: 'https://www.carlogos.org/car-logos/chery-logo.png',
    jaguar: 'https://www.carlogos.org/car-logos/jaguar-logo.png',
    'kg mobility': 'https://www.carlogos.org/car-logos/ssangyong-logo.png',
    'land rover': 'https://www.carlogos.org/car-logos/land-rover-logo.png',
    lancia: 'https://www.carlogos.org/car-logos/lancia-logo.png',
    lexus: 'https://www.carlogos.org/car-logos/lexus-logo.png',
    lincoln: 'https://www.carlogos.org/car-logos/lincoln-logo.png',
    lucid: 'https://www.carlogos.org/car-logos/lucid-logo.png',
    maybach: 'https://www.carlogos.org/car-logos/maybach-logo.png',
    mg: '/brand-logos/mg.png',
    nio: 'https://www.carlogos.org/car-logos/nio-logo.png',
    omoda: 'https://www.carlogos.org/car-logos/chery-logo.png',
    ora: 'https://www.carlogos.org/car-logos/great-wall-logo.png',
    pagani: 'https://www.carlogos.org/car-logos/pagani-logo.png',
    rivian: 'https://www.carlogos.org/car-logos/rivian-logo.png',
    roewe: 'https://www.carlogos.org/car-logos/roewe-logo.png',
    saab: 'https://www.carlogos.org/car-logos/saab-logo.png',
    ssangyong: 'https://www.carlogos.org/car-logos/ssangyong-logo.png',
    trumpchi: 'https://www.carlogos.org/car-logos/trumpchi-logo.png',
    wey: 'https://www.carlogos.org/car-logos/wey-logo.png',
    xpeng: 'https://www.carlogos.org/car-logos/xpeng-logo.png',
    koenigsegg: 'https://www.carlogos.org/car-logos/koenigsegg-logo.png',
    lotus: '/brand-logos/lotus.png',
    'li auto': 'https://www.carlogos.org/car-logos/geely-logo.png',
    'lynk co': 'https://www.carlogos.org/car-logos/geely-logo.png',
    levc: 'https://www.carlogos.org/car-logos/geely-logo.png',
  }
  const remote = remoteByKey[key] || remoteByKey[primary]
  if (remote) return remote

  // Backend logo URLs are a last resort because some providers throttle heavily.
  const mapped = logoMap[name] || logoMap[key] || logoMap[primary]
  if (mapped) return mapped

  return ''
}

function fallbackLogoDataUri(name, bg = null, fg = '#ffffff') {
  const label = (name || '?')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() || '')
    .join('')
    .slice(0, 2) || '?'

  // Keep final fallback neutral (not playful random colors).
  const fill = bg || '#f3f4f6'
  const textColor = fg || '#374151'

  const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24'>
    <rect x='0.5' y='0.5' width='23' height='23' rx='6' fill='${fill}' stroke='#d1d5db'/>
    <text x='12' y='15' text-anchor='middle' font-family='Arial, sans-serif' font-size='9' font-weight='700' fill='${textColor}'>${label}</text>
  </svg>`
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`
}

function normalizeCategoryToken(value) {
  return String(value || '').trim().toLowerCase()
}

function hierarchyCacheKey(...parts) {
  return parts.map((part) => String(part || '').trim().toUpperCase()).join('::')
}

function findPartFamilyByValue(families, value) {
  const normalized = normalizeCategoryToken(value)
  if (!normalized) return null
  return families.find((family) => {
    const aliases = [family.id, family.label, ...(family.aliases || []), ...(family.legacy_categories || [])]
    return aliases.some((alias) => normalizeCategoryToken(alias) === normalized)
  }) || null
}

function findPartCategoryNodeByValue(families, value) {
  const normalized = normalizeCategoryToken(value)
  if (!normalized) return null

  for (const family of families) {
    const familyAliases = [family.id, family.label, ...(family.aliases || []), ...(family.legacy_categories || [])]
    if (familyAliases.some((alias) => normalizeCategoryToken(alias) === normalized)) {
      return { family, subcategory: null, label: family.label, count: family.count ?? 0 }
    }
    for (const subcategory of family.subcategories || []) {
      const subAliases = [subcategory.id, subcategory.label, ...(subcategory.aliases || [])]
      if (subAliases.some((alias) => normalizeCategoryToken(alias) === normalized)) {
        return {
          family,
          subcategory,
          label: subcategory.label,
          count: subcategory.count ?? 0,
        }
      }
    }
  }
  return null
}

function categoryLabelForValue(families, value) {
  return findPartCategoryNodeByValue(families, value)?.label || value || ''
}

function partFamilyHierarchyLabel(family) {
  if (!family) return 'כל סוגי החלקים'
  return family.group ? `${family.group} / ${family.label}` : family.label
}


function MobileBottomSheet({ open, onClose, title, subtitle, children, footer = null }) {
  if (!open) return null

  return (
    <div className="fixed inset-0 z-40 sm:hidden" aria-modal="true" role="dialog">
      <button
        type="button"
        aria-label="סגור"
        className="absolute inset-0 bg-slate-950/45 backdrop-blur-[2px]"
        onClick={onClose}
      />
      <div className="absolute inset-x-0 bottom-0 max-h-[82vh] overflow-hidden rounded-t-[28px] border-t border-slate-200 bg-white shadow-[0_-24px_80px_-32px_rgba(15,23,42,0.55)]">
        <div className="flex items-center justify-between gap-3 border-b border-slate-100 px-4 py-3.5">
          <div className="min-w-0 text-right">
            <h3 className="text-base font-bold text-brand-navy">{title}</h3>
            {subtitle ? <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p> : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-slate-50 text-slate-500"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="max-h-[calc(82vh-7.5rem)] overflow-y-auto px-4 py-4">{children}</div>
        {footer ? <div className="border-t border-slate-100 px-4 py-3">{footer}</div> : null}
      </div>
    </div>
  )
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
        <h3 className="font-semibold text-brand-navy text-sm leading-snug line-clamp-2">
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
    <article className="group relative flex h-full flex-col overflow-hidden rounded-[28px] border border-slate-200 bg-white shadow-[0_18px_50px_-30px_rgba(15,23,42,0.28)] transition-all duration-200 hover:-translate-y-1 hover:shadow-[0_24px_70px_-30px_rgba(15,23,42,0.35)]">
      <div className={`absolute inset-x-0 top-0 h-24 ${accent.bg} opacity-80`} />
      <div className="relative flex flex-1 flex-col">
        <div className="border-b border-white/80 px-4 pt-4 pb-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${typeColor}`}>{typeLabel}</span>
                <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-semibold ${accent.bg} ${accent.text}`}>
                  <span>{accent.icon}</span>
                  {part.category}
                </span>
              </div>
              <h3 className="min-h-[3rem] text-base font-black leading-6 text-brand-navy line-clamp-2">{part.name}</h3>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                <span className="inline-flex items-center rounded-full border border-slate-200 bg-white/90 px-2.5 py-1 font-medium text-slate-600">{part.manufacturer || 'יצרן לא זמין'}</span>
                {part.sku ? <span className="inline-flex items-center rounded-full border border-slate-200 bg-white/90 px-2.5 py-1 font-medium text-slate-600">SKU: {part.sku}</span> : null}
              </div>
            </div>
            <div className="hidden h-11 w-11 flex-shrink-0 items-center justify-center rounded-2xl bg-white/90 text-xl shadow-sm sm:flex">{accent.icon}</div>
          </div>
        </div>

        <div className="flex flex-1 flex-col gap-3 px-4 py-4">
          {suppliers.length === 0 ? (
            <div className={`flex flex-1 flex-col items-center justify-center gap-2 rounded-[24px] border border-dashed border-slate-200 ${accent.bg} px-4 py-6 text-center`}>
              <Tag className={`h-5 w-5 ${accent.text} opacity-70`} />
              <span className={`text-sm font-semibold ${accent.text}`}>מחיר על פניה</span>
              <span className="max-w-[16rem] text-xs leading-5 text-slate-500">צור קשר כדי לקבל התאמה והצעת מחיר לחלק הזה.</span>
            </div>
          ) : (
            <div className="space-y-3">
              {suppliers.map((s, i) => (
                <div
                  key={i}
                  className={`rounded-[24px] border px-3.5 py-3.5 ${
                    i === 0
                      ? 'border-slate-200 bg-gradient-to-b from-white to-slate-50 shadow-sm'
                      : 'border-slate-200/90 bg-slate-50/85'
                  }`}
                  style={i === 0 ? { boxShadow: `inset 3px 0 0 ${accent.color}` } : undefined}
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <AvailabilityBadge availability={s.availability} deliveryDays={s.estimated_delivery_days} />
                    <div className="flex flex-wrap items-center justify-end gap-2 text-[11px] text-slate-500">
                      {s.warranty_months ? (
                        <span className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-white px-2 py-1">
                          <Shield className="h-3 w-3" />
                          {s.warranty_months} חוד׳ אחריות
                        </span>
                      ) : null}
                      {i === 0 && !s.is_base_price_fallback ? <span className="inline-flex items-center rounded-full bg-emerald-50 px-2 py-1 font-semibold text-emerald-700">מומלץ</span> : null}
                      {s.is_base_price_fallback ? <span className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2 py-1 font-semibold text-amber-700">מחיר משוער</span> : null}
                    </div>
                  </div>

                  <div className="mt-3 rounded-[20px] bg-white/90 p-3 shadow-[inset_0_0_0_1px_rgba(226,232,240,0.8)]">
                    <div className="flex items-end justify-between gap-3">
                      <div>
                        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">מחיר כולל</p>
                        <p className={`mt-1 text-3xl font-black leading-none ${accent.text}`}>₪{s.total?.toFixed(0)}</p>
                      </div>
                      <div className="text-right text-[11px] text-slate-500">
                        <p>זמין להזמנה מיידית</p>
                        <p className="mt-1 font-medium text-slate-600">{s.estimated_delivery_days ? `${s.estimated_delivery_days} ימי אספקה` : 'זמן אספקה לפי ספק'}</p>
                      </div>
                    </div>
                    <div className="mt-3 grid grid-cols-3 gap-2 text-center text-[11px] text-slate-500">
                      <div className="rounded-2xl bg-slate-50 px-2 py-2">
                        <div className="text-sm font-bold text-slate-700">₪{s.price_no_vat?.toFixed(0)}</div>
                        נטו
                      </div>
                      <div className="rounded-2xl bg-slate-50 px-2 py-2">
                        <div className="text-sm font-bold text-slate-700">₪{s.vat?.toFixed(0)}</div>
                        מע״מ
                      </div>
                      <div className="rounded-2xl bg-slate-50 px-2 py-2">
                        <div className="text-sm font-bold text-slate-700">₪{s.shipping?.toFixed(0)}</div>
                        משלוח
                      </div>
                    </div>
                  </div>

                  <button
                    onClick={() => handleAddToCart(s.supplier_part_id, s)}
                    className={`mt-3 flex min-h-12 w-full items-center justify-center gap-2 rounded-2xl text-sm font-bold transition-colors ${
                      i === 0
                        ? 'bg-brand-600 text-white hover:bg-brand-700'
                        : 'border border-brand-200 bg-white text-brand-700 hover:bg-brand-50'
                    }`}
                  >
                    <ShoppingCart className="h-4 w-4" />
                    הוסף לסל
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </article>
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
                <p className="font-bold text-brand-navy text-sm">{sv.manufacturer} {sv.model} {sv.year}</p>
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
                <p className="font-semibold text-brand-navy text-sm">{v.manufacturer} {v.model}</p>
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
            <p className="font-mono font-bold text-center text-brand-navy tracking-widest text-sm bg-green-50 border border-green-200 rounded-xl py-2 px-3">{detected}</p>
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
  const { user } = useAuthStore()
  const isAdmin = !!user?.is_admin
  const { addItem: addItemLocal } = useCartStore()
  // Wrapper: saves to local store immediately, then syncs to server in background
  const addItem = (item) => {
    addItemLocal(item)
    if (item.partId) cartApi.addItem(item.partId, item.quantity ?? 1).catch(() => {})
  }
  const { vehicles, selectedVehicle, loadVehicles, selectVehicle, addVehicle: storeAddVehicle, removeVehicle } = useVehicleStore()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const PAGE_SIZE = 50

  const [searchMode, setSearchMode] = useState('manual')

  const [query, setQuery] = useState(searchParams.get('search') || '')
  const [category, setCategory] = useState('')
  const [categories, setCategories] = useState([])
  const [categoryCounts, setCategoryCounts] = useState({})
  const [subcategoryCounts, setSubcategoryCounts] = useState({})
  const [parts, setParts] = useState([])          // flat list for photo/legacy search
  const [searchResults, setSearchResults] = useState(null) // grouped: {original,oem,aftermarket}
  const [fitmentStatus, setFitmentStatus] = useState(null)
  const [isLoading, setIsLoading] = useState(false)
  const [searched, setSearched] = useState(false)
  const [page, setPage] = useState(0)
  const [totalCount, setTotalCount] = useState(0)

  const [suggestions, setSuggestions] = useState([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const suggestRef = useRef(null)
  const manufacturerMenuRef = useRef(null)
  const partTypeMenuRef = useRef(null)

  const [newPlate, setNewPlate] = useState('')
  const [addingVehicle, setAddingVehicle] = useState(false)

  const [brands, setBrands] = useState([])
  const [brandCounts, setBrandCounts] = useState({})
  const [brandLogos, setBrandLogos] = useState({})
  const [brandCountInfoOpenFor, setBrandCountInfoOpenFor] = useState('')
  const [manufacturerMenuOpen, setManufacturerMenuOpen] = useState(false)
  const [manufacturerSearch, setManufacturerSearch] = useState('')
  const [partFamilies, setPartFamilies] = useState([])
  const [partFamilyGroups, setPartFamilyGroups] = useState([])
  const [partTypeMenuOpen, setPartTypeMenuOpen] = useState(false)
  const [partFamilySearch, setPartFamilySearch] = useState('')

  const [manualManufacturer, setManualManufacturer] = useState('')
  const [manualModel, setManualModel] = useState('')
  const [manualSubModel, setManualSubModel] = useState('')
  const [modelOptions, setModelOptions] = useState([])
  const [modelsLoading, setModelsLoading] = useState(false)
  const [modelOptionsKey, setModelOptionsKey] = useState('')
  const [subModelOptions, setSubModelOptions] = useState([])
  const [subModelsLoading, setSubModelsLoading] = useState(false)
  const [subModelOptionsKey, setSubModelOptionsKey] = useState('')
  const [manualYear, setManualYear] = useState('')
  const [yearOptions, setYearOptions] = useState([])
  const [yearsLoading, setYearsLoading] = useState(false)
  const [yearOptionsKey, setYearOptionsKey] = useState('')
  const [sortBy, setSortBy] = useState('availability')
  const [filterAvail, setFilterAvail] = useState('')
  const [filterType, setFilterType] = useState('')
  const [perType, setPerType] = useState(4)   // suppliers per type shown in grouped view
  const [showVehiclePicker, setShowVehiclePicker] = useState(false)
  const modelOptionsCacheRef = useRef(new Map())
  const subModelOptionsCacheRef = useRef(new Map())
  const yearOptionsCacheRef = useRef(new Map())
  const categoryMetaCacheRef = useRef(new Map())
  const categoryMetaRequestIdRef = useRef(0)
  const modelOptionsRequestIdRef = useRef(0)
  const subModelOptionsRequestIdRef = useRef(0)
  const yearOptionsRequestIdRef = useRef(0)
  const modelsLoadingTimerRef = useRef(null)
  const subModelsLoadingTimerRef = useRef(null)
  const yearsLoadingTimerRef = useRef(null)
  const pendingSubModelFetchRef = useRef(new Map())
  const pendingYearFetchRef = useRef(new Map())
  const searchPrewarmRef = useRef(null)
  const currentManufacturerKey = hierarchyCacheKey(manualManufacturer)
  const currentSubModelParentKey = hierarchyCacheKey(manualManufacturer, manualModel)
  const currentYearParentKey = hierarchyCacheKey(manualManufacturer, manualModel, manualSubModel)
  const effectiveManualModel = manualManufacturer ? manualModel : ''
  const effectiveManualSubModel = manualManufacturer && effectiveManualModel ? manualSubModel : ''
  const effectiveManualYear = manualManufacturer && effectiveManualModel ? manualYear : ''
  const deferredManualManufacturer = useDeferredValue(manualManufacturer)
  const deferredEffectiveManualModel = useDeferredValue(effectiveManualModel)
  const deferredEffectiveManualSubModel = useDeferredValue(effectiveManualSubModel)
  const deferredEffectiveManualYear = useDeferredValue(effectiveManualYear)

  const getSortParams = (sv) => {
    if (sv === 'price_asc')    return { sort_by: 'price_asc',    sort_dir: 'asc' }
    if (sv === 'price_desc')   return { sort_by: 'price_desc',   sort_dir: 'asc' }
    if (sv === 'name')         return { sort_by: 'name',         sort_dir: 'asc' }
    if (sv === 'availability') return { sort_by: 'availability', sort_dir: 'asc' }
    return                            { sort_by: 'name',         sort_dir: 'asc' }
  }

  const flattenGroupedResults = (grouped) => {
    const bucketToFlat = (bucket, fallbackType) => {
      if (!bucket?.part) return null
      const suppliers = bucket.suppliers || []
      const firstPriced = suppliers.find((s) => Number.isFinite(Number(s?.price_ils)))
      const availability = suppliers.some((s) => s?.availability === 'in_stock')
        ? 'in_stock'
        : (suppliers[0]?.availability || null)
      return {
        ...bucket.part,
        part_type: bucket.part.part_type || fallbackType,
        suppliers,
        pricing: {
          availability,
          total_price: firstPriced?.price_ils ?? bucket.part.min_price_ils ?? bucket.part.base_price ?? null,
        },
      }
    }

    // New response format: all_parts includes every matching part across all types
    if (Array.isArray(grouped?.all_parts) && grouped.all_parts.length > 0) {
      return grouped.all_parts
        .filter((b) => b?.part)
        .map((b) => {
          const suppliers = b.suppliers || []
          const firstPriced = suppliers.find((s) => Number.isFinite(Number(s?.price_ils)))
          const availability = suppliers.some((s) => s?.availability === 'in_stock')
            ? 'in_stock'
            : (suppliers[0]?.availability || null)
          return {
            ...b.part,
            suppliers,
            pricing: {
              availability,
              total_price: firstPriced?.price_ils ?? b.part.min_price_ils ?? b.part.base_price ?? null,
            },
          }
        })
    }

    // Legacy format: single bucket per type
    return [
      bucketToFlat(grouped?.original, 'Original'),
      bucketToFlat(grouped?.oem, 'OEM'),
      bucketToFlat(grouped?.aftermarket, 'Aftermarket'),
    ].filter(Boolean)
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
  const loadPartFamilies = useCallback(async () => {
    const requestId = ++categoryMetaRequestIdRef.current
    const cacheKey = hierarchyCacheKey(
      deferredManualManufacturer,
      deferredEffectiveManualModel,
      deferredEffectiveManualSubModel,
      deferredEffectiveManualYear
    )
    const cached = categoryMetaCacheRef.current.get(cacheKey)
    if (cached) {
      if (requestId !== categoryMetaRequestIdRef.current) return
      setPartFamilies(cached.families)
      setPartFamilyGroups(cached.groups)
      setCategories(cached.categories)
      setCategoryCounts(cached.familyCounts)
      setSubcategoryCounts(cached.subcategoryCounts || {})
      setCategory((current) => findPartFamilyByValue(cached.families, current)?.id || current)
      return
    }

    try {
      // Only pass vehicle filter when model is also set — the manufacturer-only
      // path hits a slow full-table JSONB scan (~20 s). With model the query is fast.
      const hasFullVehicleFilter = deferredManualManufacturer && deferredEffectiveManualModel
      const params = {
        vehicle_manufacturer: hasFullVehicleFilter ? deferredManualManufacturer : null,
        vehicle_model: hasFullVehicleFilter ? deferredEffectiveManualModel : null,
        vehicle_submodel: hasFullVehicleFilter ? (deferredEffectiveManualSubModel || null) : null,
        vehicle_year: hasFullVehicleFilter && deferredEffectiveManualYear ? parseInt(deferredEffectiveManualYear, 10) : null,
      }
      const { data } = await partsApi.categories(params)
      if (requestId !== categoryMetaRequestIdRef.current) return
      const families = data.families || []
      const nextPayload = {
        families,
        groups: data.groups || [],
        categories: (data.categories || []).slice(),
        familyCounts: data.family_counts || {},
        subcategoryCounts: data.subcategory_counts || {},
      }
      categoryMetaCacheRef.current.set(cacheKey, nextPayload)
      setPartFamilies(nextPayload.families)
      setPartFamilyGroups(nextPayload.groups)
      setCategories(nextPayload.categories)
      setCategoryCounts(nextPayload.familyCounts)
      setSubcategoryCounts(nextPayload.subcategoryCounts)
      setCategory((current) => findPartFamilyByValue(families, current)?.id || current)
    } catch {
      if (requestId !== categoryMetaRequestIdRef.current) return
      setPartFamilies([])
      setPartFamilyGroups([])
      setCategories([])
      setCategoryCounts({})
      setSubcategoryCounts({})
    }
  }, [deferredManualManufacturer, deferredEffectiveManualModel, deferredEffectiveManualSubModel, deferredEffectiveManualYear])

  useEffect(() => {
    const urlSearch   = searchParams.get('search')   || ''
    const urlCategory = searchParams.get('category') || ''
    loadVehicles()
    partsApi.manufacturers()
      .then(async ({ data }) => {
        let list = data.manufacturers || []
        let counts = data.counts || {}
        const logos = { ...(data.logos || {}) }

        // If vehicles dataset is sparse, enrich from brands-with-parts and normalize labels.
        if (list.length < 10) {
          try {
            const fallback = await partsApi.brandsWithParts()
            const fb = fallback.data?.brands || []
            const merged = [...list, ...fb.map((b) => b.name).filter(Boolean)]
            const map = new Map()
            for (const raw of merged) {
              const clean = normalizeBrandDisplay(raw)
              if (!clean) continue
              const key = clean.toUpperCase()
              const prev = map.get(key) || { name: clean, count: 0 }
              const rawCount = Number(counts[raw] || 0)
              map.set(key, { name: prev.name.length <= clean.length ? prev.name : clean, count: prev.count + rawCount })
            }
            list = Array.from(map.values()).map((x) => x.name).sort((a, b) => a.localeCompare(b, 'he'))
            counts = Object.fromEntries(Array.from(map.values()).map((x) => [x.name, x.count]))
            for (const b of fb) {
              if (b?.name && b?.logo_url && !logos[b.name]) logos[b.name] = b.logo_url
            }
          } catch {
            // keep primary list
          }
        }

        setBrands(list.map((name) => ({ name })))
        setBrandCounts(counts)
        setBrandLogos(logos)
      })
      .catch(async () => {
        try {
          const fallback = await partsApi.brandsWithParts()
          const fb = fallback.data?.brands || []
          const cleaned = fb
            .map((b) => normalizeBrandDisplay(b.name))
            .filter(Boolean)
          const uniq = Array.from(new Set(cleaned.map((x) => x.toUpperCase())))
            .map((k) => cleaned.find((x) => x.toUpperCase() === k))
          setBrands((uniq || []).map((name) => ({ name })))
          setBrandCounts({})
          const logos = {}
          for (const b of fb) {
            if (b?.name && b?.logo_url) logos[b.name] = b.logo_url
          }
          setBrandLogos(logos)
        } catch {
          setBrands([])
          setBrandCounts({})
          setBrandLogos({})
        }
      })
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
              const flat = flattenGroupedResults(data)
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
    loadPartFamilies()
  }, [loadPartFamilies])

  useEffect(() => {
    if (searched && sortBy !== 'availability') search(0)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sortBy])

  // Reload model options when manufacturer changes
  useEffect(() => {
    const requestId = ++modelOptionsRequestIdRef.current
    if (!manualManufacturer) {
      setManualModel('')
      setManualSubModel('')
      setManualYear('')
      setModelOptions([])
      setSubModelOptions([])
      setYearOptions([])
      setModelOptionsKey('')
      setSubModelOptionsKey('')
      setYearOptionsKey('')
      clearLoadingState(modelsLoadingTimerRef, setModelsLoading)
      clearLoadingState(subModelsLoadingTimerRef, setSubModelsLoading)
      clearLoadingState(yearsLoadingTimerRef, setYearsLoading)
      return
    }

    const modelCacheKey = hierarchyCacheKey(manualManufacturer)
    const applyNextModels = (nextModels) => {
      setModelOptions(nextModels)
      setModelOptionsKey(modelCacheKey)
      setManualModel((current) => (current && nextModels.includes(current) ? current : ''))
    }

    const cachedModels = modelOptionsCacheRef.current.get(modelCacheKey)
    if (cachedModels) {
      if (requestId !== modelOptionsRequestIdRef.current) return
      applyNextModels(cachedModels)
      clearLoadingState(modelsLoadingTimerRef, setModelsLoading)
      return
    }

    scheduleLoadingState(modelsLoadingTimerRef, setModelsLoading)
    partsApi.models(manualManufacturer || null)
      .then(({ data }) => {
        if (requestId !== modelOptionsRequestIdRef.current) return
        const nextModels = data.models || []
        modelOptionsCacheRef.current.set(modelCacheKey, nextModels)
        applyNextModels(nextModels)

        // Warm submodel/year caches for first models to make subsequent filter
        // selections feel instant.
        nextModels.slice(0, 8).forEach((modelName) => {
          prefetchSubModelsAndYears(manualManufacturer, modelName, 6)
        })
      })
      .catch(() => {})
      .finally(() => {
        if (requestId === modelOptionsRequestIdRef.current) clearLoadingState(modelsLoadingTimerRef, setModelsLoading)
      })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manualManufacturer])

  // Reload sub-model options when manufacturer/model changes
  useEffect(() => {
    const requestId = ++subModelOptionsRequestIdRef.current
    if (!manualManufacturer) {
      setManualSubModel('')
      setManualYear('')
      setSubModelOptions([])
      setYearOptions([])
      setSubModelOptionsKey('')
      setYearOptionsKey('')
      clearLoadingState(subModelsLoadingTimerRef, setSubModelsLoading)
      return
    }

    if (!effectiveManualModel) {
      if (!manualModel) {
        setManualSubModel('')
        setManualYear('')
        setSubModelOptions([])
        setYearOptions([])
        setSubModelOptionsKey('')
        setYearOptionsKey('')
      }
      clearLoadingState(subModelsLoadingTimerRef, setSubModelsLoading)
      return
    }

    const subModelCacheKey = hierarchyCacheKey(manualManufacturer, effectiveManualModel)
    const applyNextSubModels = (nextSubModels) => {
      setSubModelOptions(nextSubModels)
      setSubModelOptionsKey(subModelCacheKey)
      setManualSubModel((current) => (current && nextSubModels.includes(current) ? current : ''))
    }

    const cachedSubModels = subModelOptionsCacheRef.current.get(subModelCacheKey)
    if (cachedSubModels) {
      if (requestId !== subModelOptionsRequestIdRef.current) return
      applyNextSubModels(cachedSubModels)
      clearLoadingState(subModelsLoadingTimerRef, setSubModelsLoading)
      return
    }

    // Piggyback on pre-fetch started in the change handler if available
    const pendingSubModel = pendingSubModelFetchRef.current.get(subModelCacheKey)
    if (pendingSubModel) {
      scheduleLoadingState(subModelsLoadingTimerRef, setSubModelsLoading)
      pendingSubModel
        .then((nextSubModels) => {
          if (requestId !== subModelOptionsRequestIdRef.current) return
          applyNextSubModels(nextSubModels)
        })
        .catch(() => {})
        .finally(() => {
          if (requestId === subModelOptionsRequestIdRef.current) clearLoadingState(subModelsLoadingTimerRef, setSubModelsLoading)
        })
      return
    }

    scheduleLoadingState(subModelsLoadingTimerRef, setSubModelsLoading)
    partsApi.submodels(manualManufacturer, effectiveManualModel)
      .then(({ data }) => {
        if (requestId !== subModelOptionsRequestIdRef.current) return
        const nextSubModels = data.submodels || []
        subModelOptionsCacheRef.current.set(subModelCacheKey, nextSubModels)
        applyNextSubModels(nextSubModels)

        // Warm year options for model and likely submodels.
        prefetchYears(manualManufacturer, effectiveManualModel, '')
        nextSubModels.slice(0, 8).forEach((sm) => prefetchYears(manualManufacturer, effectiveManualModel, sm))
      })
      .catch(() => {})
      .finally(() => {
        if (requestId === subModelOptionsRequestIdRef.current) clearLoadingState(subModelsLoadingTimerRef, setSubModelsLoading)
      })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manualManufacturer, manualModel, effectiveManualModel])

  // Reload year options when manufacturer/model/sub-model changes
  useEffect(() => {
    const requestId = ++yearOptionsRequestIdRef.current
    if (!manualManufacturer) {
      setManualYear('')
      setYearOptions([])
      setYearOptionsKey('')
      clearLoadingState(yearsLoadingTimerRef, setYearsLoading)
      return
    }

    if (!effectiveManualModel) {
      if (!manualModel) {
        setManualYear('')
        setYearOptions([])
        setYearOptionsKey('')
      }
      clearLoadingState(yearsLoadingTimerRef, setYearsLoading)
      return
    }

    const yearCacheKey = hierarchyCacheKey(manualManufacturer, effectiveManualModel, effectiveManualSubModel)
    const applyNextYears = (nextYears) => {
      setYearOptions(nextYears)
      setYearOptionsKey(yearCacheKey)
      setManualYear((current) => (current && nextYears.includes(current) ? current : ''))
    }

    const cachedYears = yearOptionsCacheRef.current.get(yearCacheKey)
    if (cachedYears) {
      if (requestId !== yearOptionsRequestIdRef.current) return
      applyNextYears(cachedYears)
      clearLoadingState(yearsLoadingTimerRef, setYearsLoading)
      return
    }

    // If sub-model years are not cached yet, show model-level years immediately
    // while the precise sub-model years load in the background.
    if (effectiveManualSubModel) {
      const broadYearKey = hierarchyCacheKey(manualManufacturer, effectiveManualModel, '')
      const broadYears = yearOptionsCacheRef.current.get(broadYearKey)
      if (broadYears && requestId === yearOptionsRequestIdRef.current) {
        applyNextYears(broadYears)
      }
    }

    // Piggyback on pre-fetch started in the change handler if available
    const pendingYear = pendingYearFetchRef.current.get(yearCacheKey)
    if (pendingYear) {
      scheduleLoadingState(yearsLoadingTimerRef, setYearsLoading)
      pendingYear
        .then((nextYears) => {
          if (requestId !== yearOptionsRequestIdRef.current) return
          applyNextYears(nextYears)
        })
        .catch(() => {})
        .finally(() => {
          if (requestId === yearOptionsRequestIdRef.current) clearLoadingState(yearsLoadingTimerRef, setYearsLoading)
        })
      return
    }

    scheduleLoadingState(yearsLoadingTimerRef, setYearsLoading)
    partsApi.years(manualManufacturer, effectiveManualModel, effectiveManualSubModel || null)
      .then(({ data }) => {
        if (requestId !== yearOptionsRequestIdRef.current) return
        const nextYears = (data.years || []).map((y) => String(y))
        yearOptionsCacheRef.current.set(yearCacheKey, nextYears)
        applyNextYears(nextYears)
      })
      .catch(() => {})
      .finally(() => {
        if (requestId === yearOptionsRequestIdRef.current) clearLoadingState(yearsLoadingTimerRef, setYearsLoading)
      })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manualManufacturer, manualModel, manualSubModel, effectiveManualModel, effectiveManualSubModel])

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

  useEffect(() => {
    const handler = (e) => {
      if (manufacturerMenuRef.current && !manufacturerMenuRef.current.contains(e.target)) {
        setManufacturerMenuOpen(false)
      }
      if (partTypeMenuRef.current && !partTypeMenuRef.current.contains(e.target)) {
        setPartTypeMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])


  useEffect(() => {
    if (typeof window === 'undefined' || window.innerWidth >= 640) return undefined
    if (!manufacturerMenuOpen && !partTypeMenuOpen) return undefined

    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    return () => {
      document.body.style.overflow = previousOverflow
    }
  }, [manufacturerMenuOpen, partTypeMenuOpen])

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

  const filteredBrands = brands
    .filter((b) => {
      if (!manufacturerSearch.trim()) return true
      return b.name.toLowerCase().includes(manufacturerSearch.trim().toLowerCase())
    })
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name))

  const selectedCategoryNode = findPartCategoryNodeByValue(partFamilies, category)
  const selectedPartFamily = selectedCategoryNode?.family || null
  const selectedPartFamilyLabel = selectedCategoryNode?.label || categoryLabelForValue(partFamilies, category)
  const selectedPartCount = selectedCategoryNode?.subcategory
    ? (subcategoryCounts[selectedCategoryNode.subcategory.id] || 0)
    : (selectedPartFamily ? (categoryCounts[selectedPartFamily.id] || 0) : 0)
  const filteredPartFamilies = []
  const partFamiliesByGroup = new Map()
  const derivedPartFamilyGroupsMap = new Map()
  const normalizedPartFamilySearch = partFamilySearch.trim().toLowerCase()

  partFamilies.forEach((family) => {
    if (normalizedPartFamilySearch) {
      const searchableTokens = [
        family.label,
        family.group,
        family.id,
        ...(family.aliases || []),
        ...(family.legacy_categories || []),
        ...(family.subcategories || []).flatMap((subcategory) => [subcategory.label, subcategory.id, ...(subcategory.aliases || [])]),
      ]
      const matchesSearch = searchableTokens.some((token) => String(token || '').toLowerCase().includes(normalizedPartFamilySearch))
      if (!matchesSearch) return
    }
    const currentGroup = derivedPartFamilyGroupsMap.get(family.group_id)
    if (currentGroup) {
      currentGroup.count += categoryCounts[family.id] || 0
    } else {
      derivedPartFamilyGroupsMap.set(family.group_id, {
        id: family.group_id,
        label: family.group,
        count: categoryCounts[family.id] || 0,
      })
    }

    filteredPartFamilies.push(family)
    const groupFamilies = partFamiliesByGroup.get(family.group_id)
    if (groupFamilies) {
      groupFamilies.push(family)
    } else {
      partFamiliesByGroup.set(family.group_id, [family])
    }
  })

  for (const groupFamilies of partFamiliesByGroup.values()) {
    groupFamilies.sort((left, right) => (categoryCounts[right.id] || 0) - (categoryCounts[left.id] || 0) || left.label.localeCompare(right.label, 'he'))
  }

  const derivedPartFamilyGroups = Array.from(derivedPartFamilyGroupsMap.values())
  const sortedPartFamilyGroups = (partFamilyGroups.length ? partFamilyGroups : derivedPartFamilyGroups)
    .slice()
    .map((group) => ({
      ...group,
      count: group.count ?? derivedPartFamilyGroupsMap.get(group.id)?.count ?? 0,
    }))
    .sort((left, right) => (right.count || 0) - (left.count || 0) || left.label.localeCompare(right.label, 'he'))
  const suggestedPartFamilies = categories
    .filter((familyId) => familyId !== category)
    .sort((left, right) => (categoryCounts[right] || 0) - (categoryCounts[left] || 0))
    .slice(0, 8)

  const activeFiltersCount = [manualManufacturer, effectiveManualModel, effectiveManualSubModel, effectiveManualYear].filter(Boolean).length
  const vehicleSearchSummary = [manualManufacturer, effectiveManualModel, effectiveManualSubModel, effectiveManualYear, selectedPartFamilyLabel]
    .filter(Boolean)
    .join(' • ')
  const hasVisibleResults = parts.length > 0
  const activeVehicleFilterOrder = buildActiveVehicleFilterOrder({
    manualManufacturer,
    effectiveManualModel,
    effectiveManualSubModel,
    effectiveManualYear,
    category,
  })
  const filterStatsCards = [
    {
      id: 'manufacturers',
      label: 'יצרנים',
      value: brands.length.toLocaleString(),
      hint: manualManufacturer || 'כל היצרנים',
      icon: Car,
      tone: 'from-blue-50 to-cyan-50 border-blue-200 text-blue-700',
    },
    {
      id: 'models',
      label: 'דגמים',
      value: manualManufacturer ? modelOptions.length.toLocaleString() : '-',
      hint: effectiveManualModel || 'בחר דגם',
      icon: Search,
      tone: 'from-emerald-50 to-teal-50 border-emerald-200 text-emerald-700',
    },
    {
      id: 'submodels',
      label: 'תתי-דגם',
      value: effectiveManualModel ? subModelOptions.length.toLocaleString() : '-',
      hint: effectiveManualSubModel || 'בחר גרסה',
      icon: Tag,
      tone: 'from-violet-50 to-purple-50 border-violet-200 text-violet-700',
    },
    {
      id: 'years',
      label: 'שנות ייצור',
      value: effectiveManualModel ? yearOptions.length.toLocaleString() : '-',
      hint: effectiveManualYear || 'בחר שנה',
      icon: Hash,
      tone: 'from-amber-50 to-orange-50 border-amber-200 text-amber-700',
    },
    {
      id: 'part_types',
      label: 'סוגי חלקים',
      value: partFamilies.length.toLocaleString(),
      hint: selectedPartFamilyLabel,
      icon: Package,
      tone: 'from-rose-50 to-pink-50 border-rose-200 text-rose-700',
    },
  ]

  const clearManual = () => {
    setManualManufacturer('')
    setManualModel('')
    setManualSubModel('')
    setManualYear('')
    setBrandCountInfoOpenFor('')
  }

  const scheduleLoadingState = (timerRef, setLoading) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      timerRef.current = null
      setLoading(true)
    }, 400)
  }

  const clearLoadingState = (timerRef, setLoading) => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    setLoading(false)
  }

  const prefetchYears = (manufacturer, model, subModel = '') => {
    if (!manufacturer || !model) return
    const yearKey = hierarchyCacheKey(manufacturer, model, subModel)
    if (yearOptionsCacheRef.current.has(yearKey) || pendingYearFetchRef.current.has(yearKey)) return

    const p = partsApi.years(manufacturer, model, subModel || null)
      .then(({ data }) => {
        const items = (data.years || []).map((y) => String(y))
        yearOptionsCacheRef.current.set(yearKey, items)
        pendingYearFetchRef.current.delete(yearKey)
        return items
      })
      .catch(() => {
        pendingYearFetchRef.current.delete(yearKey)
        return []
      })

    pendingYearFetchRef.current.set(yearKey, p)
  }

  const prefetchSubModelsAndYears = (manufacturer, model, maxSubModels = 8) => {
    if (!manufacturer || !model) return
    const subKey = hierarchyCacheKey(manufacturer, model)

    if (!subModelOptionsCacheRef.current.has(subKey) && !pendingSubModelFetchRef.current.has(subKey)) {
      const p = partsApi.submodels(manufacturer, model)
        .then(({ data }) => {
          const items = data.submodels || []
          subModelOptionsCacheRef.current.set(subKey, items)
          pendingSubModelFetchRef.current.delete(subKey)
          prefetchYears(manufacturer, model, '')
          items.slice(0, maxSubModels).forEach((sm) => prefetchYears(manufacturer, model, sm))
          return items
        })
        .catch(() => {
          pendingSubModelFetchRef.current.delete(subKey)
          return []
        })

      pendingSubModelFetchRef.current.set(subKey, p)
      return
    }

    const cachedSub = subModelOptionsCacheRef.current.get(subKey) || []
    prefetchYears(manufacturer, model, '')
    cachedSub.slice(0, maxSubModels).forEach((sm) => prefetchYears(manufacturer, model, sm))
  }

  useEffect(() => () => {
    if (modelsLoadingTimerRef.current) clearTimeout(modelsLoadingTimerRef.current)
    if (subModelsLoadingTimerRef.current) clearTimeout(subModelsLoadingTimerRef.current)
    if (yearsLoadingTimerRef.current) clearTimeout(yearsLoadingTimerRef.current)
  }, [])

  const handleManualManufacturerChange = (nextManufacturer) => {
    const nextState = createManufacturerFilterTransition(nextManufacturer)
    categoryMetaRequestIdRef.current += 1
    modelOptionsRequestIdRef.current += 1
    subModelOptionsRequestIdRef.current += 1
    yearOptionsRequestIdRef.current += 1
    clearLoadingState(modelsLoadingTimerRef, setModelsLoading)
    clearLoadingState(subModelsLoadingTimerRef, setSubModelsLoading)
    clearLoadingState(yearsLoadingTimerRef, setYearsLoading)
    setManualManufacturer(nextState.manualManufacturer)
    setManualModel(nextState.manualModel)
    setManualSubModel(nextState.manualSubModel)
    setManualYear(nextState.manualYear)
    setModelOptions(nextState.modelOptions)
    setSubModelOptions(nextState.subModelOptions)
    setYearOptions(nextState.yearOptions)
    setModelOptionsKey(nextState.modelOptionsKey)
    setSubModelOptionsKey(nextState.subModelOptionsKey)
    setYearOptionsKey(nextState.yearOptionsKey)
    setBrandCountInfoOpenFor(nextState.brandCountInfoOpenFor)
  }

  const handleManualModelChange = (nextModel) => {
    const nextState = createModelFilterTransition(nextModel)
    categoryMetaRequestIdRef.current += 1
    subModelOptionsRequestIdRef.current += 1
    yearOptionsRequestIdRef.current += 1
    clearLoadingState(subModelsLoadingTimerRef, setSubModelsLoading)
    clearLoadingState(yearsLoadingTimerRef, setYearsLoading)
    setManualModel(nextState.manualModel)
    setManualSubModel(nextState.manualSubModel)
    setManualYear(nextState.manualYear)
    setSubModelOptions(nextState.subModelOptions)
    setYearOptions(nextState.yearOptions)
    setSubModelOptionsKey(nextState.subModelOptionsKey)
    setYearOptionsKey(nextState.yearOptionsKey)

    // Pre-warm caches immediately so the effect finds data already cached
    if (nextModel && manualManufacturer) {
      prefetchSubModelsAndYears(manualManufacturer, nextModel, 8)
    }

    // Pre-warm the search result for the new model selection in the background.
    // By the time the user clicks "search", the backend cache will already be warm.
    if (nextModel && manualManufacturer) {
      if (searchPrewarmRef.current) clearTimeout(searchPrewarmRef.current)
      searchPrewarmRef.current = setTimeout(() => {
        partsApi.search('', null, category || null, 3, manualManufacturer, nextModel, null, null)
          .catch(() => {}) // fire-and-forget
      }, 300)
    }
  }

  const handleManualSubModelChange = (nextSubModel) => {
    const nextState = createSubModelFilterTransition(nextSubModel)
    categoryMetaRequestIdRef.current += 1
    yearOptionsRequestIdRef.current += 1
    clearLoadingState(yearsLoadingTimerRef, setYearsLoading)
    setManualSubModel(nextState.manualSubModel)
    setManualYear(nextState.manualYear)
    setYearOptions(nextState.yearOptions)
    setYearOptionsKey(nextState.yearOptionsKey)

    // Pre-warm year cache for this sub-model immediately
    if (manualManufacturer && effectiveManualModel) {
      const subVal = nextSubModel || ''
      prefetchYears(manualManufacturer, effectiveManualModel, subVal)
      prefetchYears(manualManufacturer, effectiveManualModel, '')
    }
  }

  const handleManualYearChange = (nextYear) => {
    const nextState = createYearFilterTransition(nextYear)
    categoryMetaRequestIdRef.current += 1
    setManualYear(nextState.manualYear)
  }

  const handlePartFamilyChange = (nextCategory) => {
    const nextState = createCategoryFilterTransition(nextCategory)
    setCategory(nextState.category)
  }

  const runSelectedVehicleExactSearch = async (vehicleId, searchQuery, categoryValue) => {
    const { data } = await vehiclesApi.compatibleParts(vehicleId, {
      q: searchQuery || '',
      category: categoryValue || null,
      per_type: perType,
    })

    const grouped = data.grouped_results || null
    if (grouped) {
      setSearchResults({
        original: grouped.original || { part: null, suppliers: [] },
        oem: grouped.oem || { part: null, suppliers: [] },
        aftermarket: grouped.aftermarket || { part: null, suppliers: [] },
      })
      const flat = Array.isArray(data.parts)
        ? flattenGroupedResults({ all_parts: data.parts })
        : flattenGroupedResults(grouped)
      setParts(flat)
      setTotalCount(flat.length)
    } else {
      setSearchResults(null)
      const fallbackParts = data.parts || []
      setParts(fallbackParts)
      setTotalCount(data.total || fallbackParts.length)
    }

    const hasVerifiedParts = Boolean(data.fitment_verified)
    const hasAnyParts = Array.isArray(data.parts) && data.parts.length > 0
    setFitmentStatus({
      verified: hasVerifiedParts,
      message:
        data.message ||
        (hasAnyParts
          ? 'Exact fitment verified'
          : 'No verified fitment data'),
      source: data.fitment_source || 'strict_vehicle_search',
      confidenceBucket: data.confidence_bucket || (hasVerifiedParts ? 'verified' : 'no_data'),
      matchBasis: data.vehicle_match_basis || null,
    })
  }

  const search = async (pageNum = 0) => {
    const q = buildManualQuery()
    const hasManualVehicleFilter = Boolean(manualManufacturer || effectiveManualModel || effectiveManualSubModel || effectiveManualYear)
    const selectedVehicleId = !hasManualVehicleFilter ? (selectedVehicle?.id || null) : null

    if (!q && !category && !manualManufacturer && !selectedVehicleId) {
      toast.error('הזן שם חלק, בחר קטגוריה, או בחר יצרן')
      return
    }

    setIsLoading(true)
    setSearched(true)
    try {
      if (selectedVehicleId) {
        await runSelectedVehicleExactSearch(selectedVehicleId, q, category)
      } else {
        const { data } = await partsApi.search(
          q,
          selectedVehicleId,
          category,
          perType,
          manualManufacturer || null,
          effectiveManualModel || null,
          effectiveManualYear ? parseInt(effectiveManualYear, 10) : null,
          effectiveManualSubModel || null
        )
        // New grouped response
        if (data.original !== undefined || data.oem !== undefined || data.aftermarket !== undefined) {
          setSearchResults({ original: data.original, oem: data.oem, aftermarket: data.aftermarket })
          // Also flatten for backwards compat (stats, filters)
          const flat = flattenGroupedResults(data)
          setParts(flat)
          setTotalCount(flat.length)
        } else {
          // Fallback for old-style responses
          setSearchResults(null)
          setParts(data.parts || [])
          setTotalCount(data.total || 0)
        }
        setFitmentStatus(null)
      }
      setPage(pageNum)
      saveRecentSearch(q)
    } catch (err) {
      setFitmentStatus(null)
      const detail = err?.response?.data?.detail
      toast.error(typeof detail === 'string' ? detail : detail?.message || 'שגיאה בחיפוש')
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
      const detail = err?.response?.data?.detail
      toast.error(typeof detail === 'string' ? detail : detail?.message || 'לא הצלחנו לאתר את הרכב')
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

  const runPhotoPartsSearch = async (candidates, vehicle = null) => {
    if (!candidates || candidates.length === 0) return

    // Return cached result instantly
    const cacheKey = `${candidates[0]}__${vehicle?.id || vehicle?.manufacturer || ''}`
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
        vehicle?.manufacturer
          ? Promise.all(top.map(c => partsApi.search(
              c,
              vehicle?.id || null,
              category,
              null,
              vehicle?.manufacturer || null,
              vehicle?.model || null,
              vehicle?.year || null,
              null,
            ).catch(() => null)))
          : Promise.resolve(top.map(() => null)),
        Promise.all(top.map(c => partsApi.search(c, null, category).catch(() => null))),
      ])

      const flattenGrouped = (r) => flattenGroupedResults(r)

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
      if (!usedMfr && !vehicle?.manufacturer) {
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
        total: foundTotal, fallbackMfr: !usedMfr && vehicle?.manufacturer ? vehicle.manufacturer : '',
      }

      setQuery(usedQuery); setParts(foundParts); setSearchResults(foundGrouped)
      setTotalCount(foundTotal); setSearched(true); setPage(0)
      setPhotoFallbackMfr(!usedMfr && vehicle?.manufacturer ? vehicle.manufacturer : '')
    } finally {
      setIsLoading(false)
    }
  }

  // Re-run parts search when vehicle selection changes (if photo search is active)
  useEffect(() => {
    if (photoCandidates.length > 0) {
      runPhotoPartsSearch(photoCandidates, selectedVehicle || null)
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
        await runPhotoPartsSearch(candidates, selectedVehicle || null)
      }
    } catch (err) {
      const detail = err?.response?.data?.detail
      toast.error(typeof detail === 'string' ? detail : detail?.message || 'שגיאה בזיהוי התמונה')
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

  const runVoicePartsSearch = async (q, vehicle = null) => {
    if (!q) return
    const { sort_by, sort_dir } = getSortParams(sortBy)

    // Build candidates: full phrase first, then individual words (longest first)
    const words = q.split(/\s+/).filter(w => w.length > 1)
    const candidates = [q, ...words.filter(w => w !== q)].filter((v, i, a) => a.indexOf(v) === i)

    const cacheKey = `${candidates[0]}__${vehicle?.id || vehicle?.manufacturer || ''}`
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
          vehicle?.manufacturer
            ? top.map(c => partsApi.search(
                c,
                vehicle?.id || null,
                category,
                null,
                vehicle?.manufacturer || null,
                vehicle?.model || null,
                vehicle?.year || null,
                null,
              ).catch(() => null))
            : top.map(() => Promise.resolve(null))
        ),
        Promise.all(
          top.map(c => partsApi.search(c, null, category, null, null).catch(() => null))
        ),
      ])

      let foundParts = [], foundTotal = 0, foundGrouped = null, usedQuery = top[0], usedMfr = false

      // Helper: extract flat array from grouped response
      const flattenGrouped = (r) => flattenGroupedResults(r)

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
      if (!usedMfr && !vehicle?.manufacturer) {
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

      const fallbackMfr = !usedMfr && vehicle?.manufacturer ? vehicle.manufacturer : ''
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
      runVoicePartsSearch(cleaned, selectedVehicle || null)
    }
  }, [selectedVehicle])

  const handleVoiceSearch = () => {
    const raw = voiceTranscript.trim()
    if (!raw) { toast.error('אמור שם חלק תחילה'); return }
    const cleaned = cleanVoiceQuery(raw, selectedVehicle)
    voiceSearchCache.current = {}
    setVoiceFallbackMfr('')
    runVoicePartsSearch(cleaned, selectedVehicle || null)
  }

  // ─── VIN mode ─────────────────────────────────────────────────────────────
  const [vinInput, setVinInput] = useState('')
  const [vinLoading, setVinLoading] = useState(false)
  const [vinVehicle, setVinVehicle] = useState(null)
  const [vinPartQuery, setVinPartQuery] = useState('')
  const [showVinScanner, setShowVinScanner] = useState(false)

  const activeSearchPanel = searchMode === 'voice' ? 'photo' : searchMode
  const searchModeCards = [
    {
      id: 'manual',
      title: 'חיפוש חופשי',
      subtitle: 'שם חלק / מקט / מילות מפתח',
      icon: Search,
      badge: query?.trim() ? 'פעיל' : null,
      metric: query?.trim() ? `שאילתה: ${query.trim().slice(0, 24)}` : 'חיפוש טקסט בזמן אמת',
      tone: 'from-sky-50 to-blue-50 border-sky-200',
      iconTone: 'bg-sky-600',
    },
    {
      id: 'vehicle',
      title: 'חיפוש לפי פרטי רכב',
      subtitle: 'יצרן, דגם, שנה וסוג חלק',
      icon: SlidersHorizontal,
      badge: activeFiltersCount > 0 || category ? `${activeFiltersCount + (category ? 1 : 0)} מסננים` : null,
      metric: `${brands.length.toLocaleString()} יצרנים • ${partFamilies.length.toLocaleString()} משפחות חלקים`,
      tone: 'from-emerald-50 to-teal-50 border-emerald-200',
      iconTone: 'bg-emerald-600',
    },
    {
      id: 'vin',
      title: 'VIN / לוחית',
      subtitle: 'זיהוי רכב ואז חיפוש מדויק',
      icon: Hash,
      badge: vinVehicle ? 'רכב מזוהה' : null,
      metric: vinInput ? `VIN: ${vinInput.replace(/\s/g, '').length}/17` : 'פענוח רכב לפי VIN/לוחית',
      tone: 'from-amber-50 to-orange-50 border-amber-200',
      iconTone: 'bg-amber-600',
    },
    {
      id: 'photo',
      title: 'תמונה / קול',
      subtitle: 'זיהוי חלק חכם עם AI',
      icon: Camera,
      badge: photoPreview || isListening ? 'פעיל' : null,
      metric: isListening ? 'האזנה פעילה' : (photoPreview ? 'תמונה נטענה לזיהוי' : 'ניתוח תמונה וקול'),
      tone: 'from-violet-50 to-fuchsia-50 border-violet-200',
      iconTone: 'bg-violet-600',
    },
  ]
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
          const flat = flattenGroupedResults(gd)
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
      const detail = err?.response?.data?.detail
      toast.error(typeof detail === 'string' ? detail : detail?.message || 'VIN לא זוהה')
    } finally {
      setVinLoading(false)
      setIsLoading(false)
    }
  }

  return (
    <div className="space-y-6 pb-24 sm:pb-0">
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
      <div className="rounded-[28px] bg-gradient-to-br from-brand-700 via-brand-600 to-sky-700 px-4 py-5 sm:px-6 sm:py-6 text-white shadow-lg shadow-brand-900/10">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3 sm:gap-4">
            <div className="w-12 h-12 rounded-2xl bg-white/20 ring-1 ring-white/20 flex items-center justify-center flex-shrink-0">
              <Search className="w-5 h-5 text-white" />
            </div>
            <div className="lg:order-1">
              <h1 className="text-xl sm:text-2xl font-black tracking-tight leading-tight">חיפוש חלקי חילוף</h1>
              <p className="text-white/75 text-xs sm:text-sm mt-1">חפש לפי רכב · תמונה · קול · VIN</p>
            </div>
          </div>
          {selectedVehicle && (
            <button
              onClick={() => setShowVehiclePicker(true)}
              className="flex items-center justify-between gap-2 bg-white/15 hover:bg-white/25 transition-colors rounded-2xl px-3 py-2.5 text-right w-full sm:w-auto flex-shrink-0 backdrop-blur-sm"
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

      {/* Search mode cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3 lg:gap-4">
        {searchModeCards.map((card) => {
          const Icon = card.icon
          const active = activeSearchPanel === card.id
          return (
            <button
              key={card.id}
              type="button"
              onClick={() => switchMode(card.id)}
              className={`text-right rounded-[24px] border bg-gradient-to-br p-4 min-h-[132px] transition-all ${active ? `${card.tone} shadow-sm ring-1 ring-brand-300 -translate-y-0.5` : 'border-gray-200 from-white to-gray-50 hover:border-brand-300 hover:shadow-sm hover:-translate-y-0.5'}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className={`text-sm font-bold ${active ? 'text-brand-800' : 'text-brand-navy'}`}>{card.title}</p>
                  <p className="text-xs text-gray-500 mt-1">{card.subtitle}</p>
                </div>
                <span className={`inline-flex h-9 w-9 items-center justify-center rounded-xl ${active ? `${card.iconTone} text-white` : 'bg-gray-100 text-gray-500'}`}>
                  <Icon className="w-4 h-4" />
                </span>
              </div>
              <p className="mt-3 text-[11px] sm:text-xs text-gray-600 truncate" title={card.metric}>{card.metric}</p>
              <div className="mt-2.5 flex items-center justify-between">
                <span className={`text-[11px] font-semibold ${active ? 'text-brand-700' : 'text-gray-500'}`}>{active ? 'פעיל עכשיו' : 'לחץ למעבר'}</span>
                {card.badge ? <span className="text-[11px] rounded-full bg-white/90 border border-brand-200 px-2 py-0.5 text-brand-700 font-medium">{card.badge}</span> : null}
              </div>
            </button>
          )
        })}
      </div>

      {/* ── 4 search blocks — visual order controlled by CSS flex order ── */}
      <div className="flex flex-col gap-6">

      {/* ── BLOCK 1: Free text search ── */}
      <div className={`card p-4 ${activeSearchPanel !== 'manual' ? 'hidden' : ''}`} style={{order: 1}}>
        <div className="flex items-center gap-2 mb-3">
          <div className="w-8 h-8 rounded-lg bg-brand-100 flex items-center justify-center flex-shrink-0">
            <Search className="w-4 h-4 text-brand-600" />
          </div>
          <h3 className="font-semibold text-brand-navy">חיפוש חופשי</h3>
          {activeFiltersCount > 0 && (
            <span className="text-xs bg-brand-100 text-brand-700 px-2 py-0.5 rounded-full font-medium mr-auto">
              {activeFiltersCount} פילטרים פעילים
            </span>
          )}
        </div>
        <div className="flex flex-col sm:flex-row gap-3 sm:items-stretch">
          <div className="relative flex-1" ref={suggestRef}>
            <Search className="absolute right-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
            <input
              className="input-field pr-10 h-12 sm:h-11 text-[15px] sm:text-sm rounded-xl"
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
          <button onClick={() => search(0)} disabled={isLoading} className="btn-primary flex items-center justify-center gap-2 whitespace-nowrap w-full sm:w-auto min-h-12 sm:min-h-11 rounded-xl">
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
            🔍 מחפש: {[manualManufacturer, effectiveManualModel, effectiveManualSubModel, effectiveManualYear, query].filter(Boolean).join(' ') || 'כל החלקים'}
          </p>
        )}
      </div>

      {/* ── BLOCK 4: Search by plate / VIN ── */}
      <div className={`card p-4 space-y-4 ${activeSearchPanel !== 'vin' ? 'hidden' : ''}`} style={{order: 4}}>
          {/* Header */}
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-green-100 flex items-center justify-center flex-shrink-0">
              <Car className="w-4 h-4 text-green-600" />
            </div>
            <h3 className="font-semibold text-brand-navy">חיפוש לפי רכב (לוחית / VIN)</h3>
          </div>

          {/* ── Search inputs row: plate + VIN ── */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">

            {/* Israeli license plate input */}
            <div className="lg:order-2">
              <label className="block text-[11px] text-gray-500 mb-1 font-medium">מספר רכב</label>
              <div className="flex gap-2 flex-col xs:flex-row">
                <div className="relative flex rounded-lg overflow-hidden border border-gray-200 focus-within:border-brand-400 focus-within:ring-1 focus-within:ring-brand-300 transition-colors flex-1 min-w-0">
                  <input
                    className="w-full min-h-12 bg-white text-brand-navy font-mono font-semibold text-sm tracking-[0.15em] text-center uppercase placeholder:text-gray-400 placeholder:font-normal placeholder:text-xs placeholder:tracking-normal focus:outline-none px-3 py-3"
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
                  className="bg-brand-600 hover:bg-brand-700 disabled:opacity-50 text-white px-4 py-3 rounded-xl font-semibold text-sm flex items-center justify-center gap-1.5 transition-colors whitespace-nowrap flex-shrink-0 min-h-12 xs:min-w-[92px]"
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
                    vinInput.replace(/\s/g, '').length === 17 ? 'text-green-600' : 'text-brand-400'
                  }`}>
                    {vinInput.replace(/\s/g, '').length}/17
                  </span>
                )}
              </label>
              <div className="flex gap-2 flex-col xs:flex-row">
                <div className="relative flex-1 min-w-0">
                  <input
                    className="w-full min-h-12 border border-gray-200 rounded-xl bg-white text-brand-navy font-mono text-sm tracking-wider uppercase focus:outline-none focus:ring-2 focus:ring-brand-400 focus:border-transparent px-3 py-3 pl-8"
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
                    className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-brand-600 transition-colors p-0.5"
                  >
                    <ScanLine className="w-3.5 h-3.5" />
                  </button>
                </div>
                <button
                  onClick={() => handleVinSearch()}
                  disabled={vinLoading}
                  className="bg-brand-600 hover:bg-brand-700 disabled:opacity-50 text-white px-4 py-3 rounded-xl font-semibold text-sm flex items-center justify-center gap-1.5 transition-colors whitespace-nowrap flex-shrink-0 min-h-12 xs:min-w-[92px]"
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
              <div className="flex items-center justify-end">
                <button
                  onClick={() => {
                    setVinVehicle(null)
                    setVinInput('')
                    setVinPartQuery('')
                  }}
                  className="text-xs text-gray-500 hover:text-red-500 transition-colors flex items-center gap-1"
                >
                  <X className="w-3 h-3" /> הסר רכב VIN
                </button>
              </div>
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
                  className="flex-1 min-h-11 border border-gray-200 rounded-xl text-[15px] sm:text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-400"
                  placeholder="שם חלק לחיפוש (רפידות, פילטר...)"
                  value={vinPartQuery}
                  onChange={(e) => setVinPartQuery(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleVinSearch(vinPartQuery)}
                />
                <button
                  onClick={() => handleVinSearch(vinPartQuery)}
                  disabled={vinLoading}
                  className="bg-brand-600 hover:bg-brand-700 disabled:opacity-50 text-white px-4 py-2.5 rounded-xl text-sm font-semibold flex items-center justify-center gap-1 whitespace-nowrap min-h-11"
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
      <div className={`card p-4 pb-24 sm:pb-4 space-y-4 ${activeSearchPanel !== 'vehicle' ? 'hidden' : ''}`} style={{order: 2}}>
          {/* Header */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-brand-100 flex items-center justify-center">
                <SlidersHorizontal className="w-4 h-4 text-brand-600" />
              </div>
              <div>
                <h3 className="font-semibold text-brand-navy">חיפוש לפי פרטי רכב</h3>
                <p className="text-[11px] text-gray-400 mt-0.5">מסננים המחוברים לנתוני DB בזמן אמת</p>
              </div>
            </div>
            {(activeFiltersCount > 0 || category) && (
              <button onClick={() => { clearManual(); handlePartFamilyChange('') }} className="flex items-center gap-1 text-xs text-gray-400 hover:text-red-500 transition-colors">
                <X className="w-3 h-3" /> נקה הכל
              </button>
            )}
          </div>

          <div className="rounded-2xl border border-emerald-200 bg-gradient-to-r from-emerald-50 to-teal-50 px-3.5 py-3 text-xs sm:text-sm text-emerald-800 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
            <span className="font-semibold">המסננים מחוברים לנתוני API חיים ומעדכנים יצרנים, דגמים, שנים וקטגוריות בזמן אמת.</span>
            <span className="text-emerald-700/80">Manufacturers → Models → Years → Categories</span>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-2.5 lg:gap-3">
            {filterStatsCards.map((card) => {
              const Icon = card.icon
              return (
                <div key={card.id} className={`rounded-2xl border bg-gradient-to-br px-3 py-3 shadow-sm ${card.tone}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-semibold opacity-90">{card.label}</span>
                    <span className="inline-flex h-6 w-6 items-center justify-center rounded-lg bg-white/90">
                      <Icon className="w-3.5 h-3.5" />
                    </span>
                  </div>
                  <p className="text-xl sm:text-2xl font-extrabold leading-tight mt-1.5">{card.value}</p>
                  <p className="text-[11px] mt-0.5 truncate opacity-90" title={card.hint}>{card.hint}</p>
                </div>
              )
            })}
          </div>

          {/* Fields: Manufacturer → Model → Sub-model → Year → Part Type */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3.5">

            {/* 1. Manufacturer */}
            <div className="rounded-2xl border border-gray-200 bg-gray-50/50 p-3.5">
              <label className="block text-xs text-gray-500 mb-1.5 font-medium">יצרן</label>
              <div className="relative" ref={manufacturerMenuRef}>
                <button
                  type="button"
                  className={FILTER_MENU_TRIGGER_CLASS}
                  onClick={() => {
                    setPartTypeMenuOpen(false)
                    setManufacturerMenuOpen((v) => !v)
                  }}
                >
                  <span className="truncate inline-flex items-center gap-2">
                    {manualManufacturer ? (
                      <img
                        src={logoForManufacturer(manualManufacturer, brandLogos) || fallbackLogoDataUri(manualManufacturer)}
                        alt=""
                        aria-hidden="true"
                        className="object-contain flex-shrink-0"
                        style={{ width: MANUFACTURER_LOGO_SIZE, height: MANUFACTURER_LOGO_SIZE }}
                        onError={(e) => {
                          e.currentTarget.onerror = null
                          e.currentTarget.src = fallbackLogoDataUri(manualManufacturer)
                        }}
                      />
                    ) : null}
                    <span>{manualManufacturer || 'כל היצרנים'}</span>
                  </span>
                  <ChevronDown className={`w-4 h-4 text-gray-400 transition-transform ${manufacturerMenuOpen ? 'rotate-180' : ''}`} />
                </button>

                {manufacturerMenuOpen && (
                  <div className="absolute z-30 mt-1 hidden w-full rounded-lg border border-gray-200 bg-white shadow-lg sm:block">
                    <div className="p-2 border-b border-gray-100">
                      <input
                        className="w-full border border-gray-200 rounded-xl bg-white text-brand-navy text-sm focus:outline-none focus:ring-2 focus:ring-brand-400 focus:border-transparent px-3 py-2.5"
                        value={manufacturerSearch}
                        onChange={(e) => setManufacturerSearch(e.target.value)}
                        placeholder="חפש יצרן..."
                      />
                    </div>
                    {isAdmin && brandCountInfoOpenFor && (
                      <div
                        className="mx-2 mt-2 rounded-lg border border-brand-100 bg-brand-50 px-2 py-1.5 text-[11px] text-gray-700"
                        onClick={(e) => {
                          e.preventDefault()
                          e.stopPropagation()
                          setBrandCountInfoOpenFor('')
                        }}
                      >
                        מספר הרכבים שנשמרו במערכת עבור {brandCountInfoOpenFor}. לחץ שוב על המספר כדי לסגור.
                      </div>
                    )}
                    <div className="max-h-64 overflow-y-auto p-1">
                      <button
                        type="button"
                        className="w-full text-right px-3 py-2.5 rounded-xl hover:bg-gray-50 text-sm"
                        onClick={() => { handleManualManufacturerChange(''); setManufacturerMenuOpen(false) }}
                      >
                        כל היצרנים
                      </button>
                      {filteredBrands.map((b) => (
                        <button
                          type="button"
                          key={b.name}
                          className="w-full flex items-center justify-between px-3 py-2.5 rounded-xl hover:bg-gray-50 text-sm"
                          onClick={() => { handleManualManufacturerChange(b.name); setManufacturerMenuOpen(false) }}
                        >
                          <span className="flex items-center gap-2.5 min-w-0">
                            <span className="inline-flex items-center justify-center flex-shrink-0" style={{ width: MANUFACTURER_LOGO_SIZE, height: MANUFACTURER_LOGO_SIZE }}>
                              {logoForManufacturer(b.name, brandLogos) ? (
                                <img
                                  src={logoForManufacturer(b.name, brandLogos)}
                                  alt=""
                                  aria-hidden="true"
                                  className="object-contain"
                                  style={{ width: MANUFACTURER_LOGO_SIZE, height: MANUFACTURER_LOGO_SIZE }}
                                  onError={(e) => {
                                    e.currentTarget.onerror = null
                                    e.currentTarget.src = fallbackLogoDataUri(b.name)
                                  }}
                                />
                              ) : (
                                <img src={fallbackLogoDataUri(b.name)} alt="" aria-hidden="true" className="object-contain" style={{ width: MANUFACTURER_LOGO_SIZE, height: MANUFACTURER_LOGO_SIZE }} />
                              )}
                            </span>
                            <span className="truncate">{b.name}</span>
                          </span>
                          {isAdmin && !!brandCounts[b.name] && (
                            <span
                              className="ml-2 text-xs text-gray-500 hover:text-brand-600 cursor-pointer"
                              onClick={(e) => {
                                e.preventDefault()
                                e.stopPropagation()
                                setBrandCountInfoOpenFor((curr) => (curr === b.name ? '' : b.name))
                              }}
                            >
                              {brandCounts[b.name].toLocaleString()}
                            </span>
                          )}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* 2. Model */}
            <div className="rounded-2xl border border-gray-200 bg-gray-50/50 p-3.5">
              <label className="block text-xs text-gray-500 mb-1.5 font-medium">דגם</label>
              <select
                className={FILTER_SELECT_CLASS}
                value={effectiveManualModel}
                onChange={(e) => handleManualModelChange(e.target.value)}
                disabled={!manualManufacturer || modelsLoading}
              >
                <option value="">
                  {manualManufacturer
                    ? (modelOptions.length ? 'כל הדגמים' : 'אין דגמים ליצרן זה')
                    : 'בחר יצרן תחילה'}
                </option>
                {modelOptions.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>

            {/* 3. Sub-model */}
            <div className="lg:order-3 rounded-xl border border-gray-200 bg-gray-50/40 p-3">
              <label className="block text-xs text-gray-500 mb-1.5 font-medium">תת-דגם / גרסה</label>
              <select
                className={FILTER_SELECT_CLASS}
                value={effectiveManualSubModel}
                onChange={(e) => handleManualSubModelChange(e.target.value)}
                disabled={!manualManufacturer || !effectiveManualModel || subModelsLoading}
              >
                <option value="">
                  {getSubModelPlaceholder({
                    loading: subModelsLoading,
                    hasManufacturer: Boolean(manualManufacturer),
                    hasModel: Boolean(effectiveManualModel),
                    optionCount: subModelOptions.length,
                  })}
                </option>
                {subModelOptions.map((sm) => (
                  <option key={sm} value={sm}>{sm}</option>
                ))}
              </select>
            </div>

            {/* 4. Year */}
            <div className="lg:order-4 rounded-xl border border-gray-200 bg-gray-50/40 p-3">
              <label className="block text-xs text-gray-500 mb-1.5 font-medium">שנה</label>
              <select
                className={FILTER_SELECT_CLASS}
                value={effectiveManualYear}
                onChange={(e) => handleManualYearChange(e.target.value)}
                disabled={!manualManufacturer || !effectiveManualModel || yearsLoading}
              >
                <option value="">
                  {getYearPlaceholder({
                    loading: yearsLoading,
                    hasManufacturer: Boolean(manualManufacturer),
                    hasModel: Boolean(effectiveManualModel),
                    optionCount: yearOptions.length,
                  })}
                </option>
                {yearOptions.map((y) => (
                  <option key={y} value={y}>{y}</option>
                ))}
              </select>
            </div>

            {/* 5. Part Type / Category */}
            <div className="lg:order-5 rounded-xl border border-gray-200 bg-gray-50/40 p-3">
              <label className="block text-xs text-gray-500 mb-1.5 font-medium">סוג חלק</label>
              <div className="relative" ref={partTypeMenuRef}>
                <button
                  type="button"
                  className={FILTER_MENU_TRIGGER_CLASS}
                  onClick={() => {
                    setManufacturerMenuOpen(false)
                    setPartTypeMenuOpen((value) => !value)
                  }}
                >
                  <span className="truncate inline-flex items-center gap-2 min-w-0 flex-1">
                    {selectedPartFamily ? (
                      <span className="inline-flex items-center justify-center rounded-lg bg-white border border-gray-200 overflow-hidden shadow-sm flex-shrink-0" style={{ width: PART_FAMILY_TRIGGER_IMAGE_WIDTH, height: PART_FAMILY_TRIGGER_IMAGE_HEIGHT }}>
                        <img
                          src={partFamilyImageSrc(selectedPartFamily)}
                          alt=""
                          aria-hidden="true"
                          className="h-full w-full object-cover flex-shrink-0"
                        />
                      </span>
                    ) : null}
                    <span className="truncate">{partFamilyHierarchyLabel(selectedPartFamily)}</span>
                  </span>
                  <span className="flex items-center gap-2 pl-2 flex-shrink-0">
                    {selectedPartCount ? (
                      <span className="text-xs text-gray-500">{selectedPartCount.toLocaleString()}</span>
                    ) : null}
                    <ChevronDown className={`w-4 h-4 text-gray-400 transition-transform ${partTypeMenuOpen ? 'rotate-180' : ''}`} />
                  </span>
                </button>

                {partTypeMenuOpen && (
                  <div className="absolute z-30 mt-1 hidden w-full rounded-lg border border-gray-200 bg-white shadow-lg sm:block">
                    <div className="max-h-72 overflow-y-auto p-1">
                      <button
                        type="button"
                        className="w-full text-right px-3 py-2.5 rounded-xl hover:bg-gray-50 text-sm"
                        onClick={() => { handlePartFamilyChange(''); setPartTypeMenuOpen(false) }}
                      >
                        כל סוגי החלקים
                      </button>
                      {sortedPartFamilyGroups.map((group) => {
                        const familiesInGroup = partFamiliesByGroup.get(group.id) || []
                        if (!familiesInGroup.length) return null
                        return (
                          <div key={group.id} className="pt-1">
                            <div className="px-2 py-1 text-[11px] font-semibold text-gray-400 uppercase tracking-[0.08em]">
                              {group.label}
                            </div>
                            {familiesInGroup.map((family) => (
                              <div key={family.id} className={`rounded-xl border ${category === family.id ? 'border-brand-200 bg-brand-50/60' : 'border-transparent'}`}>
                                <button
                                  type="button"
                                  className={`w-full flex items-center justify-between px-3 py-2.5 rounded-xl text-sm ${category === family.id ? 'bg-brand-50 text-brand-700' : 'hover:bg-gray-50 text-brand-navy'} ${categoryCounts[family.id] === 0 ? 'opacity-60' : ''}`}
                                  onClick={() => { handlePartFamilyChange(family.id); setPartTypeMenuOpen(false) }}
                                >
                                  <span className="flex items-center gap-3 min-w-0">
                                    <img
                                      src={partFamilyImageSrc(family)}
                                      alt=""
                                      aria-hidden="true"
                                      className="rounded-lg object-cover border border-gray-200 bg-white shadow-sm flex-shrink-0"
                                      style={{ width: PART_FAMILY_MENU_IMAGE_WIDTH, height: PART_FAMILY_MENU_IMAGE_HEIGHT }}
                                    />
                                    <span className="truncate">{family.label}</span>
                                  </span>
                                  <span className="ml-2 text-xs text-gray-500">
                                    {(categoryCounts[family.id] || 0).toLocaleString()}
                                  </span>
                                </button>
                                {Array.isArray(family.subcategories) && family.subcategories.length > 0 && (
                                  <div className="grid grid-cols-2 gap-2 px-3 pb-3 pt-2">
                                    {family.subcategories.slice(0, 8).map((subcategory) => {
                                      const selected = category === subcategory.id
                                      const subCount = subcategoryCounts[subcategory.id] || 0
                                      return (
                                        <button
                                          type="button"
                                          key={subcategory.id}
                                          className={`rounded-lg border px-2 py-2 text-[11px] text-right leading-tight transition-colors ${selected ? 'border-brand-300 bg-white text-brand-700' : 'border-slate-200 bg-white/80 hover:bg-white text-slate-700'} ${subCount === 0 ? 'opacity-70' : ''}`}
                                          onClick={() => { handlePartFamilyChange(subcategory.id); setPartTypeMenuOpen(false) }}
                                        >
                                          <div className="font-medium">{subcategory.label}</div>
                                          <div className="mt-1 text-[10px] text-slate-400">{subCount.toLocaleString()} תוצאות</div>
                                        </button>
                                      )
                                    })}
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        )
                      })}
                      {!sortedPartFamilyGroups.length && filteredPartFamilies.map((family) => (
                        <button
                          type="button"
                          key={family.id}
                          className={`w-full flex items-center justify-between px-3 py-2.5 rounded-xl text-sm ${category === family.id ? 'bg-brand-50 text-brand-700' : 'hover:bg-gray-50 text-brand-navy'} ${categoryCounts[family.id] === 0 ? 'opacity-60' : ''}`}
                          onClick={() => { handlePartFamilyChange(family.id); setPartTypeMenuOpen(false) }}
                        >
                          <span className="flex items-center gap-3 min-w-0">
                            <img
                              src={partFamilyImageSrc(family)}
                              alt=""
                              aria-hidden="true"
                              className="rounded-lg object-cover border border-gray-200 bg-white shadow-sm flex-shrink-0"
                              style={{ width: PART_FAMILY_MENU_IMAGE_WIDTH, height: PART_FAMILY_MENU_IMAGE_HEIGHT }}
                            />
                            <span className="truncate">{family.label}</span>
                          </span>
                          <span className="ml-2 text-xs text-gray-500">
                            {(categoryCounts[family.id] || 0).toLocaleString()}
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Active filter chips */}
          {(activeFiltersCount > 0 || category) && (
            <div className="flex flex-wrap gap-2.5">
              {activeVehicleFilterOrder.includes('manufacturer') && (
                <span className="inline-flex items-center gap-1 bg-brand-100 text-brand-700 text-xs px-2.5 py-1 rounded-full font-medium">
                  <img
                    src={logoForManufacturer(manualManufacturer, brandLogos) || fallbackLogoDataUri(manualManufacturer)}
                    alt=""
                    aria-hidden="true"
                    className="object-contain flex-shrink-0"
                    style={{ width: MANUFACTURER_CHIP_LOGO_SIZE, height: MANUFACTURER_CHIP_LOGO_SIZE }}
                    onError={(e) => {
                      e.currentTarget.onerror = null
                      e.currentTarget.src = fallbackLogoDataUri(manualManufacturer)
                    }}
                  />
                  {manualManufacturer}
                  <button onClick={() => handleManualManufacturerChange('')}><X className="w-3 h-3" /></button>
                </span>
              )}
              {activeVehicleFilterOrder.includes('model') && (
                <span className="inline-flex items-center gap-1 bg-brand-100 text-brand-700 text-xs px-2.5 py-1 rounded-full font-medium">
                  {effectiveManualModel}
                  <button onClick={() => handleManualModelChange('')}><X className="w-3 h-3" /></button>
                </span>
              )}
              {activeVehicleFilterOrder.includes('submodel') && (
                <span className="inline-flex items-center gap-1 bg-brand-100 text-brand-700 text-xs px-2.5 py-1 rounded-full font-medium">
                  {effectiveManualSubModel}
                  <button onClick={() => handleManualSubModelChange('')}><X className="w-3 h-3" /></button>
                </span>
              )}
              {activeVehicleFilterOrder.includes('year') && (
                <span className="inline-flex items-center gap-1 bg-brand-100 text-brand-700 text-xs px-2.5 py-1 rounded-full font-medium">
                  {effectiveManualYear}
                  <button onClick={() => handleManualYearChange('')}><X className="w-3 h-3" /></button>
                </span>
              )}
              {selectedPartFamily && activeVehicleFilterOrder.includes('category') && (
                <span className="inline-flex items-center gap-1 bg-brand-100 text-brand-700 text-xs px-2.5 py-1 rounded-full font-medium">
                  {selectedPartFamily ? (
                    <img
                      src={partFamilyImageSrc(selectedPartFamily)}
                      alt=""
                      aria-hidden="true"
                      className="object-cover flex-shrink-0 rounded-full"
                      style={{ width: MANUFACTURER_CHIP_LOGO_SIZE, height: MANUFACTURER_CHIP_LOGO_SIZE }}
                    />
                  ) : null}
                  {selectedPartFamilyLabel}
                  <button onClick={() => handlePartFamilyChange('')}><X className="w-3 h-3" /></button>
                </span>
              )}
            </div>
          )}
          {/* Search button for Block 2 */}
          <div className="hidden sm:flex items-center justify-between gap-3 pt-1 flex-wrap">
            <p className="text-xs text-gray-500">המסננים שולחים חיפוש ישירות למסד הנתונים דרך API החלקים.</p>
            <button onClick={() => search(0)} disabled={isLoading} className="btn-primary flex items-center gap-2">
              {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
              חפש לפי פרטי רכב
            </button>
          </div>
        </div>

      <MobileBottomSheet
        open={manufacturerMenuOpen}
        onClose={() => {
          setManufacturerMenuOpen(false)
          setManufacturerSearch('')
          setBrandCountInfoOpenFor('')
        }}
        title="בחירת יצרן"
        subtitle="בחר יצרן מהרשימה או חפש לפי שם המותג"
        footer={
          <button
            type="button"
            onClick={() => {
              handleManualManufacturerChange('')
              setManufacturerMenuOpen(false)
              setManufacturerSearch('')
              setBrandCountInfoOpenFor('')
            }}
            className="flex min-h-12 w-full items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-slate-50 text-sm font-semibold text-slate-700"
          >
            <RotateCcw className="h-4 w-4" />
            כל היצרנים
          </button>
        }
      >
        <div className="space-y-3">
          <input
            className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-[15px] text-brand-navy outline-none focus:border-brand-300 focus:ring-2 focus:ring-brand-200"
            value={manufacturerSearch}
            onChange={(e) => setManufacturerSearch(e.target.value)}
            placeholder="חפש יצרן..."
          />
          <div className="space-y-2">
            {filteredBrands.map((b) => (
              <button
                type="button"
                key={b.name}
                className={`flex w-full items-center justify-between rounded-[22px] border px-3.5 py-3 text-right transition-colors ${manualManufacturer === b.name ? 'border-brand-300 bg-brand-50 text-brand-700' : 'border-slate-200 bg-white text-brand-navy'}`}
                onClick={() => {
                  handleManualManufacturerChange(b.name)
                  setManufacturerMenuOpen(false)
                  setManufacturerSearch('')
                  setBrandCountInfoOpenFor('')
                }}
              >
                <span className="flex min-w-0 items-center gap-3">
                  <span className="inline-flex items-center justify-center overflow-hidden rounded-2xl border border-slate-200 bg-slate-50" style={{ width: 42, height: 42 }}>
                    <img
                      src={logoForManufacturer(b.name, brandLogos) || fallbackLogoDataUri(b.name)}
                      alt=""
                      aria-hidden="true"
                      className="h-full w-full object-contain"
                      onError={(e) => {
                        e.currentTarget.onerror = null
                        e.currentTarget.src = fallbackLogoDataUri(b.name)
                      }}
                    />
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-semibold">{b.name}</span>
                    {brandCounts[b.name] ? <span className="mt-0.5 block text-xs text-slate-500">{brandCounts[b.name].toLocaleString()} רכבים</span> : null}
                  </span>
                </span>
                {manualManufacturer === b.name ? <Check className="h-4 w-4 flex-shrink-0 text-brand-600" /> : <ChevronRight className="h-4 w-4 flex-shrink-0 text-slate-300" />}
              </button>
            ))}
          </div>
        </div>
      </MobileBottomSheet>

      <MobileBottomSheet
        open={partTypeMenuOpen}
        onClose={() => {
          setPartTypeMenuOpen(false)
          setPartFamilySearch('')
        }}
        title="בחירת סוג חלק"
        subtitle="חפש משפחת חלקים או דפדף לפי קבוצות"
        footer={
          <button
            type="button"
            onClick={() => {
              handlePartFamilyChange('')
              setPartTypeMenuOpen(false)
              setPartFamilySearch('')
            }}
            className="flex min-h-12 w-full items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-slate-50 text-sm font-semibold text-slate-700"
          >
            <RotateCcw className="h-4 w-4" />
            כל סוגי החלקים
          </button>
        }
      >
        <div className="space-y-3">
          <input
            className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-[15px] text-brand-navy outline-none focus:border-brand-300 focus:ring-2 focus:ring-brand-200"
            value={partFamilySearch}
            onChange={(e) => setPartFamilySearch(e.target.value)}
            placeholder="חפש סוג חלק..."
          />
          <div className="space-y-3">
            {sortedPartFamilyGroups.length ? sortedPartFamilyGroups.map((group) => {
              const familiesInGroup = partFamiliesByGroup.get(group.id) || []
              if (!familiesInGroup.length) return null
              return (
                <div key={group.id} className="space-y-2">
                  <div className="flex items-center justify-between px-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                    <span>{group.label}</span>
                    <span>{(group.count || 0).toLocaleString()}</span>
                  </div>
                  <div className="space-y-2">
                    {familiesInGroup.map((family) => (
                      <div key={family.id} className={`rounded-[22px] border ${category === family.id ? 'border-brand-300 bg-brand-50/40' : 'border-slate-200 bg-white'}`}>
                        <button
                          type="button"
                          className={`flex w-full items-center justify-between rounded-[22px] px-3.5 py-3 text-right transition-colors ${category === family.id ? 'text-brand-700' : 'text-brand-navy'}`}
                          onClick={() => {
                            handlePartFamilyChange(family.id)
                            setPartTypeMenuOpen(false)
                            setPartFamilySearch('')
                          }}
                        >
                          <span className="flex min-w-0 items-center gap-3">
                            <img
                              src={partFamilyImageSrc(family)}
                              alt=""
                              aria-hidden="true"
                              className="h-[42px] w-[42px] flex-shrink-0 rounded-2xl border border-slate-200 bg-white object-cover shadow-sm"
                            />
                            <span className="min-w-0">
                              <span className="block truncate text-sm font-semibold">{family.label}</span>
                              <span className="mt-0.5 block text-xs text-slate-500">{(categoryCounts[family.id] || 0).toLocaleString()} תוצאות</span>
                            </span>
                          </span>
                          {category === family.id ? <Check className="h-4 w-4 flex-shrink-0 text-brand-600" /> : <ChevronRight className="h-4 w-4 flex-shrink-0 text-slate-300" />}
                        </button>
                        {Array.isArray(family.subcategories) && family.subcategories.length > 0 && (
                          <div className="grid grid-cols-2 gap-2 px-3 pb-3">
                            {family.subcategories.slice(0, 8).map((subcategory) => {
                              const selected = category === subcategory.id
                              const subCount = subcategoryCounts[subcategory.id] || 0
                              return (
                                <button
                                  type="button"
                                  key={subcategory.id}
                                  className={`rounded-xl border px-2 py-2 text-[11px] text-right leading-tight transition-colors ${selected ? 'border-brand-300 bg-white text-brand-700' : 'border-slate-200 bg-white/80 hover:bg-white text-slate-700'} ${subCount === 0 ? 'opacity-70' : ''}`}
                                  onClick={() => {
                                    handlePartFamilyChange(subcategory.id)
                                    setPartTypeMenuOpen(false)
                                    setPartFamilySearch('')
                                  }}
                                >
                                  <div className="font-medium">{subcategory.label}</div>
                                  <div className="mt-1 text-[10px] text-slate-400">{subCount.toLocaleString()} תוצאות</div>
                                </button>
                              )
                            })}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )
            }) : filteredPartFamilies.map((family) => (
              <button
                type="button"
                key={family.id}
                className={`flex w-full items-center justify-between rounded-[22px] border px-3.5 py-3 text-right transition-colors ${category === family.id ? 'border-brand-300 bg-brand-50 text-brand-700' : 'border-slate-200 bg-white text-brand-navy'} ${categoryCounts[family.id] === 0 ? 'opacity-60' : ''}`}
                onClick={() => {
                  handlePartFamilyChange(family.id)
                  setPartTypeMenuOpen(false)
                  setPartFamilySearch('')
                }}
              >
                <span className="flex min-w-0 items-center gap-3">
                  <img
                    src={partFamilyImageSrc(family)}
                    alt=""
                    aria-hidden="true"
                    className="h-[42px] w-[42px] flex-shrink-0 rounded-2xl border border-slate-200 bg-white object-cover shadow-sm"
                  />
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-semibold">{family.label}</span>
                    <span className="mt-0.5 block text-xs text-slate-500">{(categoryCounts[family.id] || 0).toLocaleString()} תוצאות</span>
                  </span>
                </span>
                {category === family.id ? <Check className="h-4 w-4 flex-shrink-0 text-brand-600" /> : <ChevronRight className="h-4 w-4 flex-shrink-0 text-slate-300" />}
              </button>
            ))}
          </div>
        </div>
      </MobileBottomSheet>

      {activeSearchPanel === 'vehicle' && (
        <div className="fixed inset-x-0 bottom-0 z-30 px-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] sm:hidden">
          <div className="rounded-[26px] border border-slate-200 bg-white/95 p-3 shadow-[0_-24px_60px_-32px_rgba(15,23,42,0.55)] backdrop-blur">
            <div className="mb-2 text-right">
              <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">חיפוש מהיר</p>
              <p className="mt-1 truncate text-sm font-semibold text-brand-navy">{vehicleSearchSummary || 'בחר יצרן, דגם, שנה וסוג חלק'}</p>
            </div>
            <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] gap-2">
              <button
                type="button"
                onClick={() => {
                  clearManual()
                  handlePartFamilyChange('')
                }}
                className="flex min-h-12 items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-3 text-sm font-semibold text-slate-700"
              >
                <X className="h-4 w-4" />
                נקה
              </button>
              <button
                type="button"
                onClick={() => search(0)}
                disabled={isLoading}
                className="flex min-h-12 items-center justify-center gap-2 rounded-2xl bg-brand-600 px-3 text-sm font-bold text-white shadow-sm disabled:opacity-60"
              >
                {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
                חפש עכשיו
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── BLOCK 3: Photo / Voice ── */}
      <div className={`bg-white rounded-2xl border border-gray-100 shadow-sm ${activeSearchPanel !== 'photo' ? 'hidden' : ''}`} style={{order: 3}}>
          {/* Header */}
          <div className="flex items-center gap-2 px-4 pt-4">
            <div className="w-8 h-8 rounded-lg bg-brand-100 flex items-center justify-center flex-shrink-0">
              <Camera className="w-4 h-4 text-brand-600" />
            </div>
            <h3 className="font-semibold text-brand-navy">חיפוש בתמונה / קול</h3>
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
                <button onClick={handleVoiceSearch} disabled={isLoading || !voiceTranscript.trim()} className="btn-primary flex items-center justify-center gap-2 whitespace-nowrap w-full sm:w-auto min-h-12 sm:min-h-11 rounded-xl">
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
            <h3 className="font-semibold text-brand-navy">זיהוי חלק מתמונה</h3>
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
            <h3 className="font-semibold text-brand-navy">חיפוש קולי</h3>
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
            <button onClick={handleVoiceSearch} disabled={isLoading || !voiceTranscript.trim()} className="btn-primary flex items-center justify-center gap-2 whitespace-nowrap w-full sm:w-auto min-h-12 sm:min-h-11 rounded-xl">
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
      <div className="space-y-6">
      {isLoading && !hasVisibleResults && (
        <div className="card p-4 flex items-center justify-center gap-3 text-brand-700 border border-brand-100 bg-brand-50/70">
          <Loader2 className="w-5 h-5 animate-spin text-brand-600" />
          <span className="text-sm font-medium">מחפש חלקים מתאימים...</span>
        </div>
      )}

      {isLoading && hasVisibleResults && (
        <div className="flex items-center justify-end gap-2 text-xs text-brand-700">
          <Loader2 className="w-4 h-4 animate-spin text-brand-600" />
          <span>מעדכן את התוצאות...</span>
        </div>
      )}

      {!isLoading && searched && selectedVehicle && fitmentStatus?.message && (
        <div
          className={`rounded-xl border px-4 py-3 text-sm flex items-center gap-2 ${
            fitmentStatus.verified
              ? 'bg-emerald-50 border-emerald-200 text-emerald-800'
              : 'bg-amber-50 border-amber-200 text-amber-800'
          }`}
        >
          <div className="flex items-start gap-2 w-full">
            {fitmentStatus.verified ? (
              <CheckCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            ) : (
              <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            )}
            <div className="min-w-0">
              <p className="font-medium">{fitmentStatus.message}</p>
              {(fitmentStatus.source || fitmentStatus.confidenceBucket || fitmentStatus.matchBasis) && (
                <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs opacity-90">
                  {fitmentStatus.source && (
                    <span className="px-2 py-0.5 rounded-full border border-current/30">
                      Source: {fitmentStatus.source}
                    </span>
                  )}
                  {fitmentStatus.confidenceBucket && (
                    <span className="px-2 py-0.5 rounded-full border border-current/30">
                      Confidence: {fitmentStatus.confidenceBucket}
                    </span>
                  )}
                  {fitmentStatus.matchBasis && (
                    <span className="truncate max-w-full">
                      Basis: {fitmentStatus.matchBasis}
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {!isLoading && searched && parts.length === 0 && (
        <div className="card p-8 text-center">
          <Package className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <h3 className="font-semibold text-gray-700 mb-2">
            {selectedVehicle && fitmentStatus && !fitmentStatus.verified
              ? 'No verified fitment data'
              : 'לא נמצאו חלקים'}
          </h3>
          <p className="text-sm text-gray-400 mb-5">
            {selectedVehicle && fitmentStatus && !fitmentStatus.verified
              ? `${selectedVehicle.manufacturer} ${selectedVehicle.model} ${selectedVehicle.year || ''} · ${fitmentStatus.message}`
              : category && query
              ? `לא נמצאו תוצאות עבור "${query}" בקטגוריה "${selectedPartFamilyLabel}"`
              : category
              ? `לא נמצאו תוצאות בקטגוריה "${selectedPartFamilyLabel}"`
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
                    onClick={() => handlePartFamilyChange('')}
                    className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-full bg-red-50 text-red-600 border border-red-200 hover:bg-red-100 transition-colors"
                  >
                    <X className="w-3 h-3" /> הסר פילטר: {selectedPartFamilyLabel}
                  </button>
                )}
                {suggestedPartFamilies.map((familyId) => {
                  const node = findPartCategoryNodeByValue(partFamilies, familyId)
                  return (
                    <button
                      key={familyId}
                      onClick={() => { handlePartFamilyChange(familyId) }}
                      className="inline-flex items-center gap-2 text-xs px-3 py-1.5 rounded-full bg-brand-50 text-brand-700 border border-brand-200 hover:bg-brand-100 transition-colors"
                    >
                      {node?.family ? (
                        <img
                          src={partFamilyImageSrc(node.family)}
                          alt=""
                          aria-hidden="true"
                          className="object-cover flex-shrink-0 rounded-full"
                          style={{ width: MANUFACTURER_CHIP_LOGO_SIZE, height: MANUFACTURER_CHIP_LOGO_SIZE }}
                        />
                      ) : null}
                      {categoryLabelForValue(partFamilies, familyId)}{(categoryCounts[familyId] || subcategoryCounts[familyId]) ? ` (${(categoryCounts[familyId] || subcategoryCounts[familyId]).toLocaleString()})` : ''}
                    </button>
                  )
                })}
              </div>
            </div>
          )}
          <div className="flex items-center justify-center gap-3 flex-wrap">
            {selectedVehicle && (
              <button onClick={() => {
                setParts([])
                setSearchResults(null)
                setSearched(true)
                setIsLoading(true)
                runSelectedVehicleExactSearch(selectedVehicle.id, '', category)
                  .catch(() => {})
                  .finally(() => setIsLoading(false))
              }} className="btn-ghost text-sm">
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

      {!photoFallbackMfr && !voiceFallbackMfr && parts.length > 0 && (
        <section className="space-y-4 rounded-[28px] border border-slate-200 bg-gradient-to-b from-white via-white to-slate-50/70 p-3 sm:p-5 shadow-[0_24px_80px_-40px_rgba(15,23,42,0.28)]">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
            <div className="min-w-0 flex-1 space-y-3">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-stretch">
                <div className="min-w-[200px] rounded-[24px] bg-slate-950 px-4 py-3.5 text-white shadow-lg shadow-slate-950/10">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-white/55">תוצאות חיפוש</p>
                  <p className="mt-2 text-3xl font-black leading-none">{totalCount.toLocaleString()}</p>
                  <p className="mt-2 text-xs text-white/70">{totalCount > PAGE_SIZE ? `עמוד ${page + 1} מתוך ${Math.ceil(totalCount / PAGE_SIZE)}` : 'כל התוצאות נטענו בעמוד זה'}</p>
                </div>
                <div className="grid flex-1 grid-cols-2 gap-3 sm:max-w-sm">
                  <div className="rounded-[24px] border border-emerald-200 bg-emerald-50 px-4 py-3">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-emerald-700/70">במלאי</p>
                    <p className="mt-2 text-2xl font-black text-emerald-800">{inStockCount.toLocaleString()}</p>
                    <p className="mt-1 text-xs text-emerald-700/80">זמינים להזמנה מיידית</p>
                  </div>
                  <div className="rounded-[24px] border border-amber-200 bg-amber-50 px-4 py-3">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-amber-700/70">על הזמנה</p>
                    <p className="mt-2 text-2xl font-black text-amber-800">{onOrderCount.toLocaleString()}</p>
                    <p className="mt-1 text-xs text-amber-700/80">הצעות עם זמן אספקה</p>
                  </div>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2.5">
                {selectedVehicle && parts.length > 0 && (() => {
                  const mfr = selectedVehicle.manufacturer?.toLowerCase() || ''
                  const matchCount = parts.filter(p => p.manufacturer?.toLowerCase().includes(mfr)).length
                  return matchCount > 0 ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-brand-200 bg-brand-50 px-3 py-1.5 text-xs font-semibold text-brand-700">
                      <Car className="h-3.5 w-3.5" />
                      {matchCount} מתאימים ל{selectedVehicle.manufacturer}
                    </span>
                  ) : null
                })()}
                {inStockCount > 0 && (
                  <button
                    onClick={() => setFilterAvail(filterAvail === 'in_stock' ? '' : 'in_stock')}
                    className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold transition-all ${
                      filterAvail === 'in_stock'
                        ? 'border-green-600 bg-green-600 text-white'
                        : 'border-green-200 bg-green-50 text-green-700 hover:bg-green-100'
                    }`}
                  >
                    <CheckCircle className="h-3.5 w-3.5" />
                    {inStockCount} במלאי
                  </button>
                )}
                {onOrderCount > 0 && (
                  <button
                    onClick={() => setFilterAvail(filterAvail === 'on_order' ? '' : 'on_order')}
                    className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold transition-all ${
                      filterAvail === 'on_order'
                        ? 'border-amber-600 bg-amber-600 text-white'
                        : 'border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100'
                    }`}
                  >
                    <Truck className="h-3.5 w-3.5" />
                    {onOrderCount} על הזמנה
                  </button>
                )}
                {[['Original','מקורי','bg-blue-600 text-white border-blue-600','bg-blue-50 text-blue-700 border-blue-200 hover:bg-blue-100'],
                  ['Aftermarket','חליפי','bg-amber-600 text-white border-amber-600','bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100'],
                  ['Refurbished','משופץ','bg-brand-600 text-white border-brand-600','bg-brand-50 text-brand-700 border-brand-200 hover:bg-brand-100'],
                ].filter(([k]) => typeCounts[k] > 0).map(([key, label, activeClass, inactiveClass]) => (
                  <button
                    key={key}
                    onClick={() => setFilterType(filterType === key ? '' : key)}
                    className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold transition-all ${
                      filterType === key ? activeClass : inactiveClass
                    }`}
                  >
                    {label} ({typeCounts[key]})
                  </button>
                ))}
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2 rounded-[24px] border border-slate-200 bg-white px-3 py-3 shadow-sm xl:justify-end">
              <button
                title="העתק קישור לחיפוש"
                onClick={() => {
                  const url = `${window.location.origin}/parts?search=${encodeURIComponent(query)}${category ? `&category=${encodeURIComponent(category)}` : ''}`
                  navigator.clipboard.writeText(url).then(() => toast.success('קישור הועתק! 🔗'))
                }}
                className="inline-flex h-11 w-11 items-center justify-center rounded-2xl border border-slate-200 bg-slate-50 text-slate-500 transition-colors hover:border-brand-300 hover:text-brand-600"
              >
                <Link2 className="h-4 w-4" />
              </button>
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value)}
                className="min-h-11 rounded-2xl border border-slate-200 bg-slate-50 px-3 text-sm text-brand-navy outline-none focus:border-brand-300 focus:ring-2 focus:ring-brand-200"
              >
                <option value="availability">מיין: זמינות</option>
                <option value="price_asc">מיין: מחיר ↑</option>
                <option value="price_desc">מיין: מחיר ↓</option>
                <option value="name">מיין: שם</option>
              </select>
              {searchResults && (
                <div className="flex items-center overflow-hidden rounded-2xl border border-slate-200 bg-slate-50 text-sm" title="ספקים לסוג">
                  <button
                    onClick={() => { const n = Math.max(1, perType - 1); setPerType(n); setTimeout(() => search(0), 0) }}
                    className="px-3 py-2.5 font-bold leading-none text-slate-600 hover:bg-slate-100"
                  >−</button>
                  <span className="px-3 py-2 text-sm font-semibold text-slate-700">{perType}</span>
                  <button
                    onClick={() => { const n = Math.min(10, perType + 1); setPerType(n); setTimeout(() => search(0), 0) }}
                    className="px-3 py-2.5 font-bold leading-none text-slate-600 hover:bg-slate-100"
                  >+</button>
                  <span className="hidden pr-3 text-xs text-slate-400 sm:inline">ספקים</span>
                </div>
              )}
            </div>
          </div>

          {searchResults ? (() => {
            const activeKeys = ['original', 'oem', 'aftermarket'].filter(k => searchResults[k]?.part)
            const colClass = activeKeys.length === 1 ? 'max-w-2xl mx-auto' : activeKeys.length === 2 ? 'grid grid-cols-1 xl:grid-cols-2 gap-5' : 'grid grid-cols-1 xl:grid-cols-3 gap-5'
            return activeKeys.length > 0 ? (
              <div className={colClass}>
                {activeKeys.map((key) => (
                  <TypeSection key={key} typeKey={key} data={searchResults[key]} onAddToCart={addItem} />
                ))}
              </div>
            ) : (
              <div className="py-12 text-center text-sm text-slate-400">לא נמצאו תוצאות</div>
            )
          })() : (
            <>
              <div className="grid grid-cols-1 gap-4 sm:gap-5 md:grid-cols-2 xl:grid-cols-3">
                {displayParts.map((p) => <PartCard key={p.id} part={p} onAddToCart={addItem} />)}
                {displayParts.length === 0 && filterAvail && (
                  <div className="col-span-3 rounded-[24px] border border-dashed border-slate-200 py-8 text-center text-sm text-slate-400">
                    אין חלקים עם סטטוס "{filterAvail === 'in_stock' ? 'במלאי' : 'על הזמנה'}" בדף זה
                    <button onClick={() => setFilterAvail('')} className="mr-2 font-semibold text-brand-600 underline">הצג הכל</button>
                  </div>
                )}
              </div>
              {totalCount > PAGE_SIZE && (
                <div className="flex flex-wrap items-center justify-center gap-2 border-t border-slate-100 pt-4">
                  <button
                    disabled={page === 0}
                    onClick={() => search(page - 1)}
                    className="rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition-colors disabled:opacity-40"
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
                        className={`min-w-11 rounded-2xl border px-3 py-2.5 text-sm font-bold transition-all ${
                          pg === page ? 'border-brand-600 bg-brand-600 text-white' : 'border-slate-200 bg-white text-slate-700 hover:border-brand-300'
                        }`}
                      >
                        {pg + 1}
                      </button>
                    ) : null
                  })}
                  <button
                    disabled={page >= Math.ceil(totalCount / PAGE_SIZE) - 1}
                    onClick={() => search(page + 1)}
                    className="rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition-colors disabled:opacity-40"
                  >
                    הבא →
                  </button>
                </div>
              )}
            </>
          )}
        </section>
      )}
      </div>
    </div>
  )
}
