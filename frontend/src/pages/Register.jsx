import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'
import { Eye, EyeOff, Loader2, CheckCircle2, Check, X, ShieldCheck, MessageSquare, Mail } from 'lucide-react'
import toast from 'react-hot-toast'
import SocialLoginButtons from '../components/SocialLoginButtons'
import AuthBrandHeader from '../components/auth/AuthBrandHeader'

// Validation helpers — Israeli 05XXXXXXXX or international +E.164
const isValidEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim())
const isValidPhone = (v) => /^05\d{8}$/.test(v.trim()) || /^\+\d{7,15}$/.test(v.trim())

export default function Register() {
  const navigate = useNavigate()
  const { register, isLoading } = useAuthStore()
  const [form, setForm] = useState({ full_name: '', email: '', phone: '', password: '', confirmPassword: '' })
  const [touched, setTouched] = useState({})
  const [showPass, setShowPass] = useState(false)
  const [done, setDone] = useState(false)

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })
  const blur = (k) => () => setTouched((t) => ({ ...t, [k]: true }))

  // Password rules (live)
  const pwLen = form.password.length >= 8
  const pwLetter = /[A-Za-z֐-׿؀-ۿ]/.test(form.password)
  const pwDigit = /\d/.test(form.password)
  const pwScore = [pwLen, pwLetter, pwDigit].filter(Boolean).length
  const pwOk = pwLen && pwLetter && pwDigit
  const pwMatch = form.confirmPassword.length > 0 && form.password === form.confirmPassword

  const emailOk = isValidEmail(form.email)
  const phoneOk = isValidPhone(form.phone)
  const nameOk = form.full_name.trim().length >= 2
  const formValid = nameOk && emailOk && phoneOk && pwOk && pwMatch

  const handleSubmit = async (e) => {
    e.preventDefault()
    setTouched({ full_name: true, email: true, phone: true, password: true, confirmPassword: true })
    if (!formValid) {
      if (!pwMatch && form.confirmPassword) toast.error('הסיסמאות אינן תואמות')
      else if (!phoneOk) toast.error('מספר טלפון לא תקין (ישראלי: 05XXXXXXXX או בינלאומי: ‎+XXXXXXXXXXX)')
      else if (!pwOk) toast.error('הסיסמה חייבת לכלול לפחות 8 תווים, אות אחת ומספר אחד')
      else toast.error('נא למלא את כל השדות')
      return
    }
    try {
      await register({ full_name: form.full_name, email: form.email, phone: form.phone, password: form.password })
      setDone(true)
    } catch (err) {
      const detail = err.response?.data?.detail
      const detailMsg = typeof detail === 'string' ? detail : detail?.message
      const msg = err.response?.data?.error || detailMsg || 'שגיאה בהרשמה'
      toast.error(msg)
    }
  }

  // ── Success state ──────────────────────────────────────────────────────────
  if (done) {
    return (
      <div className="auth-page min-h-screen flex items-center justify-center p-4">
        <div className="w-full max-w-md">
          <AuthBrandHeader
            title={<span className="text-brand-navy">כמעט שם 🎉</span>}
            subtitle="נשאר רק לאמת את החשבון"
          />
          <div className="auth-panel text-center">
            <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-brand-success/10">
              <CheckCircle2 className="h-9 w-9 text-brand-success" />
            </div>
            <h2 className="text-2xl font-bold text-brand-navy mb-2">נרשמת בהצלחה!</h2>
            <p className="text-slate-500 mb-6">שלחנו לך שני דברים כדי להשלים את ההצטרפות:</p>

            <div className="space-y-3 text-right mb-7">
              <div className="flex items-start gap-3 rounded-brand border border-brand-border bg-brand-surface p-3">
                <MessageSquare className="mt-0.5 h-5 w-5 shrink-0 text-brand-blue" />
                <p className="text-sm text-slate-600"><b className="text-brand-navy">קוד אימות ב-SMS</b> — הזן אותו בהתחברות כדי לאמת את הטלפון.</p>
              </div>
              <div className="flex items-start gap-3 rounded-brand border border-brand-border bg-brand-surface p-3">
                <Mail className="mt-0.5 h-5 w-5 shrink-0 text-brand-blue" />
                <p className="text-sm text-slate-600"><b className="text-brand-navy">מייל ברוכים הבאים ואימות</b> — לחיצה על הקישור מפעילה את החשבון (אפשר גם מאוחר יותר מהפרופיל).</p>
              </div>
            </div>

            <button onClick={() => navigate('/login')} className="btn-primary w-full">המשך להתחברות</button>
          </div>
        </div>
      </div>
    )
  }

  // Small field-error line
  const err = (show, text) => (show ? <p className="mt-1 flex items-center gap-1 text-xs text-red-500"><X className="h-3 w-3" />{text}</p> : null)

  // ── Form ───────────────────────────────────────────────────────────────────
  return (
    <div className="auth-page min-h-screen flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <AuthBrandHeader
          title={<span className="text-brand-navy">פתיחת חשבון חדש</span>}
          subtitle="צור חשבון מהיר וקבל גישה לכל כלי החיפוש"
        />

        <div className="auth-panel">
          <h2 className="text-xl font-bold text-brand-navy mb-1 text-center">הצטרף אלינו</h2>
          <p className="text-center text-sm text-slate-500 mb-6">פחות מדקה, בלי כרטיס אשראי</p>

          <form onSubmit={handleSubmit} className="space-y-4" noValidate>
            <div>
              <label htmlFor="full_name" className="block text-sm font-medium text-slate-700 mb-1">שם מלא</label>
              <input id="full_name" autoComplete="name" className="input-field" placeholder="ישראל ישראלי"
                     value={form.full_name} onChange={set('full_name')} onBlur={blur('full_name')} required />
              {err(touched.full_name && !nameOk, 'נא להזין שם מלא')}
            </div>

            <div>
              <label htmlFor="email" className="block text-sm font-medium text-slate-700 mb-1">אימייל</label>
              <div className="relative">
                <input id="email" type="email" dir="ltr" autoComplete="email" className="input-field pl-9" placeholder="your@email.com"
                       value={form.email} onChange={set('email')} onBlur={blur('email')} aria-invalid={touched.email && !emailOk} required />
                {emailOk && <Check className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-brand-success" />}
              </div>
              {err(touched.email && !emailOk, 'כתובת אימייל לא תקינה')}
            </div>

            <div>
              <label htmlFor="phone" className="block text-sm font-medium text-slate-700 mb-1">טלפון <span className="text-slate-400 font-normal">(לאימות דו-שלבי)</span></label>
              <div className="relative">
                <input id="phone" type="tel" dir="ltr" autoComplete="tel" maxLength={16} className="input-field pl-9" placeholder="0501234567 או ‎+18777804236"
                       value={form.phone} onChange={set('phone')} onBlur={blur('phone')} aria-invalid={touched.phone && !phoneOk} required />
                {phoneOk && <Check className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-brand-success" />}
              </div>
              {err(touched.phone && !phoneOk, 'מספר לא תקין — ישראלי 05XXXXXXXX או בינלאומי ‎+XXXXXXXXXXX')}
            </div>

            <div>
              <label htmlFor="password" className="block text-sm font-medium text-slate-700 mb-1">סיסמה</label>
              <div className="relative">
                <input id="password" type={showPass ? 'text' : 'password'} dir="ltr" autoComplete="new-password" className="input-field pl-10" placeholder="לפחות 8 תווים"
                       value={form.password} onChange={set('password')} onBlur={blur('password')} required />
                <button type="button" aria-label={showPass ? 'הסתר סיסמה' : 'הצג סיסמה'}
                        className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                        onClick={() => setShowPass(!showPass)}>
                  {showPass ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>

              {/* Strength meter (brand-blue → success) */}
              {form.password.length > 0 && (
                <div className="mt-2">
                  <div className="flex gap-1.5" aria-hidden="true">
                    {[0, 1, 2].map((i) => (
                      <span key={i} className={`h-1.5 flex-1 rounded-full transition-colors ${
                        pwScore > i ? (pwScore === 3 ? 'bg-brand-success' : 'bg-brand-blue') : 'bg-slate-200'}`} />
                    ))}
                  </div>
                  <div className="mt-1.5 grid grid-cols-3 gap-1 text-[11px]">
                    <Rule ok={pwLen}>8+ תווים</Rule>
                    <Rule ok={pwLetter}>אות</Rule>
                    <Rule ok={pwDigit}>מספר</Rule>
                  </div>
                </div>
              )}
            </div>

            <div>
              <label htmlFor="confirmPassword" className="block text-sm font-medium text-slate-700 mb-1">אישור סיסמה</label>
              <div className="relative">
                <input id="confirmPassword" type={showPass ? 'text' : 'password'} dir="ltr" autoComplete="new-password" className="input-field pl-9" placeholder="••••••••"
                       value={form.confirmPassword} onChange={set('confirmPassword')} onBlur={blur('confirmPassword')}
                       aria-invalid={touched.confirmPassword && form.confirmPassword.length > 0 && !pwMatch} required />
                {pwMatch && <Check className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-brand-success" />}
              </div>
              {err(touched.confirmPassword && form.confirmPassword.length > 0 && !pwMatch, 'הסיסמאות אינן תואמות')}
            </div>

            <p className="text-xs text-slate-400">בהרשמה אתה מאשר את <Link to="/terms" className="text-brand-600 hover:text-brand-700 underline underline-offset-2">תנאי השימוש</Link> ו<Link to="/privacy" className="text-brand-600 hover:text-brand-700 underline underline-offset-2">מדיניות הפרטיות</Link></p>

            <button type="submit" disabled={isLoading}
                    className="btn-primary w-full flex items-center justify-center gap-2 mt-1 disabled:opacity-60">
              {isLoading && <Loader2 className="w-4 h-4 animate-spin" />}
              {isLoading ? 'יוצר חשבון...' : 'הירשם'}
            </button>

            <p className="flex items-center justify-center gap-1.5 text-[11px] text-slate-400">
              <ShieldCheck className="h-3.5 w-3.5 text-brand-success" /> הנתונים שלך מוצפנים ומאובטחים
            </p>
          </form>

          {/* Divider */}
          <div className="relative my-5">
            <div className="absolute inset-0 flex items-center"><div className="w-full border-t border-slate-200" /></div>
            <div className="relative flex justify-center text-sm"><span className="bg-white px-3 text-slate-400 font-medium">או</span></div>
          </div>

          <SocialLoginButtons redirectTo="/" />

          <p className="text-center text-sm text-slate-500 mt-6">
            יש לך חשבון? <Link to="/login" className="text-brand-600 font-semibold hover:text-brand-700">כנס</Link>
          </p>
        </div>
      </div>
    </div>
  )
}

// Password-rule chip: turns success-green with a check when satisfied.
function Rule({ ok, children }) {
  return (
    <span className={`flex items-center gap-1 ${ok ? 'text-brand-success' : 'text-slate-400'}`}>
      {ok ? <Check className="h-3 w-3" /> : <span className="inline-block h-1 w-1 rounded-full bg-current opacity-60" />}
      {children}
    </span>
  )
}
