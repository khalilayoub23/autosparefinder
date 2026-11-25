/**
 * Script: whatsapp-bridge/send_journal.js
 * Purpose: Idempotent sends (mission 4, 2026-11-25). The backend gives a send a stable `client_msg_id`; the journal
 *          remembers, durably, what was already accepted under that id, so a retry after a backend crash/timeout
 *          returns the original result instead of sending the message a second time.
 * Process:
 *   sendOnce(id, doSend):
 *     1. entry 'sent'    -> return its stored key, deduplicated=true, doSend is NOT called.
 *     2. same id running -> await that same promise (concurrent retries share one send).
 *     3. otherwise persist {status:'pending'} BEFORE the provider call, call doSend(id) (the id is passed to the
 *        provider as the WhatsApp message id), persist {status:'sent', key} after it.
 *     4. a throw leaves the entry 'pending' (outcome unknown): the retry sends again with the SAME message id,
 *        which WhatsApp treats as the same message (provider-side de-duplication - NOT verifiable offline).
 * Guarantee / residual: a retry after the journal recorded 'sent' never sends again. The one window the journal cannot
 *          close is the bridge process dying after WhatsApp accepted the message and before 'sent' reached disk; the
 *          retry then re-sends with the same message id (so the provider can drop it) - documented, not hidden.
 * Data Imported/Modified: `${file}` (JSON, atomic replace, entries expire after ttlMs, capped at `max`).
 * Last Updated: 2026-11-25
 */
import fs from 'fs'
import { atomicWriteFile } from './atomic_write.js'

export function createSendJournal({ file, now = Date.now, ttlMs = 7 * 86400e3, max = 5000, writeFile = atomicWriteFile, log = () => {} }) {
  let entries = {}
  try {
    entries = JSON.parse(fs.readFileSync(file, 'utf8')) || {}
  } catch {
    entries = {}
  }
  const running = new Map()

  function prune() {
    const cutoff = now() - ttlMs
    for (const [k, v] of Object.entries(entries)) if ((v.ts || 0) < cutoff) delete entries[k]
    const keys = Object.keys(entries)
    if (keys.length > max) keys.sort((a, b) => entries[a].ts - entries[b].ts).slice(0, keys.length - max).forEach((k) => delete entries[k])
  }
  async function persist() {
    prune()
    await writeFile(file, JSON.stringify(entries))
  }

  async function sendOnce(id, doSend) {
    if (!id) return { result: await doSend(undefined), deduplicated: false }
    const done = entries[id]
    if (done && done.status === 'sent') {
      log('SEND_DEDUPLICATED', `id=${id}`)
      return { result: { key: done.key }, deduplicated: true }
    }
    if (running.has(id)) return running.get(id)
    const p = (async () => {
      const resend = !!entries[id] // a 'pending' entry = an earlier attempt of unknown outcome
      entries[id] = { status: 'pending', ts: now() }
      await persist()
      if (resend) log('SEND_RESEND_UNKNOWN_OUTCOME', `id=${id}`)
      const result = await doSend(id)
      entries[id] = { status: 'sent', ts: now(), key: result?.key || null }
      try {
        await persist()
      } catch (err) {
        log('SEND_JOURNAL_PERSIST_FAILED', `id=${id} ${err.message}`) // accepted but not durable: residual window
      }
      return { result, deduplicated: false }
    })()
    running.set(id, p)
    try {
      return await p
    } finally {
      running.delete(id)
    }
  }

  return { sendOnce, status: (id) => entries[id] || null, _entries: () => entries }
}
