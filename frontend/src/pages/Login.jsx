import { useState } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'
import { Eye, EyeOff, Loader2 } from 'lucide-react'
import toast from 'react-hot-toast'
import SocialLoginButtons from '../components/SocialLoginButtons'
import AuthBrandHeader from '../components/auth/AuthBrandHeader'

export default function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const { login, verify2fa, isLoading, pendingUserId } = useAuthStore()

  const [form, setForm] = useState({ email: '', password: '', trustDevice: true })
  const [code, setCode] = useState('')
  const [showPass, setShowPass] = useState(false)
  const [step, setStep] = useState('login') // 'login' | '2fa'

  const from = location.state?.from?.pathname || '/'

  const getLoginErrorMessage = (err) => {
    if (!err?.response) {
      return 'שרת ההתחברות אינו זמין כעת. ודא שה-backend פועל על פורט 8000.'
    }

    if (err.response.status >= 500) {
      return 'שירות ההתחברות אינו זמין כעת. נסה שוב לאחר שהשרת יעלה.'
    }

    const detail = err.response?.data?.detail
    const detailMsg = typeof detail === 'string' ? detail : detail?.message
    return err.response?.data?.error || detailMsg || 'שם משתמש או סיסמה שגויים'
  }

  const handleLogin = async (e) => {
    e.preventDefault()
    const email = form.email.trim()
    const password = form.password
    if (!email) {
      toast.error('יש להזין אימייל')
      return
    }
    if (password.length < 8) {
      toast.error('הסיסמה חייבת להכיל לפחות 8 תווים')
      return
    }
    try {
      const res = await login(email, password, form.trustDevice)
      if (res.requires2fa) {
        setStep('2fa')
        toast('קוד אימות נשלח לטלפון שלך', { icon: '📱' })
      } else {
        const firstName = useAuthStore.getState().user?.full_name?.split(' ')[0]
        toast.success(firstName ? `!ברוך הבא, ${firstName}` : '!ברוך הבא')
        navigate(from, { replace: true })
      }
    } catch (err) {
      toast.error(getLoginErrorMessage(err))
    }
  }

  const handle2FA = async (e) => {
    e.preventDefault()
    try {
      const userId = pendingUserId || useAuthStore.getState().pendingUserId
      await verify2fa(userId, code, form.trustDevice)
      const firstName = useAuthStore.getState().user?.full_name?.split(' ')[0]
      toast.success(firstName ? `!ברוך הבא, ${firstName}` : '!ברוך הבא')
      navigate(from, { replace: true })
    } catch (err) {
      const detail = err.response?.data?.detail
      const detailMsg = typeof detail === 'string' ? detail : detail?.message
      const msg = err.response?.data?.error || detailMsg || 'קוד שגוי, נסה שוב'
      toast.error(msg)
    }
  }

  return (
    <div className="auth-page min-h-screen flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <AuthBrandHeader
          title={<span className="text-brand-navy">AutoSpare Finder</span>}
          subtitle="חלקי חילוף לרכב עם התאמה חכמה"
        />

        <div className="auth-panel">
          {step === 'login' ? (
            <>
              <h2 className="text-xl font-bold text-brand-navy mb-6 text-center">ברוך הבא</h2>

              {/* Email / password form */}
              <form onSubmit={handleLogin} className="space-y-4" autoComplete="on">
                <div>
                  <input
                    id="login-email"
                    name="email"
                    type="email"
                    className="input-field"
                    placeholder="אימייל"
                    value={form.email}
                    autoComplete="email"
                    onChange={(e) => setForm({ ...form, email: e.target.value })}
                    required
                    dir="ltr"
                  />
                </div>
                <div>
                  <div className="relative">
                    <input
                      id="login-password"
                      name="password"
                      type={showPass ? 'text' : 'password'}
                      className="input-field pl-10"
                      placeholder="סיסמה"
                      value={form.password}
                      autoComplete="current-password"
                      minLength={8}
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
                    <span className="text-sm text-gray-600">זכור אותי</span>
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

              {/* OR divider */}
              <div className="relative my-5">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-gray-200" />
                </div>
                <div className="relative flex justify-center text-sm">
                  <span className="bg-white px-3 text-gray-400 font-medium">OR</span>
                </div>
              </div>

              {/* Social login — below form */}
              <SocialLoginButtons redirectTo={from} />

              <p className="text-center text-sm text-gray-500 mt-6">
                אין לך חשבון?{' '}
                <Link to="/register" className="text-brand-600 hover:text-brand-700 font-semibold">הירשם עכשיו</Link>
              </p>
            </>
          ) : (
            <>
              <div className="text-center mb-6">
                <div className="text-4xl mb-3">📱</div>
                <h2 className="text-xl font-bold text-brand-navy">אימות דו-שלבי</h2>
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
                <label className="flex items-center gap-2 cursor-pointer justify-center">
                  <input
                    type="checkbox"
                    className="w-4 h-4 rounded border-gray-300 text-brand-600"
                    checked={form.trustDevice}
                    onChange={(e) => setForm({ ...form, trustDevice: e.target.checked })}
                  />
                  <span className="text-sm text-gray-600">סמוך על המכשיר הזה (180 יום)</span>
                </label>
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
