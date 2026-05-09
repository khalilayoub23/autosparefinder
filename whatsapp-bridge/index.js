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
const logger = pino({ level: 'silent' })
const MAX_MEDIA_BYTES = Math.max(256000, Number.parseInt(process.env.WA_MEDIA_MAX_BYTES || '6291456', 10) || 6291456)

let waSocket = null

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
      await waSocket.sendMessage(jid, messagePayload)
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
      await waSocket.sendMessage(jid, payload)
      if (hasText) {
        await waSocket.sendMessage(jid, { text: text.trim() })
      }
    } else {
      console.log('[Bridge] Sending to', jid, '| text length:', text.length)
      await waSocket.sendMessage(jid, { text })
    }

    console.log('[Bridge] Sent OK to', jid)
    res.json({ ok: true })
  } catch (err) {
    console.error('[Bridge] Send error:', err.message)
    res.status(500).json({ ok: false, error: err.message })
  }
})

app.get('/health', (_, res) => res.json({ ok: true, connected: waSocket !== null }))

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

app.listen(BRIDGE_PORT, () => {
  console.log('[Bridge] Listening on port ' + BRIDGE_PORT)
})

async function startBot() {
  const { state, saveCreds } = await useMultiFileAuthState('./auth_info')
  const { version } = await fetchLatestBaileysVersion()

  const sock = makeWASocket({
    version,
    auth: state,
    logger,
    printQRInTerminal: false,
    getMessage: async () => ({ conversation: '' }),
  })

  waSocket = sock

  sock.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      console.log('\n📱 Scan QR with WhatsApp:\n')
      qrcode.generate(qr, { small: true })
    }
    if (connection === 'close') {
      const code = new Boom(lastDisconnect?.error)?.output?.statusCode
      const reconnect = code !== DisconnectReason.loggedOut
      console.log('[Bridge] Closed (' + code + '). Reconnect: ' + reconnect)
      if (reconnect) startBot()
    }
    if (connection === 'open') {
      console.log('✅ WhatsApp connected')
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
