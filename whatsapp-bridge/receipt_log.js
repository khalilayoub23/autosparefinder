// Content-free delivery evidence for OUR outbound messages (#158, 2026-11-26).
//
// "Sent OK" only means the WhatsApp server accepted the stanza; it does not prove the recipient's device decrypted it.
// A delivery/read receipt does, and a retry receipt proves it could NOT decrypt. These lines carry ONLY the scope
// (group / dm), the kind and the last 6 characters of OUR message id (matched against the id /send returned) - never a
// JID, participant, phone number or text.
const STATUS_KIND = { 3: 'delivery', 4: 'read', 5: 'played' } // proto.WebMessageInfo.Status (2 = server ack: not a receipt)

export function idTail(id) {
  return String(id || '').replace(/[^A-Za-z0-9]/g, '').slice(-6) || '-'
}

function scopeOf(remoteJid) {
  return String(remoteJid || '').endsWith('@g.us') ? 'group' : 'dm'
}

// messages.update (1:1 status changes of our own messages)
export function statusLines(updates) {
  const out = []
  for (const u of Array.isArray(updates) ? updates : []) {
    const kind = STATUS_KIND[u?.update?.status]
    if (!kind || !u?.key?.fromMe) continue
    out.push(`scope=${scopeOf(u.key.remoteJid)} kind=${kind} id=${idTail(u.key.id)}`)
  }
  return out
}

// message-receipt.update (per-participant receipts, how a GROUP delivery is reported)
export function receiptLines(updates) {
  const out = []
  for (const u of Array.isArray(updates) ? updates : []) {
    const r = u?.receipt || {}
    if (!u?.key?.fromMe) continue
    const kind = r.playedTimestamp ? 'played' : r.readTimestamp ? 'read' : r.receiptTimestamp ? 'delivery' : ''
    if (!kind) continue
    out.push(`scope=${scopeOf(u.key.remoteJid)} kind=${kind} id=${idTail(u.key.id)}`)
  }
  return out
}

// raw <receipt type="retry"> stanza: the recipient could not decrypt our message
export function retryLine(node) {
  const a = node?.attrs || {}
  if (a.type !== 'retry') return ''
  return `scope=${scopeOf(a.from)} id=${idTail(a.id)}`
}
