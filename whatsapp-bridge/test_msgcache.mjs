/**
 * Phase 6 — Defect B regression tests: msgCache contract (B1-B7)
 * Phase 7 (2026-09-27) — persistent retry-cache tests (Tests 1-6 below),
 * covering the real production defect proven live: a bridge restart wiped
 * the in-memory-only cache, so 5 of 9 real phone-side retry requests on
 * 2026-09-27 could not be served ("Waiting for this message" permanently).
 *
 * Imports the REAL module (msg_cache.js) — not a hand-copied replica — so
 * these tests exercise exactly what index.js runs in production.
 *
 * Runs standalone with:
 *   node whatsapp-bridge/test_msgcache.mjs
 */
import fs from 'fs'
import os from 'os'
import path from 'path'
import { createRequire } from 'module'
import { createMsgCache, DEFAULT_MAX_ENTRIES } from './msg_cache.js'

const require = createRequire(import.meta.url)
const { BufferJSON } = require('@whiskeysockets/baileys')

// ── Test harness ───────────────────────────────────────────────────────────────
let passed = 0
let failed = 0

function assert(label, actual, expected) {
  const ok = actual === expected
  const status = ok ? 'PASS' : 'FAIL'
  console.log(`  ${status}  ${label}`)
  if (!ok) {
    console.log(`        expected: ${JSON.stringify(expected)}`)
    console.log(`        actual:   ${JSON.stringify(actual)}`)
  }
  ok ? passed++ : failed++
}

function assertNotNull(label, actual) {
  const ok = actual !== null && actual !== undefined
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}`)
  if (!ok) console.log(`        expected non-null, got: ${JSON.stringify(actual)}`)
  ok ? passed++ : failed++
}

function isPlainRecord(v) {
  return !!v && typeof v === 'object' && !Array.isArray(v) && !Buffer.isBuffer(v)
}

function deepEqual(a, b) {
  if (a === b) return true
  if (Buffer.isBuffer(a) && Buffer.isBuffer(b)) return a.equals(b)
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v, i) => deepEqual(v, b[i]))
  }
  if (isPlainRecord(a) && isPlainRecord(b)) {
    const ak = Object.keys(a)
    const bk = Object.keys(b)
    return ak.length === bk.length && ak.every((k) => deepEqual(a[k], b[k]))
  }
  return false
}

function assertDeepEqual(label, actual, expected) {
  const ok = deepEqual(actual, expected)
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}`)
  if (!ok) {
    console.log(`        expected: ${JSON.stringify(expected)}`)
    console.log(`        actual:   ${JSON.stringify(actual)}`)
  }
  ok ? passed++ : failed++
}

function section(name) {
  console.log(`\n${name}`)
}

function tmpStorePath() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-msgcache-test-'))
  return path.join(dir, 'msg_cache.json')
}

async function flushMicrotasks() {
  // Fallback settle for cases with no cache handle to await flush() on
  // (e.g. T6d, where persist() targets a directory that doesn't exist and
  // the failure itself — not a successful write — is what must be observed).
  await new Promise((r) => setTimeout(r, 50))
}

// getMessage() as wired in makeWASocket config (index.js, current version)
function getMessageFor(cache) {
  return async (key) => cache.getCachedMessage(key.id) ?? null
}

// ── B1: Cache population — store persists via the real module ─────────────────
section('B1. Cache population: cacheOutboundMessage stores the proto payload')
{
  const cache = createMsgCache(tmpStorePath())
  const proto = { conversation: 'Hello from B1' }
  cache.cacheOutboundMessage('msg-b1-001', proto)
  assert('B1: size is 1 after one store', cache.size(), 1)
  assertDeepEqual('B1: stored value matches the proto', cache.getCachedMessage('msg-b1-001'), proto)
}

// ── B2: Exact ID lookup ─────────────────────────────────────────────────────────
section('B2. Exact ID lookup: getMessage returns the stored proto')
{
  const cache = createMsgCache(tmpStorePath())
  const getMessage = getMessageFor(cache)
  const proto = { conversation: 'Hello from B2', imageMessage: null }
  cache.cacheOutboundMessage('msg-b2-001', proto)
  const result = await getMessage({ remoteJid: 'x@s.whatsapp.net', fromMe: true, id: 'msg-b2-001' })
  assertDeepEqual('B2: getMessage returns the stored proto', result, proto)
  assertNotNull('B2: returned value is truthy', result)
}

// ── B3: Unknown ID returns null ─────────────────────────────────────────────────
section('B3. Unknown ID returns null (not truthy empty object)')
{
  const cache = createMsgCache(tmpStorePath())
  const getMessage = getMessageFor(cache)
  cache.cacheOutboundMessage('msg-b3-known', { conversation: 'known' })
  const resultUnknown = await getMessage({ remoteJid: 'y@s.whatsapp.net', fromMe: true, id: 'msg-b3-UNKNOWN' })
  assert('B3: unknown ID -> null (not {} or truthy stub)', resultUnknown, null)
  assert('B3: null is strictly falsy (Baileys if(msg) skips relay)', !!resultUnknown, false)
}

// ── B4: Retry path simulation ────────────────────────────────────────────────────
section('B4. Retry path: truthiness gate matches Baileys sendMessagesAgain logic')
{
  const cache = createMsgCache(tmpStorePath())
  const getMessage = getMessageFor(cache)
  cache.cacheOutboundMessage('msg-b4-retry', { conversation: 'retry me' })

  let relayCount = 0
  let skipCount = 0
  async function simulateSendMessagesAgain(ids, key) {
    const msgs = await Promise.all(ids.map((id) => getMessage({ ...key, id })))
    for (const msg of msgs) (msg ? relayCount++ : skipCount++)
  }
  await simulateSendMessagesAgain(
    ['msg-b4-retry', 'msg-b4-UNKNOWN', 'msg-b4-ALSO-UNKNOWN'],
    { remoteJid: 'z@s.whatsapp.net', fromMe: true },
  )
  assert('B4: known ID fires relay (relayCount=1)', relayCount, 1)
  assert('B4: unknown IDs skip relay (skipCount=2)', skipCount, 2)
}

// ── B5 / TEST 3: Capacity — oldest entry evicted at maxEntries boundary ────────
section('B5/T3. Capacity: oldest entry removed at the configured maximum')
{
  const cache = createMsgCache(tmpStorePath(), { maxEntries: 5 })
  for (let i = 0; i < 5; i++) cache.cacheOutboundMessage(`evict-${i}`, { conversation: `msg-${i}` })
  assert('T3: cache is exactly at capacity before overflow', cache.size(), 5)

  cache.cacheOutboundMessage('evict-new', { conversation: 'new' })
  assert('T3: cache stays bounded at capacity after overflow', cache.size(), 5)
  assert('T3: oldest entry (evict-0) is gone', cache.getCachedMessage('evict-0'), null)
  assertNotNull('T3: second-oldest (evict-1) still present', cache.getCachedMessage('evict-1'))
  assertNotNull('T3: new entry is present', cache.getCachedMessage('evict-new'))
}
// Same guarantee at the real production default (500), matching the original B5 exactly.
section('B5b. Capacity at the real DEFAULT_MAX_ENTRIES (500)')
{
  const cache = createMsgCache(tmpStorePath())
  for (let i = 0; i < DEFAULT_MAX_ENTRIES; i++) cache.cacheOutboundMessage(`d-${i}`, { conversation: `${i}` })
  assert('B5b: cache is exactly DEFAULT_MAX_ENTRIES before eviction', cache.size(), DEFAULT_MAX_ENTRIES)
  cache.cacheOutboundMessage('d-new', { conversation: 'new' })
  assert('B5b: cache stays at DEFAULT_MAX_ENTRIES after eviction', cache.size(), DEFAULT_MAX_ENTRIES)
  assert('B5b: oldest entry (d-0) is gone', cache.getCachedMessage('d-0'), null)
}

// ── B6: First-write-wins ─────────────────────────────────────────────────────────
section('B6. Duplicate IDs: first write wins, second write is a no-op')
{
  const cache = createMsgCache(tmpStorePath())
  const firstProto = { conversation: 'first' }
  cache.cacheOutboundMessage('msg-b6-dup', firstProto)
  cache.cacheOutboundMessage('msg-b6-dup', { conversation: 'second (must NOT replace first)' })
  assert('B6: cache still has 1 entry (no duplicate key)', cache.size(), 1)
  assertDeepEqual('B6: stored value is still the first proto', cache.getCachedMessage('msg-b6-dup'), firstProto)
}

// ── B7: Null/falsy guards ─────────────────────────────────────────────────────────
section('B7. Null/falsy guards: invalid cacheOutboundMessage inputs are no-ops')
{
  const cache = createMsgCache(tmpStorePath())
  cache.cacheOutboundMessage(null, { conversation: 'x' })
  cache.cacheOutboundMessage(undefined, { conversation: 'x' })
  cache.cacheOutboundMessage('', { conversation: 'x' })
  cache.cacheOutboundMessage('valid-id', null)
  cache.cacheOutboundMessage('valid-id', undefined)
  assert('B7: cache is empty after all invalid inputs', cache.size(), 0)
  cache.cacheOutboundMessage('valid-id', { conversation: 'real' })
  assert('B7: valid call after nulls stores correctly', cache.size(), 1)
}

// ── TEST 4: Expiration ────────────────────────────────────────────────────────────
section('T4. Expiration: entries older than ttlMs are not returned for retry')
{
  const cache = createMsgCache(tmpStorePath(), { ttlMs: 10 })
  cache.cacheOutboundMessage('expiring', { conversation: 'will expire' })
  assertNotNull('T4: readable immediately after store', cache.getCachedMessage('expiring'))
  await new Promise((r) => setTimeout(r, 30))
  assert('T4: expired entry returns null', cache.getCachedMessage('expiring'), null)
  assert('T4: expired entry is actually removed (size drops)', cache.size(), 0)
}

// ── TEST 5: Restart simulation — persistence survives a fresh instance ────────────
section('T5. Restart simulation: instance B (fresh) recovers what instance A wrote')
{
  const storePath = tmpStorePath()
  const instanceA = createMsgCache(storePath)
  const proto = { conversation: 'survives restart', extendedTextMessage: { text: 'hi' } }
  instanceA.cacheOutboundMessage('pre-restart-msg', proto)
  await instanceA.flush() // deterministically wait for the atomic write to land on disk

  assert('T5: the store file now exists on disk', fs.existsSync(storePath), true)

  // instance A is discarded here — no explicit close() exists (stateless module,
  // nothing but the file itself is the persisted state) — this simulates the
  // process exiting/restarting.
  const instanceB = createMsgCache(storePath)
  assert('T5: fresh instance recovers the persisted entry count', instanceB.size(), 1)
  assertDeepEqual(
    'T5: fresh instance returns the EXACT pre-restart message for a retry lookup',
    instanceB.getCachedMessage('pre-restart-msg'),
    proto,
  )

  // This is the literal defect proven live on 2026-09-27: a getMessage() lookup for
  // a message sent in a PRIOR process lifetime must now succeed, not return null.
  const getMessage = getMessageFor(instanceB)
  const retryResult = await getMessage({ remoteJid: 'x@s.whatsapp.net', fromMe: true, id: 'pre-restart-msg' })
  assertNotNull('T5: getMessage() succeeds for a pre-restart id (the real defect)', retryResult)
}

// ── TEST 5b: Persisted Buffers round-trip via Baileys' own BufferJSON ─────────────
section('T5b. Buffer fields inside a proto (e.g. an image mediaKey) survive restart')
{
  const storePath = tmpStorePath()
  const opts = { replacer: BufferJSON.replacer, reviver: BufferJSON.reviver }
  const instanceA = createMsgCache(storePath, opts)
  const mediaKey = Buffer.from('0123456789abcdef0123456789abcdef', 'hex')
  const proto = { imageMessage: { mediaKey, caption: 'a photo' } }
  instanceA.cacheOutboundMessage('media-msg', proto)
  await instanceA.flush()

  const instanceB = createMsgCache(storePath, opts)
  const recovered = instanceB.getCachedMessage('media-msg')
  assertNotNull('T5b: recovered message is present', recovered)
  assert('T5b: mediaKey round-trips as a real Buffer, not a plain object',
    Buffer.isBuffer(recovered?.imageMessage?.mediaKey), true)
  assert('T5b: mediaKey bytes are byte-for-byte identical after restart',
    Buffer.isBuffer(recovered?.imageMessage?.mediaKey) && recovered.imageMessage.mediaKey.equals(mediaKey), true)
}

// ── TEST 5c: Capacity is enforced across a restart, not just in-process ───────────
section('T5c. Capacity across restart: only the newest maxEntries survive reload')
{
  const storePath = tmpStorePath()
  const instanceA = createMsgCache(storePath, { maxEntries: 3 })
  for (let i = 0; i < 5; i++) instanceA.cacheOutboundMessage(`cap-${i}`, { conversation: `${i}` })
  await instanceA.flush()

  const instanceB = createMsgCache(storePath, { maxEntries: 3 })
  assert('T5c: reloaded store is pruned to maxEntries', instanceB.size(), 3)
  assert('T5c: oldest evicted entries stay gone after reload', instanceB.getCachedMessage('cap-0'), null)
  assertNotNull('T5c: newest entry survives reload', instanceB.getCachedMessage('cap-4'))
}

// ── TEST 6: Corrupt/missing state — the bridge must fail safe, never crash ───────
section('T6. Corrupt/missing state: safe handling, no crash')
{
  // 6a — store file has never existed (fresh install)
  {
    const storePath = tmpStorePath()
    fs.rmSync(storePath, { force: true }) // tmpStorePath's dir exists, file does not
    let threw = false
    let cache
    try {
      cache = createMsgCache(storePath)
    } catch {
      threw = true
    }
    assert('T6a: missing store file does not throw', threw, false)
    assert('T6a: missing store file starts with an empty cache', cache.size(), 0)
  }

  // 6b — malformed JSON body
  {
    const storePath = tmpStorePath()
    fs.writeFileSync(storePath, '{not valid json::::')
    const errors = []
    let threw = false
    let cache
    try {
      cache = createMsgCache(storePath, { onError: (stage, err) => errors.push(stage) })
    } catch {
      threw = true
    }
    assert('T6b: malformed JSON does not throw', threw, false)
    assert('T6b: malformed JSON falls back to an empty cache', cache.size(), 0)
    assert('T6b: onError was invoked with load_parse_failed', errors.includes('load_parse_failed'), true)
  }

  // 6c — one malformed record inside an otherwise-valid store
  {
    const storePath = tmpStorePath()
    fs.writeFileSync(storePath, JSON.stringify({
      good: { message: { conversation: 'ok' }, ts: Date.now() },
      bad_missing_ts: { message: { conversation: 'no ts field' } },
      bad_not_object: 'just a string',
    }))
    const errors = []
    const cache = createMsgCache(storePath, { onError: (stage) => errors.push(stage) })
    assert('T6c: valid record survives alongside malformed ones', cache.size(), 1)
    assertNotNull('T6c: the good record is actually retrievable', cache.getCachedMessage('good'))
    assert('T6c: onError reports the skipped malformed records',
      errors.includes('load_skipped_malformed_records'), true)
  }

  // 6d — persist() failure (target directory does not exist) must not throw
  // synchronously into the caller, and must be observable via onError.
  {
    const badPath = path.join(os.tmpdir(), `wa-msgcache-nonexistent-dir-${process.pid}`, 'msg_cache.json')
    const errors = []
    const cache = createMsgCache(badPath, { onError: (stage) => errors.push(stage) })
    let threw = false
    try {
      cache.cacheOutboundMessage('will-fail-to-persist', { conversation: 'x' })
    } catch {
      threw = true
    }
    assert('T6d: a persist failure does not throw synchronously', threw, false)
    assertNotNull('T6d: the in-memory cache still serves the message THIS process wrote',
      cache.getCachedMessage('will-fail-to-persist'))
    await flushMicrotasks()
    assert('T6d: the persist failure is observable via onError', errors.includes('persist_failed'), true)
  }
}

// ── Summary ────────────────────────────────────────────────────────────────────
console.log('\n' + '='.repeat(70))
console.log(`MSG CACHE TESTS (in-memory contract + persistence): ${passed} passed, ${failed} failed`)
if (failed > 0) {
  console.log('SOME TESTS FAILED')
  process.exit(1)
} else {
  console.log('ALL MSG CACHE TESTS PASS')
}
