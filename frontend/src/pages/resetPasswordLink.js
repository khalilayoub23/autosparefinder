// Reset-link logic for pages/ResetPassword.jsx (FIXES_TRACKER #163/#164), kept free of React so vitest can cover it.
//
// The reset token is a password-equivalent secret for one hour. It is read from the link once, held only in
// component state, sent only in a POST body, and never rendered, stored or left in the address bar.

const STATUSES = ['valid', 'used', 'expired', 'invalid']

// New links carry the token in the fragment (#token=...), which no server or log ever receives; links emailed
// before that change used ?token=... and stay supported until they expire.
export function readResetToken(hash, search) {
  const fromHash = new URLSearchParams(String(hash || '').replace(/^#/, '')).get('token')
  const fromQuery = new URLSearchParams(String(search || '')).get('token')
  return (fromHash || fromQuery || '').trim()
}

export function normalizeResetStatus(status) {
  return STATUSES.includes(status) ? status : 'invalid'
}

// Why the link cannot be used. Never names the account, the email or the token.
export const RESET_LINK_MESSAGES = {
  used: {
    title: 'הקישור כבר אינו בתוקף',
    body: 'הקישור הזה כבר שימש לאיפוס הסיסמה, או שהסיסמה שונתה מאז. כל קישור לאיפוס סיסמה פועל פעם אחת בלבד.',
  },
  expired: {
    title: 'פג תוקף הקישור',
    body: 'קישור לאיפוס סיסמה תקף לשעה אחת בלבד, והזמן הזה עבר.',
  },
  invalid: {
    title: 'הקישור אינו תקין',
    body: 'לא הצלחנו לזהות את הקישור. ייתכן שהוא הועתק באופן חלקי.',
  },
  unavailable: {
    title: 'לא ניתן לבדוק את הקישור כרגע',
    body: 'אירעה תקלה זמנית בבדיקת הקישור. אפשר לנסות שוב בעוד רגע.',
  },
}

export const RESET_LINK_NEXT_STEP = 'כדי לאפס את הסיסמה, בקש קישור חדש. נשלח אותו לכתובת המייל שלך.'

export function resetLinkMessage(status) {
  return RESET_LINK_MESSAGES[status] || RESET_LINK_MESSAGES.invalid
}

// A confirm that failed because the link died while the page was open -> the link state to show, else null.
export function linkStatusFromConfirmError(err) {
  if (err?.response?.status !== 400) return null
  return normalizeResetStatus(err.response.data?.reason)
}

// The validate endpoint does not exist yet (frontend deployed before the backend, or a backend rollback): behave like
// the page did before #163 - show the form and let the confirm answer decide.
export function validateEndpointMissing(err) {
  return [404, 405].includes(err?.response?.status)
}

// A displayable message from an API error: only a string, never an object/array (a 422 `detail` is a list).
export function apiErrorText(err, fallback) {
  const data = err?.response?.data
  for (const value of [data?.error, data?.detail]) {
    if (typeof value === 'string' && value.trim()) return value
  }
  return fallback
}
