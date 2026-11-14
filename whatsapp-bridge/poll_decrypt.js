/**
 * Poll-vote decryption that survives WhatsApp's PN/LID address mix (2026-11-13).
 * Baileys decrypts a vote with the creator + voter JIDs exactly as the VOTER's client wrote them. Our business
 * account is addressed as a phone JID in Baileys (creds.me.id) but chats run on LIDs, so Baileys' own attempt
 * failed ("failed to decrypt poll vote") for every real vote. AES-GCM authenticates the JIDs (they are in the key
 * derivation and the AAD), so a wrong pair fails cleanly and only the right pair decrypts - trying a small,
 * fixed candidate set is therefore safe. Pure: the decryptor is injected.
 */
const bare = (jid) => String(jid || '').replace(/:\d+(?=@)/, '')

export function uniq(list) {
  return [...new Set(list.filter(Boolean).map(String))]
}

/** creator = us (phone JID + our LID); voter = whoever voted (their LID/PN as delivered + the chat JID). */
export function candidatePairs({ meId, meLid, voterKeyAuthor, chatJid, voterAlt = [] }) {
  const creators = uniq([bare(meId), bare(meLid), meId, meLid])
  const voters = uniq([voterKeyAuthor, bare(voterKeyAuthor), chatJid, bare(chatJid), ...voterAlt])
  const pairs = []
  for (const c of creators) for (const v of voters) pairs.push([c, v])
  return pairs
}

export function tryDecryptVote(decryptPollVote, enc, { pollEncKey, pollMsgId, pairs }) {
  for (const [pollCreatorJid, voterJid] of pairs) {
    try {
      const vote = decryptPollVote(enc, { pollEncKey, pollCreatorJid, pollMsgId, voterJid })
      return { vote, pollCreatorJid, voterJid }
    } catch { /* wrong pair - GCM auth failure */ }
  }
  return null
}
