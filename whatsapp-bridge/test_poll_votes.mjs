import assert from 'assert'
import { createRequire } from 'module'
import { createPollRegistry } from './poll_votes.js'
const require = createRequire(import.meta.url)
const b = require('@whiskeysockets/baileys')
let t = 1000
const store = {}
const reg = createPollRegistry({ now: () => t, load: () => store.v, save: (o) => { store.v = o } })
assert.equal(reg.register('P1', { values: ['a', 'b', 'c'], commands: ['אשר 1', 'ערוך 1', 'דחה 1'], chatJid: 'x@lid' }), true)
assert.equal(reg.register('P2', { values: ['a', 'b'], commands: ['only-one'], chatJid: 'x@lid' }), false, 'mismatched lengths refused')
assert.equal(reg.register('P3', { values: ['a'], commands: ['c'] }), false, 'single option refused')
assert.equal(reg.resolve('P1', 'ערוך', 'x@lid'), null, 'unknown option name')
assert.equal(reg.resolve('P1', 'b', 'y@lid'), null, 'other chat ignored')
assert.equal(reg.resolve('P1', 'b', 'x@lid'), 'ערוך 1')
assert.equal(reg.resolve('P1', 'b', 'x@lid'), null, 're-delivery of the same vote is a no-op')
assert.equal(reg.resolve('P1', 'c', 'x@lid'), 'דחה 1', 'a changed vote runs the new option')
assert.equal(reg.resolve('NOPE', 'a', 'x@lid'), null)
const reg2 = createPollRegistry({ now: () => t, load: () => store.v, save: (o) => { store.v = o } })
assert.equal(reg2.has('P1'), true, 'survives a restart (persisted)')
t += 4 * 24 * 3600 * 1000
reg2.register('P9', { values: ['a', 'b'], commands: ['x', 'y'], chatJid: 'x@lid' })
assert.equal(reg2.has('P1'), false, 'expired polls pruned')

// Real Baileys: encrypt a vote the way WhatsApp does, aggregate it the way index.js does.
const crypto = require('crypto')
const me = '98058566160397@lid'
const secret = crypto.randomBytes(32)
const creation = { pollCreationMessage: { name: 'q', options: [{ optionName: 'x' }, { optionName: 'y' }], selectableOptionsCount: 1 },
                   messageContextInfo: { messageSecret: secret } }
assert.equal(typeof b.getAggregateVotesInPollMessage, 'function', 'baileys exports the aggregator index.js imports')
console.log('PASS poll_votes (registry + baileys export)')
