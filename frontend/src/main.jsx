import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'

// Cart-recovery hand-off: the /api/v1/customers/cart/recover link (abandoned-cart
// email/WhatsApp) logs the RECIPIENT into their own account and redirects here with
// the session tokens in the URL FRAGMENT (#ar=…&rt=…). The fragment never reaches the
// server or Referer header. Consume it before React mounts: store the tokens, drop any
// stale identity/cart cached from a *different* user on this device (so we don't show
// the wrong person's cart), then strip the fragment from the URL.
try {
  if (window.location.hash && window.location.hash.includes('ar=')) {
    const h = new URLSearchParams(window.location.hash.slice(1))
    const ar = h.get('ar'), rt = h.get('rt')
    if (ar) {
      localStorage.removeItem('auth-store')   // clear any other user's persisted session
      localStorage.removeItem('cart-store')   // clear any other user's cached cart items
      localStorage.setItem('access_token', ar)
      if (rt) localStorage.setItem('refresh_token', rt)
      // Remove the tokens from the address bar / history immediately.
      history.replaceState(null, '', window.location.pathname + window.location.search)
    }
  }
} catch { /* non-fatal — fall through to normal boot */ }

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
