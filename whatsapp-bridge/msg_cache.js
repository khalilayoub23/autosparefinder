/**
 * Persistent, bounded outbound-message retry cache (root-fix 2026-09-27).
 *
 * BACKGROUND: index.js caches each outbound message's proto.IMessage payload
 * so that when the recipient's WhatsApp fails to decrypt it and sends a
 * retry receipt, Baileys' getMessage(key) can hand back the original content
 * for relay (see the "Waiting for this message" investigation, 2026-09-10).
 * That cache was an in-memory-only Map — a bridge restart (crash, redeploy,
 * `docker restart`) wiped it. Real production evidence (2026-09-27,
 * 04:00:57Z): 9 phone-side retry requests arrived after a restart; the 4
 * for messages sent AFTER the restart were served correctly, the 5 for
 * messages sent BEFORE it returned `wa_get_message result=not_found` and
 * could never be redelivered.
 *
 * FIX: back the same bounded Map with a JSON file, written via the existing
 * atomicWriteFile() primitive (atomic_write.js — the same crash-safe
 * temp-file+fsync+rename sequence already used for auth_info/creds.json, so
 * this introduces no new persistence pattern). The in-memory Map stays the
 * hot read path for getMessage(); the file is the recovery source after a
 * restart. There is exactly one source of truth at any instant: whichever
 * the Map holds, which `persist()` mirrors to disk after every mutation.
 *
 * STORAGE LOCATION: `${APP_DIR}/msg_cache.json`, i.e. inside the
 * `./whatsapp-bridge:/app` bind mount (docker-compose.yml) — the exact same
 * mount auth_info/ and connection_events.log already rely on. This survives
 * a container restart AND a container recreation (bind mount is host-backed,
 * not the writable container layer); it would NOT survive if the bind mount
 * itself were ever removed from the compose file.
 *
 * SECURITY / DATA MINIMIZATION: only {message, ts} per id is persisted — no
 * WhatsApp session/auth state, no keys beyond whatever a proto.IMessage
 * itself already carries for the ORIGINAL send (e.g. an image's mediaKey,
 * which is useless without the corresponding encrypted blob already sitting
 * on WhatsApp's media CDN). File mode 0o600 (owner-read/write only) — the
 * bridge already runs as a single root process on the box, but message text
 * (order numbers, prices, customer names) does not need to be
 * group/world-readable the way e.g. a log file might. Buffers inside a
 * proto.IMessage are serialized with Baileys' OWN `BufferJSON` replacer/
 * reviver (already imported by index.js for creds.json) — no parallel
 * encoding scheme invented for this file.
 *
 * RETENTION: WhatsApp's own retry window is minutes-to-hours in practice;
 * the original in-memory design was sized for "500 entries ~= 10 days at the
 * observed ~2 msgs/hour baseline". `DEFAULT_TTL_MS` (14 days) is a generous
 * backstop that only matters if send volume rises far above that baseline —
 * it bounds disk growth without discarding anything a real retry could
 * plausibly still reference. `DEFAULT_MAX_ENTRIES` (500) is unchanged from
 * the original in-memory cap; nothing here changes normal-operation
 * behavior, only what survives a restart.
 *
 * FAILURE HANDLING: a missing file (first run), a malformed JSON body, or an
 * individual corrupt record inside an otherwise-valid file are all
 * non-fatal — the cache falls back to empty (or to whatever subset of
 * records DID parse) and logs via the injected `onError` hook. A persist()
 * failure (disk full, permissions) is likewise swallowed after logging: it
 * never throws into the caller (message sending must never be blocked or
 * crashed by a retry-cache write failure), and the in-memory Map remains
 * correct for the running process — the next successful persist() catches
 * the file up.
 */
import fs from 'fs'
import { atomicWriteFile } from './atomic_write.js'

export const DEFAULT_MAX_ENTRIES = 500
export const DEFAULT_TTL_MS = 14 * 24 * 60 * 60 * 1000 // 14 days

function isPlainRecord(v) {
  return !!v && typeof v === 'object' && !Array.isArray(v)
}

/**
 * Create a persistent msgCache instance backed by a single JSON file at
 * `storePath`. Loads (and prunes) any existing store synchronously before
 * returning, so the instance is immediately ready to serve getMessage()
 * lookups for messages sent in a prior process lifetime.
 *
 * @param {string} storePath
 * @param {{maxEntries?: number, ttlMs?: number, replacer?: Function, reviver?: Function, onError?: (stage: string, err: Error) => void}} [opts]
 */
export function createMsgCache(storePath, opts = {}) {
  const maxEntries = opts.maxEntries ?? DEFAULT_MAX_ENTRIES
  const ttlMs = opts.ttlMs ?? DEFAULT_TTL_MS
  const replacer = opts.replacer // optional — Baileys' BufferJSON.replacer, injected by index.js
  const reviver = opts.reviver   // optional — Baileys' BufferJSON.reviver, injected by index.js
  const onError = opts.onError ?? (() => {})

  const map = new Map() // msgId -> { message, ts }
  let pendingPersist = Promise.resolve() // test-only hook (flush()) — see below

  function pruneExpired(now = Date.now()) {
    let changed = false
    for (const [id, entry] of map) {
      if (now - entry.ts > ttlMs) {
        map.delete(id)
        changed = true
      }
    }
    return changed
  }

  function evictToCapacity() {
    let changed = false
    while (map.size > maxEntries) {
      map.delete(map.keys().next().value) // oldest first (Map preserves insertion order)
      changed = true
    }
    return changed
  }

  function serialize() {
    const obj = {}
    for (const [id, entry] of map) obj[id] = entry
    return JSON.stringify(obj, replacer)
  }

  function persist() {
    pendingPersist = atomicWriteFile(storePath, serialize(), { mode: 0o600 }).catch((err) => {
      onError('persist_failed', err)
    })
  }

  function load() {
    let raw
    try {
      raw = fs.readFileSync(storePath, 'utf8')
    } catch (err) {
      if (err.code === 'ENOENT') return // first run — empty cache, not an error
      onError('load_read_failed', err)
      return
    }
    let parsed
    try {
      parsed = JSON.parse(raw, reviver)
    } catch (err) {
      onError('load_parse_failed', err) // malformed file — fail safe, start empty
      return
    }
    if (!isPlainRecord(parsed)) {
      onError('load_shape_invalid', new Error('msg_cache store root is not an object'))
      return
    }
    let skipped = 0
    for (const [id, entry] of Object.entries(parsed)) {
      if (!isPlainRecord(entry) || !isPlainRecord(entry.message) || typeof entry.ts !== 'number') {
        skipped += 1
        continue // one malformed record must not take down the rest of the store
      }
      map.set(id, entry)
    }
    if (skipped > 0) {
      onError('load_skipped_malformed_records', new Error(`skipped ${skipped} malformed record(s)`))
    }
    const prunedExpired = pruneExpired()
    const prunedCapacity = evictToCapacity()
    if (prunedExpired || prunedCapacity) persist() // write back the pruned snapshot
  }

  function cacheOutboundMessage(msgId, protoMessage) {
    if (!msgId || !protoMessage) return
    if (map.has(msgId)) return // first write wins; no overwrite on resend
    pruneExpired()
    if (map.size >= maxEntries) {
      map.delete(map.keys().next().value)
    }
    map.set(msgId, { message: protoMessage, ts: Date.now() })
    persist()
  }

  function getCachedMessage(msgId) {
    const entry = map.get(msgId)
    if (!entry) return null
    if (Date.now() - entry.ts > ttlMs) {
      map.delete(msgId)
      persist()
      return null
    }
    return entry.message
  }

  function size() {
    return map.size
  }

  // Test-only: await the most recently queued persist() write. Production
  // code (index.js) never calls this — persistence is fire-and-forget by
  // design (see the module docstring's FAILURE HANDLING section) — but tests
  // need a deterministic way to know a write has landed before simulating a
  // restart, rather than guessing at a sleep duration.
  function flush() {
    return pendingPersist
  }

  load()

  return { cacheOutboundMessage, getCachedMessage, size, flush }
}
