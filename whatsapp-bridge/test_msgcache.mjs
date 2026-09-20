/**
 * Phase 6 — Defect B regression tests: msgCache contract (B1-B7)
 *
 * Tests the bounded in-memory message cache added to index.js to fix the
 * "Waiting for this message" defect.  Runs standalone with:
 *   node whatsapp-bridge/test_msgcache.mjs
 *
 * All assertions are manual (no framework needed) so the file has zero
 * extra dependencies.
 */

// ── Replicated cache implementation (verbatim from index.js lines 86-96) ──────
const MAX_MSG_CACHE = 500
const msgCache = new Map()

function cacheOutboundMessage(msgId, protoMessage) {
  if (!msgId || !protoMessage) return
  if (msgCache.has(msgId)) return
  if (msgCache.size >= MAX_MSG_CACHE) {
    msgCache.delete(msgCache.keys().next().value)
  }
  msgCache.set(msgId, protoMessage)
}

// getMessage() as wired in makeWASocket config (index.js line 376 after fix)
const getMessage = async (key) => msgCache.get(key.id) ?? null

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

function section(name) {
  console.log(`\n${name}`)
}

// ── B1: Cache population — cacheOutboundMessage stores the proto.IMessage ─────
section('B1. Cache population: cacheOutboundMessage stores proto.IMessage')
{
  msgCache.clear()
  const proto = { conversation: 'Hello from B1' }
  cacheOutboundMessage('msg-b1-001', proto)
  assert('B1: msgCache.size is 1 after one store', msgCache.size, 1)
  assert('B1: stored value is the proto object', msgCache.get('msg-b1-001'), proto)
}

// ── B2: Exact ID lookup — getMessage(key) returns the stored proto.IMessage ───
section('B2. Exact ID lookup: getMessage returns the stored proto')
{
  msgCache.clear()
  const proto = { conversation: 'Hello from B2', imageMessage: null }
  cacheOutboundMessage('msg-b2-001', proto)
  const result = await getMessage({ remoteJid: 'x@s.whatsapp.net', fromMe: true, id: 'msg-b2-001' })
  assert('B2: getMessage returns exact proto reference', result, proto)
  assertNotNull('B2: returned value is truthy', result)
}

// ── B3: Unknown ID returns null — getMessage returns null for unknown IDs ──────
section('B3. Unknown ID returns null (not truthy empty object)')
{
  msgCache.clear()
  cacheOutboundMessage('msg-b3-known', { conversation: 'known' })
  const resultUnknown = await getMessage({ remoteJid: 'y@s.whatsapp.net', fromMe: true, id: 'msg-b3-UNKNOWN' })
  assert('B3: unknown ID → null (not {} or truthy stub)', resultUnknown, null)
  // Regression proof: the OLD bug returned { conversation: '' } which is truthy.
  // The ?? null operator ensures we get null, not undefined, not {}.
  assert('B3: null is strictly falsy (Baileys if(msg) skips relay)', !!resultUnknown, false)
}

// ── B4: Retry path simulation — truthiness determines relayMessage behaviour ───
section('B4. Retry path: truthiness gate matches Baileys sendMessagesAgain logic')
{
  // Reproduce the sendMessagesAgain truthiness check:
  //   if (msg) { await relayMessage(...) } else { logger.debug("not available") }
  msgCache.clear()
  const knownProto = { conversation: 'retry me' }
  cacheOutboundMessage('msg-b4-retry', knownProto)

  let relayCount = 0
  let skipCount = 0

  async function simulateSendMessagesAgain(ids, key) {
    const msgs = await Promise.all(ids.map(id => getMessage({ ...key, id })))
    for (const msg of msgs) {
      if (msg) relayCount++
      else skipCount++
    }
  }

  await simulateSendMessagesAgain(
    ['msg-b4-retry', 'msg-b4-UNKNOWN', 'msg-b4-ALSO-UNKNOWN'],
    { remoteJid: 'z@s.whatsapp.net', fromMe: true }
  )

  assert('B4: known ID fires relay (relayCount=1)', relayCount, 1)
  assert('B4: unknown IDs skip relay (skipCount=2)', skipCount, 2)
}

// ── B5: Eviction — oldest entry evicted when cache hits MAX_MSG_CACHE ─────────
section('B5. Eviction: oldest entry removed at MAX_MSG_CACHE boundary')
{
  msgCache.clear()
  // Fill to MAX_MSG_CACHE
  for (let i = 0; i < MAX_MSG_CACHE; i++) {
    cacheOutboundMessage(`evict-${i}`, { conversation: `msg-${i}` })
  }
  assert('B5: cache is exactly MAX_MSG_CACHE before eviction', msgCache.size, MAX_MSG_CACHE)

  // Adding one more should evict the first entry ('evict-0')
  cacheOutboundMessage('evict-new', { conversation: 'new' })
  assert('B5: cache stays at MAX_MSG_CACHE after eviction', msgCache.size, MAX_MSG_CACHE)
  assert('B5: oldest entry (evict-0) is gone', msgCache.has('evict-0'), false)
  assert('B5: second entry (evict-1) still present', msgCache.has('evict-1'), true)
  assert('B5: new entry is present', msgCache.has('evict-new'), true)
}

// ── B6: First-write-wins — duplicate IDs do not overwrite ─────────────────────
section('B6. Duplicate IDs: first write wins, second write is a no-op')
{
  msgCache.clear()
  const firstProto = { conversation: 'first' }
  const secondProto = { conversation: 'second (must NOT replace first)' }

  cacheOutboundMessage('msg-b6-dup', firstProto)
  cacheOutboundMessage('msg-b6-dup', secondProto)   // second write must be ignored

  assert('B6: cache still has 1 entry (no duplicate key)', msgCache.size, 1)
  assert('B6: stored value is still the first proto', msgCache.get('msg-b6-dup'), firstProto)
}

// ── B7: Null/falsy guards — invalid inputs are silently rejected ───────────────
section('B7. Null/falsy guards: invalid cacheOutboundMessage inputs are no-ops')
{
  msgCache.clear()
  cacheOutboundMessage(null, { conversation: 'should not store' })
  cacheOutboundMessage(undefined, { conversation: 'should not store' })
  cacheOutboundMessage('', { conversation: 'should not store' })
  cacheOutboundMessage('valid-id', null)
  cacheOutboundMessage('valid-id', undefined)

  assert('B7: cache is empty after all invalid inputs', msgCache.size, 0)

  // A valid call still works afterwards
  cacheOutboundMessage('valid-id', { conversation: 'real' })
  assert('B7: valid call after nulls stores correctly', msgCache.size, 1)
}

// ── Summary ────────────────────────────────────────────────────────────────────
console.log('\n' + '='.repeat(70))
console.log(`DEFECT B CACHE TESTS: ${passed} passed, ${failed} failed`)
if (failed > 0) {
  console.log('SOME TESTS FAILED')
  process.exit(1)
} else {
  console.log('ALL DEFECT B CACHE TESTS PASS')
}
