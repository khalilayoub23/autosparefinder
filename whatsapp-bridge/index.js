import { createRequire } from 'module'
const require = createRequire(import.meta.url)
const baileys = require('@whiskeysockets/baileys')
const makeWASocket = baileys.default || baileys.makeWASocket || baileys
const { useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = baileys

import { Boom } from '@hapi/boom'
import axios from 'axios'
import express from 'express'
import qrcode from 'qrcode-terminal'
import pino from 'pino'

const BACKEND_WEBHOOK = process.env.BACKEND_URL || 'http://backend:8000/api/v1/webhooks/whatsapp'
const BRIDGE_PORT = 3001
const logger = pino({ level: 'silent' })

let waSocket = null

const app = express()
app.use(express.json())

app.post('/send', async (req, res) => {
  const { to, text, reply_jid } = req.body
  if (!waSocket || !to || !text) {
    return res.status(400).json({ ok: false, error: 'Missing params or socket not ready' })
  }
  try {
    let jid = reply_jid || null
    if (!jid) {
      const digits = to.replace(/\D/g, '')
      const e164 = digits.startsWith('0') ? '972' + digits.slice(1) : digits
      jid = e164 + '@s.whatsapp.net'
    }
    console.log('[Bridge] Sending to', jid, '| text length:', text.length)
    await waSocket.sendMessage(jid, { text })
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
      const text = msg.message.conversation
                || msg.message.extendedTextMessage?.text
                || ''
      if (!text.trim()) continue
      const rawId = jid.split('@')[0]
      const digits = rawId.replace(/\D/g, '')
      const isLid = jid.endsWith('@lid')
      const e164 = isLid ? jid : (digits.startsWith('972') ? '+' + digits : '+972' + digits.slice(1))
      console.log('[WA IN] ' + (isLid ? jid : e164) + ': ' + text)
      try {
        await axios.post(BACKEND_WEBHOOK, {
          from: 'whatsapp:' + e164,
          body: text,
          profile_name: msg.pushName || '',
          reply_jid: jid,
        }, { timeout: 90000 })
      } catch (err) {
        console.error('[Bridge] Backend error:', err.message)
        await sock.sendMessage(jid, { text: 'מצטערים, אירעה שגיאה. נסה שוב בעוד רגע.' })
      }
    }
  })
}

startBot()
