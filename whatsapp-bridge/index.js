import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion
} from '@whiskeysockets/baileys'
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
  const { to, text } = req.body
  if (!waSocket || !to || !text) {
    return res.status(400).json({ ok: false, error: 'Missing params or socket not ready' })
  }
  try {
    const digits = to.replace(/\D/g, '')
    const e164 = digits.startsWith('0') ? '972' + digits.slice(1) : digits
    const jid = e164 + '@s.whatsapp.net'
    await waSocket.sendMessage(jid, { text })
    res.json({ ok: true })
  } catch (err) {
    console.error('[Bridge] Send error:', err.message)
    res.status(500).json({ ok: false, error: err.message })
  }
})

app.get('/health', (_, res) => res.json({
  ok: true,
  connected: waSocket !== null
}))

app.listen(BRIDGE_PORT, () => {
  console.log(`[Bridge] Listening on port ${BRIDGE_PORT}`)
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
      console.log(`[Bridge] Closed (${code}). Reconnect: ${reconnect}`)
      if (reconnect) startBot()
    }
    if (connection === 'open') {
      console.log('\u2705 WhatsApp connected')
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

      const digits = jid.replace('@s.whatsapp.net', '')
      const phone  = digits.startsWith('972') ? '0' + digits.slice(3) : digits
      const e164   = '+972' + phone.slice(1)

      console.log(`[WA IN] ${phone}: ${text}`)

      try {
        await axios.post(BACKEND_WEBHOOK, {
          from:         `whatsapp:${e164}`,
          body:         text,
          profile_name: msg.pushName || '',
        }, { timeout: 30000 })
      } catch (err) {
        console.error('[Bridge] Backend error:', err.message)
        await sock.sendMessage(jid, {
          text: '\u05de\u05e6\u05d8\u05e2\u05e8\u05d9\u05dd, \u05d0\u05d9\u05e8\u05e2\u05d4 \u05e9\u05d2\u05d9\u05d0\u05d4. \u05e0\u05e1\u05d4 \u05e9\u05d5\u05d1 \u05d1\u05e2\u05d5\u05d3 \u05e8\u05d2\u05e2.'
        })
      }
    }
  })
}

startBot()
