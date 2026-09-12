/**
 * Regression tests — safe_logging.js (2026-09-12).
 *
 * Proves both logging defects found during the "Waiting for this message"
 * forensic investigation are closed:
 *   1. Useful Baileys retry/getMessage/session/decrypt telemetry is now
 *      actually observable (was previously fully silenced).
 *   2. libsignal's raw SessionEntry console leak, and any equivalent
 *      unvetted object Baileys might log, can never reach the real console/
 *      log output — only hand-picked-safe fields ever do.
 *
 * Uses the REAL module (not a replica) so what's tested is exactly what
 * index.js runs. Captures the real `console.info/warn/error` and the real
 * pino logger's output stream — no framework, matching this project's
 * existing test convention.
 *
 * Run: node whatsapp-bridge/test_safe_logging.mjs
 */
import {
  installSafeConsoleFilter,
  sanitizeSessionEntry,
  jidHint,
  createSafeBaileysLogger,
} from './safe_logging.js'

let passed = 0
let failed = 0

function assert(label, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  console.error(`  ${ok ? 'PASS' : 'FAIL'}  ${label}`) // console.error so it's never confused with captured test output
  if (!ok) {
    console.error(`        expected: ${JSON.stringify(expected)}`)
    console.error(`        actual:   ${JSON.stringify(actual)}`)
  }
  ok ? passed++ : failed++
}

function assertTrue(label, cond) {
  console.error(`  ${cond ? 'PASS' : 'FAIL'}  ${label}`)
  cond ? passed++ : failed++
}

function section(name) {
  console.error(`\n${name}`)
}

// A representative, fully-populated SessionEntry-shaped object — same field
// names actually observed in production logs (values are fabricated dummy
// bytes, never real key material, but the SHAPE matches exactly).
function fakeSessionEntry() {
  return {
    _chains: {
      'FAKE_CHAIN_KEY_ID_1': { chainKey: { key: Buffer.from('FAKE_CHAIN_KEY_BYTES') }, chainType: 2, messageKeys: {} },
    },
    registrationId: 123456,
    currentRatchet: {
      ephemeralKeyPair: {
        pubKey: Buffer.from('FAKE_PUB_KEY_BYTES'),
        privKey: Buffer.from('FAKE_PRIVATE_KEY_BYTES_MUST_NEVER_APPEAR'),
      },
      lastRemoteEphemeralKey: Buffer.from('FAKE_REMOTE_EPHEMERAL_BYTES'),
      previousCounter: 7,
      rootKey: Buffer.from('FAKE_ROOT_KEY_BYTES_MUST_NEVER_APPEAR'),
    },
    indexInfo: {
      baseKey: Buffer.from('FAKE_BASE_KEY_BYTES_MUST_NEVER_APPEAR'),
      baseKeyType: 1,
      closed: -1,
      used: 1789200490011,
      created: 1789200490011,
      remoteIdentityKey: Buffer.from('FAKE_REMOTE_IDENTITY_KEY_MUST_NEVER_APPEAR'),
    },
    pendingPreKey: {
      signedKeyId: 4136895,
      baseKey: Buffer.from('FAKE_PENDING_BASE_KEY_MUST_NEVER_APPEAR'),
      preKeyId: 11154968,
    },
  }
}

const SENSITIVE_MARKERS = [
  'FAKE_CHAIN_KEY_BYTES', 'FAKE_PUB_KEY_BYTES', 'FAKE_PRIVATE_KEY_BYTES_MUST_NEVER_APPEAR',
  'FAKE_REMOTE_EPHEMERAL_BYTES', 'FAKE_ROOT_KEY_BYTES_MUST_NEVER_APPEAR',
  'FAKE_BASE_KEY_BYTES_MUST_NEVER_APPEAR', 'FAKE_REMOTE_IDENTITY_KEY_MUST_NEVER_APPEAR',
  'FAKE_PENDING_BASE_KEY_MUST_NEVER_APPEAR', '_chains', 'chainKey', 'currentRatchet',
  'ephemeralKeyPair', 'rootKey', 'baseKey', 'remoteIdentityKey',
]

function containsAnySensitiveMarker(text) {
  return SENSITIVE_MARKERS.some((m) => text.includes(m))
}

// ═══════════════════════════════════════════════════════════════════════
section('A. sanitizeSessionEntry() — only safe scalar fields survive')
{
  const safe = sanitizeSessionEntry(fakeSessionEntry())
  assert('registration_id preserved', safe.registration_id, 123456)
  assert('prekey_id preserved', safe.prekey_id, 11154968)
  assert('signed_prekey_id preserved', safe.signed_prekey_id, 4136895)
  assert('has_pending_prekey preserved', safe.has_pending_prekey, true)
  assert('session_closed_at preserved (open session -> null)', safe.session_closed_at, null)
  assertTrue('no key named _chains/currentRatchet/indexInfo/pendingPreKey in output',
    !('_chains' in safe) && !('currentRatchet' in safe) && !('indexInfo' in safe) && !('pendingPreKey' in safe))
  const serialized = JSON.stringify(safe)
  assertTrue('serialized sanitized output contains ZERO sensitive markers',
    !containsAnySensitiveMarker(serialized))
}

// ═══════════════════════════════════════════════════════════════════════
section('B. installSafeConsoleFilter() — libsignal-shaped console calls never leak raw objects')
{
  const restore = installSafeConsoleFilter()
  const captured = []
  const realWrite = process.stderr.write.bind(process.stderr)
  // console.info/warn/error write to stdout/stderr depending on Node config;
  // capture both streams around the calls under test.
  const realStdoutWrite = process.stdout.write.bind(process.stdout)
  process.stdout.write = (chunk, ...rest) => { captured.push(String(chunk)); return realStdoutWrite(chunk, ...rest) }
  process.stderr.write = (chunk, ...rest) => { captured.push(String(chunk)); return realWrite(chunk, ...rest) }

  try {
    console.info('Closing session:', fakeSessionEntry())
    console.warn('Session already closed', fakeSessionEntry())
    console.info('Opening session:', fakeSessionEntry())
    console.info('Removing old closed session:', fakeSessionEntry())
  } finally {
    process.stdout.write = realStdoutWrite
    process.stderr.write = realWrite
    restore()
  }

  const allOutput = captured.join('\n')
  assertTrue('captured SOME output for the 4 intercepted calls', captured.length >= 4)
  assertTrue('output contains the safe [wa_signal_session] marker', allOutput.includes('wa_signal_session'))
  assertTrue('output does NOT contain any sensitive marker across all 4 calls',
    !containsAnySensitiveMarker(allOutput))
  assertTrue('output does NOT contain the literal string "SessionEntry"', !allOutput.includes('SessionEntry'))
}

// ═══════════════════════════════════════════════════════════════════════
section('C. installSafeConsoleFilter() — unrelated console calls pass through unchanged')
{
  const restore = installSafeConsoleFilter()
  const captured = []
  const realLog = console.log.bind(console)
  const realStdoutWrite = process.stdout.write.bind(process.stdout)
  process.stdout.write = (chunk, ...rest) => { captured.push(String(chunk)); return realStdoutWrite(chunk, ...rest) }
  try {
    console.log('[Bridge] Sent OK to 972500000000@s.whatsapp.net') // console.log is NOT wrapped at all
    console.info('some unrelated informational line', { safe: true })
  } finally {
    process.stdout.write = realStdoutWrite
    restore()
    void realLog
  }
  const out = captured.join('\n')
  assertTrue('unrelated console.log content passed through verbatim', out.includes('[Bridge] Sent OK to'))
  assertTrue('unrelated console.info content passed through verbatim (not in the libsignal prefix list)',
    out.includes('some unrelated informational line'))
}

// ═══════════════════════════════════════════════════════════════════════
section('D. installSafeConsoleFilter() — idempotent (installing twice does not double-wrap)')
{
  const restore1 = installSafeConsoleFilter()
  const wrappedOnce = console.info
  const restore2 = installSafeConsoleFilter()
  assertTrue('second install() is a no-op (same wrapped function reference)', console.info === wrappedOnce)
  restore2()
  restore1()
}

// ═══════════════════════════════════════════════════════════════════════
section('E. createSafeBaileysLogger() — vetted retry/getMessage/decrypt events ARE observable')
{
  async function captureLoggerOutput(fn) {
    const lines = []
    const realWrite = process.stdout.write.bind(process.stdout)
    process.stdout.write = (chunk) => { lines.push(String(chunk)); return true }
    try {
      await fn()
    } finally {
      process.stdout.write = realWrite
    }
    return lines.map((l) => { try { return JSON.parse(l) } catch { return null } }).filter(Boolean)
  }

  const logger = createSafeBaileysLogger()

  const lines = await captureLoggerOutput(() => {
    logger.debug({ jid: '972586050155@s.whatsapp.net', id: 'MSG123' }, 'recv retry request, but message not available')
    logger.debug({ key: { id: 'MSG456', remoteJid: '972586050155@s.whatsapp.net' } }, 'recv retry request')
    logger.debug({ participant: '972586050155@s.whatsapp.net', sendToAll: false }, 'forced new session for retry recp')
    logger.error({ error: new Error('decrypt boom'), node: { tag: 'enc', content: Buffer.from('CIPHERTEXT_MUST_NOT_APPEAR') } }, 'error in handling message')
  })

  assert('exactly 4 log lines produced for 4 vetted calls', lines.length, 4)
  assertTrue('event 1 is wa_get_message with result=not_found', lines[0]?.event === 'wa_get_message' && lines[0]?.result === 'not_found')
  assertTrue('event 1 msg_id preserved', lines[0]?.msg_id === 'MSG123')
  assertTrue('event 1 jid_hint is masked, not the full JID', lines[0]?.jid_hint === '...0155')
  assertTrue('event 2 is wa_retry', lines[1]?.event === 'wa_retry')
  assertTrue('event 3 is wa_signal_session (forced_for_retry)', lines[2]?.event === 'wa_signal_session' && lines[2]?.reason === 'forced_for_retry')
  assertTrue('event 4 is wa_decrypt_error with only the error message text', lines[3]?.event === 'wa_decrypt_error' && lines[3]?.error === 'decrypt boom')
  const allSerialized = JSON.stringify(lines)
  assertTrue('none of the 4 log lines contain the raw XML node/ciphertext', !allSerialized.includes('CIPHERTEXT_MUST_NOT_APPEAR'))
  assertTrue('none of the 4 log lines contain a "node" key', !lines.some((l) => 'node' in l))
}

// ═══════════════════════════════════════════════════════════════════════
section('F. createSafeBaileysLogger() — unvetted debug noise is suppressed (volume control)')
{
  async function captureRawLines(fn) {
    const lines = []
    const realWrite = process.stdout.write.bind(process.stdout)
    process.stdout.write = (chunk) => { lines.push(String(chunk)); return true }
    try { await fn() } finally { process.stdout.write = realWrite }
    return lines
  }
  const logger = createSafeBaileysLogger()
  const lines = await captureRawLines(() => {
    logger.debug({ recv: { tag: 'ack' }, sent: {} }, 'sent ack') // real Baileys line, NOT in the vetted list
    logger.trace('sendActiveReceipts set to "true"')
  })
  assert('unvetted debug/trace-level Baileys noise produces ZERO output', lines.length, 0)
}

// ═══════════════════════════════════════════════════════════════════════
section('G. createSafeBaileysLogger() — unvetted info/warn/error still visible as text, object stripped')
{
  async function captureLoggerOutput(fn) {
    const lines = []
    const realWrite = process.stdout.write.bind(process.stdout)
    process.stdout.write = (chunk) => { lines.push(String(chunk)); return true }
    try { await fn() } finally { process.stdout.write = realWrite }
    return lines.map((l) => { try { return JSON.parse(l) } catch { return null } }).filter(Boolean)
  }
  const logger = createSafeBaileysLogger()
  const lines = await captureLoggerOutput(() => {
    logger.info({ jid: '972500000000@s.whatsapp.net', secretStuff: 'MUST_NOT_APPEAR' }, 'identity changed') // real Baileys line, not vetted
  })
  assert('exactly one line produced', lines.length, 1)
  assertTrue('message text preserved', lines[0]?.msg === 'identity changed')
  assertTrue('the unvetted object payload (including its secretStuff field) is NOT present',
    !JSON.stringify(lines[0]).includes('MUST_NOT_APPEAR') && !('secretStuff' in lines[0]))
}

// ═══════════════════════════════════════════════════════════════════════
section('H. jidHint() — never returns the full JID')
{
  assert('phone JID masked to last 4 digits', jidHint('972586050155@s.whatsapp.net'), '...0155')
  assert('undefined input handled safely', jidHint(undefined), undefined)
  assert('non-string input handled safely', jidHint(12345), undefined)
}

// ═══════════════════════════════════════════════════════════════════════
console.error(`\n${passed} passed, ${failed} failed`)
if (failed > 0) process.exit(1)
