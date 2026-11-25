/** Crash-injection tests for send_journal.js. Run: node whatsapp-bridge/test_send_journal.mjs
 *  A "crash" = a thrown sentinel, after which a NEW journal is built from the same file (a process restart). */
import os from 'os'
import fs from 'fs'
import path from 'path'
import { createSendJournal } from './send_journal.js'
import { atomicWriteFile } from './atomic_write.js'

let pass = 0, fail = 0
const ok = (label, cond) => { console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${label}`); cond ? pass++ : fail++ }
const tmp = () => path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'sj-')), 'j.json')
const CRASH = new Error('simulated process death')

// provider: records every wire send; the message id is what WhatsApp would see
const mkProvider = () => { const wire = []; return { wire, send: async (id) => { wire.push(id); return { key: { id: id || 'auto', fromMe: true } } } } }

console.log('1. crash BEFORE the provider call (pending persisted, nothing sent)')
{ const f = tmp(), p = mkProvider()
  const j1 = createSendJournal({ file: f })
  await j1.sendOnce('A1B2C3D4E5', async () => { throw CRASH }).catch(() => {})
  ok('nothing on the wire yet', p.wire.length === 0)
  const j2 = createSendJournal({ file: f })
  const r = await j2.sendOnce('A1B2C3D4E5', p.send)
  ok('retry after restart sends exactly once, same id', p.wire.length === 1 && p.wire[0] === 'A1B2C3D4E5' && !r.deduplicated) }

console.log('2. crash AFTER provider accepted, BEFORE "sent" reached disk (the residual window)')
{ const f = tmp(), p = mkProvider()
  let n = 0
  const failingWrite = async (file, c) => { if (++n === 2) throw CRASH; return atomicWriteFile(file, c) }
  const j1 = createSendJournal({ file: f, writeFile: failingWrite })
  const r1 = await j1.sendOnce('A1B2C3D4E5', p.send)   // accepted; persist of "sent" failed -> logged, caller still told ok
  ok('first attempt accepted on the wire', p.wire.length === 1 && r1.result.key.id === 'A1B2C3D4E5')
  const j2 = createSendJournal({ file: f })              // restart: journal only knows "pending"
  await j2.sendOnce('A1B2C3D4E5', p.send)
  ok('retry re-sends with the SAME message id (provider-side dedup; NOT journal-provable)', p.wire.length === 2 && p.wire[0] === p.wire[1]) }

console.log('3. crash AFTER "sent" persisted (backend never got the reply)')
{ const f = tmp(), p = mkProvider()
  const j1 = createSendJournal({ file: f }); await j1.sendOnce('A1B2C3D4E5', p.send)
  const j2 = createSendJournal({ file: f })
  const r = await j2.sendOnce('A1B2C3D4E5', p.send)
  ok('retry is answered from the journal, wire untouched', p.wire.length === 1 && r.deduplicated === true && r.result.key.id === 'A1B2C3D4E5') }

console.log('4. concurrent retries of one id share ONE send')
{ const f = tmp(), p = mkProvider(); const j = createSendJournal({ file: f })
  await Promise.all([1, 2, 3].map(() => j.sendOnce('A1B2C3D4E5', p.send)))
  ok('one wire send for three concurrent calls', p.wire.length === 1) }

console.log('5. provider error keeps the entry pending; next attempt sends; distinct ids never collide')
{ const f = tmp(), p = mkProvider(); const j = createSendJournal({ file: f })
  await j.sendOnce('A1B2C3D4E5', async () => { throw new Error('Connection Closed') }).catch(() => {})
  ok('status is pending (outcome unknown)', j.status('A1B2C3D4E5').status === 'pending')
  await j.sendOnce('A1B2C3D4E5', p.send); await j.sendOnce('ZZZZZZZZZZ', p.send)
  ok('retry sent, other id sent separately', p.wire.length === 2 && j.status('A1B2C3D4E5').status === 'sent') }

console.log('6. expiry and cap')
{ const f = tmp(); let t = 1e12; const j = createSendJournal({ file: f, now: () => t, ttlMs: 1000, max: 2 })
  const p = mkProvider()
  await j.sendOnce('AAAAAAAA01', p.send); await j.sendOnce('AAAAAAAA02', p.send); await j.sendOnce('AAAAAAAA03', p.send)
  ok('cap keeps the newest 2', !j.status('AAAAAAAA01') && !!j.status('AAAAAAAA03'))
  t += 5000; await j.sendOnce('AAAAAAAA04', p.send)
  ok('expired entries are dropped', !j.status('AAAAAAAA02')) }

console.log('7. no id = plain send, no journal entry')
{ const f = tmp(), p = mkProvider(); const j = createSendJournal({ file: f })
  const r = await j.sendOnce('', p.send)
  ok('sent without journalling', p.wire.length === 1 && !r.deduplicated && Object.keys(j._entries()).length === 0) }

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
