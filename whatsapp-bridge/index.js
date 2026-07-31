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
} = baileys

import { Boom } from '@hapi/boom'
import axios from 'axios'
import express from 'express'
import qrcode from 'qrcode-terminal'
import pino from 'pino'

const BACKEND_WEBHOOK = process.env.BACKEND_URL || 'http://backend:8000/api/v1/webhooks/whatsapp'
const BRIDGE_PORT = 3001

// Connection-event log (added 2026-07-05): persistent, timestamped record of
// every connect/disconnect/watchdog action so disconnection patterns can be
// tracked over time. Lives on the bind mount → survives restarts, readable
// from the host at /opt/autosparefinder/whatsapp-bridge/connection_events.log
import fs from 'fs'
const EVENT_LOG = '/app/connection_events.log'
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
const logger = pino({ level: 'silent' })
const MAX_MEDIA_BYTES = Math.max(256000, Number.parseInt(process.env.WA_MEDIA_MAX_BYTES || '6291456', 10) || 6291456)

let waSocket = null
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
  const digits = String(to || '').replace(/\D/g, '')
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

  if (!waSocket || !to || (!hasText && !hasImage && !hasAudio)) {
    return res.status(400).json({ ok: false, error: 'Missing params or socket not ready' })
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
      if (hasText) {
        sent = await waSocket.sendMessage(jid, { text: text.trim() })
      }
    } else {
      console.log('[Bridge] Sending to', jid, '| text length:', text.length)
      sent = await waSocket.sendMessage(jid, { text })
    }

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
app.get('/health', (_, res) => res.json({
  ok: true,
  connected: !!(waSocket && waSocket.user),
  awaiting_qr_scan: latestQR !== null,
  jid: waSocket?.user?.id || null,
  expected_number: EXPECTED_NUMBER,
  // Non-null => the bridge is linked to the WRONG WhatsApp account. Surfaced
  // here so the backend health monitor can alert instead of it going unnoticed.
  account_mismatch: accountMismatch,
}))

app.post('/typing', async (req, res) => {
  const { to, reply_jid } = req.body
  if (!waSocket || (!to && !reply_jid)) {
    return res.status(400).json({ ok: false })
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
  const { key } = req.body || {}
  if (!waSocket || !key || !key.remoteJid || !key.id) {
    return res.status(400).json({ ok: false, error: 'Missing key or socket not ready' })
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

async function startBot() {
  const { state, saveCreds } = await useMultiFileAuthState('./auth_info')
  const { version } = await fetchLatestBaileysVersion()

  // Connection-health config (root fix 2026-07-05): with the defaults, a
  // dropped TCP connection to WhatsApp went undetected — no 'close' event,
  // bridge believed it was online, customers got no replies (silent death).
  // Same disease we fixed for Postgres with TCP keepalives. These settings
  // make the library itself detect a dead socket within ~35s and fire the
  // normal close→reconnect path (graceful, no process restart needed):
  const sock = makeWASocket({
    version,
    auth: state,
    logger,
    printQRInTerminal: false,
    getMessage: async () => ({ conversation: '' }),
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
        try { fs.writeFileSync('/app/pair_code.txt', pretty) } catch (e) {}
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
      logEvent('QR_DISPLAYED', 'awaiting scan')
      console.log('\n📱 Scan QR with WhatsApp:\n')
      qrcode.generate(qr, { small: true })
      // Also persist the raw payload so the link QR can be rendered as an IMAGE
      // (the terminal blocks are unreadable in some clients). /app is the
      // bind-mounted ./whatsapp-bridge, so this lands on the host filesystem —
      // deliberately NOT an HTTP endpoint: whoever scans this QR links THEIR
      // phone to this bridge, so it must never be reachable over the network.
      latestQR = qr
      try { fs.writeFileSync('/app/qr.txt', qr) } catch (e) { /* non-fatal */ }
    }
    if (connection === 'close') {
      const code = new Boom(lastDisconnect?.error)?.output?.statusCode
      const reconnect = code !== DisconnectReason.loggedOut
      logEvent('DISCONNECTED', `code=${code} reason=${lastDisconnect?.error?.message || 'unknown'} reconnect=${reconnect}`)
      console.log('[Bridge] Closed (' + code + '). Reconnect: ' + reconnect)
      if (reconnect) startBot()
    }
    if (connection === 'open') {
      logEvent('CONNECTED')
      console.log('✅ WhatsApp connected')
      latestQR = null
      latestPairCode = null
      try { fs.existsSync('/app/qr.txt') && fs.unlinkSync('/app/qr.txt') } catch (e) {}
      try { fs.existsSync('/app/pair_code.txt') && fs.unlinkSync('/app/pair_code.txt') } catch (e) {}

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

  sock.ev.on('creds.update', saveCreds)

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
        await sock.sendMessage(jid, { text: 'מצטערים, אירעה שגיאה. נסה שוב בעוד רגע.' })
      }
    }
  })
}

startBot()
