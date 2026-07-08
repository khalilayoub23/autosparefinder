# AutoSpare Typography System

> Typography choices justified by logo geometry, not aesthetic preference.

---

## Font Selections

### Primary — Inter
**Why Inter:** The logo's "AutoSpare" text uses a geometric sans with balanced
letter proportions, consistent stroke contrast, and clean diagonals. Inter is the
closest system-ready match. It also ships with numeric tabular figures by default —
critical for price tables, OEM numbers, and analytics dashboards.

```
font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
```

Install via: `@fontsource/inter` or Google Fonts `Inter:wght@300;400;500;600;700;800`

### Monospace — JetBrains Mono
**Why JetBrains Mono:** OEM numbers, VIN codes, SKUs, API keys, and part references
require monospace for scanning/alignment. JetBrains Mono has clean zero disambiguation
(`0` vs `O`) and matches Inter's x-height — they coexist without visual tension.

```
font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace;
```

Used for: OEM numbers, VIN, SKU, price in tables, API responses, code, reference numbers

---

## Type Scale

Derived from a **1.25 minor third ratio** — the gear ring's proportional steps
between inner rings loosely maps to a 1.25× scale progression.

```
--text-xs:    0.75rem   / 12px   — badge labels, fine print, table metadata
--text-sm:    0.875rem  / 14px   — secondary content, table body, helper text
--text-base:  1rem      / 16px   — body text, form labels, card content
--text-lg:    1.125rem  / 18px   — section intro, card headlines
--text-xl:    1.25rem   / 20px   — subsection headings, modal titles
--text-2xl:   1.5rem    / 24px   — page section headings
--text-3xl:   1.875rem  / 30px   — page titles, feature headings
--text-4xl:   2.25rem   / 36px   — hero subtitle
--text-5xl:   3rem      / 48px   — hero headline
--text-6xl:   3.75rem   / 60px   — brand statement (home hero only)
--text-7xl:   4.5rem    / 72px   — oversized display (marketing)
```

---

## Weight Usage

| Weight | Token | Usage |
|--------|-------|-------|
| 300 | `font-light` | Subheadings on dark, large at 3xl+ only |
| 400 | `font-normal` | Body text, descriptions, table content |
| 500 | `font-medium` | Labels, nav items, secondary headings |
| 600 | `font-semibold` | Card titles, button text, form labels |
| 700 | `font-bold` | Page headings, primary actions, prices |
| 800 | `font-extrabold` | Hero headlines, brand name display |

**Rule:** Never use weight 300 for text below 24px — at small sizes on dark
backgrounds it disappears. Only 400+ for body copy.

---

## Line Heights

```
--leading-none:    1       — Display type, logo lockup
--leading-tight:   1.2     — Headlines h1-h2
--leading-snug:    1.35    — h3-h4, card titles
--leading-normal:  1.5     — Body text, descriptions
--leading-relaxed: 1.65    — Long-form content, documentation
--leading-loose:   2       — Table rows, form fields (scannable data)
```

---

## Letter Spacing

```
--tracking-tightest: -0.04em   — Display headlines (6xl+)
--tracking-tight:    -0.02em   — h1-h2
--tracking-normal:   0         — Body text
--tracking-wide:     0.04em    — Small labels, nav items
--tracking-wider:    0.08em    — Uppercase labels
--tracking-widest:   0.12em    — Badge text (from logo "SPARE FINDER" badge)
```

The logo's "SPARE FINDER" all-caps badge uses ~0.12em spacing. This becomes
the standard for all uppercase label components in the system.

---

## Typography Hierarchy

### Page Hero (landing, category root)
```
font-size: 4.5rem           /* --text-7xl for marketing, --text-5xl for product */
font-weight: 800
line-height: 1.1
letter-spacing: -0.04em
color: #FFFFFF
```

### Page Title (within dashboard, portal)
```
font-size: 1.875rem         /* --text-3xl */
font-weight: 700
line-height: 1.2
letter-spacing: -0.02em
color: #FFFFFF
```

### Section Heading
```
font-size: 1.5rem           /* --text-2xl */
font-weight: 600
line-height: 1.35
color: #E2E8F0              /* --color-metal-200 */
```

### Card Title
```
font-size: 1rem             /* --text-base */
font-weight: 600
line-height: 1.35
color: #FFFFFF
```

### Body Text
```
font-size: 1rem             /* --text-base */
font-weight: 400
line-height: 1.5
color: #94A3B8              /* --color-metal-400 */
```

### Table / Data Dense
```
font-size: 0.875rem         /* --text-sm */
font-weight: 400
line-height: 2              /* --leading-loose */
font-family: Inter
color: #E2E8F0
```

### OEM / VIN / SKU (Monospace)
```
font-size: 0.875rem
font-weight: 500
font-family: JetBrains Mono
letter-spacing: 0.04em
color: #38BDF8              /* --color-primary-400 — links to AI/tech */
```

### Badge / Tag Label
```
font-size: 0.75rem          /* --text-xs */
font-weight: 600
letter-spacing: 0.12em      /* From logo badge proportions */
text-transform: uppercase
```

### Price Display
```
font-size: 1.25rem          /* --text-xl */
font-weight: 700
font-family: Inter (tabular nums)
font-variant-numeric: tabular-nums
color: #FFFFFF
```

### Price (large / hero price)
```
font-size: 2.25rem          /* --text-4xl */
font-weight: 800
font-variant-numeric: tabular-nums
color: #38BDF8              /* halo blue — price is the value the platform creates */
```

---

## Specific Component Typography

### Navigation
- Top nav links: `--text-sm`, `font-medium`, `letter-spacing: 0.04em`
- Active nav: `font-semibold`, `color: #0EA5E9`
- Breadcrumbs: `--text-xs`, `font-normal`, `color: #64748B`

### Search Input
- Placeholder: `--text-base`, `color: #475569`, italic
- Typed text: `--text-base`, `color: #FFFFFF`, `font-medium`
- Results label: `--text-xs`, uppercase, `letter-spacing: 0.12em`

### Supplier / Vendor Names
- `font-semibold`, `--text-sm`, `color: #E2E8F0`
- Never show raw supplier name to customers (masking rule from CLAUDE.md)
- Display as: `"Source A"`, `"Source B"` in customer-facing views

### AI Output Text
- `font-normal`, `--text-base`, `color: #E2E8F0`
- First word or key term: `color: #38BDF8` (blue accent)
- Response container: always uses `font-family: Inter` (not mono)

### Error Messages
- `--text-sm`, `font-medium`, `color: #EF4444`
- Icon + message always paired — never message alone

### Empty States
- Heading: `--text-xl`, `font-semibold`, `color: #E2E8F0`
- Subtext: `--text-base`, `color: #64748B`

---

## Tailwind Config Extension

```js
// tailwind.config.js
module.exports = {
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'Courier New', 'monospace'],
      },
      fontSize: {
        'display': ['4.5rem', { lineHeight: '1.1', letterSpacing: '-0.04em' }],
      },
      letterSpacing: {
        'badge': '0.12em',
        'label': '0.08em',
      },
    },
  },
}
```
