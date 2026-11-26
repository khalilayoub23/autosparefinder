// #158: Status updates / broadcast lists / channels are never conversations. Run: node test_jid_policy.mjs
import assert from 'assert'
import fs from 'fs'
import { isNonChatJid } from './jid_policy.js'

for (const j of ['status@broadcast', 'STATUS@BROADCAST', '12345@broadcast', '120363000000000000@newsletter']) {
  assert.equal(isNonChatJid(j), true, j)
}
for (const j of ['972500000000@s.whatsapp.net', '98058566160397@lid', '120363428683447639@g.us', '', null, undefined]) {
  assert.equal(isNonChatJid(j), false, String(j))
}

// Wiring: the guard must sit on BOTH paths of index.js (inbound drop before any routing, /send refusal).
const src = fs.readFileSync(new URL('./index.js', import.meta.url), 'utf8')
const upsert = src.indexOf("sock.ev.on('messages.upsert'")
const guardIn = src.indexOf('if (isNonChatJid(jid)) continue', upsert)
const firstRoute = src.indexOf('extractInteractiveResponse(content)', upsert)
assert.ok(upsert > 0 && guardIn > upsert && guardIn < firstRoute, 'inbound guard must run before any routing')
const send = src.indexOf("app.post('/send'")
const guardOut = src.indexOf('isNonChatJid(reply_jid) || isNonChatJid(to)', send)
const firstSend = src.indexOf('waSocket.sendMessage', send)
assert.ok(send > 0 && guardOut > send && guardOut < firstSend, '/send must refuse non-chat destinations before sending')
console.log('PASS jid_policy')
