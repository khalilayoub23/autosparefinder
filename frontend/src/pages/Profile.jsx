import { useState, useEffect } from 'react'
import { useAuthStore } from '../stores/authStore'
import { useVehicleStore } from '../stores/vehicleStore'
import api from '../api/client'
import { User, Car, Lock, Bell, ChevronRight, Loader2, Save, Plus, Trash2, Star } from 'lucide-react'
import toast from 'react-hot-toast'

function Section({ title, icon: Icon, children }) {
  return (
    <div className="card p-6">
      <div className="flex items-center gap-2 mb-5">
        <Icon className="w-5 h-5 text-brand-600" />
        <h2 className="font-bold text-gray-900">{title}</h2>
      </div>
      {children}
    </div>
  )
}

export default function Profile() {
  const { user, fetchMe, logout } = useAuthStore()
  const { vehicles, loadVehicles, addVehicle, removeVehicle, setPrimary } = useVehicleStore()
  const [form, setForm] = useState({ full_name: '', address_line1: '', city: '', postal_code: '' })
  const [passwords, setPasswords] = useState({ current: '', new: '', confirm: '' })
  const [newPlate, setNewPlate] = useState('')
  const [saving, setSaving] = useState(false)
  const [savingPass, setSavingPass] = useState(false)

  useEffect(() => {
    fetchMe()
    loadVehicles()
    api.get('/profile').then(({ data }) => {
      setForm({
        full_name: data.user?.full_name || '',
        address_line1: data.profile?.address || '',
        city: data.profile?.city || '',
        postal_code: data.profile?.postal_code || '',
      })
    }).catch(() => {})
  }, [])

  const saveProfile = async (e) => {
    e.preventDefault()
    setSaving(true)
    try {
      await api.put('/profile', null, { params: form })
      await fetchMe()
      toast.success('הפרופיל עודכן')
    } catch { toast.error('שגיאה בשמירה') }
    finally { setSaving(false) }
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
        <p className="text-gray-500 mt-1">{user?.email}</p>
      </div>

      {/* Personal info */}
      <Section title="פרטים אישיים" icon={User}>
        <form onSubmit={saveProfile} className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">שם מלא</label>
              <input className="input-field" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">אימייל</label>
              <input className="input-field bg-gray-50" value={user?.email || ''} readOnly dir="ltr" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">טלפון</label>
              <input className="input-field bg-gray-50" value={user?.phone || ''} readOnly dir="ltr" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">עיר</label>
              <input className="input-field" value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} />
            </div>
            <div className="col-span-full">
              <label className="block text-sm font-medium text-gray-700 mb-1">כתובת</label>
              <input className="input-field" value={form.address_line1} onChange={(e) => setForm({ ...form, address_line1: e.target.value })} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">מיקוד</label>
              <input className="input-field" value={form.postal_code} onChange={(e) => setForm({ ...form, postal_code: e.target.value })} />
            </div>
          </div>
          <div className="flex items-center gap-3">
            {user?.is_verified ? (
              <span className="badge bg-green-100 text-green-700">✓ מאומת</span>
            ) : (
              <span className="badge bg-yellow-100 text-yellow-700">⚠ לא מאומת</span>
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
          {vehicles.length === 0 && <p className="text-sm text-gray-400">לא נוספו רכבים</p>}
          {vehicles.map((v) => (
            <div key={v.id} className="flex items-center justify-between p-3 rounded-xl border border-gray-200 bg-gray-50">
              <div className="flex items-center gap-2">
                <Car className="w-4 h-4 text-brand-500" />
                <span className="text-sm font-medium text-gray-900">{v.nickname || `${v.manufacturer} ${v.model}`}</span>
                <span className="text-xs text-gray-400">{v.year}</span>
                {v.is_primary && <span className="badge bg-brand-50 text-brand-600 text-xs">ראשי</span>}
              </div>
              <div className="flex items-center gap-2">
                {!v.is_primary && (
                  <button onClick={() => setPrimary(v.id)} title="הגדר כראשי" className="p-1.5 rounded-lg hover:bg-yellow-100 text-yellow-500">
                    <Star className="w-3.5 h-3.5" />
                  </button>
                )}
                <button onClick={() => removeVehicle(v.id)} className="p-1.5 rounded-lg hover:bg-red-100 text-red-400">
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
            <label className="block text-sm font-medium text-gray-700 mb-1">סיסמה נוכחית</label>
            <input type="password" dir="ltr" className="input-field" value={passwords.current} onChange={(e) => setPasswords({ ...passwords, current: e.target.value })} required />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">סיסמה חדשה</label>
            <input type="password" dir="ltr" className="input-field" value={passwords.new} onChange={(e) => setPasswords({ ...passwords, new: e.target.value })} required />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">אישור סיסמה חדשה</label>
            <input type="password" dir="ltr" className="input-field" value={passwords.confirm} onChange={(e) => setPasswords({ ...passwords, confirm: e.target.value })} required />
          </div>
          <button type="submit" disabled={savingPass} className="btn-primary flex items-center gap-2">
            {savingPass ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
            שנה סיסמה
          </button>
        </form>
      </Section>

      {/* Danger zone */}
      <div className="card p-6 border-red-200">
        <h2 className="font-bold text-red-600 mb-3">אזור סכנה</h2>
        <button onClick={() => { logout(); window.location.href = '/login' }} className="text-sm text-red-600 hover:text-red-700 font-medium border border-red-200 px-4 py-2 rounded-lg hover:bg-red-50">
          התנתק מכל המכשירים
        </button>
      </div>
    </div>
  )
}
