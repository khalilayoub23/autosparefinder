import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'
import { Loader2 } from 'lucide-react'
import toast from 'react-hot-toast'

const GOOGLE_CLIENT_ID  = import.meta.env.VITE_GOOGLE_CLIENT_ID
const FACEBOOK_APP_ID   = import.meta.env.VITE_FACEBOOK_APP_ID

/**
 * Reusable Google + Facebook login buttons.
 * Props:
 *   redirectTo {string}  — path to navigate on success (default "/")
 *   onSuccess  {fn}      — optional callback(user) after successful social login
 */
export default function SocialLoginButtons({ redirectTo = '/', onSuccess }) {
  const navigate = useNavigate()
  const { socialLogin } = useAuthStore()
  const [loading, setLoading] = useState(null) // 'google' | 'facebook' | null

  // ── Load Google Identity Services ─────────────────────────────────────────
  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) return
    if (document.getElementById('gsi-script')) return
    const s = document.createElement('script')
    s.id = 'gsi-script'
    s.src = 'https://accounts.google.com/gsi/client'
    s.async = true
    document.head.appendChild(s)
  }, [])

  // ── Load Facebook SDK ─────────────────────────────────────────────────────
  useEffect(() => {
    if (!FACEBOOK_APP_ID) return
    if (document.getElementById('fb-sdk-script')) return
    window.fbAsyncInit = () => {
      window.FB.init({ appId: FACEBOOK_APP_ID, cookie: true, xfbml: false, version: 'v19.0' })
    }
    const s = document.createElement('script')
    s.id = 'fb-sdk-script'
    s.src = 'https://connect.facebook.net/en_US/sdk.js'
    s.async = true
    document.head.appendChild(s)
  }, [])

  // ── Shared success handler ────────────────────────────────────────────────
  const handleSuccess = async (provider, token) => {
    try {
      const res = await socialLogin(provider, token)
      toast.success('!ברוך הבא')
      onSuccess?.(res?.user)
      navigate(redirectTo, { replace: true })
    } catch (err) {
      const msg = err.response?.data?.detail || `שגיאה בכניסה עם ${provider === 'google' ? 'Google' : 'Facebook'}`
      toast.error(msg)
    } finally {
      setLoading(null)
    }
  }

  // ── Google ────────────────────────────────────────────────────────────────
  const handleGoogle = () => {
    if (!GOOGLE_CLIENT_ID) {
      toast.error('כניסה עם Google לא הוגדרה — הוסף VITE_GOOGLE_CLIENT_ID ל-.env')
      return
    }
    if (!window.google) {
      toast.error('ספריית Google עוד לא נטענה, נסה שוב')
      return
    }
    setLoading('google')
    window.google.accounts.id.initialize({
      client_id: GOOGLE_CLIENT_ID,
      callback: ({ credential }) => handleSuccess('google', credential),
    })
    window.google.accounts.id.prompt((n) => {
      if (n.isNotDisplayed() || n.isSkippedMoment()) setLoading(null)
    })
  }

  // ── Facebook ──────────────────────────────────────────────────────────────
  const handleFacebook = () => {
    if (!FACEBOOK_APP_ID) {
      toast.error('כניסה עם Facebook לא הוגדרה — הוסף VITE_FACEBOOK_APP_ID ל-.env')
      return
    }
    if (!window.FB) {
      toast.error('ספריית Facebook עוד לא נטענה, נסה שוב')
      return
    }
    setLoading('facebook')
    window.FB.login(
      (resp) => {
        if (resp.authResponse) {
          handleSuccess('facebook', resp.authResponse.accessToken)
        } else {
          setLoading(null)
        }
      },
      { scope: 'email,public_profile' }
    )
  }

  return (
    <>
      {/* Divider */}
      <div className="relative my-5">
        <div className="absolute inset-0 flex items-center">
          <div className="w-full border-t border-gray-200" />
        </div>
        <div className="relative flex justify-center text-sm">
          <span className="bg-white px-3 text-gray-400">או המשך עם</span>
        </div>
      </div>

      {/* Buttons */}
      <div className="grid grid-cols-2 gap-3">
        {/* Google */}
        <button
          type="button"
          onClick={handleGoogle}
          disabled={!!loading}
          className="flex items-center justify-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
        >
          {loading === 'google' ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" aria-hidden="true">
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
              <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z"/>
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
            </svg>
          )}
          Google
        </button>

        {/* Facebook */}
        <button
          type="button"
          onClick={handleFacebook}
          disabled={!!loading}
          className="flex items-center justify-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
        >
          {loading === 'facebook' ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" aria-hidden="true">
              <path fill="#1877F2" d="M24 12.073C24 5.405 18.627 0 12 0S0 5.405 0 12.073C0 18.1 4.388 23.094 10.125 24v-8.437H7.078v-3.49h3.047V9.41c0-3.025 1.792-4.697 4.533-4.697 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.93-1.956 1.883v2.263h3.328l-.532 3.49h-2.796V24C19.612 23.094 24 18.1 24 12.073z"/>
            </svg>
          )}
          Facebook
        </button>
      </div>
    </>
  )
}
