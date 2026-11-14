import assert from 'assert'
import crypto from 'crypto'
import { createRequire } from 'module'
import { candidatePairs, tryDecryptVote } from './poll_decrypt.js'
const require = createRequire(import.meta.url)
const b = require('@whiskeysockets/baileys')
const { decryptPollVote, aesEncryptGCM, hmacSign } = b
assert.equal(typeof decryptPollVote, 'function', 'baileys exports decryptPollVote')

// Encrypt a vote exactly as a WhatsApp client does (mirror of decryptPollVote).
function encrypt(optionHashes, { pollEncKey, pollMsgId, creator, voter }) {
  const sign = Buffer.concat([Buffer.from(pollMsgId), Buffer.from(creator), Buffer.from(voter), Buffer.from('Poll Vote'), new Uint8Array([1])])
  const key0 = hmacSign(pollEncKey, new Uint8Array(32), 'sha256')
  const decKey = hmacSign(sign, key0, 'sha256')
  const iv = crypto.randomBytes(12)
  const aad = Buffer.from(`${pollMsgId}\u0000${voter}`)
  const msg = b.proto.Message.PollVoteMessage.encode({ selectedOptions: optionHashes }).finish()
  return { encPayload: aesEncryptGCM(msg, decKey, iv, aad), encIv: iv }
}

const secret = crypto.randomBytes(32), id = '3EB0TEST'
const me = '972532426920:11@s.whatsapp.net', meLid = '192350731149514:11@lid', owner = '98058566160397@lid'
const hash = crypto.createHash('sha256').update('📖 פרטים').digest()
const pairs = candidatePairs({ meId: me, meLid, voterKeyAuthor: owner, chatJid: owner })

// Baileys' own pair (phone JID creator) fails; the LID creator is what the client used.
const enc = encrypt([hash], { pollEncKey: secret, pollMsgId: id, creator: '192350731149514@lid', voter: owner })
assert.throws(() => decryptPollVote(enc, { pollEncKey: secret, pollMsgId: id, pollCreatorJid: '972532426920@s.whatsapp.net', voterJid: owner }))
const got = tryDecryptVote(decryptPollVote, enc, { pollEncKey: secret, pollMsgId: id, pairs })
assert.ok(got, 'LID creator pair found'); assert.equal(got.pollCreatorJid, '192350731149514@lid')
assert.equal(Buffer.from(got.vote.selectedOptions[0]).toString('hex'), hash.toString('hex'))

// Phone-JID creator (no LID involved) still works.
const enc2 = encrypt([hash], { pollEncKey: secret, pollMsgId: id, creator: '972532426920@s.whatsapp.net', voter: owner })
assert.equal(tryDecryptVote(decryptPollVote, enc2, { pollEncKey: secret, pollMsgId: id, pairs }).pollCreatorJid, '972532426920@s.whatsapp.net')
// A vote with a different secret / poll id never decrypts (no false positive).
assert.equal(tryDecryptVote(decryptPollVote, enc, { pollEncKey: crypto.randomBytes(32), pollMsgId: id, pairs }), null)
assert.equal(tryDecryptVote(decryptPollVote, enc, { pollEncKey: secret, pollMsgId: 'OTHER', pairs }), null)
console.log('PASS poll_decrypt')
