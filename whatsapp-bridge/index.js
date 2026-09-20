import { createRequire } from 'module'
const require = createRequire(import.meta.url)
const baileys = require('@whiskeysockets/baileys')
const makeWASocket = baileys.default || baileys.makeWASocket || baileys
const {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  normalizeMessageContent,
  downloadContentFromMessage,
  BufferJSON,
} = baileys

import { Boom } from '@hapi/boom'
import axios from 'axios'
import express from 'express'
import qrcode from 'qrcode-terminal'
import path from 'path'
import { atomicWriteFile } from './atomic_write.js'
import { installSafeConsoleFilter, createSafeBaileysLogger } from './safe_logging.js'
import { installOrphanTimeoutGuard, createConnectionState, socketUnavailable, healthSnapshot, safeListenerSend } from './bridge_lifecycle.js'

// Root-fix 2026-09-12 (WhatsApp logging/telemetry hardening): installed as
// early as possible, before any Signal session operation can occur, so the
// libsignal SessionEntry leak (see safe_logging.js) is intercepted from the
// very first connection attempt onward. This bridge's own console.log/error
// calls elsewhere in this file are completely unaffected — the filter only
// rewrites the small set of libsignal message prefixes proven to carry raw
// key material.
installSafeConsoleFilter()

const BACKEND_WEBHOOK = process.env.BACKEND_URL || 'http://backend:8000/api/v1/webhooks/whatsapp'
const BRIDGE_PORT = Number.parseInt(process.env.BRIDGE_PORT || '3001', 10) || 3001
// Overridable ONLY so the regression harness can run the real index.js without touching the live
// qr.txt / connection_events.log; production leaves it unset (=> /app, the bind-mounted bridge dir).
const APP_DIR = process.env.BRIDGE_APP_DIR || '/app'

// Connection-event log (added 2026-07-05): persistent, timestamped record of
// every connect/disconnect/watchdog action so disconnection patterns can be
// tracked over time. Lives on the bind mount → survives restarts, readable
// from the host at /opt/autosparefinder/whatsapp-bridge/connection_events.log
import fs from 'fs'
const EVENT_LOG = `${APP_DIR}/connection_events.log`
function logEvent(event, detail = '') {
  const line = `${new Date().toISOString()} | ${event}${detail ? ' | ' + detail : ''}\n`
  console.log('[ConnLog]', line.trim())
  try {
    // Cap growth: keep the newest ~2000 lines once the file passes 512KB
    try {
      const st = fs.statSync(EVENT_LOG)
      if (st.size > 524288) {
        const tail = fs.readFileSync(EVENT_LOG, 'utf8').split('\n').slice(-2000).join('\n')
        fs.writeFileSync(EVENT_LOG, tail + '\n')
      }
    } catch {}
    fs.appendFileSync(EVENT_LOG, line)
  } catch (err) {
    console.error('[ConnLog] write failed:', err.message)
  }
}
logEvent('PROCESS_START', `pid=${process.pid}`)
// Contain ONLY Baileys' orphaned query timeout (see bridge_lifecycle.js); every other unhandled
// rejection still terminates the process. Timeouts stay observable as ORPHAN_QUERY_TIMEOUT events.
installOrphanTimeoutGuard({ log: logEvent })

// Liveness watchdog (added 2026-07-05): Baileys sockets can die SILENTLY —
// no 'close' event fires, the process keeps running, and inbound messages
// just stop (customers get no replies). Every 3 minutes we push a presence
// update; if the write fails twice in a row the socket is dead — exit(1)
// and Docker's unless-stopped restart policy revives us with a live socket
// (session persists in auth_info, no QR re-scan needed).
let livenessTimer = null
function startLivenessWatchdog(sock) {
  if (livenessTimer) clearInterval(livenessTimer)
  let consecutiveFailures = 0
  livenessTimer = setInterval(async () => {
    try {
      await sock.sendPresenceUpdate('available')
      consecutiveFailures = 0
    } catch (err) {
      consecutiveFailures += 1
      logEvent('WATCHDOG_PING_FAILED', `attempt=${consecutiveFailures}/2 err=${err.message}`)
      if (consecutiveFailures >= 2) {
        logEvent('WATCHDOG_RESTART', 'socket dead — exiting for Docker restart')
        process.exit(1)
      }
    }
  }, 180000)
}
// Root-fix 2026-09-12: was `pino({ level: 'silent' })`, which silenced ALL
// Baileys telemetry — including the retry/getMessage/session-recovery/
// decrypt-error events that would have made the "Waiting for this message"
// incident diagnosable in real time. createSafeBaileysLogger() (safe_logging.js)
// surfaces exactly those events with hand-picked-safe fields only; every
// other Baileys log call is either suppressed (debug/trace noise) or kept as
// text-only with its object payload stripped (info/warn/error) — never an
// unvetted raw object.
const logger = createSafeBaileysLogger()
const MAX_MEDIA_BYTES = Math.max(256000, Number.parseInt(process.env.WA_MEDIA_MAX_BYTES || '6291456', 10) || 6291456)

// Bounded outbound message cache for Baileys retry recovery (Defect B fix 2026-09-10).
// When the recipient's WhatsApp fails to decrypt a message it sends a retry receipt back
// to the sender. Baileys calls getMessage({ remoteJid, fromMe, id }) on the sender side;
// if that returns a truthy value it calls relayMessage() with it.  The old stub returned
// { conversation: '' } for every message ID — including unknown ones — which caused
// Baileys to relay an empty body on every retry, producing "Waiting for this message".
// Fix: cache the proto.IMessage payload (sent.message) by its ID (sent.key.id) after
// each successful outbound send.  Unknown IDs return null, which makes Baileys log
// "message not available" and skip the relay — the correct failure mode.
// Capacity: 500 ≈ 10 days at normal production rate (~2 msgs/hour).  A restart clears
// the cache; any message sent before the restart will return null on retry (skip relay),
// which is correct — we cannot reconstruct what was sent from memory alone.
const MAX_MSG_CACHE = 500
const msgCache = new Map()  // messageId (string) → proto.IMessage

function cacheOutboundMessage(msgId, protoMessage) {
  if (!msgId || !protoMessage) return
  if (msgCache.has(msgId)) return       // first write wins; no overwrite on resend
  if (msgCache.size >= MAX_MSG_CACHE) {
    msgCache.delete(msgCache.keys().next().value)   // evict oldest (Map insertion order)
  }
  msgCache.set(msgId, protoMessage)
}

let waSocket = null
// Connection state derived from connection.update events only (never from stored credentials).
const connState = createConnectionState()
// Raw payload of the pending link QR, or null once authenticated. Kept in
// memory + /app/qr.txt so it can be rendered as an image; never served over
// HTTP (scanning it links a phone to this bridge).
let latestQR = null
// The BUSINESS WhatsApp number this bridge is supposed to run as (digits only,
// country code, no +). A QR scan links whatever phone scans it, so without this
// the bridge will happily run as the wrong account and fail silently.
const EXPECTED_NUMBER = (process.env.WHATSAPP_EXPECTED_NUMBER || '972532426920').replace(/\D/g, '')
let accountMismatch = null
// 'code' => link with an 8-char pairing code typed on the phone; anything else
// keeps the classic QR flow.
const PAIR_MODE = (process.env.WHATSAPP_PAIR_MODE || 'qr').toLowerCase()
let latestPairCode = null

const app = express()
app.use(express.json({ limit: '20mb' }))

function normalizeTargetJid(to, replyJid = '') {
  if (replyJid && replyJid.trim()) return replyJid.trim()
  const raw = String(to || '').trim()
  // Already a full JID (group "…@g.us" or user "…@s.whatsapp.net") — pass through
  // unchanged. The digit-strip below would otherwise mangle a group JID. This is how
  // outbound messages reach the owner's "updates" group (2026-08-04).
  if (raw.includes('@')) return raw
  const digits = raw.replace(/\D/g, '')
  const e164 = digits.startsWith('0') ? '972' + digits.slice(1) : digits
  return e164 + '@s.whatsapp.net'
}

async function streamToBuffer(stream) {
  const chunks = []
  for await (const chunk of stream) {
    chunks.push(chunk)
  }
  return Buffer.concat(chunks)
}

async function downloadInboundMedia(messageNode, mediaType) {
  const stream = await downloadContentFromMessage(messageNode, mediaType)
  return await streamToBuffer(stream)
}

app.post('/send', async (req, res) => {
  const unavailable = socketUnavailable(connState, waSocket)
  if (unavailable) {
    return res.status(503).json({ ok: false, error: unavailable })
  }
  const {
    to,
    text,
    reply_jid,
    image_base64,
    mime_type,
    caption,
    audio_base64,
    audio_mime,
    audio_ptt,
  } = req.body

  const hasText = typeof text === 'string' && text.trim().length > 0
  const hasImage = typeof image_base64 === 'string' && image_base64.trim().length > 0
  const hasAudio = typeof audio_base64 === 'string' && audio_base64.trim().length > 0

  // Root-fix 2026-09-12: `waSocket` is created (non-null) BEFORE authentication
  // completes and stays non-null for the entire time the bridge sits at the QR
  // screen — same defect already identified and fixed for /health, /qr and
  // /groups (see the comment above the /qr route), but never applied here.
  // Calling sendMessage() on an unauthenticated socket reaches Baileys'
  // internals at authState.creds.me.id (messages-send.js, no optional
  // chaining there) and throws "Cannot read properties of undefined (reading
  // 'id')" — a confusing 500 instead of a clear, fast, actionable failure.
  // Checking `.user` (only set once Baileys confirms authentication) matches
  // the already-correct /health check exactly.
  if (!waSocket || !waSocket.user || !to || (!hasText && !hasImage && !hasAudio)) {
    return res.status(503).json({
      ok: false,
      error: waSocket && !waSocket.user
        ? 'WhatsApp not authenticated — awaiting QR scan'
        : 'Missing params or socket not ready',
    })
  }

  try {
    const jid = normalizeTargetJid(to, reply_jid)
    let sent = null

    if (hasImage) {
      const b64 = image_base64.includes(',') ? image_base64.split(',').pop() : image_base64
      const imageBuffer = Buffer.from((b64 || '').trim(), 'base64')
      if (!imageBuffer.length) {
        return res.status(400).json({ ok: false, error: 'Invalid image payload' })
      }
      if (imageBuffer.length > MAX_MEDIA_BYTES) {
        return res.status(413).json({ ok: false, error: 'Image payload too large' })
      }
      const messagePayload = {
        image: imageBuffer,
        caption: typeof caption === 'string' ? caption : '',
      }
      if (typeof mime_type === 'string' && mime_type.trim()) {
        messagePayload.mimetype = mime_type.trim()
      }
      console.log('[Bridge] Sending image to', jid, '| bytes:', imageBuffer.length)
      sent = await waSocket.sendMessage(jid, messagePayload)
    } else if (hasAudio) {
      const b64 = audio_base64.includes(',') ? audio_base64.split(',').pop() : audio_base64
      const audioBuffer = Buffer.from((b64 || '').trim(), 'base64')
      if (!audioBuffer.length) {
        return res.status(400).json({ ok: false, error: 'Invalid audio payload' })
      }
      if (audioBuffer.length > MAX_MEDIA_BYTES) {
        return res.status(413).json({ ok: false, error: 'Audio payload too large' })
      }
      const payload = {
        audio: audioBuffer,
        mimetype: (typeof audio_mime === 'string' && audio_mime.trim()) ? audio_mime.trim() : 'audio/ogg; codecs=opus',
        ptt: audio_ptt !== false,
      }
      console.log('[Bridge] Sending audio to', jid, '| bytes:', audioBuffer.length)
      sent = await waSocket.sendMessage(jid, payload)
      cacheOutboundMessage(sent?.key?.id, sent?.message)
      if (hasText) {
        sent = await waSocket.sendMessage(jid, { text: text.trim() })
      }
    } else {
      console.log('[Bridge] Sending to', jid, '| text length:', text.length)
      sent = await waSocket.sendMessage(jid, { text })
    }

    cacheOutboundMessage(sent?.key?.id, sent?.message)
    console.log('[Bridge] Sent OK to', jid)
    // Return the message key so the caller can later delete-for-everyone (used to
    // remove a 2FA code from the chat once it's been verified). Non-breaking add.
    res.json({ ok: true, key: sent?.key || null })
  } catch (err) {
    console.error('[Bridge] Send error:', err.message)
    res.status(500).json({ ok: false, error: err.message })
  }
})

// `waSocket !== null` only proves a socket OBJECT exists — it is created before
// authentication and stays non-null while the bridge sits at the QR screen. It
// therefore reported {ok:true, connected:true} through a full logged-out outage,
// which is why nothing alerted while every owner message failed. Baileys sets
// sock.user only once the session is actually authenticated, so test THAT.
// Live QR page — auto-refreshes every 15s so the user can scan without racing the expiry.
// Returns an HTML page with the QR as an inline SVG/PNG generated from the raw payload.
app.get('/qr', (_, res) => {
  if (!latestQR) {
    if (waSocket && waSocket.user) {
      return res.send('<html><body style="font-family:sans-serif;text-align:center;padding:60px"><h2>✅ Already connected!</h2><p>WhatsApp bridge is linked and running.</p></body></html>')
    }
    return res.send('<html><meta http-equiv="refresh" content="3"><body style="font-family:sans-serif;text-align:center;padding:60px"><h2>⏳ Waiting for QR…</h2><p>Page refreshes automatically.</p></body></html>')
  }
  // Encode the raw QR payload as a data URI via the qrcode-terminal module isn't ideal;
  // instead write it to a temp file and serve an HTML page that embeds it via an <img>
  // pointing at /qr.png which we handle below.
  res.send(`<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="18">
<title>WhatsApp QR</title>
<style>body{margin:0;background:#111;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;font-family:sans-serif;color:#fff}
h2{margin-bottom:8px}p{color:#aaa;margin-top:0}img{border:16px solid white;border-radius:8px}</style>
</head><body>
<h2>📱 Scan with WhatsApp</h2>
<p>Open WhatsApp → Settings → Linked Devices → Link a Device</p>
<img src="/qr.png?t=${Date.now()}" width="320" height="320" alt="QR Code">
<p style="margin-top:16px;font-size:13px">Auto-refreshes every 18 seconds</p>
</body></html>`)
})

app.get('/qr.png', async (_, res) => {
  if (!latestQR) return res.status(404).json({ error: 'no QR available' })
  try {
    const QRCode = await import('qrcode')
    const png = await QRCode.default.toBuffer(latestQR, { type: 'png', width: 320, margin: 4 })
    res.set('Content-Type', 'image/png')
    res.set('Cache-Control', 'no-store')
    res.send(png)
  } catch (err) {
    res.status(500).json({ error: String(err.message) })
  }
})

// `connected` is USABLE connectivity (event-derived state + live ws), not the mere presence of stored
// credentials: `waSocket.user` stays populated from creds.json after WhatsApp rejects the login, which
// made this report connected=true through two logged-out outages. Cheap: reads in-memory state only.
app.get('/health', (_, res) => res.json(healthSnapshot({
  conn: connState,
  sock: waSocket,
  latestQR,
  expectedNumber: EXPECTED_NUMBER,
  accountMismatch,
})))

// List the WhatsApp groups this account participates in — so the owner can pick which
// one receives system/agent updates (2026-08-04). Returns [{jid, subject, size}].
app.get('/groups', async (_, res) => {
  const unavailable = socketUnavailable(connState, waSocket)
  if (unavailable) {
    return res.status(503).json({ ok: false, error: unavailable })
  }
  try {
    const groups = await waSocket.groupFetchAllParticipating()
    const list = Object.values(groups || {}).map((g) => ({
      jid: g.id,
      subject: g.subject || '',
      size: Array.isArray(g.participants) ? g.participants.length : 0,
    }))
    res.json({ ok: true, groups: list })
  } catch (err) {
    res.status(500).json({ ok: false, error: String(err && err.message || err) })
  }
})

// Create a new WhatsApp group from THIS account and add the given participant(s) —
// added 2026-08-13 so the owner's "updates group" (see /groups above) can be
// provisioned automatically instead of asking the owner to create it by hand on
// his phone. Body: { subject, participants: ["9725XXXXXXXX", ...] }.
// Returns { ok, jid, subject }.
app.post('/group/create', async (req, res) => {
  const unavailable = socketUnavailable(connState, waSocket)
  if (unavailable) {
    return res.status(503).json({ ok: false, error: unavailable })
  }
  const { subject, participants } = req.body || {}
  if (!subject || !Array.isArray(participants) || participants.length === 0) {
    return res.status(400).json({ ok: false, error: 'Missing subject or participants[]' })
  }
  try {
    const jids = participants.map((p) => {
      const digits = String(p).replace(/\D/g, '')
      return (digits.startsWith('0') ? '972' + digits.slice(1) : digits) + '@s.whatsapp.net'
    })
    const meta = await waSocket.groupCreate(subject, jids)
    console.log('[Bridge] Created group', meta.id, subject)
    res.json({ ok: true, jid: meta.id, subject: meta.subject || subject })
  } catch (err) {
    res.status(500).json({ ok: false, error: String(err && err.message || err) })
  }
})

app.post('/typing', async (req, res) => {
  if (socketUnavailable(connState, waSocket)) {
    return res.status(503).json({ ok: false })
  }
  const { to, reply_jid } = req.body
  // Same readiness-check root-fix as /send (2026-09-12) — see its comment.
  if (!waSocket || !waSocket.user || (!to && !reply_jid)) {
    return res.status(503).json({ ok: false })
  }
  try {
    const jid = reply_jid || (() => {
      const digits = to.replace(/\D/g, '')
      return (digits.startsWith('0') ? '972' + digits.slice(1) : digits) + '@s.whatsapp.net'
    })()
    await waSocket.sendPresenceUpdate('composing', jid)
    setTimeout(() => waSocket.sendPresenceUpdate('paused', jid).catch(() => {}), 25000)
    res.json({ ok: true })
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message })
  }
})

// Delete-for-everyone a message we previously sent — used to remove a 2FA code from
// the chat after it has been verified. Best-effort: the caller ignores failures, and a
// failed delete never affects login (the code is already used/expired). Expects the
// full message key returned by /send: { remoteJid, id, fromMe }.
app.post('/delete', async (req, res) => {
  const unavailable = socketUnavailable(connState, waSocket)
  if (unavailable) {
    return res.status(503).json({ ok: false, error: unavailable })
  }
  const { key } = req.body || {}
  // Same readiness-check root-fix as /send (2026-09-12) — see its comment.
  if (!waSocket || !waSocket.user || !key || !key.remoteJid || !key.id) {
    return res.status(503).json({ ok: false, error: 'Missing key or socket not ready' })
  }
  try {
    await waSocket.sendMessage(key.remoteJid, { delete: key })
    console.log('[Bridge] Deleted message', key.id, 'in', key.remoteJid)
    res.json({ ok: true })
  } catch (err) {
    console.error('[Bridge] Delete error:', err.message)
    res.status(500).json({ ok: false, error: err.message })
  }
})

app.listen(BRIDGE_PORT, () => {
  console.log('[Bridge] Listening on port ' + BRIDGE_PORT)
})

// Root-fix 2026-09-12: kept as its own named constant (not just './auth_info'
// inlined at each use) so the auth-state loader and the atomic creds writer
// below are provably pointed at the EXACT SAME directory/file — see
// atomic_write.js for why the temp file and the target must share a filesystem.
const AUTH_DIR = './auth_info'
const CREDS_PATH = path.join(AUTH_DIR, 'creds.json')

async function startBot() {
  // `saveCreds` (Baileys' own non-atomic writer — see atomic_write.js's
  // docstring for the full forensic background) is intentionally NOT used
  // below. `state.keys` (sessions, pre-keys, app-state-sync) is untouched —
  // this fix is scoped to the one file that was actually proven vulnerable.
  const { state } = await useMultiFileAuthState(AUTH_DIR)
  const { version } = await fetchLatestBaileysVersion()

  // Connection-health config (root fix 2026-07-05): with the defaults, a
  // dropped TCP connection to WhatsApp went undetected — no 'close' event,
  // bridge believed it was online, customers got no replies (silent death).
  // Same disease we fixed for Postgres with TCP keepalives. These settings
  // make the library itself detect a dead socket within ~35s and fire the
  // normal close→reconnect path (graceful, no process restart needed):
  connState.onConnecting()
  const sock = makeWASocket({
    version,
    auth: state,
    logger,
    printQRInTerminal: false,
    getMessage: async (key) => msgCache.get(key.id) ?? null,
    keepAliveIntervalMs: 15000,     // probe every 15s (default 30s)
    connectTimeoutMs: 30000,        // fail dead connects fast
    defaultQueryTimeoutMs: 60000,   // never wait forever on a query
    retryRequestDelayMs: 3000,
  })

  // ── PAIRING CODE (alternative to scanning a QR) ────────────────────────────
  // Baileys can link a companion device with an 8-character code typed on the
  // phone instead of a scanned QR. That matters here because the QR is the
  // fragile step: it is unreadable in some terminals, it rotates every ~20s,
  // and it can be scanned by the WRONG phone (which is exactly what happened on
  // 2026-07-29 — the owner's personal account got linked instead of the
  // business one). A pairing code is requested FOR A SPECIFIC NUMBER, so it
  // cannot silently link the wrong account.
  // Opt in with WHATSAPP_PAIR_MODE=code.
  if (PAIR_MODE === 'code' && !sock.authState.creds.registered) {
    setTimeout(async () => {
      try {
        const code = await sock.requestPairingCode(EXPECTED_NUMBER)
        const pretty = String(code).match(/.{1,4}/g).join('-')
        latestPairCode = pretty
        try { fs.writeFileSync(`${APP_DIR}/pair_code.txt`, pretty) } catch (e) {}
        logEvent('PAIR_CODE', `issued for ${EXPECTED_NUMBER}`)
        console.log(
          `\n🔗 PAIRING CODE for ${EXPECTED_NUMBER}:  ${pretty}\n` +
          `   On the BUSINESS phone: WhatsApp → Settings → Linked Devices →\n` +
          `   Link a Device → "Link with phone number instead" → enter this code.\n` +
          `   Valid for a few minutes; restart the bridge to get a new one.\n`)
      } catch (e) {
        console.error('[Bridge] requestPairingCode failed:', e?.message || e)
      }
    }, 4000)   // the socket must finish opening before a code can be requested
  }

  waSocket = sock

  sock.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      connState.onQr()
      logEvent('QR_DISPLAYED', 'awaiting scan')
      console.log('\n📱 Scan QR with WhatsApp:\n')
      qrcode.generate(qr, { small: true })
      // Also persist the raw payload so the link QR can be rendered as an IMAGE
      // (the terminal blocks are unreadable in some clients). /app is the
      // bind-mounted ./whatsapp-bridge, so this lands on the host filesystem —
      // deliberately NOT an HTTP endpoint: whoever scans this QR links THEIR
      // phone to this bridge, so it must never be reachable over the network.
      latestQR = qr
      try { fs.writeFileSync(`${APP_DIR}/qr.txt`, qr) } catch (e) { /* non-fatal */ }
    }
    if (connection === 'close') {
      const code = new Boom(lastDisconnect?.error)?.output?.statusCode
      const reconnect = code !== DisconnectReason.loggedOut
      connState.onClose(code, lastDisconnect?.error?.message)
      logEvent('DISCONNECTED', `code=${code} reason=${lastDisconnect?.error?.message || 'unknown'} reconnect=${reconnect}`)
      console.log('[Bridge] Closed (' + code + '). Reconnect: ' + reconnect)
      if (reconnect) startBot()
    }
    if (connection === 'open') {
      connState.onOpen()
      logEvent('CONNECTED')
      console.log('✅ WhatsApp connected')
      latestQR = null
      latestPairCode = null
      try { fs.existsSync(`${APP_DIR}/qr.txt`) && fs.unlinkSync(`${APP_DIR}/qr.txt`) } catch (e) {}
      try { fs.existsSync(`${APP_DIR}/pair_code.txt`) && fs.unlinkSync(`${APP_DIR}/pair_code.txt`) } catch (e) {}

      // WHICH ACCOUNT did we just link? A QR scan links whatever phone scanned
      // it, and nothing here previously checked. On 2026-07-29 the bridge was
      // re-linked with the owner's PERSONAL number instead of the business
      // account: customers messaging the business line reached nobody, and
      // every owner notification became a self-send. Both fail silently.
      const jid = sock?.user?.id || ''
      const num = jid.split(':')[0].split('@')[0]
      if (EXPECTED_NUMBER && num && num !== EXPECTED_NUMBER) {
        accountMismatch = { linked: num, expected: EXPECTED_NUMBER,
                            name: sock?.user?.name || null }
        logEvent('WRONG_ACCOUNT', `linked=${num} expected=${EXPECTED_NUMBER}`)
        console.error(
          `\n🚨 WRONG WHATSAPP ACCOUNT LINKED\n` +
          `   linked   : ${num} (${sock?.user?.name || 'unknown'})\n` +
          `   expected : ${EXPECTED_NUMBER}\n` +
          `   Customers messaging ${EXPECTED_NUMBER} will NOT reach the platform.\n` +
          `   Re-link by scanning with the BUSINESS phone.\n`)
      } else {
        accountMismatch = null
        console.log(`   linked account: ${num} (${sock?.user?.name || ''})`)
      }
      startLivenessWatchdog(sock)
    }
  })

  // Root-fix 2026-09-12 (creds.json 0-byte forensic): `state.creds` is the
  // SAME object Baileys mutates in place before firing this event — reading
  // it here at call time always captures the current, complete credential
  // state, exactly like the original saveCreds() closure did. Only the
  // ON-DISK write changed: atomicWriteFile() replaces creds.json via a
  // temp-file + fsync + rename sequence instead of Baileys' own direct,
  // truncating writeFile() — see atomic_write.js. A write failure is logged,
  // never thrown into the event emitter (an unhandled rejection here would
  // crash the whole bridge process over a transient disk issue).
  sock.ev.on('creds.update', () => {
    const serialized = JSON.stringify(state.creds, BufferJSON.replacer)
    atomicWriteFile(CREDS_PATH, serialized).catch((err) => {
      logEvent('CREDS_SAVE_FAILED', err?.message || String(err))
      console.error('[Bridge] Atomic creds save failed:', err?.message || err)
    })
  })

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return

    for (const msg of messages) {
      if (!msg.message || msg.key.fromMe) continue
      const jid = msg.key.remoteJid
      if (!jid || jid.endsWith('@g.us')) continue

      const content = normalizeMessageContent(msg.message) || msg.message
      const text = content.conversation
        || content.extendedTextMessage?.text
        || content.imageMessage?.caption
        || content.videoMessage?.caption
        || content.documentMessage?.caption
        || ''

      let mediaKind = ''
      let mediaBase64 = ''
      let mediaMime = ''
      let mediaCaption = ''
      let mediaTooLarge = false
      let audioPtt = false

      try {
        if (content.imageMessage) {
          mediaKind = 'image'
          mediaMime = String(content.imageMessage.mimetype || 'image/jpeg')
          mediaCaption = String(content.imageMessage.caption || '')
          const mediaBuffer = await downloadInboundMedia(content.imageMessage, 'image')
          if (mediaBuffer.length > MAX_MEDIA_BYTES) {
            mediaTooLarge = true
          } else {
            mediaBase64 = mediaBuffer.toString('base64')
          }
        } else if (content.audioMessage) {
          mediaKind = 'audio'
          mediaMime = String(content.audioMessage.mimetype || 'audio/ogg; codecs=opus')
          mediaCaption = ''
          audioPtt = !!content.audioMessage.ptt
          const mediaBuffer = await downloadInboundMedia(content.audioMessage, 'audio')
          if (mediaBuffer.length > MAX_MEDIA_BYTES) {
            mediaTooLarge = true
          } else {
            mediaBase64 = mediaBuffer.toString('base64')
          }
        } else if (content.documentMessage && typeof content.documentMessage.mimetype === 'string') {
          const docMime = content.documentMessage.mimetype.trim().toLowerCase()
          if (docMime.startsWith('image/')) {
            mediaKind = 'image'
            mediaMime = docMime
            mediaCaption = String(content.documentMessage.caption || '')
            const mediaBuffer = await downloadInboundMedia(content.documentMessage, 'document')
            if (mediaBuffer.length > MAX_MEDIA_BYTES) {
              mediaTooLarge = true
            } else {
              mediaBase64 = mediaBuffer.toString('base64')
            }
          } else if (docMime.startsWith('audio/')) {
            mediaKind = 'audio'
            mediaMime = docMime
            mediaCaption = ''
            audioPtt = false
            const mediaBuffer = await downloadInboundMedia(content.documentMessage, 'document')
            if (mediaBuffer.length > MAX_MEDIA_BYTES) {
              mediaTooLarge = true
            } else {
              mediaBase64 = mediaBuffer.toString('base64')
            }
          }
        }
      } catch (mediaErr) {
        console.error('[Bridge] Media decode error:', mediaErr.message)
      }

      if (!text.trim() && !mediaKind) continue

      const rawId = jid.split('@')[0]
      const digits = rawId.replace(/\D/g, '')
      const isLid = jid.endsWith('@lid')
      const e164 = isLid ? jid : (digits.startsWith('972') ? '+' + digits : '+972' + digits.slice(1))

      const payload = {
        from: 'whatsapp:' + e164,
        body: text,
        profile_name: msg.pushName || '',
        reply_jid: jid,
        message_id: msg.key.id || '',
      }

      if (mediaKind) {
        payload.media_kind = mediaKind
        payload.media_mime = mediaMime
        payload.media_caption = mediaCaption
        payload.media_too_large = mediaTooLarge
        payload.audio_ptt = audioPtt
        if (mediaBase64) {
          payload.media_base64 = mediaBase64
        }
      }

      console.log('[WA IN]', isLid ? jid : e164, '| text:', text.length, '| media:', mediaKind || 'none')

      try {
        await axios.post(BACKEND_WEBHOOK, payload, { timeout: 90000 })
      } catch (err) {
        console.error('[Bridge] Backend error:', err.message)
        // Listener boundary: this runs inside an un-awaited async listener, so a dead-socket throw here
        // used to become an unhandled rejection (process exit). Skip/contain expected dead-socket
        // failures; any other error is re-thrown unchanged.
        await safeListenerSend({
          conn: connState, sock, jid, log: logEvent, what: 'inbound-error-fallback-reply',
          payload: { text: 'מצטערים, אירעה שגיאה. נסה שוב בעוד רגע.' },
        })
      }
    }
  })
}

startBot()
