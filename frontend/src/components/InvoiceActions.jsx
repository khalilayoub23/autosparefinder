import { useState, useRef, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { Download, Eye, Printer, Loader2, X, Share2, Check, FileText, ChevronDown } from 'lucide-react'
import { ordersApi } from '../api/orders'
import toast from 'react-hot-toast'

export default function InvoiceActions({ orderId, orderNumber, compact = false, buttonClassName = '' }) {
  const [loading, setLoading]       = useState(null)
  const [previewUrl, setPreviewUrl] = useState(null)
  const [copied, setCopied]         = useState(false)
  const [open, setOpen]             = useState(false)
  const [dropPos, setDropPos]       = useState({ top: 0, left: 0, width: 0 })
  const btnRef                      = useRef(null)

  // Reposition dropdown whenever it opens
  useEffect(() => {
    if (!open || !btnRef.current) return
    const r = btnRef.current.getBoundingClientRect()
    setDropPos({
      top:   r.bottom + window.scrollY + 6,
      left:  r.left   + window.scrollX,
      width: Math.max(r.width, 210),
    })
  }, [open])

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (btnRef.current && btnRef.current.contains(e.target)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  // Reposition on scroll/resize
  useEffect(() => {
    if (!open) return
    const update = () => {
      if (!btnRef.current) return
      const r = btnRef.current.getBoundingClientRect()
      setDropPos({ top: r.bottom + window.scrollY + 6, left: r.left + window.scrollX, width: Math.max(r.width, 210) })
    }
    window.addEventListener('scroll', update, true)
    window.addEventListener('resize', update)
    return () => { window.removeEventListener('scroll', update, true); window.removeEventListener('resize', update) }
  }, [open])

  const fetchBlob = async () => {
    const { data } = await ordersApi.invoice(orderId)
    return URL.createObjectURL(new Blob([data], { type: 'application/pdf' }))
  }

  const handlePreview = async () => {
    setOpen(false); setLoading('preview')
    try   { setPreviewUrl(await fetchBlob()) }
    catch { toast.error('שגיאה בטעינת החשבונית') }
    finally { setLoading(null) }
  }
  const closePreview = () => { if (previewUrl) URL.revokeObjectURL(previewUrl); setPreviewUrl(null) }

  const handlePrint = async () => {
    setOpen(false); setLoading('print')
    try {
      const url = await fetchBlob()
      const win = window.open(url, '_blank')
      if (win) win.addEventListener('load', () => { win.focus(); win.print() })
      else toast.error('אפשר חלונות קופצים כדי להדפיס')
    } catch { toast.error('שגיאה בהדפסת החשבונית') }
    finally { setLoading(null) }
  }

  const handleDownload = async () => {
    setOpen(false); setLoading('download')
    try {
      const url = await fetchBlob()
      const a = document.createElement('a')
      a.href = url; a.download = `invoice-${orderNumber}.pdf`; a.click()
      setTimeout(() => URL.revokeObjectURL(url), 10000)
      toast.success('החשבונית הורדה')
    } catch { toast.error('שגיאה בהורדת החשבונית') }
    finally { setLoading(null) }
  }

  const handleShare = async () => {
    setOpen(false); setLoading('share')
    try {
      const { data } = await ordersApi.invoice(orderId)
      const blob = new Blob([data], { type: 'application/pdf' })
      const file = new File([blob], `invoice-${orderNumber}.pdf`, { type: 'application/pdf' })
      if (navigator.canShare?.({ files: [file] })) {
        await navigator.share({ title: `חשבונית ${orderNumber}`, files: [file] })
      } else if (navigator.share) {
        await navigator.share({ title: `חשבונית ${orderNumber}`, url: window.location.href })
      } else {
        await navigator.clipboard.writeText(window.location.href)
        setCopied(true); setTimeout(() => setCopied(false), 2500)
        toast.success('הקישור הועתק ללוח')
      }
    } catch (e) { if (e?.name !== 'AbortError') toast.error('שגיאה בשיתוף') }
    finally { setLoading(null) }
  }

  const isLoading = !!loading

  const actions = [
    { key: 'preview',  label: 'צפה בחשבונית',     icon: <Eye className="w-4 h-4" />,     onClick: handlePreview  },
    { key: 'print',    label: 'הדפס חשבונית',      icon: <Printer className="w-4 h-4" />, onClick: handlePrint    },
    { key: 'download', label: 'הורד חשבונית PDF',  icon: <Download className="w-4 h-4" />,onClick: handleDownload },
    {
      key: 'share',
      label: copied ? 'הועתק!' : 'שתף חשבונית',
      icon: copied ? <Check className="w-4 h-4 text-green-500" /> : <Share2 className="w-4 h-4" />,
      onClick: handleShare,
    },
  ]

  const dropdown = open && createPortal(
    <div
      style={{ position: 'absolute', top: dropPos.top, left: dropPos.left, minWidth: dropPos.width, zIndex: 9999 }}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <div className="bg-white rounded-xl shadow-2xl border border-gray-100 overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-2.5 bg-orange-50 border-b border-orange-100">
          <FileText className="w-4 h-4 text-orange-600" />
          <span className="text-xs font-semibold text-orange-700 tracking-wide">חשבונית מס / קבלה</span>
        </div>
        {actions.map((a) => (
          <button
            key={a.key}
            onClick={a.onClick}
            disabled={isLoading}
            className="w-full flex items-center gap-3 px-4 py-2.5 text-sm font-medium text-gray-700 hover:bg-orange-50 hover:text-orange-700 transition-colors disabled:opacity-50"
          >
            {loading === a.key ? <Loader2 className="w-4 h-4 animate-spin text-orange-500" /> : a.icon}
            {a.label}
          </button>
        ))}
      </div>
    </div>,
    document.body
  )

  return (
    <>
      {/* ── Trigger button ── */}
      <button
        ref={btnRef}
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o) }}
        disabled={isLoading}
        className={`btn-secondary text-sm flex items-center gap-1 text-orange-600 hover:bg-orange-50 border-orange-200 disabled:opacity-50 ${buttonClassName}`}
      >
        {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" />}
        חשבונית
        <ChevronDown className={`w-3.5 h-3.5 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
      </button>

      {dropdown}

      {/* ── Preview Modal ── */}
      {previewUrl && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
          onClick={closePreview}
        >
          <div
            className="relative bg-white rounded-2xl shadow-2xl flex flex-col"
            style={{ width: 'min(900px, 95vw)', height: 'min(1100px, 90vh)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-5 py-3 border-b border-gray-100">
              <div className="flex items-center gap-2">
                <FileText className="w-4 h-4 text-orange-600" />
                <span className="font-semibold text-gray-800">חשבונית — {orderNumber}</span>
              </div>
              <div className="flex items-center gap-2">
                <button onClick={handlePrint} disabled={isLoading} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border border-gray-200 hover:bg-gray-50 text-gray-700 transition-colors disabled:opacity-50">
                  {loading === 'print' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Printer className="w-3.5 h-3.5" />} הדפס
                </button>
                <button onClick={handleDownload} disabled={isLoading} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-orange-600 hover:bg-orange-700 text-white transition-colors disabled:opacity-50">
                  {loading === 'download' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />} הורד
                </button>
                <button onClick={handleShare} disabled={isLoading} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border border-gray-200 hover:bg-gray-50 text-gray-700 transition-colors disabled:opacity-50">
                  {loading === 'share' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : copied ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Share2 className="w-3.5 h-3.5" />}
                  {copied ? 'הועתק!' : 'שתף'}
                </button>
                <button onClick={closePreview} className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-500 transition-colors">
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>
            <iframe src={previewUrl} title="חשבונית" className="flex-1 w-full rounded-b-2xl" style={{ border: 'none' }} />
          </div>
        </div>
      )}
    </>
  )
}
