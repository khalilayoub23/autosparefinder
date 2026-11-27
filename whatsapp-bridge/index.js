// ESM namespace import (2026-11-26, #158): Baileys 6.7.x is an ES module with an import cycle that require(esm) cannot
// initialise ("Cannot access 'WAMessageStubType' before initialization"). 6.7.24 is pinned because it is the first
// line that sends to LID-addressed groups (addressing_mode + lid device JIDs); 6.17.16 encoded group devices as
// <lid>@s.whatsapp.net and every group send failed with 'not-acceptable'.
import * as baileys from '@whiskeysockets/baileys'
const makeWASocket = baileys.default || baileys.makeWASocket || baileys
const {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  normalizeMessageContent,
  getAggregateVotesInPollMessage,
  decryptPollVote,
  downloadContentFromMessage,
  BufferJSON,
} = baileys

import { Boom } from '@hapi/boom'
import { isNonChatJid } from './jid_policy.js'
import { statusLines, receiptLines, retryLine, rawReceiptLine } from './receipt_log.js'
import axios from 'axios'
import express from 'express'
import qrcode from 'qrcode-terminal'
import path from 'path'
import { atomicWriteFile } from './atomic_write.js'
import { createMsgCache } from './msg_cache.js'
import { createSendJournal } from './send_journal.js'
import { createPollRegistry } from './poll_votes.js'
import { candidatePairs, tryDecryptVote } from './poll_decrypt.js'
import { createHash } from 'crypto'
import { wrapForRelay, buildInteractiveMessage, extractInteractiveResponse, responseContext, extractMentions, validateMentions, parseButtonId, shapeParticipants, isTypedBotMention } from './interactive.js'
import { installSafeConsoleFilter, createSafeBaileysLogger, jidHint, maskNumbers } from './safe_logging.js'
import { installOrphanTimeoutGuard, createConnectionState, socketUnavailable, healthSnapshot, safeListenerSend } from './bridge_lifecycle.js'

// Root-fix 2026-09-12 (WhatsApp logging/telemetry hardening): installed as
// early as possible, before any Signal session operation can occur, so the
// libsignal SessionEntry leak (see safe_logging.js) is intercepted from the
// very first connection attempt onward. This bridge's own console.log/error
// calls elsewhere in this file are completely unaffected — the filter only
// rewrites the small set of libsignal message prefixes proven to carry raw
// key material.
installSafeConsoleFilter()

// Interactive owner messages / group-mention inbound: OFF unless explicitly enabled (rendering on a real device is
// unverified; with the flag off the bridge behaves exactly as before - polls + text).
const WA_INTERACTIVE_ENABLED = process.env.WA_INTERACTIVE_ENABLED === '1'
const WA_GROUP_INBOUND_ENABLED = process.env.WA_GROUP_INBOUND_ENABLED === '1'
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

// Bounded, PERSISTENT outbound message cache for Baileys retry recovery
// (Defect B fix 2026-09-10; made persistent 2026-09-27 — see msg_cache.js's
// docstring for the full defect history and design rationale). When the
// recipient's WhatsApp fails to decrypt a message it sends a retry receipt
// back to the sender. Baileys calls getMessage({ remoteJid, fromMe, id }) on
// the sender side; if that returns a truthy value it calls relayMessage()
// with it. The old stub returned { conversation: '' } for every message ID
// — including unknown ones — which caused Baileys to relay an empty body on
// every retry, producing "Waiting for this message". The fix below caches
// the proto.IMessage payload (sent.message) by its ID (sent.key.id) after
// each successful outbound send, now backed by msg_cache.json (same
// bind-mounted directory as auth_info/) so it survives a bridge restart —
// real production retries on 2026-09-27 proved the previous in-memory-only
// version could not serve pre-restart messages. Unknown/expired IDs still
// return null, which makes Baileys log "message not available" and skip the
// relay — the correct failure mode, unchanged.
const msgCache = createMsgCache(`${APP_DIR}/msg_cache.json`, {
  replacer: BufferJSON.replacer,
  reviver: BufferJSON.reviver,
  onError: (stage, err) => logEvent('MSG_CACHE_ERROR', `stage=${stage} err=${err.message}`),
})

const sendJournal = createSendJournal({ file: `${APP_DIR}/send_journal.json`, log: logEvent })

const pollRegistry = createPollRegistry({
  load: () => { try { return JSON.parse(fs.readFileSync(`${APP_DIR}/poll_map.json`, 'utf8')) } catch { return {} } },
  save: (obj) => atomicWriteFile(`${APP_DIR}/poll_map.json`, JSON.stringify(obj)).catch(() => {}),
})

function cacheOutboundMessage(msgId, protoMessage) {
  msgCache.cacheOutboundMessage(msgId, protoMessage)
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

// Status of an idempotent send: {status:'sent'|'pending'|null}. Read-only; no message content is stored.
app.get('/sent/:id', (req, res) => res.json({ ok: true, entry: sendJournal.status(req.params.id) }))

app.post('/send', async (req, res) => {
  const unavailable = socketUnavailable(connState, waSocket)
  if (unavailable) {
    // Nothing was sent. Transient states are retried by the backend (whatsapp_provider);
    // this used to be silent, so a message refused during a reconnect left no trace.
    const retryable = /connecting|reconnecting|not created yet/.test(unavailable)
    logEvent('SEND_REJECTED', `retryable=${retryable} reason=${unavailable}`)
    return res.status(503).json({ ok: false, error: unavailable, retryable })
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
    poll,
    interactive,
    mentions,
    client_msg_id,
  } = req.body
  if (isNonChatJid(reply_jid) || isNonChatJid(to)) {
    // Never publish to Status / a broadcast list / a channel (#158). 400 = caller error, not retryable.
    logEvent('SEND_REJECTED', 'retryable=false reason=non-chat destination')
    return res.status(400).json({ ok: false, error: 'non-chat destination (status/broadcast/newsletter)', retryable: false })
  }
  const clientMsgId = typeof client_msg_id === 'string' && /^[A-Za-z0-9]{8,64}$/.test(client_msg_id) ? client_msg_id : ''

  const hasText = typeof text === 'string' && text.trim().length > 0
  const hasImage = typeof image_base64 === 'string' && image_base64.trim().length > 0
  const hasAudio = typeof audio_base64 === 'string' && audio_base64.trim().length > 0
  const hasInteractive = !!(interactive && typeof interactive.body === 'string' && interactive.interaction_id && Array.isArray(interactive.actions))
  const hasPoll = !!(poll && typeof poll.name === 'string' && Array.isArray(poll.values) && poll.values.length >= 2)

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
  if (!waSocket || !waSocket.user || !to || (!hasText && !hasImage && !hasAudio && !hasPoll && !hasInteractive)) {
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

    if (hasInteractive) {
      if (!WA_INTERACTIVE_ENABLED) return res.status(409).json({ ok: false, error: 'interactive disabled (WA_INTERACTIVE_ENABLED!=1)', fallback: 'poll' })
      const msg = buildInteractiveMessage(baileys.proto, {
        body: interactive.body, footer: interactive.footer || '', interactionId: interactive.interaction_id, actions: interactive.actions,
      })
      // viewOnce wrapper + biz node: the form community nativeFlow senders use; device rendering UNVERIFIED.
      const wrapped = wrapForRelay(msg)
      const full = baileys.generateWAMessageFromContent(jid, wrapped, { userJid: waSocket.user.id })
      await waSocket.relayMessage(jid, full.message, {
        messageId: full.key.id,
        additionalNodes: [{ tag: 'biz', attrs: {}, content: [{ tag: 'interactive', attrs: { type: 'native_flow', v: '1' }, content: [{ tag: 'native_flow', attrs: { v: '9', name: 'mixed' } }] }] }],
      })
      cacheOutboundMessage(full.key.id, full.message)
      console.log('[Bridge] Sent interactive to', jidHint(jid), '| actions:', interactive.actions.length)
      return res.json({ ok: true, key: full.key, interactive: true })
    }

    if (hasPoll) {
      const values = poll.values.slice(0, 6).map((v) => String(v).slice(0, 40))
      const commands = Array.isArray(poll.commands) ? poll.commands.slice(0, values.length).map(String) : []
      console.log('[Bridge] Sending poll to', jidHint(jid), '| options:', values.length)
      sent = await waSocket.sendMessage(jid, { poll: { name: String(poll.name).slice(0, 90), values, selectableCount: 1 } })
      if (sent?.key?.id) pollRegistry.register(sent.key.id, { values, commands, chatJid: jid })
    } else if (hasImage) {
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
      console.log('[Bridge] Sending image to', jidHint(jid), '| bytes:', imageBuffer.length)
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
      console.log('[Bridge] Sending audio to', jidHint(jid), '| bytes:', audioBuffer.length)
      sent = await waSocket.sendMessage(jid, payload)
      cacheOutboundMessage(sent?.key?.id, sent?.message)
      if (hasText) {
        sent = await waSocket.sendMessage(jid, { text: text.trim() })
      }
    } else {
      console.log('[Bridge] Sending to', jidHint(jid), '| text length:', text.length)  // last 4 digits only (#70)
      if (clientMsgId) {
        // Idempotent text send (owner digest): a retry of the same client_msg_id never sends twice (send_journal.js);
        // the id doubles as the WhatsApp message id so the provider can also drop a re-send of the same message.
        const once = await sendJournal.sendOnce(clientMsgId, async (mid) => {
          const s = await waSocket.sendMessage(jid, { text }, { messageId: mid })
          cacheOutboundMessage(s?.key?.id, s?.message)
          return { key: s?.key || null }
        })
        console.log('[Bridge] Sent OK to', jidHint(jid), once.deduplicated ? '(deduplicated)' : '')
        return res.json({ ok: true, key: once.result?.key || null, deduplicated: once.deduplicated })
      }
      const payload = { text }
      if (Array.isArray(mentions) && mentions.length && jid.endsWith('@g.us')) {
        const meta = await waSocket.groupMetadata(jid)
        const ok = validateMentions(mentions, (meta.participants || []).flatMap((p) => [p.id, p.lid, p.phoneNumber].filter(Boolean)))
        if (ok.length !== new Set(mentions).size) return res.status(400).json({ ok: false, error: 'mention is not a participant of this group' })
        payload.mentions = ok
      }
      sent = await waSocket.sendMessage(jid, payload)
    }

    cacheOutboundMessage(sent?.key?.id, sent?.message)
    console.log('[Bridge] Sent OK to', jidHint(jid))
    // Return the message key so the caller can later delete-for-everyone (used to
    // remove a 2FA code from the chat once it's been verified). Non-breaking add.
    res.json({ ok: true, key: sent?.key || null })
  } catch (err) {
    console.error('[Bridge] Send error:', maskNumbers(err.message))
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

// Read-only participant list of ONE group (mention autocomplete). Sends nothing. `id` is the JID the backend maps to
// an opaque token - the UI never sees phone numbers.
app.get('/groups/:jid/participants', async (req, res) => {
  const unavailable = socketUnavailable(connState, waSocket)
  if (unavailable) return res.status(503).json({ ok: false, error: unavailable })
  const gjid = String(req.params.jid || '')
  if (!/^[0-9-]+@g\.us$/.test(gjid)) return res.status(400).json({ ok: false, error: 'bad group jid' })
  try {
    const meta = await waSocket.groupMetadata(gjid)
    res.json({ ok: true, subject: meta.subject || '', size: (meta.participants || []).length,
      participants: shapeParticipants(meta.participants, [waSocket.user?.id, waSocket.user?.lid]) })
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
    console.log('[Bridge] Deleted message', key.id, 'in', jidHint(key.remoteJid))
    res.json({ ok: true })
  } catch (err) {
    console.error('[Bridge] Delete error:', maskNumbers(err.message))
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
    getMessage: async (key) => msgCache.getCachedMessage(key.id) ?? null,
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
  // Membership changed (add/remove/promote/demote): tell the backend to drop its cached participant list so the
  // composer never offers a removed member. Fire-and-forget; carries no message content.
  sock.ev.on('group-participants.update', async ({ id, action }) => {
    try {
      logEvent('GROUP_PARTICIPANTS', `action=${action}`)
      await axios.post(BACKEND_WEBHOOK, { from: 'whatsapp:group', body: '', message_id: '', group_update: { group_jid: id, action } }, { timeout: 15000 })
    } catch (err) { console.error('[Bridge] group update forward error:', maskNumbers(err.message)) }
  })

  // Delivery evidence for our own messages, content-free (receipt_log.js, #158): "Sent OK" is only the server ack.
  sock.ev.on('messages.update', (updates) => {
    try { for (const l of statusLines(updates)) logEvent('RECEIPT', l) } catch {}
  })
  sock.ev.on('message-receipt.update', (updates) => {
    try { for (const l of receiptLines(updates)) logEvent('RECEIPT', l) } catch {}
  })
  try {
    sock.ws.on('CB:receipt', (node) => {
      try { const l = retryLine(node); if (l) logEvent('RETRY_RECV', l) } catch {}
      // group only: 1:1 customer receipts would flood the capped event log (expert review r3, S6)
      try { if (String(node?.attrs?.from || '').endsWith('@g.us')) logEvent('RECEIPT_RAW', rawReceiptLine(node)) } catch {}
    })
  } catch {}

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
      if (!jid) continue
      if (isNonChatJid(jid)) continue   // Status updates, broadcast lists, channels are not conversations (#158)
      const content = normalizeMessageContent(msg.message) || msg.message

      // Button / list response (owner taps). Posted to the SAME webhook with the structured selection; the backend
      // validates owner + interaction and resolves the action. Never executed here.
      const ir = extractInteractiveResponse(content)
      if (ir && (!jid.endsWith('@g.us') || WA_GROUP_INBOUND_ENABLED)) {
        const parsed = parseButtonId(ir.selectedId)
        const sender = jid.endsWith('@g.us') ? (msg.key.participant || '') : jid
        logEvent('INTERACTIVE_RESPONSE', `kind=${ir.kind} parsed=${!!parsed}`)
        try {
          await axios.post(BACKEND_WEBHOOK, {
            from: 'whatsapp:' + (sender.endsWith('@lid') ? sender : '+' + sender.split('@')[0].replace(/\D/g, '')),
            body: '', profile_name: msg.pushName || '', reply_jid: jid, message_id: msg.key.id || '',
            interactive_response: { kind: ir.kind, selected_id: ir.selectedId, interaction_id: parsed?.interactionId || '',
              action_id: parsed?.actionId || '', original_message_id: responseContext(content).stanzaId, is_group: jid.endsWith('@g.us') },
          }, { timeout: 90000 })
        } catch (err) { console.error('[Bridge] interactive response forward error:', maskNumbers(err.message)) }
        continue
      }

      // Group messages: dropped unless WA_GROUP_INBOUND_ENABLED=1; then ONLY messages carrying real mention metadata
      // of THIS account are forwarded (routing signal, never an approval).
      if (jid.endsWith('@g.us')) {
        if (!WA_GROUP_INBOUND_ENABLED) continue
        const mentioned = extractMentions(content)
        const me = [sock.user?.id, sock.user?.lid].filter(Boolean).map((x) => x.split(':')[0].split('@')[0])
        const mentionsMe = mentioned.some((m) => me.includes(m.split('@')[0]))
        // Content-free (#158 follow-up): mention metadata is only present when the sender picked the bot from WhatsApp's
        // native @ menu; a hand-typed "@name" has none and is dropped here. No text, no numbers.
        // Only messages that try to address someone (metadata, or an "@" typed by hand) - ordinary group chatter would evict
        // connection history from the capped event log (expert review r3, S6). The text itself is never logged.
        const gt = content.conversation || content.extendedTextMessage?.text || ''
        const typed = !mentionsMe && isTypedBotMention(gt)   // hand-typed "@AutoSpareFinder ..." (no native metadata)
        if (mentioned.length || gt.includes('@')) {
          logEvent('GROUP_INBOUND', `mentioned_jid_present=${mentioned.length > 0} mentions_me=${mentionsMe} typed_mention=${typed} forwarded=${mentionsMe || typed}`)
        }
        if (!mentionsMe && !typed) continue
        const gtext = content.conversation || content.extendedTextMessage?.text || ''
        try {
          await axios.post(BACKEND_WEBHOOK, {
            from: 'whatsapp:group', body: gtext, profile_name: msg.pushName || '', reply_jid: jid, message_id: msg.key.id || '',
            group_message: { group_jid: jid, message_id: msg.key.id || '', sender_jid: msg.key.participant || '', mentioned_jids: mentioned,
              typed_mention: typed },
          }, { timeout: 90000 })
        } catch (err) { console.error('[Bridge] group mention forward error:', maskNumbers(err.message)) }
        continue
      }
      if (content.pollUpdateMessage) {
        await handlePollVoteMessage(sock, msg, content.pollUpdateMessage)
        continue
      }
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
        console.error('[Bridge] Media decode error:', maskNumbers(mediaErr.message))
      }

      if (!text.trim() && !mediaKind) continue

      const rawId = jid.split('@')[0]
      const digits = rawId.replace(/\D/g, '')
      const isLid = jid.endsWith('@lid')
      const e164 = isLid ? jid : (digits.startsWith('972') ? '+' + digits : '+972' + digits.slice(1))

      // Quote-reply context (2026-11-13): the id + text of the message the owner replied to.
      const ctxInfo = content.extendedTextMessage?.contextInfo
        || content.imageMessage?.contextInfo || content.videoMessage?.contextInfo
        || content.documentMessage?.contextInfo || content.audioMessage?.contextInfo || null
      let quotedText = ''
      if (ctxInfo?.quotedMessage) {
        const qc = normalizeMessageContent(ctxInfo.quotedMessage) || ctxInfo.quotedMessage
        quotedText = String(qc.conversation || qc.extendedTextMessage?.text || qc.imageMessage?.caption
          || qc.videoMessage?.caption || qc.documentMessage?.caption || '').slice(0, 1500)
      }

      const payload = {
        from: 'whatsapp:' + e164,
        body: text,
        quoted_message_id: ctxInfo?.stanzaId || '',
        quoted_text: quotedText,
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

      console.log('[WA IN]', jidHint(isLid ? jid : `${e164}@`), '| text:', text.length, '| media:', mediaKind || 'none')

      try {
        await axios.post(BACKEND_WEBHOOK, payload, { timeout: 90000 })
      } catch (err) {
        console.error('[Bridge] Backend error:', maskNumbers(err.message))
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

  // Owner poll votes (2026-11-13): a tap runs the SAME typed command the poll was registered with. The vote
  // is decrypted from the cached poll creation message, mapped by poll_votes.js, and posted to the normal
  // inbound webhook as if the owner had typed it - so no approval logic lives in the bridge.
  sock.ev.on('messages.update', async (updates) => {
    for (const { key, update } of updates || []) {
      try {
        if (!update?.pollUpdates || !key?.id || !pollRegistry.has(key.id)) continue
        const original = msgCache.getCachedMessage(key.id)
        if (!original) { logEvent('POLL_VOTE_NO_ORIGINAL', `id=${key.id}`); continue }
        const votes = getAggregateVotesInPollMessage({ message: original, pollUpdates: update.pollUpdates }, sock.user?.id)
        const picked = (votes || []).find((v) => Array.isArray(v.voters) && v.voters.length > 0)
        const jid = key.remoteJid
        const command = pollRegistry.resolve(key.id, picked?.name, jid)
        if (!command || !jid || jid.endsWith('@g.us')) continue
        const rawId = jid.split('@')[0]
        const digits = rawId.replace(/\D/g, '')
        const e164 = jid.endsWith('@lid') ? jid : (digits.startsWith('972') ? '+' + digits : '+972' + digits.slice(1))
        logEvent('POLL_VOTE', `id=${key.id} option=${picked.name}`)
        await axios.post(BACKEND_WEBHOOK, {
          from: 'whatsapp:' + e164, body: command, quoted_message_id: '', quoted_text: '',
          profile_name: '', reply_jid: jid, message_id: `poll:${key.id}:${picked.name}`,
        }, { timeout: 90000 })
      } catch (err) {
        console.error('[Bridge] Poll vote error:', maskNumbers(err.message))
      }
    }
  })
}

// A vote arrives as a pollUpdateMessage. Baileys' own decrypt fails on our PN/LID address mix, so decrypt here with
// the candidate JID pairs (poll_decrypt.js) and run the poll's registered command through the normal inbound webhook.
async function handlePollVoteMessage(sock, msg, update) {
  try {
    const creationKey = update.pollCreationMessageKey
    if (!creationKey?.id || !pollRegistry.has(creationKey.id)) return
    const original = msgCache.getCachedMessage(creationKey.id)
    const pollEncKey = original?.messageContextInfo?.messageSecret
    if (!original || !pollEncKey) { logEvent('POLL_VOTE_NO_ORIGINAL', `id=${creationKey.id}`); return }
    const jid = msg.key.remoteJid
    const pairs = candidatePairs({
      meId: sock.user?.id, meLid: sock.user?.lid,
      voterKeyAuthor: msg.key.participant || jid, chatJid: jid,
    })
    const got = tryDecryptVote(decryptPollVote, update.vote, { pollEncKey, pollMsgId: creationKey.id, pairs })
    if (!got) { logEvent('POLL_VOTE_DECRYPT_FAILED', `id=${creationKey.id} pairs=${pairs.length}`); return }
    const names = original.pollCreationMessage?.options?.map((o) => o.optionName)
      || original.pollCreationMessageV3?.options?.map((o) => o.optionName) || []
    const picked = (got.vote.selectedOptions || [])
      .map((h) => names.find((n) => createHash('sha256').update(n).digest('hex') === Buffer.from(h).toString('hex')))
      .find(Boolean)
    const command = pollRegistry.resolve(creationKey.id, picked, jid)
    if (!command || jid.endsWith('@g.us')) return
    const digits = jid.split('@')[0].replace(/\D/g, '')
    const e164 = jid.endsWith('@lid') ? jid : (digits.startsWith('972') ? '+' + digits : '+972' + digits.slice(1))
    logEvent('POLL_VOTE', `id=${creationKey.id} option=${picked}`)
    await axios.post(BACKEND_WEBHOOK, {
      from: 'whatsapp:' + e164, body: command, quoted_message_id: '', quoted_text: '',
      profile_name: '', reply_jid: jid, message_id: `poll:${creationKey.id}:${picked}`,
    }, { timeout: 90000 })
  } catch (err) {
    console.error('[Bridge] Poll vote error:', maskNumbers(err.message))
  }
}

startBot()
