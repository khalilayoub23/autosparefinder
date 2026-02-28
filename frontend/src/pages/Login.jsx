import { useState } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'
import { Eye, EyeOff, Wrench, Loader2 } from 'lucide-react'
import toast from 'react-hot-toast'

export default function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const { login, verify2fa, isLoading, pendingUserId } = useAuthStore()

  const [form, setForm] = useState({ email: '', password: '', trustDevice: false })
  const [code, setCode] = useState('')
  const [showPass, setShowPass] = useState(false)
  const [step, setStep] = useState('login') // 'login' | '2fa'

  const from = location.state?.from?.pathname || '/'

  const handleLogin = async (e) => {
    e.preventDefault()
    try {
      const res = await login(form.email, form.password, form.trustDevice)
      if (res.requires2fa) {
        setStep('2fa')
        toast('קוד אימות נשלח לטלפון שלך', { icon: '📱' })
      } else {
        toast.success('!ברוך הבא')
        navigate(from, { replace: true })
      }
    } catch (err) {
      const msg = err.response?.data?.error || err.response?.data?.detail || 'שם משתמש או סיסמה שגויים'
      toast.error(msg)
    }
  }

  const handle2FA = async (e) => {
    e.preventDefault()
    try {
      const userId = pendingUserId || useAuthStore.getState().pendingUserId
      await verify2fa(userId, code, form.trustDevice)
      toast.success('!ברוך הבא')
      navigate(from, { replace: true })
    } catch (err) {
      const msg = err.response?.data?.error || 'קוד שגוי, נסה שוב'
      toast.error(msg)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-brand-50 to-orange-50 p-4">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-brand-600 rounded-2xl mb-4 shadow-lg">
            <Wrench className="w-9 h-9 text-white" />
          </div>
          <h1 className="text-3xl font-bold text-gray-900">Auto <span className="text-brand-600">Spare</span></h1>
          <p className="text-gray-500 mt-1">חלקי חילוף בעזרת בינה מלאכותית</p>
        </div>

        <div className="card p-8 shadow-md">
          {step === 'login' ? (
            <>
              <h2 className="text-xl font-bold text-gray-900 mb-6">כניסה לחשבון</h2>
              <form onSubmit={handleLogin} className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">אימייל</label>
                  <input
                    type="email"
                    className="input-field"
                    placeholder="your@email.com"
                    value={form.email}
                    onChange={(e) => setForm({ ...form, email: e.target.value })}
                    required
                    dir="ltr"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">סיסמה</label>
                  <div className="relative">
                    <input
                      type={showPass ? 'text' : 'password'}
                      className="input-field pl-10"
                      placeholder="••••••••"
                      value={form.password}
                      onChange={(e) => setForm({ ...form, password: e.target.value })}
                      required
                      dir="ltr"
                    />
                    <button
                      type="button"
                      className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                      onClick={() => setShowPass(!showPass)}
                    >
                      {showPass ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
                </div>
                <div className="flex items-center justify-between">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      className="w-4 h-4 rounded border-gray-300 text-brand-600"
                      checked={form.trustDevice}
                      onChange={(e) => setForm({ ...form, trustDevice: e.target.checked })}
                    />
                    <span className="text-sm text-gray-600">סמוך על המכשיר הזה</span>
                  </label>
                  <Link to="/reset-password" className="text-sm text-brand-600 hover:text-brand-700 font-medium">
                    שכחת סיסמה?
                  </Link>
                </div>
                <button type="submit" disabled={isLoading} className="btn-primary w-full flex items-center justify-center gap-2 mt-2">
                  {isLoading && <Loader2 className="w-4 h-4 animate-spin" />}
                  {isLoading ? 'מתחבר...' : 'כניסה'}
                </button>
              </form>
              <p className="text-center text-sm text-gray-500 mt-6">
                אין לך חשבון?{' '}
                <Link to="/register" className="text-brand-600 hover:text-brand-700 font-semibold">הירשם עכשיו</Link>
              </p>
            </>
          ) : (
            <>
              <div className="text-center mb-6">
                <div className="text-4xl mb-3">📱</div>
                <h2 className="text-xl font-bold text-gray-900">אימות דו-שלבי</h2>
                <p className="text-sm text-gray-500 mt-1">הזן את הקוד שנשלח לטלפון שלך</p>
              </div>
              <form onSubmit={handle2FA} className="space-y-4">
                <input
                  type="text"
                  className="input-field text-center text-2xl font-bold tracking-widest"
                  placeholder="000000"
                  maxLength={6}
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                  required
                  dir="ltr"
                  autoFocus
                />
                <button type="submit" disabled={isLoading || code.length < 6} className="btn-primary w-full flex items-center justify-center gap-2">
                  {isLoading && <Loader2 className="w-4 h-4 animate-spin" />}
                  אמת קוד
                </button>
                <button type="button" onClick={() => { setStep('login'); setCode('') }} className="btn-secondary w-full">
                  חזור
                </button>
              </form>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
