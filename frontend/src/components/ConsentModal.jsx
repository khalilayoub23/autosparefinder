import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ShieldCheck, FileText, CheckCircle2 } from 'lucide-react'
import { useAuthStore } from '../stores/authStore'
import toast from 'react-hot-toast'

/**
 * ConsentModal — shown once per user (first login).
 * User must tick both checkboxes before they can continue.
 * Dismissed only by clicking "אני מאשר/ת ומסכים/ה".
 */
export default function ConsentModal() {
  const [privacyChecked, setPrivacyChecked] = useState(false)
  const [termsChecked, setTermsChecked] = useState(false)
  const [loading, setLoading] = useState(false)
  const { acceptTerms } = useAuthStore()

  const canSubmit = privacyChecked && termsChecked

  const handleAccept = async () => {
    if (!canSubmit) return
    setLoading(true)
    try {
      await acceptTerms()
      toast.success('תודה! הסכמתך נשמרה.')
    } catch {
      toast.error('אירעה שגיאה, נסה שוב.')
    } finally {
      setLoading(false)
    }
  }

  return (
    /* Full-screen dark overlay — pointer-events blocked so user can't click behind */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      style={{ direction: 'rtl' }}
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden">
        {/* Header */}
        <div className="bg-gradient-to-l from-orange-600 to-orange-500 px-6 py-5">
          <div className="flex items-center gap-3">
            <ShieldCheck className="text-white w-8 h-8 flex-shrink-0" />
            <div>
              <h2 className="text-white text-xl font-bold">ברוכים הבאים ל-AutoSpareFinder</h2>
              <p className="text-orange-100 text-sm mt-0.5">אנא קראו ואשרו לפני שממשיכים</p>
            </div>
          </div>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-4">
          <p className="text-gray-700 text-sm leading-relaxed">
            לפני השימוש בשירות, עליכם לקרוא ולאשר את המסמכים הבאים. השימוש בפלטפורמה
            מהווה הסכמה לתנאים אלה.
          </p>

          {/* Privacy Policy checkbox */}
          <label className="flex items-start gap-3 cursor-pointer group">
            <div className="relative mt-0.5 flex-shrink-0">
              <input
                type="checkbox"
                className="sr-only"
                checked={privacyChecked}
                onChange={e => setPrivacyChecked(e.target.checked)}
              />
              <div
                className={`w-5 h-5 rounded border-2 flex items-center justify-center transition-colors
                  ${privacyChecked
                    ? 'bg-orange-500 border-orange-500'
                    : 'border-gray-300 group-hover:border-orange-400'}`}
              >
                {privacyChecked && <CheckCircle2 className="text-white w-3.5 h-3.5" strokeWidth={3} />}
              </div>
            </div>
            <span className="text-sm text-gray-700">
              קראתי ואני מסכים/ה{' '}
              <Link
                to="/privacy"
                target="_blank"
                rel="noopener noreferrer"
                className="text-orange-600 font-semibold underline hover:text-orange-700"
                onClick={e => e.stopPropagation()}
              >
                למדיניות הפרטיות
              </Link>
            </span>
          </label>

          {/* Terms of Use checkbox */}
          <label className="flex items-start gap-3 cursor-pointer group">
            <div className="relative mt-0.5 flex-shrink-0">
              <input
                type="checkbox"
                className="sr-only"
                checked={termsChecked}
                onChange={e => setTermsChecked(e.target.checked)}
              />
              <div
                className={`w-5 h-5 rounded border-2 flex items-center justify-center transition-colors
                  ${termsChecked
                    ? 'bg-orange-500 border-orange-500'
                    : 'border-gray-300 group-hover:border-orange-400'}`}
              >
                {termsChecked && <CheckCircle2 className="text-white w-3.5 h-3.5" strokeWidth={3} />}
              </div>
            </div>
            <span className="text-sm text-gray-700">
              קראתי ואני מסכים/ה{' '}
              <Link
                to="/terms"
                target="_blank"
                rel="noopener noreferrer"
                className="text-orange-600 font-semibold underline hover:text-orange-700"
                onClick={e => e.stopPropagation()}
              >
                לתנאי השימוש
              </Link>
            </span>
          </label>

          {/* Notice if not checked */}
          {!canSubmit && (
            <p className="text-xs text-gray-400 text-center">
              יש לסמן את שני התיבות לפני שממשיכים
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 pb-6">
          <button
            onClick={handleAccept}
            disabled={!canSubmit || loading}
            className={`w-full py-3 rounded-xl font-bold text-base transition-all
              ${canSubmit && !loading
                ? 'bg-orange-500 hover:bg-orange-600 text-white shadow-md hover:shadow-lg active:scale-95'
                : 'bg-gray-200 text-gray-400 cursor-not-allowed'}`}
          >
            {loading ? (
              <span className="flex items-center justify-center gap-2">
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                שומר...
              </span>
            ) : (
              'אני מאשר/ת ומסכים/ה — המשך'
            )}
          </button>
          <p className="text-center text-xs text-gray-400 mt-3">
            ניתן לעיין בהם תמיד דרך דף הפרופיל
          </p>
        </div>
      </div>
    </div>
  )
}
