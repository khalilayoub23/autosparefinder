import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { authApi } from '../api/auth'
import { Wrench, Loader2, CheckCircle2, KeyRound, Mail } from 'lucide-react'
import toast from 'react-hot-toast'

export default function ResetPassword() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const tokenFromUrl = searchParams.get('token') || ''

  const [step, setStep] = useState(tokenFromUrl ? 'reset' : 'request') // 'request' | 'sent' | 'reset' | 'done'
  const [email, setEmail] = useState('')
  const [token, setToken] = useState(tokenFromUrl)
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [isLoading, setIsLoading] = useState(false)

  const handleRequest = async (e) => {
    e.preventDefault()
    setIsLoading(true)
    try {
      await authApi.resetPassword(email)
      setStep('sent')
    } catch (err) {
      const msg = err.response?.data?.error || err.response?.data?.detail || 'שגיאה בשליחת הבקשה'
      toast.error(msg)
    } finally {
      setIsLoading(false)
    }
  }

  const handleReset = async (e) => {
    e.preventDefault()
    if (password !== confirm) { toast.error('הסיסמאות אינן תואמות'); return }
    if (password.length < 8) { toast.error('הסיסמה חייבת להכיל לפחות 8 תווים'); return }
    if (!token) { toast.error('קוד איפוס חסר'); return }
    setIsLoading(true)
    try {
      await authApi.resetPasswordConfirm(token, password)
      setStep('done')
    } catch (err) {
      const msg = err.response?.data?.error || err.response?.data?.detail || 'קוד לא תקין או שפג תוקפו'
      toast.error(msg)
    } finally {
      setIsLoading(false)
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
        </div>

        <div className="card p-8 shadow-md">
          {step === 'request' && (
            <>
              <div className="flex items-center gap-3 mb-6">
                <div className="w-10 h-10 bg-brand-50 rounded-xl flex items-center justify-center">
                  <KeyRound className="w-5 h-5 text-brand-600" />
                </div>
                <div>
                  <h2 className="text-xl font-bold text-gray-900">שכחת סיסמה?</h2>
                  <p className="text-sm text-gray-500">נשלח לך קישור לאיפוס</p>
                </div>
              </div>
              <form onSubmit={handleRequest} className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">כתובת אימייל</label>
                  <input
                    type="email"
                    className="input-field"
                    placeholder="your@email.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                    dir="ltr"
                  />
                </div>
                <button
                  type="submit"
                  disabled={isLoading}
                  className="btn-primary w-full flex items-center justify-center gap-2 mt-2"
                >
                  {isLoading && <Loader2 className="w-4 h-4 animate-spin" />}
                  {isLoading ? 'שולח...' : 'שלח קישור איפוס'}
                </button>
              </form>
            </>
          )}

          {step === 'sent' && (
            <div className="text-center py-4">
              <Mail className="w-14 h-14 text-brand-600 mx-auto mb-4" />
              <h2 className="text-xl font-bold text-gray-900 mb-2">בדוק את האימייל שלך</h2>
              <p className="text-gray-500 text-sm mb-6">
                אם הכתובת קיימת במערכת, תקבל קישור לאיפוס הסיסמה תוך מספר דקות.
              </p>
              <button
                onClick={() => setStep('reset')}
                className="btn-secondary w-full mb-3"
              >
                יש לי קוד איפוס
              </button>
            </div>
          )}

          {step === 'reset' && (
            <>
              <div className="flex items-center gap-3 mb-6">
                <div className="w-10 h-10 bg-brand-50 rounded-xl flex items-center justify-center">
                  <KeyRound className="w-5 h-5 text-brand-600" />
                </div>
                <div>
                  <h2 className="text-xl font-bold text-gray-900">איפוס סיסמה</h2>
                  <p className="text-sm text-gray-500">הזן את הקוד מהאימייל וסיסמה חדשה</p>
                </div>
              </div>
              <form onSubmit={handleReset} className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">קוד איפוס</label>
                  <input
                    type="text"
                    className="input-field"
                    placeholder="הדבק כאן את הקוד מהאימייל"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    required
                    dir="ltr"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">סיסמה חדשה</label>
                  <input
                    type="password"
                    className="input-field"
                    placeholder="לפחות 8 תווים"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    dir="ltr"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">אישור סיסמה</label>
                  <input
                    type="password"
                    className="input-field"
                    placeholder="••••••••"
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    required
                    dir="ltr"
                  />
                </div>
                <button
                  type="submit"
                  disabled={isLoading}
                  className="btn-primary w-full flex items-center justify-center gap-2 mt-2"
                >
                  {isLoading && <Loader2 className="w-4 h-4 animate-spin" />}
                  {isLoading ? 'מאפס...' : 'אפס סיסמה'}
                </button>
              </form>
            </>
          )}

          {step === 'done' && (
            <div className="text-center py-4">
              <CheckCircle2 className="w-14 h-14 text-green-500 mx-auto mb-4" />
              <h2 className="text-xl font-bold text-gray-900 mb-2">הסיסמה שונתה בהצלחה!</h2>
              <p className="text-gray-500 text-sm mb-6">כעת תוכל להתחבר עם הסיסמה החדשה שלך.</p>
              <button onClick={() => navigate('/login')} className="btn-primary w-full">
                לדף ההתחברות
              </button>
            </div>
          )}

          {step !== 'done' && (
            <p className="text-center text-sm text-gray-500 mt-6">
              נזכרת?{' '}
              <Link to="/login" className="text-brand-600 hover:text-brand-700 font-semibold">
                חזור להתחברות
              </Link>
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
