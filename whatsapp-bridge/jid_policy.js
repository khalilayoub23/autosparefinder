// Which WhatsApp JIDs are conversations the bridge may receive from or send to (#158, 2026-11-26).
//
// `status@broadcast` carries contacts' Status updates (and replies to them); `…@broadcast` is a broadcast list and
// `…@newsletter` a channel. None of them is a chat: before this guard a contact's emoji Status ("❤️❤️❤️") reached
// the customer brain as an anonymous WhatsApp customer (70 messages in 30 days) and the reply was sent back to
// `status@broadcast`, i.e. published as the business account's own Status. The rule is applied on BOTH sides:
// inbound messages from these JIDs are dropped, and /send refuses them.
export function isNonChatJid(jid) {
  const j = String(jid || '').trim().toLowerCase()
  if (!j) return false
  return j === 'status@broadcast' || j.endsWith('@broadcast') || j.endsWith('@newsletter')
}
