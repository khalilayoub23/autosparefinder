import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { authApi } from '../api/auth'
import { Loader2, CheckCircle2, KeyRound, Mail, Link2Off, RefreshCw } from 'lucide-react'
import toast from 'react-hot-toast'
import AuthBrandHeader from '../components/auth/AuthBrandHeader'
import {
  apiErrorText, linkStatusFromConfirmError, normalizeResetStatus, readResetToken,
  resetLinkMessage, RESET_LINK_NEXT_STEP, validateEndpointMissing,
} from './resetPasswordLink'

// Steps: request -> sent (email flow); checking -> reset -> done, or checking -> invalid (link flow).
// The reset token is never rendered: no input holds it, so no password manager can save it as a username (#164),
// and the link state is checked BEFORE the password form is shown (#163).
export default function ResetPassword() {
  const navigate = useNavigate()
  const [initialToken] = useState(() => readResetToken(window.location.hash, window.location.search))
  const [token, setLinkToken] = useState(initialToken)
  const [step, setStep] = useState(initialToken ? 'checking' : 'request')
  const [linkStatus, setLinkStatus] = useState(null)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [isLoading, setIsLoading] = useState(false)

  const showInvalid = (status) => {
    setPassword('')
    setConfirm('')
    setLinkStatus(status)
    setStep('invalid')
  }

  const checkSeq = useRef(0)   // only the answer for the latest opened link may change the page
  const checkLink = async (linkToken = token) => {
    const seq = ++checkSeq.current
    setStep('checking')
    try {
      const { data } = await authApi.validateResetToken(linkToken)
      if (seq !== checkSeq.current) return
      const status = normalizeResetStatus(data?.status)
      if (status === 'valid') setStep('reset')
      else showInvalid(status)
    } catch (err) {
      if (seq !== checkSeq.current) return
      if (validateEndpointMissing(err)) setStep('reset')
      else showInvalid('unavailable')
    }
  }

  useEffect(() => {
    const openLink = (linkToken) => {
      // Take the token out of the address bar and the history entry (it stays in component state only).
      navigate('/reset-password', { replace: true })
      setLinkToken(linkToken)
      checkLink(linkToken)
    }
    if (initialToken) openLink(initialToken)
    // A link opened in a tab already showing this page changes only the fragment: no reload, so listen for it.
    const onHashChange = () => {
      const linkToken = readResetToken(window.location.hash, '')
      if (linkToken) openLink(linkToken)
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleRequest = async (e) => {
    e.preventDefault()
    setIsLoading(true)
    try {
      await authApi.resetPassword(email)
      setStep('sent')
    } catch (err) {
      toast.error(apiErrorText(err, 'שגיאה בשליחת הבקשה'))
    } finally {
      setIsLoading(false)
    }
  }

  const handleReset = async (e) => {
    e.preventDefault()
    if (password !== confirm) { toast.error('הסיסמאות אינן תואמות'); return }
    if (password.length < 8) { toast.error('הסיסמה חייבת להכיל לפחות 8 תווים'); return }
    setIsLoading(true)
    try {
      await authApi.resetPasswordConfirm(token, password)
      setPassword('')
      setConfirm('')
      setStep('done')
    } catch (err) {
      const status = linkStatusFromConfirmError(err)
      if (status) showInvalid(status)   // the link died while the page was open
      else toast.error(apiErrorText(err, 'שגיאה באיפוס הסיסמה. נסה שוב.'))
    } finally {
      setIsLoading(false)
    }
  }

  const requestNewLink = () => {
    checkSeq.current += 1
    setLinkToken('')
    setLinkStatus(null)
    setStep('request')
  }

  const invalidMessage = resetLinkMessage(linkStatus)

  return (
    <div className="auth-page min-h-screen flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <AuthBrandHeader
          title={<span className="text-slate-100">איפוס סיסמה</span>}
          subtitle="התחברות מאובטחת עם שחזור סיסמה מהיר"
        />

        <div className="auth-panel">
          {step === 'request' && (
            <>
              <div className="flex items-center gap-3 mb-6">
                <div className="w-10 h-10 bg-brand-50 rounded-xl flex items-center justify-center">
                  <KeyRound className="w-5 h-5 text-brand-600" />
                </div>
                <div>
                  <h2 className="text-xl font-bold text-slate-100">שכחת סיסמה?</h2>
                  <p className="text-sm text-slate-400">נשלח לך קישור לאיפוס</p>
                </div>
              </div>
              <form onSubmit={handleRequest} className="space-y-4">
                <div>
                  <label htmlFor="resetpassword-f1" className="block text-sm font-medium text-slate-200 mb-1">כתובת אימייל</label>
                  <input id="resetpassword-f1"
                    type="email"
                    className="input-field"
                    placeholder="your@email.com"
                    autoComplete="email"
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
              <h2 className="text-xl font-bold text-slate-100 mb-2">בדוק את האימייל שלך</h2>
              <p className="text-slate-400 text-sm">
                אם הכתובת קיימת במערכת, תקבל קישור לאיפוס הסיסמה תוך מספר דקות. הקישור תקף לשעה אחת ולשימוש חד-פעמי.
              </p>
            </div>
          )}

          {step === 'checking' && (
            <div className="text-center py-8" role="status">
              <Loader2 className="w-10 h-10 text-brand-600 mx-auto mb-4 animate-spin" />
              <p className="text-slate-400 text-sm">בודק את הקישור...</p>
            </div>
          )}

          {step === 'invalid' && (
            <div className="text-center py-4" role="alert" data-testid="reset-link-invalid" data-link-status={linkStatus || 'invalid'}>
              <Link2Off className="w-14 h-14 text-amber-400 mx-auto mb-4" />
              <h2 className="text-xl font-bold text-slate-100 mb-2">{invalidMessage.title}</h2>
              <p className="text-slate-400 text-sm mb-2">{invalidMessage.body}</p>
              <p className="text-slate-300 text-sm mb-6">{RESET_LINK_NEXT_STEP}</p>
              {linkStatus === 'unavailable' && (
                <button onClick={() => checkLink()} className="btn-secondary w-full mb-3 flex items-center justify-center gap-2">
                  <RefreshCw className="w-4 h-4" />
                  נסה שוב
                </button>
              )}
              <button onClick={requestNewLink} className="btn-primary w-full" data-testid="request-new-link">
                שלח לי קישור חדש
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
                  <h2 className="text-xl font-bold text-slate-100">בחירת סיסמה חדשה</h2>
                  <p className="text-sm text-slate-400">הקישור תקין. בחר סיסמה חדשה לחשבון שלך.</p>
                </div>
              </div>
              <form onSubmit={handleReset} className="space-y-4">
                <div>
                  <label htmlFor="resetpassword-f3" className="block text-sm font-medium text-slate-200 mb-1">סיסמה חדשה</label>
                  <input id="resetpassword-f3"
                    type="password"
                    className="input-field"
                    placeholder="לפחות 8 תווים"
                    autoComplete="new-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    dir="ltr"
                  />
                </div>
                <div>
                  <label htmlFor="resetpassword-f4" className="block text-sm font-medium text-slate-200 mb-1">אישור סיסמה</label>
                  <input id="resetpassword-f4"
                    type="password"
                    className="input-field"
                    placeholder="••••••••"
                    autoComplete="new-password"
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
              <h2 className="text-xl font-bold text-slate-100 mb-2">הסיסמה שונתה בהצלחה!</h2>
              <p className="text-slate-400 text-sm mb-6">כעת תוכל להתחבר עם הסיסמה החדשה שלך. מטעמי אבטחה, החשבון נותק מכל המכשירים שהיו מחוברים אליו.</p>
              <button onClick={() => navigate('/login')} className="btn-primary w-full">
                לדף ההתחברות
              </button>
            </div>
          )}

          {step !== 'done' && (
            <p className="text-center text-sm text-slate-400 mt-6">
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
