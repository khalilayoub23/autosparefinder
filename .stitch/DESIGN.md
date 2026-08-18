# Design System: AutoSpareFinder
**Project ID:** _(set after first Stitch project is created)_

> This file was synthesized from AutoSpareFinder's brand documents (`brand/COLOR.md`,
> `brand/TYPOGRAPHY.md`, `brand/COMPONENTS.md`). All tokens are logo-derived — nothing
> invented. Keep this file as the ground truth when prompting Stitch. Do NOT let
> Stitch generate a generic AI look — force it to honour this palette.

---

## 1. Visual Theme & Atmosphere

**Premium dark-mode marketplace.** The feel is "intelligent automotive infrastructure" —
not a generic e-commerce shop. Deep near-black backgrounds read as a professional
diagnostics environment; the electric blue halo (from the logo) provides the single
luminous accent that makes prices, AI results, and primary CTAs pop without noise.

The aesthetic is **technical, precise, and globally trusted**: clean geometric forms,
monospace OEM numbers, controlled use of glow (only on AI/interactive elements), and
speed-line motion dividers that evoke the automotive world. Dense information is OK
— this is a data-heavy parts catalog — but the hierarchy must always be clear so a
customer looking at 20 search results knows exactly where to look next.

**Density:** Medium-high. Cards sit close together but breathe with 24px internal
padding. No excessive whitespace — parts need to be scannable.

**Mood words:** Authoritative · Precise · Luminous · Global · Trustworthy · Fast.

---

## 2. Color Palette & Roles

| Name | Hex | Role |
|---|---|---|
| **Void Black** | `#0F1218` | Page root background — absolute darkest surface |
| **Deep Navy** | `#151B27` | Card level 1 — the surface everything sits on |
| **Steel Navy** | `#1E2535` | Card level 2 — elevated panels, modals, dropdowns |
| **Surface Highlight** | `#252D3D` | Card level 3 — active states, selected rows |
| **Modal Scrim** | `#0A0E17` | Overlay behind dialogs and drawers |
| **Electric Blue** | `#0EA5E9` | PRIMARY — halo core. CTAs, active nav, AI labels |
| **Halo Edge Blue** | `#38BDF8` | Hover state, price display, link color, OEM text |
| **Deep Blue** | `#0284C7` | Pressed/active on primary button |
| **Network Blue** | `#1D4ED8` | Speed-line section dividers only |
| **Chrome Silver** | `#94A3B8` | Secondary text, body descriptions, card subtext |
| **Muted Silver** | `#475569` | Placeholder text, muted metadata |
| **Chrome Highlight** | `#E2E8F0` | Section headings, card titles, high-emphasis text |
| **Pure White** | `#FFFFFF` | Primary text, hero headlines, button labels |
| **Success Green** | `#22C55E` | In stock, confirmed, ✅ fitment verified |
| **Warning Amber** | `#F59E0B` | Low stock, pending, ⚠️ fitment unverified |
| **Error Red** | `#EF4444` | Out of stock, failed, blocked |

**The one rule:** Never use Electric Blue (`#0EA5E9`) for body text. It exists only
for interactive elements, AI markers, and the primary button gradient.

**AI glow (use sparingly, AI components only):**
- Subtle: `0 0 24px rgba(14, 165, 233, 0.40)`
- Strong: `0 0 48px rgba(14, 165, 233, 0.60)`
- Focus ring: `0 0 0 2px #0EA5E9`

---

## 3. Typography Rules

**Primary family:** Inter — geometric sans, matches logo letterform geometry.
Numeric figures are tabular by default — essential for price tables and OEM numbers.
Every heading and body block uses Inter.

**Monospace family:** JetBrains Mono — for OEM numbers, VIN codes, SKUs, part
references. At 0.875rem / 500 weight in Halo Edge Blue (`#38BDF8`). Never use a
proportional font for identifiers — they must align in columns.

**Scale (1.25 minor-third ratio):**
- Display (hero marketing): 3.75rem / 800 weight / −0.04em tracking
- Hero (product page): 3rem / 800 weight / −0.04em tracking
- Page title: 1.875rem / 700 weight / −0.02em tracking
- Section heading: 1.5rem / 600 weight / normal tracking, Chrome Highlight color
- Card title: 1rem / 600 weight / normal
- Body: 1rem / 400 weight / 1.5 line-height / Chrome Silver (`#94A3B8`)
- Secondary: 0.875rem / 400 weight — table body, helper text
- Badge / label: 0.75rem / 600 weight / 0.12em tracking / UPPERCASE

**Price display:** 1.25rem (inline card) or 2.25rem (featured), 700–800 weight,
Halo Edge Blue (`#38BDF8`), tabular-nums, always with ₪ prefix.

**RTL support is mandatory.** All components must mirror correctly for Hebrew (he)
and Arabic (ar). Use `dir="rtl"` on the `<html>` element when locale is he/ar.
Search inputs, price alignment, and card icon positions all flip.

---

## 4. Component Styling

**Search bar (primary CTA — most important component):**
- Background: Steel Navy (`#1E2535`)
- Border: `1px solid rgba(148, 163, 184, 0.12)` default → `rgba(14, 165, 233, 0.40)` focused
- Focus glow: `0 0 0 2px #0EA5E9`
- Border-radius: 12px
- Height: 56px desktop / 48px mobile
- Placeholder text in italic, Muted Silver (`#475569`)
- Right side slots: "AI Search" button + "VIN" scanner button

**Primary button:**
- Gradient: `linear-gradient(135deg, #38BDF8 0%, #0EA5E9 60%, #0284C7 100%)`
- Text: Void Black (`#0F1218`), 600 weight — dark text on light gradient, not white
- Border-radius: 6px (NOT pill-shaped — geometric, not friendly-round)
- Height: 40px standard / 48px large / 56px hero CTA
- Hover: `brightness(1.1)` + subtle blue glow

**Secondary button:**
- Background: transparent
- Border: `1px solid rgba(148, 163, 184, 0.20)`
- Text: Pure White
- Hover: border → `rgba(14, 165, 233, 0.40)`, text → `#38BDF8`

**AI button (special, for AI-trigger actions only):**
- Background: `rgba(14, 165, 233, 0.10)`
- Border: `1px solid rgba(14, 165, 233, 0.60)`
- Text: Halo Edge Blue (`#38BDF8`)
- Box-shadow: `0 0 24px rgba(14, 165, 233, 0.40)` — only AI elements glow

**Part result card:**
- Background: Deep Navy (`#151B27`)
- Border: `1px solid rgba(148, 163, 184, 0.12)`
- Border-radius: 12px (generously rounded)
- Internal padding: 24px
- Thumbnail: 80×80px, white square background (neutral for part images)
- Price: top-right, Halo Edge Blue, 700 weight, 1.25rem
- OEM number: JetBrains Mono, 0.875rem, Halo Edge Blue
- Fitment badge: Success Green `#22C55E` with ✅ prefix; Warning Amber `#F59E0B` for unverified
- Hover: border → `rgba(14, 165, 233, 0.20)`, very subtle blue glow

**AI result card (slightly different from standard):**
- Background: `linear-gradient(135deg, rgba(14,165,233,0.08) 0%, rgba(14,165,233,0.02) 100%)`
- Border: `1px solid rgba(14, 165, 233, 0.20)`
- Box-shadow: `0 0 24px rgba(14, 165, 233, 0.40)`

**Forms / inputs:**
- Background: Surface Highlight (`#252D3D`) — slightly lighter than card
- Border: `1px solid rgba(148, 163, 184, 0.12)` → focus `rgba(14, 165, 233, 0.40)`
- Border-radius: 8px
- Height: 40px

**Navigation:**
- Background: Void Black (`#0F1218`) with `backdrop-filter: blur(12px)`
- Nav links: 0.875rem / 500 weight / 0.04em tracking / Chrome Silver
- Active link: Electric Blue (`#0EA5E9`) / 600 weight

---

## 5. Layout Principles

**Grid:** 12-column, 24px gutter, 1280px max-width with 24px side padding on desktop.
Mobile: single column, 16px padding. Tablet: 2-column cards with 16px gap.

**Section rhythm:** Major sections separated by a 1px speed-line divider:
`linear-gradient(90deg, transparent, #1D4ED8 30%, #0EA5E9 50%, #1D4ED8 70%, transparent)`
This is a brand motif — use it between hero/search, search/results, results/footer.

**Elevation hierarchy (z-axis):**
1. Page root → Void Black `#0F1218`
2. Content cards → Deep Navy `#151B27`
3. Hover/elevated → Steel Navy `#1E2535`
4. Modal/drawer → Surface Highlight `#252D3D` content on Modal Scrim `#0A0E17` backdrop

**Spacing tokens (8px base unit):**
- 4px, 8px, 12px, 16px, 24px, 32px, 48px, 64px, 96px

**Shadows (for depth, not decoration):**
- Card: `0 1px 3px rgba(0,0,0,0.4), 0 4px 12px rgba(0,0,0,0.3)`
- Elevated: `0 4px 16px rgba(0,0,0,0.5), 0 8px 32px rgba(0,0,0,0.4)`
- Modal: `0 20px 60px rgba(0,0,0,0.7)`

---

## 6. Design System Notes for Stitch Generation

> Copy this entire section into every Stitch generation prompt.

**Always generate in DARK MODE.** Background: `#0F1218`. Cards: `#151B27`.
Do not use white or light backgrounds for any surface.

**Color primaries for Stitch prompts:**
- Background: deep near-black (#0F1218)
- Card surface: dark navy (#151B27)
- Accent: electric blue (#0EA5E9)
- Hover accent: bright sky blue (#38BDF8)
- Text: white (#FFFFFF) primary, silver (#94A3B8) secondary
- Price/highlight: bright blue (#38BDF8)

**Typography for Stitch:**
- Font: Inter
- Headlines: bold/extrabold, tight letter-spacing
- Body: regular/medium weight, silver text
- Monospace codes (OEM, VIN, SKU): distinct monospace treatment in blue

**Corner roundness:** 12px on cards, 6px on buttons, 8px on inputs — "Subtly rounded,
not pill-shaped." Geometric edges, not bubbly.

**What NOT to generate:**
- ❌ Light/white backgrounds
- ❌ Rounded pill buttons
- ❌ Colorful gradient backgrounds
- ❌ Gradients on card backgrounds (only on primary buttons)
- ❌ Comic/friendly illustration style
- ❌ Generic e-commerce blue (`#1a73e8` Google blue, `#0070f3` Vercel blue)
- ❌ Any green as a primary/accent color

**Marketplace context:** This is an automotive parts comparison platform serving
Israeli and global markets. RTL layouts (Hebrew/Arabic) are first-class. Expect:
part images, OEM numbers, prices in Israeli Shekel (₪), fitment badges, supplier
comparison tables, and a prominent AI search bar.
