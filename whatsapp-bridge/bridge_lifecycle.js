/**
 * Script: whatsapp-bridge/bridge_lifecycle.js
 * Purpose: Two root fixes for the Baileys bridge (2026-09-20), kept in one small testable module.
 *
 * 1) ORPHANED QUERY TIMEOUT -> PROCESS CRASH (169 crashes in the 2026-09-14..20 dead-session period)
 *    Baileys' socket.js query() does:
 *        const wait = waitForMessage(msgId, timeoutMs)   // promise created FIRST, timer armed
 *        await sendNode(node)                            // throws 'Connection Closed' on a dead socket
 *        const result = await wait                       // never reached
 *    When sendNode throws, `wait` is orphaned: nobody awaits it, and exactly defaultQueryTimeoutMs
 *    (60 s here) later its timer rejects `Boom('Timed Out', 408)` with no handler -> Node's default
 *    unhandled-rejection behaviour terminates the process -> Docker restarts the bridge (measured
 *    gap send-error -> crash: median 60 s). The orphan is created INSIDE the library, so no caller
 *    can attach a handler to it; process-level `unhandledRejection` is the only place it is observable.
 *    Fix, two layers:
 *      a. PREVENTION (primary): socketUnavailable() - /send, /typing, /delete, /groups, /group/create
 *         refuse (503, controlled) unless the socket is actually usable, so no query is ever started on
 *         a dead / reconnecting / logged-out socket.
 *      b. CONTAINMENT (residual race, e.g. the socket dies between the gate and sendNode):
 *         installOrphanTimeoutGuard() handles ONLY the exact signature (Boom 'Timed Out', status 408,
 *         captured stack contains `waitForMessage`), logs it (ORPHAN_QUERY_TIMEOUT) and keeps the
 *         process alive. ANY other unhandled rejection is logged and re-thrown, i.e. still crashes
 *         exactly like Node's default - real failures are never swallowed.
 *
 * 2) FALSE HEALTH SIGNAL: /health.connected was `!!waSocket.user`, which is true whenever stored
 *    credentials exist - including after WhatsApp rejected the login (401). connected is now derived
 *    from the connection.update events (open / close / qr) plus the live ws state, so a logged-out or
 *    reconnecting socket reports connected=false. No WhatsApp API call is made per /health request.
 *
 * 3) LISTENER BOUNDARY (messages.upsert): the inbound error branch awaited sock.sendMessage() inside an
 *    async event listener. Nobody awaits a listener, so a dead-socket 'Connection Closed' there became an
 *    unhandled rejection (process exit), and it also aborted the rest of the message batch.
 *    safeListenerSend() owns that boundary: skip when the socket is unusable, contain the expected
 *    dead-socket failure (logged as LISTENER_SEND_*), re-throw anything else unchanged.
 *
 * Data Imported/Modified: none (in-memory state only).
 * Last Updated: 2026-09-20
 */

export const STATES = Object.freeze({
  CONNECTING: 'CONNECTING',
  AWAITING_QR: 'AWAITING_QR',
  CONNECTED: 'CONNECTED',
  DISCONNECTED: 'DISCONNECTED',
  SESSION_INVALID: 'SESSION_INVALID',
})

const LOGGED_OUT = 401 // Baileys DisconnectReason.loggedOut: credentials rejected server-side

/** Connection state derived ONLY from connection.update events (no stored-credential inference). */
export function createConnectionState(now = () => new Date().toISOString()) {
  let state = STATES.CONNECTING
  let since = now()
  let lastDisconnect = null
  const set = (next) => {
    if (next !== state) {
      state = next
      since = now()
    }
  }
  return {
    get state() { return state },
    get since() { return since },
    get lastDisconnect() { return lastDisconnect },
    onConnecting() { set(STATES.CONNECTING) },
    onQr() { set(STATES.AWAITING_QR) },
    onOpen() { set(STATES.CONNECTED) },
    onClose(code, reason) {
      lastDisconnect = { code: code ?? null, reason: reason || null, at: now() }
      set(code === LOGGED_OUT ? STATES.SESSION_INVALID : STATES.DISCONNECTED)
    },
  }
}

/** null when the socket can be used for a WhatsApp operation, otherwise a human-readable reason. */
export function socketUnavailable(conn, sock) {
  if (!sock) return 'WhatsApp socket not created yet'
  switch (conn.state) {
    case STATES.SESSION_INVALID:
      return 'WhatsApp session invalid (logged out by WhatsApp) — re-pairing required'
    case STATES.AWAITING_QR:
      return 'WhatsApp not authenticated — awaiting QR scan'
    case STATES.CONNECTING:
      return 'WhatsApp socket connecting'
    case STATES.DISCONNECTED:
      return 'WhatsApp disconnected — reconnecting'
    default:
      break
  }
  if (!sock.user) return 'WhatsApp not authenticated'
  if (sock.ws && sock.ws.isOpen === false) return 'WhatsApp socket closed — reconnecting'
  return null
}

export const isUsable = (conn, sock) => socketUnavailable(conn, sock) === null

/** /health payload. Cheap and side-effect free: only reads in-memory state. */
export function healthSnapshot({ conn, sock, latestQR, expectedNumber, accountMismatch }) {
  const usable = isUsable(conn, sock)
  return {
    ok: true,
    connected: usable,
    state: usable ? STATES.CONNECTED : conn.state,
    awaiting_qr_scan: latestQR !== null || conn.state === STATES.AWAITING_QR,
    session_invalid: conn.state === STATES.SESSION_INVALID,
    jid: sock?.user?.id || null,
    expected_number: expectedNumber,
    // Non-null => the bridge is linked to the WRONG WhatsApp account.
    account_mismatch: accountMismatch,
    state_since: conn.since,
    last_disconnect: conn.lastDisconnect,
  }
}

/** Dead/closed-socket failure that an awaited Baileys call reports (Boom 'Connection Closed', 428). */
export function isExpectedDeadSocketError(err) {
  return !!err && (err.message === 'Connection Closed' || err.output?.statusCode === 428)
}

/**
 * Best-effort send from an EVENT LISTENER (nobody awaits a listener, so a throw there becomes an
 * unhandled rejection that terminates the process). Ownership boundary = the listener itself:
 *   - socket not usable  -> skip WITHOUT calling Baileys (no query, no orphan timer), log it
 *   - expected dead-socket failure (Connection Closed) -> caught + logged as a controlled event
 *   - ANY other error    -> re-thrown unchanged (visibility/behaviour preserved, never swallowed)
 */
export async function safeListenerSend({ conn, sock, jid, payload, log, what }) {
  const unavailable = socketUnavailable(conn, sock)
  if (unavailable) {
    log?.('LISTENER_SEND_SKIPPED', `${what}: ${unavailable}`)
    return { ok: false, skipped: true }
  }
  try {
    await sock.sendMessage(jid, payload)
    return { ok: true }
  } catch (err) {
    if (isExpectedDeadSocketError(err)) {
      log?.('LISTENER_SEND_FAILED_DEAD_SOCKET', `${what}: ${err.message}`)
      return { ok: false, expected: true }
    }
    throw err
  }
}

/** Exact signature of Baileys' orphaned query timer - deliberately narrow. */
export function isOrphanQueryTimeout(reason) {
  return !!reason
    && reason.isBoom === true
    && reason.message === 'Timed Out'
    && reason.output?.statusCode === 408
    && typeof reason.data?.stack === 'string'
    && /\bat waitForMessage\b/.test(reason.data.stack)
}

/**
 * Contain ONLY the orphaned-query timeout; everything else keeps Node's default fatal behaviour.
 * `log(event, detail)` keeps the timeout observable (bridge passes logEvent -> connection_events.log).
 */
export function installOrphanTimeoutGuard({ log, proc = process } = {}) {
  let contained = 0
  const handler = (reason) => {
    if (isOrphanQueryTimeout(reason)) {
      contained += 1
      log?.('ORPHAN_QUERY_TIMEOUT', `contained=${contained} status=408 origin=baileys.query.waitForMessage (unawaited after a failed send on a closed socket)`)
      return
    }
    log?.('UNHANDLED_REJECTION_FATAL', `${reason?.name || typeof reason}: ${String(reason?.message ?? reason).slice(0, 200)}`)
    throw reason instanceof Error ? reason : new Error('Unhandled rejection: ' + String(reason))
  }
  proc.on('unhandledRejection', handler)
  return {
    handler,
    get contained() { return contained },
    uninstall() { proc.off('unhandledRejection', handler) },
  }
}
