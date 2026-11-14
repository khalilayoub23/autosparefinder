/**
 * Owner polls (2026-11-13): remembers, per poll message id, which command each option runs, and turns a
 * decrypted vote into that command. The backend decides the commands (they are the SAME typed owner
 * commands); the bridge only maps option -> command and posts it to the normal inbound webhook, so a
 * tap behaves exactly like the owner typing it. Pure module: no socket, no network.
 */
export const MAX_POLLS = 200
export const POLL_TTL_MS = 3 * 24 * 3600 * 1000

export function createPollRegistry({ now = () => Date.now(), load = null, save = null } = {}) {
  let polls = new Map(Object.entries(load ? (load() || {}) : {}))
  const persist = () => { try { save && save(Object.fromEntries(polls)) } catch { /* best effort */ } }
  const prune = () => {
    const t = now()
    for (const [id, p] of polls) if (t - p.ts > POLL_TTL_MS) polls.delete(id)
    while (polls.size > MAX_POLLS) polls.delete(polls.keys().next().value)
  }
  return {
    register(id, { values, commands, chatJid }) {
      if (!id || !Array.isArray(values) || values.length < 2 || values.length !== commands.length) return false
      polls.set(id, { values, commands, chatJid, ts: now(), done: null })
      prune(); persist()
      return true
    },
    has: (id) => polls.has(id),
    /** The command for the option a voter selected, or null (unknown poll / no selection / same vote
     *  already handled / a vote from another chat). Marks the vote handled so a re-delivery is a no-op. */
    resolve(id, selectedName, fromJid) {
      const p = polls.get(id)
      if (!p || !selectedName) return null
      if (p.chatJid && fromJid && p.chatJid !== fromJid) return null
      const idx = p.values.indexOf(selectedName)
      if (idx < 0) return null
      if (p.done === idx) return null
      p.done = idx; persist()
      return p.commands[idx]
    },
    size: () => polls.size,
  }
}
