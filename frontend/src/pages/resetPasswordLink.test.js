import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import {
  apiErrorText, linkStatusFromConfirmError, normalizeResetStatus, readResetToken, resetLinkMessage,
  RESET_LINK_MESSAGES, RESET_LINK_NEXT_STEP, validateEndpointMissing,
} from './resetPasswordLink'

const TOKEN = 'AbC_dEf-0123456789abcdefghijklmnopqrstuvwxy'
const page = readFileSync(new URL('./ResetPassword.jsx', import.meta.url), 'utf8')
const HEBREW = /[֐-׿]/

describe('readResetToken (#164)', () => {
  it('reads the fragment form of new links', () => {
    expect(readResetToken(`#token=${TOKEN}`, '')).toBe(TOKEN)
  })
  it('still reads the ?token= form of links sent before the change', () => {
    expect(readResetToken('', `?token=${TOKEN}`)).toBe(TOKEN)
  })
  it('prefers the fragment and returns empty when there is no token', () => {
    expect(readResetToken('#token=frag', '?token=query')).toBe('frag')
    expect(readResetToken('', '')).toBe('')
    expect(readResetToken(undefined, undefined)).toBe('')
  })
})

describe('link states (#163)', () => {
  it('maps unknown or missing states to invalid', () => {
    for (const s of ['valid', 'used', 'expired', 'invalid']) expect(normalizeResetStatus(s)).toBe(s)
    expect(normalizeResetStatus('weird')).toBe('invalid')
    expect(normalizeResetStatus(undefined)).toBe('invalid')
  })
  it('has a distinct Hebrew title and explanation for used, expired, invalid and unavailable', () => {
    const titles = new Set()
    for (const s of ['used', 'expired', 'invalid', 'unavailable']) {
      const m = resetLinkMessage(s)
      expect(m.title).toMatch(HEBREW)
      expect(m.body).toMatch(HEBREW)
      titles.add(m.title)
    }
    expect(titles.size).toBe(4)
    expect(RESET_LINK_NEXT_STEP).toMatch(/קישור חדש/)
    expect(resetLinkMessage('nonsense')).toBe(RESET_LINK_MESSAGES.invalid)
  })
  it('the used message also covers a link consumed by a password change, and never claims a newer link replaced it', () => {
    expect(RESET_LINK_MESSAGES.used.body).toMatch(/הסיסמה שונתה/)
    expect(RESET_LINK_MESSAGES.used.body).not.toMatch(/הוחלף/)
  })
  it('a missing validate endpoint (404/405) falls back to the form; other errors do not', () => {
    expect(validateEndpointMissing({ response: { status: 404 } })).toBe(true)
    expect(validateEndpointMissing({ response: { status: 405 } })).toBe(true)
    expect(validateEndpointMissing({ response: { status: 503 } })).toBe(false)
    expect(validateEndpointMissing(new Error('network'))).toBe(false)
  })
  it('a confirm 400 becomes the link state; other errors do not', () => {
    expect(linkStatusFromConfirmError({ response: { status: 400, data: { reason: 'used' } } })).toBe('used')
    expect(linkStatusFromConfirmError({ response: { status: 400, data: { reason: 'expired' } } })).toBe('expired')
    expect(linkStatusFromConfirmError({ response: { status: 400, data: {} } })).toBe('invalid')
    expect(linkStatusFromConfirmError({ response: { status: 422, data: {} } })).toBeNull()
    expect(linkStatusFromConfirmError(new Error('network'))).toBeNull()
  })
})

describe('apiErrorText', () => {
  it('returns only strings, never a 422 detail list', () => {
    expect(apiErrorText({ response: { data: { error: 'שגיאה' } } }, 'x')).toBe('שגיאה')
    expect(apiErrorText({ response: { data: { detail: [{ msg: 'bad' }] } } }, 'fallback')).toBe('fallback')
    expect(apiErrorText(undefined, 'fallback')).toBe('fallback')
  })
})

describe('ResetPassword page source (#163/#164)', () => {
  it('never binds the token to an input or renders it', () => {
    expect(page).not.toMatch(/value=\{token\}/)
    expect(page).not.toMatch(/\bsetToken\b/)
    expect(page).not.toMatch(/\{token\}/)
    expect(page).not.toMatch(/קוד איפוס/)
  })
  it('validates the link before the form and removes the token from the URL', () => {
    expect(page).toMatch(/validateResetToken\(linkToken\)/)
    expect(page).toMatch(/navigate\('\/reset-password', \{ replace: true \}\)/)
    expect(page).toMatch(/useState\(initialToken \? 'checking' : 'request'\)/)
    expect(page).toMatch(/addEventListener\('hashchange'/)
  })
  it('password fields are new-password fields (no username field for a password manager to fill)', () => {
    expect((page.match(/autoComplete="new-password"/g) || []).length).toBe(2)
    expect(page).not.toMatch(/autoComplete="username"/)
  })
  it('never stores the token in browser storage', () => {
    expect(page).not.toMatch(/localStorage|sessionStorage/)
  })
})
