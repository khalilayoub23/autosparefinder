// #158 follow-up: a hand-typed bot/agent mention at the start of a group message is a routing signal.
import assert from 'node:assert/strict'
import { isTypedBotMention } from './interactive.js'

for (const t of ['@AutoSpareFinder מה המצב של המערכת?', '@autosparefinder status', '@ Auto Spare Finder מה המצב',
  '@בוט מה המצב?', '@הבוט תגיד', '@אבי מה קורה', '@נועה איזה פוסט?', '  @NOA hi', '@Bone עזרה']) {
  assert.equal(isTypedBotMention(t), true, t)
}
for (const t of ['מה המצב @AutoSpareFinder', 'שלום לכולם', '@חליל מה נשמע', '@בוטים זה לא', '@אביב', 'mail me @ home',
  '', null, undefined, '@AutoSpareFinderX']) {
  assert.equal(isTypedBotMention(t), false, String(t))
}
console.log('test_typed_mention: ok')
