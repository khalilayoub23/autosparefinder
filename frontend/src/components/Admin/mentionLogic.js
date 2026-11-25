// Pure helpers for the group @mention composer (no DOM, unit-tested in mentionLogic.test.js).

/** The active "@query" immediately before the caret, or null. An @ only opens the menu at a word start. */
export function activeMention(text, caret) {
  const before = String(text || '').slice(0, caret)
  const m = /(^|[\s(])@([^\s@]{0,30})$/u.exec(before)
  if (!m) return null
  return { start: caret - m[2].length - 1, query: m[2] }
}

/** Members whose name contains the query (case/accents-insensitive); prefix matches first; capped. */
export function filterMembers(members, query, limit = 8) {
  const q = String(query || '').trim().toLocaleLowerCase()
  const rows = (members || []).map((m) => ({ m, i: m.name.toLocaleLowerCase().indexOf(q) })).filter((r) => r.i >= 0)
  rows.sort((a, b) => (a.i === 0 ? 0 : 1) - (b.i === 0 ? 0 : 1) || a.m.name.localeCompare(b.m.name))
  return rows.slice(0, limit).map((r) => r.m)
}

/** Replace the "@query" with "@Name " and report the new caret + the member. */
export function insertMention(text, mention, member) {
  const caret = mention.start + 1 + mention.query.length
  const insert = `@${member.name} `
  return { text: text.slice(0, mention.start) + insert + text.slice(caret), caret: mention.start + insert.length }
}

/** The text the server expects: every still-present "@Name" of a chosen member becomes "@{token}". Longest name first. */
export function toWireText(text, chosen) {
  let out = String(text || '')
  const seen = new Set()
  for (const m of [...(chosen || [])].sort((a, b) => b.name.length - a.name.length)) {
    if (seen.has(m.token)) continue
    const re = new RegExp(`@${m.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![\\p{L}\\p{N}_])`, 'gu')
    if (re.test(out)) { out = out.replace(re, `@{${m.token}}`); seen.add(m.token) }
  }
  return { text: out, mentions: [...seen] }
}
