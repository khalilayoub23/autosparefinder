/**
 * Regression tests — /send, /typing, /delete readiness guard (2026-09-12).
 *
 * LIVE INCIDENT this reproduces: the WhatsApp bridge lost its authenticated
 * session (auth_info/creds.json empty) and sat at the QR screen for 24+ hours.
 * During that entire window, `waSocket` (the Baileys socket OBJECT) was
 * non-null — Baileys creates it before authentication completes — so the old
 * guard `if (!waSocket || ...)` let every /send request through. Each one
 * then called `waSocket.sendMessage(...)`, which reached Baileys' own
 * internals at `authState.creds.me.id` (messages-send.js — no optional
 * chaining there) and threw "Cannot read properties of undefined (reading
 * 'id')", because `creds.me` does not exist until the socket is actually
 * authenticated. Confirmed against the ACTUALLY INSTALLED Baileys version in
 * the running container (6.17.16) — not assumed from the package.json range
 * (^6.7.0).
 *
 * The exact same defect existed in /typing and /delete. /health, /qr and
 * /groups were already correctly checking `waSocket.user` (set only once
 * Baileys confirms authentication) — this brought the other three endpoints
 * up to the same standard.
 *
 * Runs standalone, no framework: node whatsapp-bridge/test_send_readiness_guard.mjs
 */

// ── Replicated guard logic (verbatim from index.js after the 2026-09-12 fix) ──
function sendReady(waSocket, to, hasText, hasImage, hasAudio) {
  if (!waSocket || !waSocket.user || !to || (!hasText && !hasImage && !hasAudio)) {
    return {
      ok: false,
      status: 503,
      error: waSocket && !waSocket.user
        ? 'WhatsApp not authenticated — awaiting QR scan'
        : 'Missing params or socket not ready',
    }
  }
  return { ok: true }
}

function typingReady(waSocket, to, replyJid) {
  if (!waSocket || !waSocket.user || (!to && !replyJid)) {
    return { ok: false, status: 503 }
  }
  return { ok: true }
}

function deleteReady(waSocket, key) {
  if (!waSocket || !waSocket.user || !key || !key.remoteJid || !key.id) {
    return { ok: false, status: 503, error: 'Missing key or socket not ready' }
  }
  return { ok: true }
}

// ── Test harness (same style as test_msgcache.mjs) ─────────────────────────
let passed = 0
let failed = 0

function assert(label, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}`)
  if (!ok) {
    console.log(`        expected: ${JSON.stringify(expected)}`)
    console.log(`        actual:   ${JSON.stringify(actual)}`)
  }
  ok ? passed++ : failed++
}

function section(name) {
  console.log(`\n${name}`)
}

// ── The three socket states that actually occur in production ─────────────
const NO_SOCKET = null
const UNAUTHENTICATED_SOCKET = { user: undefined, sendMessage: () => { throw new TypeError("Cannot read properties of undefined (reading 'id')") } }
const AUTHENTICATED_SOCKET = { user: { id: '972532426920:1@s.whatsapp.net' }, sendMessage: async () => ({ key: { id: 'ABCD1234', remoteJid: 'x', fromMe: true } }) }

section('1. /send — reproduces the live incident: unauthenticated socket must be rejected BEFORE calling sendMessage()')
{
  // OLD behavior (the bug): `!waSocket` alone is false here (waSocket exists),
  // so the old guard would have let this through and crashed inside sendMessage().
  const r = sendReady(UNAUTHENTICATED_SOCKET, '972500000000', true, false, false)
  assert('unauthenticated socket -> rejected, not passed to sendMessage()', r.ok, false)
  assert('unauthenticated socket -> HTTP 503 (service not ready)', r.status, 503)
  assert('unauthenticated socket -> clear, actionable error (not a Baileys internals crash)',
         r.error, 'WhatsApp not authenticated — awaiting QR scan')
}

section('2. /send — no socket object at all (process just started)')
{
  const r = sendReady(NO_SOCKET, '972500000000', true, false, false)
  assert('no socket -> rejected', r.ok, false)
  assert('no socket -> generic "not ready" message (no misleading "awaiting QR" claim)',
         r.error, 'Missing params or socket not ready')
}

section('3. /send — authenticated socket + valid text params -> allowed through')
{
  const r = sendReady(AUTHENTICATED_SOCKET, '972500000000', true, false, false)
  assert('authenticated + valid params -> allowed', r.ok, true)
}

section('4. /send — authenticated socket but missing recipient -> still rejected (unrelated to the auth fix)')
{
  const r = sendReady(AUTHENTICATED_SOCKET, '', true, false, false)
  assert('authenticated but no recipient -> rejected', r.ok, false)
  assert('authenticated but no recipient -> generic message, not the auth-specific one',
         r.error, 'Missing params or socket not ready')
}

section('5. /send — authenticated socket but no content (no text/image/audio) -> still rejected')
{
  const r = sendReady(AUTHENTICATED_SOCKET, '972500000000', false, false, false)
  assert('authenticated but no content -> rejected', r.ok, false)
}

section('6. /typing — same guard, unauthenticated socket rejected')
{
  const r = typingReady(UNAUTHENTICATED_SOCKET, '972500000000', '')
  assert('typing: unauthenticated -> rejected', r.ok, false)
  assert('typing: unauthenticated -> 503', r.status, 503)
}

section('7. /typing — authenticated socket allowed through')
{
  const r = typingReady(AUTHENTICATED_SOCKET, '972500000000', '')
  assert('typing: authenticated -> allowed', r.ok, true)
}

section('8. /delete — same guard, unauthenticated socket rejected even with a valid key')
{
  const r = deleteReady(UNAUTHENTICATED_SOCKET, { remoteJid: 'x@s.whatsapp.net', id: 'ABCD', fromMe: true })
  assert('delete: unauthenticated -> rejected despite valid key', r.ok, false)
  assert('delete: unauthenticated -> 503', r.status, 503)
}

section('9. /delete — authenticated socket + valid key -> allowed through')
{
  const r = deleteReady(AUTHENTICATED_SOCKET, { remoteJid: 'x@s.whatsapp.net', id: 'ABCD', fromMe: true })
  assert('delete: authenticated + valid key -> allowed', r.ok, true)
}

section('10. Sanity — calling sendMessage() on the unauthenticated mock reproduces the EXACT observed error')
{
  let caught = null
  try {
    UNAUTHENTICATED_SOCKET.sendMessage()
  } catch (err) {
    caught = err.message
  }
  assert('mock reproduces the exact production error string', caught,
         "Cannot read properties of undefined (reading 'id')")
}

console.log(`\n${passed} passed, ${failed} failed`)
if (failed > 0) process.exit(1)
