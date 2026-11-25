/**
 * Interactive owner messages (2026-11-25).
 *
 * Baileys 6.17.16 has NO builder for buttons/lists: generateWAMessageContent() silently turns
 * `{ text, buttons }` / `{ text, sections }` into a plain extendedTextMessage (proved by
 * test_interactive.mjs). So an interactive message is a hand-built proto sent via relayMessage.
 * Pure module: no socket, no network. The caller passes `proto` (Baileys' protobuf namespace).
 *
 * Rendering on a real device is UNVERIFIED (WhatsApp may ignore nativeFlow on non-Business linked
 * devices) - the backend therefore always sends the plain text with its typed commands FIRST and
 * treats the interactive message as an additive convenience.
 */
export const MAX_REPLY_BUTTONS = 3
export const ACTION_PREFIX = 'asf'
const MAX_LABEL = 20 // WhatsApp truncates quick-reply labels around 20 chars

/** Stable button id: asf:<interactionId>:<actionId>. The visible label is never an identifier. */
export function buttonId(interactionId, actionId) { return `${ACTION_PREFIX}:${interactionId}:${actionId}` }

export function parseButtonId(id) {
  const m = /^asf:([A-Za-z0-9_-]{4,64}):([a-z_]{2,32})$/.exec(String(id || ''))
  return m ? { interactionId: m[1], actionId: m[2] } : null
}

/** Reply buttons (max 3) when they fit, else a single_select list (nativeFlow). */
export function buildInteractiveMessage(proto, { body, footer = '', interactionId, actions }) {
  const acts = (Array.isArray(actions) ? actions : []).filter((a) => a && a.id && a.label)
  if (!interactionId || !body || acts.length < 1) throw new Error('interactive: body, interactionId and actions required')
  let buttons
  if (acts.length <= MAX_REPLY_BUTTONS) {
    buttons = acts.map((a) => ({
      name: 'quick_reply',
      buttonParamsJson: JSON.stringify({ display_text: String(a.label).slice(0, MAX_LABEL), id: buttonId(interactionId, a.id) }),
    }))
  } else {
    buttons = [{
      name: 'single_select',
      buttonParamsJson: JSON.stringify({
        title: 'בחר פעולה',
        sections: [{ title: 'פעולות', rows: acts.slice(0, 10).map((a) => ({ title: String(a.label).slice(0, 24), id: buttonId(interactionId, a.id) })) }],
      }),
    }]
  }
  return proto.Message.fromObject({
    interactiveMessage: {
      body: { text: String(body).slice(0, 1024) },
      footer: footer ? { text: String(footer).slice(0, 60) } : undefined,
      nativeFlowMessage: { buttons, messageParamsJson: '{}' },
    },
  })
}

/** Every response shape a client may produce -> { kind, selectedId, text } or null. */
export function extractInteractiveResponse(content) {
  if (!content) return null
  const nf = content.interactiveResponseMessage?.nativeFlowResponseMessage
  if (nf?.paramsJson) {
    try { const p = JSON.parse(nf.paramsJson); if (p?.id) return { kind: 'native_flow', selectedId: String(p.id), text: p.display_text || '' } } catch { /* malformed -> null */ }
    return null
  }
  if (content.buttonsResponseMessage?.selectedButtonId)
    return { kind: 'buttons', selectedId: content.buttonsResponseMessage.selectedButtonId, text: content.buttonsResponseMessage.selectedDisplayText || '' }
  if (content.listResponseMessage?.singleSelectReply?.selectedRowId)
    return { kind: 'list', selectedId: content.listResponseMessage.singleSelectReply.selectedRowId, text: content.listResponseMessage.title || '' }
  if (content.templateButtonReplyMessage?.selectedId)
    return { kind: 'template', selectedId: content.templateButtonReplyMessage.selectedId, text: content.templateButtonReplyMessage.selectedDisplayText || '' }
  return null
}

/** The original interactive message's id: the response's contextInfo.stanzaId. */
export function responseContext(content) {
  const ci = content?.interactiveResponseMessage?.contextInfo || content?.buttonsResponseMessage?.contextInfo
    || content?.listResponseMessage?.contextInfo || content?.templateButtonReplyMessage?.contextInfo || null
  return { stanzaId: ci?.stanzaId || '', participant: ci?.participant || '' }
}

/** Real mention metadata of an inbound message: the JIDs in contextInfo.mentionedJid. */
export function extractMentions(content) {
  const ci = content?.extendedTextMessage?.contextInfo || content?.imageMessage?.contextInfo
    || content?.videoMessage?.contextInfo || content?.documentMessage?.contextInfo || null
  const list = Array.isArray(ci?.mentionedJid) ? ci.mentionedJid : []
  return [...new Set(list.filter((j) => typeof j === 'string' && /@(s\.whatsapp\.net|lid)$/.test(j)))]
}

/** Outgoing mentions: only JIDs that are real participants of the target group, order-preserving. */
export function validateMentions(requested, participantJids) {
  const allowed = new Set(participantJids || [])
  const out = []
  for (const j of Array.isArray(requested) ? requested : []) if (allowed.has(j) && !out.includes(j)) out.push(j)
  return out
}

/** The viewOnce wrapper community nativeFlow senders use (device rendering UNVERIFIED). */
export function wrapForRelay(msg) {
  const inner = typeof msg.toJSON === 'function' ? msg.toJSON() : msg
  return { viewOnceMessage: { message: { messageContextInfo: { deviceListMetadata: {}, deviceListMetadataVersion: 2 }, ...inner } } }
}
