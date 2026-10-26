// #70: the bridge's own console lines never print a full phone number / JID (customers' numbers were
// kept in the docker log on every send). Source check + the masking helper's real output.
import fs from 'node:fs'
import assert from 'node:assert/strict'
import { jidHint, maskNumbers } from './safe_logging.js'

const src = fs.readFileSync(new URL('./index.js', import.meta.url), 'utf8')
const lines = src.split('\n').filter(l => /console\.(log|error|warn)\(/.test(l))
const raw = lines.filter(l => /,\s*(jid|e164|key\.remoteJid|to|from|sender)\s*[,)]/.test(l) || /isLid \? jid : e164,/.test(l))
assert.deepEqual(raw, [], 'console lines printing a raw recipient:\n' + raw.join('\n'))
assert.equal(jidHint('972586050155@s.whatsapp.net'), '...0155')
assert.equal(jidHint('98058566160397@lid'), '...0397')
assert.equal(jidHint('+972501234567@'), '...4567')
// error messages are masked too (#72): Baileys/HTTP errors could carry a JID
for (const path of ['Send error', 'Delete error', 'Media decode error', 'Backend error']) {
  assert.ok(lines.some(l => l.includes(`'[Bridge] ${path}:', maskNumbers(`)), `unmasked error path: ${path}`)
}
assert.equal(maskNumbers('send failed for 972586050155@s.whatsapp.net: timeout'), 'send failed for ...0155: timeout')
assert.equal(maskNumbers('jid 98058566160397:33@lid closed'), 'jid ...0397 closed')
assert.equal(maskNumbers('+972501234567 rejected'), '...4567 rejected')
assert.equal(maskNumbers('HTTP 503 after 15 s, code 428'), 'HTTP 503 after 15 s, code 428')   // short numbers untouched
assert.equal(maskNumbers(undefined), undefined)
console.log('NO RAW RECIPIENT LOGGING: 10 passed, 0 failed')
