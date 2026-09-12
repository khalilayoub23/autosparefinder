/**
 * Safe WhatsApp/Signal diagnostic telemetry (root-fix 2026-09-12).
 *
 * Closes two independent, related logging defects found during the
 * "Waiting for this message" forensic investigation:
 *
 * 1. Baileys' own retry / getMessage / session-recovery / decrypt-error
 *    telemetry is emitted through the `logger` object passed into
 *    `makeWASocket()` — but this bridge previously configured that logger as
 *    `pino({ level: 'silent' })`, silencing ALL of it, including the small
 *    set of events that would actually help diagnose a future incident like
 *    the one investigated.
 *
 * 2. A SEPARATE, unrelated leak: `libsignal` (`@whiskeysockets/libsignal-node`,
 *    a TRANSITIVE dependency of Baileys — node_modules/libsignal/src/
 *    session_record.js) calls the bare, global `console.info` / `console.warn`
 *    / `console.error` DIRECTLY — never through the `logger` object Baileys
 *    is configured with — to print raw `SessionEntry` objects (root/chain/
 *    ephemeral Signal key material, in the clear) every time a session opens,
 *    closes, or is pruned. Because this bypasses Baileys' logger entirely, no
 *    pino level or config can silence it — it can only be intercepted at the
 *    `console` object itself, which is what `installSafeConsoleFilter()` does.
 *
 * DESIGN PRINCIPLE for both fixes: default-deny, not default-allow.
 *   - libsignal's bare console calls: only the 4 known message-carrying
 *     prefixes (verified against the actual installed source — see the
 *     accompanying report) are ever intercepted and replaced with sanitized
 *     metadata; the raw object argument is NEVER passed to the real console
 *     method for those. Anything else libsignal (or our own code) ever logs
 *     via console is passed through completely unchanged.
 *   - Baileys' pino logger: only a fixed, manually-vetted set of message
 *     strings (again, confirmed against the installed
 *     lib/Socket/messages-recv.js source) are allowed to carry an extracted,
 *     hand-picked-safe-fields object. Any OTHER Baileys log call — known or
 *     not yet seen — has its object argument stripped entirely; at
 *     debug/trace severity it is suppressed outright (volume control), at
 *     info/warn/error it still prints as plain text (never silently losing a
 *     genuinely important signal), just never with an unvetted object
 *     attached.
 *
 * Nothing here changes Signal Protocol behavior, Baileys' own decision
 * logic, or credential/session persistence — this module ONLY changes what
 * gets printed to Docker logs.
 */
import pino from 'pino'

// ── Part 1: libsignal's bare `console.*` SessionEntry leak ─────────────────

// Exact message prefixes confirmed via the installed source
// (node_modules/libsignal/src/session_record.js, SessionRecord class) that
// carry a raw session object as their SECOND argument.
const LIBSIGNAL_SESSION_PREFIXES = new Map([
  ['Session already closed', 'already_closed'],
  ['Closing session:', 'closing'],
  ['Opening session:', 'opening'],
  ['Removing old closed session:', 'pruned'],
])

/**
 * Extract ONLY fields proven safe from a libsignal SessionEntry-shaped
 * object: numeric IDs/timestamps/counters and booleans. Explicitly NEVER
 * touches `_chains`, `currentRatchet` (ephemeralKeyPair/rootKey/
 * lastRemoteEphemeralKey), or `indexInfo.baseKey` / `indexInfo.
 * remoteIdentityKey` / `pendingPreKey.baseKey` — every one of those is raw
 * Signal Protocol key material (Buffers).
 */
export function sanitizeSessionEntry(session) {
  if (!session || typeof session !== 'object') return {}
  const info = session.indexInfo && typeof session.indexInfo === 'object' ? session.indexInfo : {}
  const pending = session.pendingPreKey && typeof session.pendingPreKey === 'object' ? session.pendingPreKey : null
  const safe = {
    registration_id: typeof session.registrationId === 'number' ? session.registrationId : undefined,
    has_pending_prekey: !!pending,
    prekey_id: pending && typeof pending.preKeyId === 'number' ? pending.preKeyId : undefined,
    signed_prekey_id: pending && typeof pending.signedKeyId === 'number' ? pending.signedKeyId : undefined,
    session_closed_at: typeof info.closed === 'number' && info.closed !== -1 ? info.closed : null,
    session_used_at: typeof info.used === 'number' ? info.used : undefined,
    session_created_at: typeof info.created === 'number' ? info.created : undefined,
  }
  // Never emit an `undefined`-valued key (keeps output compact and avoids
  // accidentally shipping a key whose value type wasn't validated above).
  for (const k of Object.keys(safe)) {
    if (safe[k] === undefined) delete safe[k]
  }
  return safe
}

/**
 * Install the console.info/warn/error interceptor. Returns a restore
 * function (test-only use; production never calls it). Idempotent — calling
 * it twice does not double-wrap (guarded by a marker on the wrapped function).
 */
export function installSafeConsoleFilter() {
  if (console.info && console.info.__waSafeWrapped) {
    return () => {} // already installed — no-op restore
  }
  const original = {
    info: console.info.bind(console),
    warn: console.warn.bind(console),
    error: console.error.bind(console),
  }

  function makeWrapped(level) {
    const wrapped = (...args) => {
      const first = args[0]
      if (typeof first === 'string') {
        for (const [prefix, reason] of LIBSIGNAL_SESSION_PREFIXES) {
          if (first.startsWith(prefix)) {
            const safe = sanitizeSessionEntry(args[1])
            original[level](`[wa_signal_session] reason=${reason}`, JSON.stringify(safe))
            return
          }
        }
      }
      // Not a recognized sensitive pattern (includes libsignal's own scalar-only
      // lines like "Migrating session to:" / "V1 session storage migration
      // error: ..." / "Session already open", and every one of this project's
      // own console.log/error calls) — pass through completely unchanged.
      original[level](...args)
    }
    wrapped.__waSafeWrapped = true
    return wrapped
  }

  console.info = makeWrapped('info')
  console.warn = makeWrapped('warn')
  console.error = makeWrapped('error')

  return function restoreConsole() {
    console.info = original.info
    console.warn = original.warn
    console.error = original.error
  }
}

// ── Part 2: Baileys' own pino-logger telemetry (retry/getMessage/session) ──

const _LAST4 = /(\d{4})(?:@|:|$)/

/** `972586050155@s.whatsapp.net` -> `...0155`. Never the full JID. */
export function jidHint(jid) {
  if (typeof jid !== 'string' || !jid) return undefined
  const m = jid.match(_LAST4)
  return m ? `...${m[1]}` : '...'
}

// Exact message strings/prefixes confirmed via the installed source
// (node_modules/@whiskeysockets/baileys/lib/Socket/messages-recv.js).
// Each extractor picks ONLY the fields manually verified safe at that call
// site — see the report for the exact grep evidence per line.
const WA_EVENT_EXTRACTORS = [
  ['recv retry request, but message not available', (o) => ({
    event: 'wa_get_message', result: 'not_found', msg_id: o?.id, jid_hint: jidHint(o?.jid),
  })],
  ['recv retry request', (o) => ({
    event: 'wa_retry', msg_id: o?.key?.id, jid_hint: jidHint(o?.key?.remoteJid),
  })],
  ['recv retry for not fromMe message', (o) => ({
    event: 'wa_retry', msg_id: o?.key?.id,
  })],
  ['will not send message again, as sent too many times', (o) => ({
    event: 'wa_retry_limit', msg_id: o?.key?.id,
  })],
  ['reached retry limit, clearing', (o) => ({
    event: 'wa_retry_limit', retry_count: o?.retryCount, msg_id: o?.msgId,
  })],
  ['error in sending message again', (o) => ({
    event: 'wa_get_message_error',
    msg_ids: Array.isArray(o?.ids) ? o.ids : undefined,
    error: typeof o?.trace === 'string' ? o.trace.split('\n')[0] : undefined,
  })],
  ['forced new session for retry recp', (o) => ({
    event: 'wa_signal_session', reason: 'forced_for_retry', send_to_all: !!o?.sendToAll,
  })],
  ['error in handling message', (o) => ({
    // `node` (the XML stanza, which can carry ciphertext) is DELIBERATELY
    // never included — only the error's own message text.
    event: 'wa_decrypt_error',
    error: (o?.error && typeof o.error.message === 'string') ? o.error.message
      : (typeof o?.error === 'string' ? o.error : undefined),
  })],
  ['recv pre-key count', (o) => ({
    event: 'wa_signal_session', reason: 'prekey_count', count: o?.count,
    should_upload_more: !!o?.shouldUploadMorePreKeys,
  })],
  ['sendRetryRequest:', () => ({ event: 'wa_retry', reason: 'sent_placeholder_resend_request' })],
]

function findExtractor(msg) {
  for (const [prefix, extractor] of WA_EVENT_EXTRACTORS) {
    if (msg === prefix || msg.startsWith(prefix)) return extractor
  }
  return null
}

/**
 * A pino logger for Baileys that surfaces retry/getMessage/session/decrypt
 * telemetry (see WA_EVENT_EXTRACTORS) with safe, hand-picked fields only,
 * while suppressing (at debug/trace) or text-stripping (at info+) every
 * OTHER Baileys log call — so enabling real logging here cannot (a) leak an
 * object we have not manually vetted, or (b) flood production logs with
 * routine per-stanza debug noise.
 */
export function createSafeBaileysLogger() {
  return pino({
    level: 'debug',
    hooks: {
      logMethod(inputArgs, method, level) {
        const msg = inputArgs[inputArgs.length - 1]
        const obj = inputArgs.length > 1 ? inputArgs[0] : undefined
        if (typeof msg !== 'string') {
          // Unexpected call shape — never guess; log nothing but the level.
          return
        }
        const extractor = findExtractor(msg)
        if (extractor) {
          const safe = extractor(obj)
          return method.apply(this, [safe, `[${safe.event}]`])
        }
        // level numbers: trace=10 debug=20 info=30 warn=40 error=50 fatal=60
        if (level < 30) {
          return // suppress unvetted debug/trace noise entirely (volume control)
        }
        // info/warn/error we don't have a specific extractor for: keep the
        // message TEXT (it's operationally useful to know something notable
        // happened) but never the attached object — default-deny for
        // anything not explicitly vetted above.
        return method.apply(this, [msg])
      },
    },
  })
}
