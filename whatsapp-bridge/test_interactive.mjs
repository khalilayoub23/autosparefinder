// Compatibility proof for installed Baileys (no network, no socket). Run: node test_interactive.mjs
import assert from 'assert'
import { createRequire } from 'module'
import {
  buildInteractiveMessage, buttonId, parseButtonId, extractInteractiveResponse, responseContext,
  extractMentions, validateMentions, wrapForRelay,
} from './interactive.js'
const require = createRequire(import.meta.url)
const b = require('@whiskeysockets/baileys')
const { proto, generateWAMessageContent } = b
const ver = require('@whiskeysockets/baileys/package.json').version
const roundTrip = (m) => proto.Message.decode(proto.Message.encode(m).finish())

// 1. The installed generator does NOT build buttons/lists: it degrades them to plain text (the reason this module exists).
const opts = { logger: { info() {}, warn() {}, error() {}, debug() {}, trace() {}, child() { return this } }, userJid: '1@s.whatsapp.net' }
for (const extra of [{ buttons: [{ buttonId: 'a', buttonText: { displayText: 'A' }, type: 1 }] }, { sections: [{ title: 's', rows: [{ title: 'r', rowId: '1' }] }], title: 't', buttonText: 'b' }]) {
  const g = await generateWAMessageContent({ text: 'hi', ...extra }, opts)
  assert.deepEqual(Object.keys(g), ['extendedTextMessage'], `Baileys ${ver} unexpectedly builds ${Object.keys(extra)}`)
  assert.equal(g.extendedTextMessage.text, 'hi')
}

// 2. Reply buttons: wire round trip keeps body, footer, three quick_reply buttons and their stable ids.
const acts = [{ id: 'approve', label: 'אישור' }, { id: 'reject', label: 'דחייה' }, { id: 'edit', label: 'עריכה' }]
const rb = roundTrip(buildInteractiveMessage(proto, { body: 'לאשר פוסט?', footer: 'ref ab12', interactionId: 'ix_abc123', actions: acts }))
const nf = rb.interactiveMessage.nativeFlowMessage
assert.equal(rb.interactiveMessage.body.text, 'לאשר פוסט?')
assert.deepEqual(nf.buttons.map((x) => x.name), ['quick_reply', 'quick_reply', 'quick_reply'])
const ids = nf.buttons.map((x) => JSON.parse(x.buttonParamsJson).id)
assert.deepEqual(ids, acts.map((a) => buttonId('ix_abc123', a.id)))
assert.deepEqual(parseButtonId(ids[1]), { interactionId: 'ix_abc123', actionId: 'reject' })
assert.equal(parseButtonId('אישור'), null, 'a visible label is never an identifier')

// 3. More than 3 actions -> single_select list, ids preserved.
const six = ['approve', 'reject', 'change', 'edit', 'details', 'recheck'].map((id) => ({ id, label: id }))
const ls = roundTrip(buildInteractiveMessage(proto, { body: 'x', interactionId: 'ix_abc123', actions: six })).interactiveMessage.nativeFlowMessage
assert.equal(ls.buttons.length, 1); assert.equal(ls.buttons[0].name, 'single_select')
assert.equal(JSON.parse(ls.buttons[0].buttonParamsJson).sections[0].rows.length, 6)
assert.throws(() => buildInteractiveMessage(proto, { body: 'x', interactionId: 'ix_abc123', actions: [] }))

// 4. Inbound response shapes, built as real protos and round-tripped.
const sel = buttonId('ix_abc123', 'approve')
const nfResp = roundTrip(proto.Message.fromObject({ interactiveResponseMessage: {
  body: { text: 'אישור' }, nativeFlowResponseMessage: { name: 'quick_reply', paramsJson: JSON.stringify({ id: sel, display_text: 'אישור' }) },
  contextInfo: { stanzaId: 'WAMSGID1' } } }))
assert.deepEqual(extractInteractiveResponse(nfResp), { kind: 'native_flow', selectedId: sel, text: 'אישור' })
assert.equal(responseContext(nfResp).stanzaId, 'WAMSGID1')
assert.equal(extractInteractiveResponse(roundTrip(proto.Message.fromObject({ buttonsResponseMessage: { selectedButtonId: sel, selectedDisplayText: 'x' } }))).kind, 'buttons')
assert.equal(extractInteractiveResponse(roundTrip(proto.Message.fromObject({ listResponseMessage: { title: 't', singleSelectReply: { selectedRowId: sel } } }))).selectedId, sel)
assert.equal(extractInteractiveResponse(roundTrip(proto.Message.fromObject({ templateButtonReplyMessage: { selectedId: sel } }))).kind, 'template')
assert.equal(extractInteractiveResponse(roundTrip(proto.Message.fromObject({ interactiveResponseMessage: { nativeFlowResponseMessage: { paramsJson: '{bad' } } }))), null)
assert.equal(extractInteractiveResponse(roundTrip(proto.Message.fromObject({ conversation: 'אישור' }))), null, 'plain text is never a button response')

// 5. Mentions: Baileys writes mentionedJid into contextInfo AND it survives the wire; inbound parse recovers it.
const J1 = '972500000001@s.whatsapp.net', J2 = '98058566160397@lid'
const gm = await generateWAMessageContent({ text: 'hi @a @b', mentions: [J1, J2] }, opts)
const wire = roundTrip(proto.Message.fromObject(gm))
assert.deepEqual(wire.extendedTextMessage.contextInfo.mentionedJid, [J1, J2])
assert.deepEqual(extractMentions(wire), [J1, J2])
assert.deepEqual(extractMentions(roundTrip(proto.Message.fromObject({ conversation: 'x' }))), [])
assert.deepEqual(validateMentions([J1, 'evil@s.whatsapp.net', J1, J2], [J1, J2]), [J1, J2], 'only real participants, deduped')
// 6. The exact object index.js relays: wrapped, built by generateWAMessageFromContent, survives encode/decode and the
// inbound normalizer unwraps it to the same interactiveMessage.
const full = b.generateWAMessageFromContent('120363000000000000@g.us', wrapForRelay(buildInteractiveMessage(proto, { body: 'b', interactionId: 'ix_abc123', actions: acts })), { userJid: '972532426920:12@s.whatsapp.net' })
const wr = roundTrip(full.message)
const un = b.normalizeMessageContent(wr)
assert.equal(un.interactiveMessage.nativeFlowMessage.buttons.length, 3)
assert.ok(full.key.id)
console.log(`ok test_interactive (Baileys ${ver})`)
