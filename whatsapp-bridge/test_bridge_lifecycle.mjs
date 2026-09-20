/**
 * Regression — Baileys bridge: orphaned-query timeout crash + false /health (2026-09-20).
 *
 * Runs the REAL index.js + bridge_lifecycle.js (real express routes) in a temp dir against a STUB
 * Baileys, so no WhatsApp connection can exist and no message can be sent. The stub reproduces the
 * exact library defect: query() creates the waitForMessage promise (real Baileys promiseTimeout,
 * armed timer) BEFORE sendNode throws 'Connection Closed', so the promise is orphaned and rejects
 * `Boom('Timed Out', 408)` later with no handler.
 *
 * Run:  node --test whatsapp-bridge/test_bridge_lifecycle.mjs      (Node >= 18)
 *
 * Covers: 1 healthy /health true · 2 healthy send ok · 3 dead /send controlled failure ·
 * 4 dead /groups controlled failure · 5 orphan timeout does NOT crash the process ·
 * 6 timeout stays logged · 7 /health false when unusable even with stored creds ·
 * 8 reconnect restores true · 9 428/503/515 recovery intact (401 does not reconnect) ·
 * + harness validity (unguarded orphan DOES crash; unrelated rejection still crashes).
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { fork, spawnSync } from 'node:child_process'
import http from 'node:http'
import { createRequire } from 'node:module'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const require = createRequire(import.meta.url)
const REAL_NM = path.join(HERE, 'node_modules')
const { promiseTimeout } = require(path.join(REAL_NM, '@whiskeysockets/baileys/lib/Utils/generics.js'))
const lifecycle = await import(path.join(HERE, 'bridge_lifecycle.js'))
const ORPHAN_MS = 250

// ── stub Baileys (CommonJS, injected as node_modules/@whiskeysockets/baileys) ───────────────
const STUB = `
const EventEmitter = require('events')
const { Boom } = require('@hapi/boom')
const { promiseTimeout } = require(${JSON.stringify(path.join(REAL_NM, '@whiskeysockets/baileys/lib/Utils/generics.js'))})
const sockets = []
const mode = { send: 'ok', groups: 'ok' }
// Same shape as Baileys socket.js: an async arrow named waitForMessage that awaits promiseTimeout.
const waitForMessage = async (ms) => { return await promiseTimeout(ms, (resolve, reject) => {}) }
async function deadSocketOp() {
  const wait = waitForMessage(${ORPHAN_MS}) // armed timer, promise never awaited (== query() before sendNode throws)
  void wait
  throw new Boom('Connection Closed', { statusCode: 428 })
}
function makeWASocket() {
  const ev = new EventEmitter()
  const sock = {
    ev, ws: { isOpen: true },
    // creds-populated user BEFORE any authentication: the false-health scenario
    user: { id: '972500000000:11@s.whatsapp.net' },
    authState: { creds: { registered: true } },
    async sendMessage(jid, payload) {
      process.send({ type: 'send_called' })
      if (mode.send === 'dead_orphan') return deadSocketOp()
      if (mode.send === 'type_error') throw new TypeError('boom-unrelated')
      return { key: { id: 'STUB' + Date.now(), remoteJid: jid, fromMe: true }, message: { conversation: 'x' } }
    },
    async groupFetchAllParticipating() {
      process.send({ type: 'groups_called' })
      if (mode.groups === 'dead_orphan') return deadSocketOp()
      return { g1: { id: '1@g.us', subject: 'stub group', participants: [] } }
    },
    async sendPresenceUpdate() {},
  }
  sockets.push(sock)
  process.send({ type: 'created', n: sockets.length })
  return sock
}
process.on('message', (m) => {
  const last = sockets[sockets.length - 1]
  if (m.cmd === 'mode') mode[m.target] = m.value
  if (m.cmd === 'ws') last.ws.isOpen = m.isOpen
  if (m.cmd === 'emit_event') last.ev.emit(m.event, m.payload)
  if (m.cmd === 'emit') {
    const payload = { ...m.payload }
    if (payload.code) { payload.lastDisconnect = { error: new Boom(payload.message || 'x', { statusCode: payload.code }) }; delete payload.code; delete payload.message }
    last.ev.emit('connection.update', payload)
  }
})
module.exports = makeWASocket
module.exports.default = makeWASocket
module.exports.makeWASocket = makeWASocket
module.exports.useMultiFileAuthState = async () => ({ state: { creds: { registered: true }, keys: {} } })
module.exports.fetchLatestBaileysVersion = async () => ({ version: [2, 3000, 1] })
module.exports.DisconnectReason = { loggedOut: 401, connectionClosed: 428, timedOut: 408 }
module.exports.normalizeMessageContent = (m) => m
module.exports.downloadContentFromMessage = async () => (async function* () {})()
module.exports.BufferJSON = { replacer: (k, v) => v, reviver: (k, v) => v }
`

async function startBridge() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bridge-lifecycle-'))
  for (const f of ['index.js', 'bridge_lifecycle.js', 'safe_logging.js', 'atomic_write.js', 'package.json']) {
    fs.copyFileSync(f === 'index.js' && process.env.BRIDGE_TEST_INDEX ? process.env.BRIDGE_TEST_INDEX : path.join(HERE, f), path.join(dir, f))
  }
  const nm = path.join(dir, 'node_modules')
  fs.mkdirSync(path.join(nm, '@whiskeysockets/baileys'), { recursive: true })
  fs.mkdirSync(path.join(nm, '@hapi'), { recursive: true })
  for (const pkg of ['express', 'axios', 'pino', 'qrcode', 'qrcode-terminal']) fs.symlinkSync(path.join(REAL_NM, pkg), path.join(nm, pkg), 'dir')
  fs.symlinkSync(path.join(REAL_NM, '@hapi/boom'), path.join(nm, '@hapi/boom'), 'dir')
  fs.writeFileSync(path.join(nm, '@whiskeysockets/baileys/index.js'), STUB)
  fs.writeFileSync(path.join(nm, '@whiskeysockets/baileys/package.json'), '{"name":"@whiskeysockets/baileys","main":"index.js"}')
  const port = 39000 + Math.floor(Math.random() * 900)
  // fake BACKEND webhook (inbound messages are forwarded here; status is controllable per test)
  const hook = { bodies: [], status: 200 }
  const hookServer = http.createServer((req, res) => {
    let raw = ''; req.on('data', (d) => { raw += d })
    req.on('end', () => { try { hook.bodies.push(JSON.parse(raw)) } catch {} ; res.statusCode = hook.status; res.end('{}') })
  })
  await new Promise((r) => hookServer.listen(0, '127.0.0.1', r))
  const child = fork(path.join(dir, 'index.js'), [], {
    cwd: dir, silent: true,
    env: { ...process.env, BRIDGE_PORT: String(port), BRIDGE_APP_DIR: dir, WHATSAPP_EXPECTED_NUMBER: '972500000000',
           BACKEND_URL: `http://127.0.0.1:${hookServer.address().port}/hook` },
  })
  let out = ''
  child.stdout.on('data', (d) => { out += d })
  child.stderr.on('data', (d) => { out += d })
  const events = []
  child.on('message', (m) => events.push(m))
  const b = {
    child, port, dir, events, hook,
    out: () => out,
    alive: () => child.exitCode === null && child.signalCode === null,
    count: (t) => events.filter((e) => e.type === t).length,
    send: (m) => child.send(m),
    emit: (payload) => child.send({ cmd: 'emit', payload }),
    async get(p) { const r = await fetch(`http://127.0.0.1:${port}${p}`); return { status: r.status, body: await r.json() } },
    async post(p, body) {
      const r = await fetch(`http://127.0.0.1:${port}${p}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
      return { status: r.status, body: await r.json() }
    },
    async until(fn, ms = 4000) { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await fn()) return true; await sleep(25) } return false },
    async stop() { try { child.kill('SIGKILL') } catch {} ; hookServer.close(); fs.rmSync(dir, { recursive: true, force: true }) },
  }
  assert.ok(await b.until(async () => { try { return (await b.get('/health')).status === 200 } catch { return false } }), 'bridge did not start:\n' + out)
  assert.ok(await b.until(() => b.count('created') >= 1), 'stub socket not created')
  return b
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const open = async (b) => { b.emit({ connection: 'open' }); assert.ok(await b.until(async () => (await b.get('/health')).body.connected === true)) }
const SEND = { to: '972500000001', text: 'stub' }

// ── unit: state machine / gates / classifier ────────────────────────────────────────────────
test('unit: connection state machine derives state only from events', () => {
  const c = lifecycle.createConnectionState()
  assert.equal(c.state, 'CONNECTING')
  c.onQr(); assert.equal(c.state, 'AWAITING_QR')
  c.onOpen(); assert.equal(c.state, 'CONNECTED')
  c.onClose(428, 'Connection Terminated'); assert.equal(c.state, 'DISCONNECTED')
  c.onConnecting(); c.onOpen(); assert.equal(c.state, 'CONNECTED')
  c.onClose(401, 'Connection Failure'); assert.equal(c.state, 'SESSION_INVALID')
  assert.equal(c.lastDisconnect.code, 401)
})

test('unit: orphan classifier matches the REAL Baileys orphan and nothing broader', async () => {
  const waitForMessage = async (ms) => promiseTimeout(ms, () => {})       // same frame name as socket.js
  const other = async (ms) => promiseTimeout(ms, () => {})                 // Timed Out from a different caller
  const orphan = await waitForMessage(15).catch((e) => e)
  const notOrphan = await other(15).catch((e) => e)
  assert.equal(lifecycle.isOrphanQueryTimeout(orphan), true)
  assert.equal(lifecycle.isOrphanQueryTimeout(notOrphan), false, 'a Timed Out from any other operation must NOT be contained')
  assert.equal(lifecycle.isOrphanQueryTimeout(new Error('Timed Out')), false)
  assert.equal(lifecycle.isOrphanQueryTimeout(null), false)
})

function runScript(script) {
  return spawnSync(process.execPath, ['--input-type=module', '-e', script], { encoding: 'utf8', timeout: 8000 })
}
const PRELUDE = `import { createRequire } from 'node:module'; const require = createRequire(${JSON.stringify(import.meta.url)});
const { promiseTimeout } = require(${JSON.stringify(path.join(REAL_NM, '@whiskeysockets/baileys/lib/Utils/generics.js'))});
const waitForMessage = async (ms) => promiseTimeout(ms, () => {});
async function orphan() { const w = waitForMessage(60); void w; throw new Error('Connection Closed') }
const lc = await import(${JSON.stringify(path.join(HERE, 'bridge_lifecycle.js'))});`

test('harness validity: WITHOUT the guard an orphaned timeout terminates the process (the production crash)', () => {
  const r = runScript(PRELUDE + `try { await orphan() } catch {} ; setTimeout(() => console.log('STILL_ALIVE'), 400)`)
  assert.notEqual(r.status, 0, 'unguarded orphan should crash')
  assert.ok(!r.stdout.includes('STILL_ALIVE'))
})

test('guard: contains + logs the orphan, but an unrelated unhandled rejection STILL crashes', () => {
  const ok = runScript(PRELUDE + `const seen = []; lc.installOrphanTimeoutGuard({ log: (e, d) => console.log('LOG', e) });
    try { await orphan() } catch {} ; setTimeout(() => { console.log('STILL_ALIVE'); }, 400)`)
  assert.equal(ok.status, 0, ok.stderr)
  assert.ok(ok.stdout.includes('LOG ORPHAN_QUERY_TIMEOUT') && ok.stdout.includes('STILL_ALIVE'))
  const bad = runScript(PRELUDE + `lc.installOrphanTimeoutGuard({ log: (e) => console.log('LOG', e) });
    Promise.reject(new Error('real bug')); setTimeout(() => console.log('STILL_ALIVE'), 400)`)
  assert.notEqual(bad.status, 0, 'unrelated rejection must keep default fatal behaviour')
  assert.ok(bad.stdout.includes('LOG UNHANDLED_REJECTION_FATAL') && !bad.stdout.includes('STILL_ALIVE'))
})

// ── integration: real index.js ──────────────────────────────────────────────────────────────
test('7. /health is FALSE while unauthenticated even though the socket already carries stored credentials', async () => {
  const b = await startBridge()
  try {
    const h = (await b.get('/health')).body
    assert.equal(h.connected, false, 'connected must not be derived from waSocket.user')
    assert.equal(h.state, 'CONNECTING')
    assert.equal(h.jid, '972500000000:11@s.whatsapp.net', 'creds-populated user IS present — and is not enough')
    const s = await b.post('/send', SEND)
    assert.equal(s.status, 503); assert.equal(b.count('send_called'), 0)
  } finally { await b.stop() }
})

test('1+2. healthy socket: /health connected=true and /send succeeds (fake, no WhatsApp)', async () => {
  const b = await startBridge()
  try {
    await open(b)
    const h = (await b.get('/health')).body
    assert.equal(h.connected, true); assert.equal(h.state, 'CONNECTED'); assert.equal(h.awaiting_qr_scan, false)
    const s = await b.post('/send', SEND)
    assert.equal(s.status, 200); assert.equal(s.body.ok, true); assert.equal(b.count('send_called'), 1)
    const g = await b.get('/groups')
    assert.equal(g.status, 200); assert.equal(g.body.ok, true)
  } finally { await b.stop() }
})

test('3+4. dead/reconnecting socket: /send and /groups fail in a controlled way and never touch Baileys', async () => {
  const b = await startBridge()
  try {
    await open(b)
    b.emit({ connection: 'close', code: 428, message: 'Connection Terminated' })
    assert.ok(await b.until(() => b.count('created') === 2))            // reconnect started a new socket (CONNECTING)
    assert.equal((await b.get('/health')).body.connected, false)
    const s = await b.post('/send', SEND), g = await b.get('/groups')
    assert.equal(s.status, 503); assert.equal(s.body.ok, false); assert.match(s.body.error, /connecting|reconnecting/i)
    assert.equal(g.status, 503); assert.equal(g.body.ok, false)
    assert.equal(b.count('send_called'), 0); assert.equal(b.count('groups_called'), 0, 'no query may be started on an unusable socket')
    assert.equal((await b.post('/typing', { to: '972500000001' })).status, 503)
  } finally { await b.stop() }
})

test('5+6. RACE (socket dies after the gate): /send and /groups return 500, orphan timeout does NOT crash, and is logged', async () => {
  const b = await startBridge()
  try {
    await open(b)
    b.send({ cmd: 'mode', target: 'send', value: 'dead_orphan' }); b.send({ cmd: 'mode', target: 'groups', value: 'dead_orphan' })
    const s = await b.post('/send', SEND), g = await b.get('/groups')
    assert.equal(s.status, 500); assert.equal(s.body.ok, false); assert.match(s.body.error, /Connection Closed/)
    assert.equal(g.status, 500); assert.equal(g.body.ok, false)
    await sleep(ORPHAN_MS * 3)                                            // both orphan timers have fired by now
    assert.ok(b.alive(), 'process must survive the orphaned Timed Out\n' + b.out())
    assert.equal((await b.get('/health')).status, 200)                    // still serving
    const contained = (b.out().match(/ORPHAN_QUERY_TIMEOUT/g) || []).length
    assert.ok(contained >= 2, `orphan timeouts must stay observable in the log (saw ${contained})\n` + b.out())
    assert.ok(fs.readFileSync(path.join(b.dir, 'connection_events.log'), 'utf8').includes('ORPHAN_QUERY_TIMEOUT'))
    assert.ok(!/UNHANDLED_REJECTION_FATAL/.test(b.out()))
    // healthy behaviour is unaffected afterwards
    b.send({ cmd: 'mode', target: 'send', value: 'ok' })
    assert.equal((await b.post('/send', SEND)).status, 200)
  } finally { await b.stop() }
})

test('7b+9. 401 = SESSION_INVALID: health false, sends refused, NO reconnect, process alive', async () => {
  const b = await startBridge()
  try {
    await open(b)
    b.emit({ connection: 'close', code: 401, message: 'Connection Failure' })
    assert.ok(await b.until(async () => (await b.get('/health')).body.state === 'SESSION_INVALID'))
    const h = (await b.get('/health')).body
    assert.equal(h.connected, false); assert.equal(h.session_invalid, true); assert.equal(h.last_disconnect.code, 401)
    assert.equal(h.jid, '972500000000:11@s.whatsapp.net', 'stored user still there, health still false')
    const s = await b.post('/send', SEND)
    assert.equal(s.status, 503); assert.match(s.body.error, /invalid|re-pair/i)
    assert.equal((await b.get('/groups')).status, 503)
    await sleep(400)
    assert.equal(b.count('created'), 1, '401 must not trigger a reconnect (existing behaviour)')
    assert.ok(b.alive()); assert.equal(b.count('send_called'), 0)
  } finally { await b.stop() }
})

for (const code of [428, 503, 515]) {
  test(`8+9. ${code} recovery intact: reconnects with a new socket and /health returns to connected=true`, async () => {
    const b = await startBridge()
    try {
      await open(b)
      b.emit({ connection: 'close', code, message: 'Stream Errored' })
      assert.ok(await b.until(() => b.count('created') === 2), `${code} must trigger startBot() again`)
      assert.equal((await b.get('/health')).body.connected, false)        // reconnecting is NOT connected
      await open(b)                                                       // new socket opens
      const h = (await b.get('/health')).body
      assert.equal(h.connected, true); assert.equal(h.state, 'CONNECTED')
      assert.equal((await b.post('/send', SEND)).status, 200)             // sends work again
      assert.ok(b.alive())
    } finally { await b.stop() }
  })
}

test('awaiting QR is reported distinctly', async () => {
  const b = await startBridge()
  try {
    b.emit({ qr: 'stub-qr-payload' })
    assert.ok(await b.until(async () => (await b.get('/health')).body.state === 'AWAITING_QR'))
    const h = (await b.get('/health')).body
    assert.equal(h.connected, false); assert.equal(h.awaiting_qr_scan, true)
    assert.match((await b.post('/send', SEND)).body.error, /QR/)
  } finally { await b.stop() }
})

// ── messages.upsert listener boundary (inbound error-fallback reply) ───────────────────────────
const inbound = (b, n = 1) => b.send({
  cmd: 'emit_event', event: 'messages.upsert',
  payload: { type: 'notify', messages: Array.from({ length: n }, (_, i) => ({
    key: { remoteJid: `97250000010${i}@s.whatsapp.net`, id: `M${i}`, fromMe: false },
    message: { conversation: 'שלום' }, pushName: 'T' })) },
})

test('unit: safeListenerSend skips / contains / re-throws', async () => {
  const logs = []; const log = (e, d) => logs.push(e)
  const okConn = lifecycle.createConnectionState(); okConn.onOpen()
  const good = { user: { id: 'x' }, ws: { isOpen: true }, sendMessage: async () => ({}) }
  assert.deepEqual(await lifecycle.safeListenerSend({ conn: okConn, sock: good, jid: 'j', payload: {}, log, what: 'w' }), { ok: true })
  let called = 0
  const dead = { user: { id: 'x' }, ws: { isOpen: false }, sendMessage: async () => { called++ } }
  const r1 = await lifecycle.safeListenerSend({ conn: okConn, sock: dead, jid: 'j', payload: {}, log, what: 'w' })
  assert.equal(r1.skipped, true); assert.equal(called, 0)
  const closed = { user: { id: 'x' }, ws: { isOpen: true }, sendMessage: async () => { const e = new Error('Connection Closed'); e.output = { statusCode: 428 }; throw e } }
  assert.equal((await lifecycle.safeListenerSend({ conn: okConn, sock: closed, jid: 'j', payload: {}, log, what: 'w' })).expected, true)
  const bad = { user: { id: 'x' }, ws: { isOpen: true }, sendMessage: async () => { throw new TypeError('unrelated') } }
  await assert.rejects(lifecycle.safeListenerSend({ conn: okConn, sock: bad, jid: 'j', payload: {}, log, what: 'w' }), /unrelated/)
  assert.deepEqual(logs, ['LISTENER_SEND_SKIPPED', 'LISTENER_SEND_FAILED_DEAD_SOCKET'])
})

test('L1. healthy messages.upsert: forwarded to the backend, no fallback send, process fine', async () => {
  const b = await startBridge()
  try {
    await open(b)
    inbound(b)
    assert.ok(await b.until(() => b.hook.bodies.length === 1), 'inbound message was not forwarded\n' + b.out())
    assert.match(b.hook.bodies[0].from, /^whatsapp:\+972/); assert.equal(b.hook.bodies[0].body, 'שלום')
    await sleep(150)
    assert.equal(b.count('send_called'), 0); assert.ok(b.alive()); assert.ok(!/LISTENER_SEND|FATAL/.test(b.out()))
  } finally { await b.stop() }
})

test('L2. healthy socket + backend failure: the fallback reply is still sent exactly as before', async () => {
  const b = await startBridge()
  try {
    await open(b); b.hook.status = 500
    inbound(b)
    assert.ok(await b.until(() => b.count('send_called') === 1), 'fallback reply must be sent on a healthy socket\n' + b.out())
    await sleep(150)
    assert.ok(b.alive()); assert.ok(!/LISTENER_SEND|FATAL/.test(b.out()))
  } finally { await b.stop() }
})

test('L3. dead socket + backend failure: reply skipped WITHOUT touching Baileys, logged, process alive, batch continues', async () => {
  const b = await startBridge()
  try {
    await open(b); b.hook.status = 500
    b.send({ cmd: 'ws', isOpen: false })                                  // socket died; no close event yet
    inbound(b, 2)
    assert.ok(await b.until(() => b.hook.bodies.length === 2), 'the 2nd message of the batch must still be processed\n' + b.out())
    await sleep(ORPHAN_MS * 2)
    assert.equal(b.count('send_called'), 0, 'no Baileys call on an unusable socket')
    assert.ok(b.alive(), b.out())
    assert.ok((b.out().match(/LISTENER_SEND_SKIPPED/g) || []).length >= 2, 'controlled skip must be logged\n' + b.out())
    assert.ok(!/UNHANDLED_REJECTION_FATAL/.test(b.out()))
  } finally { await b.stop() }
})

test('L4. RACE: sendMessage throws Connection Closed (+orphan timer) inside the listener: caught, logged, process alive', async () => {
  const b = await startBridge()
  try {
    await open(b); b.hook.status = 500
    b.send({ cmd: 'mode', target: 'send', value: 'dead_orphan' })
    inbound(b, 2)
    assert.ok(await b.until(() => b.hook.bodies.length === 2 && b.count('send_called') === 2), 'both messages must be processed\n' + b.out())
    await sleep(ORPHAN_MS * 3)                                            // orphan timers fire
    assert.ok(b.alive(), 'process must survive\n' + b.out())
    assert.ok((b.out().match(/LISTENER_SEND_FAILED_DEAD_SOCKET/g) || []).length >= 2, b.out())
    assert.ok(/ORPHAN_QUERY_TIMEOUT/.test(b.out()), 'the orphan timeout stays logged')
    assert.ok(!/UNHANDLED_REJECTION_FATAL/.test(b.out()))
    assert.equal((await b.get('/health')).status, 200)
  } finally { await b.stop() }
})

test('L5. an UNRELATED error from the listener send is NOT swallowed: it is logged and still terminates (existing behaviour)', async () => {
  const b = await startBridge()
  try {
    await open(b); b.hook.status = 500
    b.send({ cmd: 'mode', target: 'send', value: 'type_error' })
    inbound(b)
    assert.ok(await b.until(() => !b.alive(), 5000), 'unexpected errors must keep their fatal visibility\n' + b.out())
    assert.match(b.out(), /UNHANDLED_REJECTION_FATAL/); assert.match(b.out(), /boom-unrelated/)
    assert.ok(!/LISTENER_SEND_FAILED_DEAD_SOCKET/.test(b.out()))
  } finally { await b.stop() }
})
