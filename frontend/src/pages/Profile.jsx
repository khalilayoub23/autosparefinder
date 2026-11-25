import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'
import { useVehicleStore } from '../stores/vehicleStore'
import api from '../api/client'
import { User, Car, Lock, Bell, ChevronRight, Loader2, Save, Plus, Trash2, Star, ShieldCheck, FileText, ExternalLink, MapPin, CheckCircle2, XCircle } from 'lucide-react'
import toast from 'react-hot-toast'

function Section({ title, icon: Icon, children }) {
  return (
    <div className="card p-6">
      <div className="flex items-center gap-2 mb-5">
        <Icon className="w-5 h-5 text-[#38BDF8]" />
        <h2 className="font-bold text-white">{title}</h2>
      </div>
      {children}
    </div>
  )
}

export default function Profile() {
  const { user, fetchMe, logout } = useAuthStore()
  const { vehicles, loadVehicles, addVehicle, removeVehicle, setPrimary } = useVehicleStore()
  const [form, setForm] = useState({ full_name: '', phone: '', address_line1: '', apartment: '', city: '', postal_code: '' })
  const [passwords, setPasswords] = useState({ current: '', new: '', confirm: '' })
  const [newPlate, setNewPlate] = useState('')
  const [saving, setSaving] = useState(false)
  const [savingPass, setSavingPass] = useState(false)
  const [geoResult, setGeoResult] = useState(null)  // null | 'loading' | { ok, display, lat, lon } | 'error'
  const [geoChecking, setGeoChecking] = useState(false)
  const [account, setAccount] = useState({ email: '', verified: null })
  const [resending, setResending] = useState(false)
  useEffect(() => {
    api.get('/auth/me')
      .then(({ data }) => setAccount({ email: data.email || data.user?.email || '', verified: !!(data.is_verified ?? data.user?.is_verified) }))
      .catch(() => {})
  }, [])
  const resendVerify = async () => {
    setResending(true)
    try { await api.post('/auth/resend-verification'); toast.success('מייל אימות נשלח 📧 — בדוק את תיבת הדואר') }
    catch { toast.error('לא הצלחנו לשלוח כרגע') }
    finally { setResending(false) }
  }

  const NOTIF_KEYS = [
    { key: 'order_update', label: 'עדכוני הזמנות', desc: 'שינוי סטטוס, אישור, משלוח' },
    { key: 'marketing',   label: 'מבצעים ושיווק',  desc: 'הנחות, מוצרים חדשים' },
    { key: 'message',     label: 'הודעות צ׳אט',    desc: 'תגובות מסוכן שירות הלקוחות' },
  ]
  const [notifPrefs, setNotifPrefs] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem('notifPrefs') || '{}')
      return { order_update: true, marketing: true, message: true, ...saved }
    } catch { return { order_update: true, marketing: true, message: true } }
  })
  const toggleNotif = (key) => {
    const updated = { ...notifPrefs, [key]: !notifPrefs[key] }
    setNotifPrefs(updated)
    localStorage.setItem('notifPrefs', JSON.stringify(updated))
    toast.success(updated[key] ? 'ההתראות הופעלו' : 'ההתראות הושבתו')
  }

  useEffect(() => {
    fetchMe()
    loadVehicles()
    api.get('/profile').then(({ data }) => {
      setForm({
        full_name: data.user?.full_name || '',
        phone: data.user?.phone || '',
        address_line1: data.profile?.address_line1 || data.profile?.address || '',
        apartment: data.profile?.address_line2 || data.profile?.apartment || '',
        city: data.profile?.city || '',
        postal_code: data.profile?.postal_code || '',
      })
    }).catch(() => {})
  }, [])

  const saveProfile = async (e) => {
    e.preventDefault()
    setSaving(true)
    try {
      await api.put('/profile', null, { params: {
        full_name: form.full_name,
        phone: form.phone || undefined,
        address_line1: form.address_line1,
        address_line2: form.apartment,
        city: form.city,
        postal_code: form.postal_code,
      }})
      await fetchMe()
      toast.success('הפרופיל עודכן')
    } catch (err) {
      const detail = err.response?.data?.detail
      toast.error(typeof detail === 'string' ? detail : detail?.message || 'שגיאה בשמירה')
    }
    finally { setSaving(false) }
  }

  const validateAddress = async () => {
    const street = [form.address_line1, form.apartment].filter(Boolean).join(' ')
    if (!street && !form.city) { toast.error('הכנס כתובת לפני האימות'); return }
    setGeoChecking(true)
    setGeoResult(null)
    try {
      const params = new URLSearchParams({
        format: 'json',
        limit: '1',
        countrycodes: 'il',
        addressdetails: '1',
        ...(street && { street }),
        ...(form.city && { city: form.city }),
        ...(form.postal_code && { postalcode: form.postal_code }),
      })
      const res = await fetch(`https://nominatim.openstreetmap.org/search?${params}`, {
        headers: { 'Accept-Language': 'he', 'User-Agent': 'AutoSpare/1.0' }
      })
      const data = await res.json()
      if (data.length > 0) {
        setGeoResult({ ok: true, display: data[0].display_name, lat: data[0].lat, lon: data[0].lon })
      } else {
        setGeoResult({ ok: false })
      }
    } catch {
      setGeoResult({ ok: false })
    } finally {
      setGeoChecking(false)
    }
  }

  const changePassword = async (e) => {
    e.preventDefault()
    if (passwords.new !== passwords.confirm) { toast.error('הסיסמאות אינן תואמות'); return }
    if (passwords.new.length < 8) { toast.error('סיסמה קצרה מדי'); return }
    setSavingPass(true)
    try {
      await api.post('/auth/change-password', { current_password: passwords.current, new_password: passwords.new })
      toast.success('הסיסמה שונתה')
      setPasswords({ current: '', new: '', confirm: '' })
    } catch (err) {
      toast.error(err.response?.data?.error || 'שגיאה')
    } finally { setSavingPass(false) }
  }

  const handleAddVehicle = async () => {
    if (!newPlate.trim()) return
    try {
      await addVehicle(newPlate.trim())
      setNewPlate('')
      toast.success('רכב נוסף')
    } catch { toast.error('לא הצלחנו לזהות את הרכב') }
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="section-title">הפרופיל שלי</h1>
      </div>

      {account.verified !== null && (
        <div className={`flex items-center justify-between gap-3 rounded-2xl border px-4 py-3 ${account.verified ? 'border-[rgba(34,197,94,0.35)] bg-[rgba(34,197,94,0.12)]' : 'border-[rgba(245,158,11,0.35)] bg-[rgba(245,158,11,0.12)]'}`}>
          <div className="flex items-center gap-2 text-sm">
            <span className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-[#0F1218] ${account.verified ? 'bg-emerald-500' : 'bg-amber-500'}`}>
              {account.verified ? '✓' : '!'}
            </span>
            <span className={account.verified ? 'text-[#4ADE80]' : 'text-[#FBBF24]'}>
              {account.verified ? 'המייל שלך מאומת' : `המייל ${account.email} עדיין לא אומת`}
            </span>
          </div>
          {!account.verified && (
            <button type="button" onClick={resendVerify} disabled={resending}
              className="rounded-brand bg-amber-500 px-3 py-1.5 text-xs font-bold text-white hover:bg-amber-600 disabled:opacity-50">
              {resending ? 'שולח…' : 'שלח מייל אימות'}
            </button>
          )}
        </div>
      )}

      <div className="hidden">
        <p className="text-[#94A3B8] mt-1">{user?.email}</p>
      </div>

      {/* Personal info */}
      <Section title="פרטים אישיים" icon={User}>
        <form onSubmit={saveProfile} className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label htmlFor="profile-f1" className="block text-sm font-medium text-[#E2E8F0] mb-1">שם מלא</label>
              <input id="profile-f1" className="input-field" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
            </div>
            <div>
              <label htmlFor="profile-f2" className="block text-sm font-medium text-[#E2E8F0] mb-1">אימייל</label>
              <input id="profile-f2" className="input-field bg-[#1E2535]" value={user?.email || ''} readOnly dir="ltr" />
            </div>
            <div>
              <label htmlFor="profile-f3" className="block text-sm font-medium text-[#E2E8F0] mb-1">טלפון</label>
              <input id="profile-f3" className="input-field" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} dir="ltr" placeholder="972501234567" />
            </div>
            <div>
              <label htmlFor="profile-f4" className="block text-sm font-medium text-[#E2E8F0] mb-1">עיר</label>
              <input id="profile-f4" className="input-field" value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} />
            </div>
            <div className="col-span-full">
              <label htmlFor="profile-f5" className="block text-sm font-medium text-[#E2E8F0] mb-1">כתובת</label>
              <input id="profile-f5" className="input-field" value={form.address_line1} placeholder="רחוב ומספר בית" onChange={(e) => { setForm({ ...form, address_line1: e.target.value }); setGeoResult(null) }} />
            </div>
            <div>
              <label htmlFor="profile-f6" className="block text-sm font-medium text-[#E2E8F0] mb-1">מספר דירה</label>
              <input id="profile-f6" className="input-field" value={form.apartment} placeholder="דירה / קומה" onChange={(e) => { setForm({ ...form, apartment: e.target.value }); setGeoResult(null) }} />
            </div>
            <div>
              <label htmlFor="profile-f7" className="block text-sm font-medium text-[#E2E8F0] mb-1">מיקוד</label>
              <input id="profile-f7" className="input-field" value={form.postal_code} onChange={(e) => { setForm({ ...form, postal_code: e.target.value }); setGeoResult(null) }} />
            </div>
            {/* Geo validation */}
            <div className="col-span-full">
              <button
                type="button"
                onClick={validateAddress}
                disabled={geoChecking}
                className="flex items-center gap-2 text-sm px-3 py-1.5 rounded-brand border border-[rgba(14,165,233,0.35)] text-[#38BDF8] hover:bg-[#252D3D] transition-colors disabled:opacity-50"
              >
                {geoChecking ? <Loader2 className="w-4 h-4 animate-spin" /> : <MapPin className="w-4 h-4" />}
                אמת כתובת
              </button>
              {geoResult && geoResult.ok && (
                <div className="mt-2 flex items-start gap-2 p-3 rounded-xl bg-[rgba(34,197,94,0.12)] border border-[rgba(34,197,94,0.35)]">
                  <CheckCircle2 className="w-4 h-4 text-[#4ADE80] mt-0.5 shrink-0" />
                  <div className="text-xs text-[#4ADE80]">
                    <p className="font-medium mb-0.5">הכתובת אומתה ✓</p>
                    <p className="text-[#4ADE80]">{geoResult.display}</p>
                    <a
                      href={`https://www.openstreetmap.org/?mlat=${geoResult.lat}&mlon=${geoResult.lon}&zoom=17`}
                      target="_blank" rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 mt-1 underline text-[#4ADE80]"
                    >
                      <ExternalLink className="w-3 h-3" /> הצג במפה
                    </a>
                  </div>
                </div>
              )}
              {geoResult && !geoResult.ok && (
                <div className="mt-2 flex items-center gap-2 p-3 rounded-xl bg-[rgba(239,68,68,0.12)] border border-[rgba(239,68,68,0.35)]">
                  <XCircle className="w-4 h-4 text-red-500 shrink-0" />
                  <p className="text-xs text-[#F87171]">הכתובת לא נמצאה — בדוק שגיאות ונסה שוב</p>
                </div>
              )}
            </div>
          </div>
          <div className="flex items-center gap-3">
            {user?.is_verified ? (
              <span className="badge bg-[rgba(34,197,94,0.12)] text-[#4ADE80]">✓ מאומת</span>
            ) : (
              <span className="badge bg-yellow-500/15 text-yellow-300">⚠ לא מאומת</span>
            )}
          </div>
          <button type="submit" disabled={saving} className="btn-primary flex items-center gap-2">
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            שמור שינויים
          </button>
        </form>
      </Section>

      {/* Vehicles */}
      <Section title="הרכבים שלי" icon={Car}>
        <div className="space-y-2 mb-4">
          {vehicles.length === 0 && <p className="text-sm text-[#94A3B8]">לא נוספו רכבים</p>}
          {vehicles.map((v) => (
            <div key={v.id} className="flex items-center justify-between p-3 rounded-xl border border-[rgba(148,163,184,0.18)] bg-[#1E2535]">
              <div className="flex items-center gap-2">
                <Car className="w-4 h-4 text-[#38BDF8]" />
                <span className="text-sm font-medium text-white">{v.nickname || `${v.manufacturer} ${v.model}`}</span>
                <span className="text-xs text-[#94A3B8]">{v.year}</span>
                {v.is_primary && <span className="badge bg-[rgba(14,165,233,0.12)] text-[#38BDF8] text-xs">ראשי</span>}
              </div>
              <div className="flex items-center gap-2">
                {!v.is_primary && (
                  <button onClick={() => setPrimary(v.id)} title="הגדר כראשי" className="p-1.5 rounded-lg hover:bg-yellow-500/15 text-yellow-500">
                    <Star className="w-3.5 h-3.5" />
                  </button>
                )}
                <button aria-label="מחק" onClick={() => removeVehicle(v.id)} className="p-1.5 rounded-lg hover:bg-[rgba(239,68,68,0.12)] text-red-400">
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
        <div className="flex gap-2">
          <input className="input-field flex-1" placeholder="לוחית רישוי" dir="ltr" value={newPlate} onChange={(e) => setNewPlate(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && handleAddVehicle()} />
          <button onClick={handleAddVehicle} className="btn-secondary flex items-center gap-2 whitespace-nowrap">
            <Plus className="w-4 h-4" /> הוסף
          </button>
        </div>
      </Section>

      {/* Change password */}
      <Section title="שינוי סיסמה" icon={Lock}>
        <form onSubmit={changePassword} className="space-y-4">
          <div>
            <label htmlFor="profile-f8" className="block text-sm font-medium text-[#E2E8F0] mb-1">סיסמה נוכחית</label>
            <input id="profile-f8" type="password" dir="ltr" className="input-field" value={passwords.current} onChange={(e) => setPasswords({ ...passwords, current: e.target.value })} required />
          </div>
          <div>
            <label htmlFor="profile-f9" className="block text-sm font-medium text-[#E2E8F0] mb-1">סיסמה חדשה</label>
            <input id="profile-f9" type="password" dir="ltr" className="input-field" value={passwords.new} onChange={(e) => setPasswords({ ...passwords, new: e.target.value })} required />
          </div>
          <div>
            <label htmlFor="profile-f10" className="block text-sm font-medium text-[#E2E8F0] mb-1">אישור סיסמה חדשה</label>
            <input id="profile-f10" type="password" dir="ltr" className="input-field" value={passwords.confirm} onChange={(e) => setPasswords({ ...passwords, confirm: e.target.value })} required />
          </div>
          <button type="submit" disabled={savingPass} className="btn-primary flex items-center gap-2">
            {savingPass ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
            שנה סיסמה
          </button>
        </form>
      </Section>

      {/* Notification preferences */}
      <Section title="העדפות התראות" icon={Bell}>
        <div className="space-y-3">
          {NOTIF_KEYS.map(({ key, label, desc }) => (
            <div key={key} className="flex items-center justify-between py-2 border-b border-brand-border last:border-0">
              <div>
                <p className="text-sm font-medium text-white">{label}</p>
                <p className="text-xs text-[#94A3B8]">{desc}</p>
              </div>
              <button
                role="switch"
                aria-checked={!!notifPrefs[key]}
                aria-label={label}
                onClick={() => toggleNotif(key)}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors shrink-0 ${
                  notifPrefs[key] ? 'bg-brand-500' : 'bg-brand-50'
                }`}
              >
                <span className={`inline-block h-4 w-4 transform rounded-full bg-[#1E2535] shadow transition-transform ${
                  notifPrefs[key] ? 'translate-x-6' : 'translate-x-1'
                }`} />
              </button>
            </div>
          ))}
        </div>
      </Section>

      {/* Privacy & Legal */}
      <Section title="פרטיות ומשפט" icon={ShieldCheck}>
        <div className="space-y-3">
          <a
            href="/privacy"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-between p-3 rounded-xl border border-[rgba(148,163,184,0.18)] hover:bg-[#1E2535] transition-colors"
          >
            <div className="flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-[#38BDF8]" />
              <span className="text-sm font-medium text-white">מדיניות הפרטיות</span>
            </div>
            <ExternalLink className="w-3.5 h-3.5 text-[#94A3B8]" />
          </a>
          <a
            href="/terms"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-between p-3 rounded-xl border border-[rgba(148,163,184,0.18)] hover:bg-[#1E2535] transition-colors"
          >
            <div className="flex items-center gap-2">
              <FileText className="w-4 h-4 text-[#38BDF8]" />
              <span className="text-sm font-medium text-white">תנאי השימוש</span>
            </div>
            <ExternalLink className="w-3.5 h-3.5 text-[#94A3B8]" />
          </a>
          <a
            href="/refund"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-between p-3 rounded-xl border border-[rgba(148,163,184,0.18)] hover:bg-[#1E2535] transition-colors"
          >
            <div className="flex items-center gap-2">
              <FileText className="w-4 h-4 text-[#38BDF8]" />
              <span className="text-sm font-medium text-white">מדיניות ביטולים והחזרות</span>
            </div>
            <ExternalLink className="w-3.5 h-3.5 text-[#94A3B8]" />
          </a>
          <div className="mt-2 p-3 bg-[#1E2535] rounded-xl text-xs text-[#94A3B8] leading-relaxed">
            <p className="font-medium text-[#E2E8F0] mb-1">הנתונים שאנו שומרים עליך:</p>
            <ul className="list-disc list-inside space-y-0.5">
              <li>שם, אימייל, טלפון (מוצפנים, לא משותפים)</li>
              <li>היסטריית הזמנות (7 שנים לפי חוק)</li>
              <li>פרטי רכב (לצורך חיפוש חלפים בלבד)</li>
            </ul>
            <p className="mt-2">לבקשת מחיקת נתונים או עיין בהם, פנה/י ל: <a href="mailto:info@autosparefinder.co.il" className="text-[#38BDF8] underline">info@autosparefinder.co.il</a></p>
          </div>
        </div>
      </Section>

      {/* Danger zone */}
      <div className="card p-6 border-[rgba(239,68,68,0.35)]">
        <h2 className="font-bold text-[#F87171] mb-3">אזור סכנה</h2>
        <button onClick={() => { logout(); window.location.href = '/login' }} className="text-sm text-[#F87171] hover:text-[#F87171] font-medium border border-[rgba(239,68,68,0.35)] px-4 py-2 rounded-lg hover:bg-[rgba(239,68,68,0.12)]">
          התנתק מכל המכשירים
        </button>
      </div>
    </div>
  )
}
