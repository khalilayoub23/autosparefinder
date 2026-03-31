import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'
import { Eye, EyeOff, Wrench, Loader2, CheckCircle2 } from 'lucide-react'
import toast from 'react-hot-toast'

export default function Register() {
  const navigate = useNavigate()
  const { register, isLoading } = useAuthStore()
  const [form, setForm] = useState({ full_name: '', email: '', phone: '05', password: '', confirmPassword: '' })
  const [showPass, setShowPass] = useState(false)
  const [done, setDone] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (form.password !== form.confirmPassword) {
      toast.error('הסיסמאות אינן תואמות')
      return
    }
    if (form.password.length < 8) {
      toast.error('הסיסמה חייבת להכיל לפחות 8 תווים')
      return
    }
    if (!/^05\d{8}$/.test(form.phone)) {
      toast.error('מספר טלפון לא תקין (צריך להתחיל ב-05 וכולל 10 ספרות)')
      return
    }
    try {
      await register({ full_name: form.full_name, email: form.email, phone: form.phone, password: form.password })
      setDone(true)
    } catch (err) {
      const msg = err.response?.data?.error || err.response?.data?.detail || 'שגיאה בהרשמה'
      toast.error(msg)
    }
  }

  if (done) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-brand-50 to-orange-50 p-4">
        <div className="card p-10 max-w-md w-full text-center shadow-md">
          <CheckCircle2 className="w-16 h-16 text-green-500 mx-auto mb-4" />
          <h2 className="text-2xl font-bold text-gray-900 mb-2">נרשמת בהצלחה!</h2>
          <p className="text-gray-500 mb-6">קוד אימות נשלח לטלפון שלך. אמת את מספר הטלפון כדי להשלים את ההרשמה.</p>
          <button onClick={() => navigate('/login')} className="btn-primary w-full">המשך להתחברות</button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-brand-50 to-orange-50 p-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-brand-600 rounded-2xl mb-4 shadow-lg">
            <Wrench className="w-9 h-9 text-white" />
          </div>
          <h1 className="text-3xl font-bold text-gray-900">הצטרף ל-<span className="text-brand-600">Auto Spare</span></h1>
          <p className="text-gray-500 mt-1">צור חשבון חינמי עכשיו</p>
        </div>

        <div className="card p-8 shadow-md">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">שם מלא</label>
              <input className="input-field" placeholder="ישראל ישראלי" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">אימייל</label>
              <input type="email" dir="ltr" className="input-field" placeholder="your@email.com" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">טלפון (לאימות דו-שלבי)</label>
              <input type="tel" dir="ltr" className="input-field" placeholder="0501234567" maxLength={10} value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} required />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">סיסמה</label>
              <div className="relative">
                <input type={showPass ? 'text' : 'password'} dir="ltr" className="input-field pl-10" placeholder="לפחות 8 תווים" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required />
                <button type="button" className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" onClick={() => setShowPass(!showPass)}>
                  {showPass ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">אישור סיסמה</label>
              <input type="password" dir="ltr" className="input-field" placeholder="••••••••" value={form.confirmPassword} onChange={(e) => setForm({ ...form, confirmPassword: e.target.value })} required />
            </div>
            <p className="text-xs text-gray-400">בהרשמה אתה מאשר את <Link to="/terms" className="text-brand-600">תנאי השימוש</Link> ו<Link to="/privacy" className="text-brand-600">מדיניות הפרטיות</Link></p>
            <button type="submit" disabled={isLoading} className="btn-primary w-full flex items-center justify-center gap-2 mt-1">
              {isLoading && <Loader2 className="w-4 h-4 animate-spin" />}
              {isLoading ? 'יוצר חשבון...' : 'הירשם'}
            </button>
          </form>
          <p className="text-center text-sm text-gray-500 mt-6">
            יש לך חשבון? <Link to="/login" className="text-brand-600 font-semibold hover:text-brand-700">כנס</Link>
          </p>
        </div>
      </div>
    </div>
  )
}
