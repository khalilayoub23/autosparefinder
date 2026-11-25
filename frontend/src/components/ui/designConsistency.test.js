// Source-level guards for the design-system rules (audit 2026-11-24): the button radius scale and the dark result cards.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC = join(__dirname, '..', '..')
function walk(dir, out = []) {
  for (const f of readdirSync(dir)) {
    const p = join(dir, f)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (p.endsWith('.jsx')) out.push(p)
  }
  return out
}
const files = walk(SRC).map((p) => ({ p, s: readFileSync(p, 'utf8') }))

describe('button radius scale', () => {
  it('no <button> uses a non-token radius (icon-only circles excepted)', () => {
    const bad = []
    for (const { p, s } of files) {
      for (const m of s.matchAll(/<button\b[\s\S]*?>/g)) {
        const cls = (m[0].match(/className=(?:"([^"]*)"|\{`([^`]*)`\})/) || [])[1] || (m[0].match(/className=\{`([^`]*)`\}/) || [])[1] || ''
        const iconCircle = /\bh-\d+\b/.test(cls) && /\bw-\d+\b/.test(cls) && cls.includes('rounded-full')
        if (!iconCircle && /\brounded-(lg|xl|2xl|3xl|full|md)\b/.test(cls)) bad.push(p.replace(SRC, '') + ': ' + cls.slice(0, 80))
      }
    }
    expect(bad).toEqual([])
  })
  it('btn-* classes never get a radius override', () => {
    const bad = []
    for (const { p, s } of files) {
      for (const m of s.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)) {
        const cls = m[1] || m[2] || ''
        if (/\bbtn-(primary|secondary|ghost|danger)\b/.test(cls) && /\brounded-(lg|xl|2xl|full|md)\b/.test(cls)) bad.push(p.replace(SRC, ''))
      }
    }
    expect(bad).toEqual([])
  })
})

describe('result cards use the dark surface', () => {
  it('Parts.jsx result cards do not use the light-theme card utilities', () => {
    const s = readFileSync(join(SRC, 'pages', 'Parts.jsx'), 'utf8')
    expect(s).toContain('part-card')
    expect(s).not.toMatch(/rounded-2xl border border-gray-100 overflow-hidden shadow-sm/)
  })
  it('category slugs are localised, never printed raw', () => {
    const s = readFileSync(join(SRC, 'pages', 'Parts.jsx'), 'utf8')
    expect(s).not.toMatch(/<\/span>\{part\.category\}/)
    expect(s).toContain("categoryLabel(part.category")
    expect(s).toContain("categoryLabel(s.category")
  })
})

describe('dark-theme colour guards (mission 3, 2026-11-25)', () => {
  const scoped = files.filter(({ p }) => !p.endsWith('.test.js'))
  it('no light-theme utilities or dark-on-dark text colours remain in pages/components', () => {
    const bad = []
    const re = /\b(bg-white(?!\/)|bg-gray-(?:50|100|200)|bg-slate-(?:50|100)|text-gray-[5-9]00|text-slate-[6-9]00|border-gray-[123]00|text-brand-navy|text-\[#1B2228\]|bg-(?:yellow|green|red|blue|amber|orange|purple|indigo|cyan|emerald|sky|pink|teal)-(?:50|100))\b/
    for (const { p, s } of scoped) {
      const m = s.match(re)
      if (m) bad.push(p.replace(SRC, '') + ': ' + m[0])
    }
    expect(bad).toEqual([])
  })
  it('white text is never placed on the sky-blue brand backgrounds (contrast < 3:1)', () => {
    const bad = []
    for (const { p, s } of scoped) {
      for (const m of s.matchAll(/class(?:Name)?=(?:"([^"]*)"|\{`([^`]*)`\})/g)) {
        const cls = m[1] || m[2] || ''
        if (/\bbg-brand-(?:300|400|500|600|700)\b(?!\/)/.test(cls) && /\btext-white\b/.test(cls)) bad.push(p.replace(SRC, '') + ': ' + cls.slice(0, 70))
      }
    }
    expect(bad).toEqual([])
  })
})
