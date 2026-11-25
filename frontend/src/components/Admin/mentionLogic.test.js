import { describe, it, expect } from 'vitest'
import { activeMention, filterMembers, insertMention, toWireText } from './mentionLogic'

const M = [{ token: 'aaaaaaaaaaaaaaa1', name: 'דנה' }, { token: 'aaaaaaaaaaaaaaa2', name: 'דניאל כהן' }, { token: 'aaaaaaaaaaaaaaa3', name: 'Ron' }]

describe('mentionLogic', () => {
  it('opens only at a word start', () => {
    expect(activeMention('היי @דנ', 7)).toEqual({ start: 4, query: 'דנ' })
    expect(activeMention('mail@דנ', 7)).toBeNull()
    expect(activeMention('היי @', 5)).toEqual({ start: 4, query: '' })
    expect(activeMention('היי @דנה שלום', 13)).toBeNull()
  })
  it('filters, prefix first, capped, empty query lists all', () => {
    expect(filterMembers(M, 'דנ').map((m) => m.name)).toEqual(['דנה', 'דניאל כהן'])
    expect(filterMembers(M, 'כהן').map((m) => m.name)).toEqual(['דניאל כהן'])
    expect(filterMembers(M, '').length).toBe(3)
    expect(filterMembers(M, 'zzz')).toEqual([])
    expect(filterMembers(Array.from({ length: 50 }, (_, i) => ({ token: String(i), name: `n${i}` })), '').length).toBe(8)
  })
  it('inserts at the caret and keeps the tail', () => {
    const t = 'היי @דנ מה נשמע'
    const r = insertMention(t, activeMention(t, 7), M[0])
    expect(r.text).toBe('היי @דנה  מה נשמע')
    expect(r.text.startsWith('היי @דנה ')).toBe(true)
  })
  it('wire text: multiple mentions, longest name first, deleted mention dropped', () => {
    const w = toWireText('@דניאל כהן ו@דנה ו@Ron', M)
    expect(w.text).toBe('@{aaaaaaaaaaaaaaa2} ו@{aaaaaaaaaaaaaaa1} ו@{aaaaaaaaaaaaaaa3}')
    expect(w.mentions.length).toBe(3)
    expect(toWireText('שלום', M)).toEqual({ text: 'שלום', mentions: [] })
  })
})
