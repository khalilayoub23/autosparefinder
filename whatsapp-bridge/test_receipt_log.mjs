// #158: receipt lines are content-free (no JID, no phone digits, no text). Run: node test_receipt_log.mjs
import assert from 'assert'
import fs from 'fs'
import { statusLines, receiptLines, retryLine, idTail } from './receipt_log.js'

const G = '120363428683447639@g.us', OWNER = '98058566160397@lid', PHONE = '972586050155@s.whatsapp.net'
const ID = '3EB0C37244203410E1F9E4640B127320'
const r = receiptLines([
  { key: { remoteJid: G, id: ID, fromMe: true, participant: OWNER }, receipt: { userJid: OWNER, receiptTimestamp: 1 } },
  { key: { remoteJid: G, id: ID, fromMe: true }, receipt: { userJid: OWNER, readTimestamp: 2 } },
  { key: { remoteJid: G, id: ID, fromMe: false }, receipt: { userJid: OWNER, receiptTimestamp: 1 } },   // not ours
])
assert.deepEqual(r, ['scope=group kind=delivery id=4B1273'.replace('4B1273', idTail(ID)), `scope=group kind=read id=${idTail(ID)}`])
const s = statusLines([{ key: { remoteJid: PHONE, id: ID, fromMe: true }, update: { status: 3 } },
                       { key: { remoteJid: PHONE, id: ID, fromMe: true }, update: { status: 2 } }])   // server ack: not a receipt
assert.deepEqual(s, [`scope=dm kind=delivery id=${idTail(ID)}`])
assert.equal(retryLine({ attrs: { type: 'retry', from: OWNER, id: ID, participant: OWNER } }), `scope=dm id=${idTail(ID)}`)
assert.equal(retryLine({ attrs: { type: 'read', from: OWNER, id: ID } }), '')
for (const line of [...r, ...s, retryLine({ attrs: { type: 'retry', from: G, id: ID } })]) {
  for (const digits of ['120363428683447639', '98058566160397', '972586050155', '0155', '0397', '7639']) {
    assert.ok(!line.includes(digits), `line leaks recipient digits: ${line}`)
  }
}
const src = fs.readFileSync(new URL('./index.js', import.meta.url), 'utf8')
assert.ok(src.includes("sock.ev.on('message-receipt.update'") && src.includes("CB:receipt"), 'listeners wired')
console.log('PASS receipt_log')
