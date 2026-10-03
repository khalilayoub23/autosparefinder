# AutoSpareFinder — Product Design System MASTER
**Version:** 1.0.0 | **Created:** 2026-09-29 | **Status:** Authoritative

> Ground-truth for all UI/UX work on AutoSpareFinder.  
> Every design decision, Stitch generation, and frontend change MUST reference this file.  
> When this file and code conflict, this file defines intent — code must be corrected.

---

## 1. PRODUCT IDENTITY

**Product:** AutoSpareFinder  
**Domain:** Global automotive spare-parts comparison marketplace  
**Primary Markets:** Israel (Hebrew RTL), Arabic-speaking markets, English-speaking global  
**Business Model:** Multi-supplier price comparison + direct purchase (eBay/AliExpress model for car parts, enhanced with AI fitment verification)

**Core user promise:** *Find the right part for your specific vehicle — verified to fit — at the best available price, from trusted global suppliers.*

**Differentiators:**
1. AI-assisted fitment verification (vehicle → part match, not just keyword search)
2. Multi-supplier price comparison on a single part
3. OEM / OE-equivalent / aftermarket tiers clearly labeled
4. RTL-first (Hebrew, Arabic) — not an English product with a translation layer

---

## 2. DESIGN PRINCIPLES

1. **Precision over personality** — every element earns its presence by serving a task
2. **Hierarchy before decoration** — price, fitment, availability: always the most prominent information on a part card
3. **Trust through clarity** — users are spending real money on safety-critical parts; ambiguity destroys confidence
4. **Dark-first, always** — the premium technical aesthetic; light sections are the exception, not the rule
5. **RTL as first-class** — Hebrew and Arabic layouts are designed first, not adapted
6. **Density with breathing room** — parts catalogs are information-dense; 24px card padding, 8px-grid spacing maintains scannability
7. **One accent, used precisely** — Electric Blue `#0EA5E9` is reserved for interactive elements and AI features; never for decoration
8. **AI glow is sacred** — the `box-shadow: 0 0 24px rgba(14,165,233,0.40)` glow appears ONLY on AI-powered elements

---

## 3. VISUAL DIRECTION

**Product character:** Premium dark-mode automotive marketplace  
**Aesthetic keywords:** Authoritative · Precise · Luminous · Technical · Global · Trustworthy  
**Reference products (visual tone only):** Bloomberg Terminal meets Stripe Dashboard meets CarParts.com  
**What it is NOT:** Generic SaaS blue, friendly pastel e-commerce, generic Amazon-clone light marketplace

**The design resolves the CURRENT SPLIT:** The landing page (`/`) currently uses a completely different light design system (`#f4f7fd` backgrounds, `#2563eb` Google blue, white sections). This is the single largest UX problem in the product. The correct direction is a **unified dark-premium system** from landing page to checkout, with the same tokens used everywhere.

---

## 4. COLOR SYSTEM

### Surface Palette (dark elevation hierarchy)

| Token | Hex | Tailwind | Role |
|---|---|---|---|
| `--bg` / `brand-surface` | `#0F1218` | `bg-[#0F1218]` | Page root — absolute darkest layer |
| `--card` / `brand-200` | `#151B27` | `bg-[#151B27]` | Card level 1 — everything sits on this |
| `--card2` / `brand-100` | `#1E2535` | `bg-[#1E2535]` | Elevated panels, modals, dropdowns |
| `--card3` / `brand-50` | `#252D3D` | `bg-[#252D3D]` | Active states, selected rows, inputs |
| `--scrim` | `#0A0E17` | `bg-[#0A0E17]` | Modal backdrop |

### Accent Palette

| Token | Hex | Tailwind | Role |
|---|---|---|---|
| `--blue` / `brand-blue` | `#0EA5E9` | `text-[#0EA5E9]` | PRIMARY — CTAs, active nav, AI labels, focus rings |
| `--blue-hi` / `brand-400` | `#38BDF8` | `text-[#38BDF8]` | Hover, price display, OEM text, link color |
| `--blue-deep` / `brand-800` | `#0284C7` | `bg-[#0284C7]` | Button pressed/active state |
| `--blue-net` | `#1D4ED8` | — | Speed-line dividers ONLY — never use elsewhere |

### Text Palette

| Token | Hex | Usage |
|---|---|---|
| `--text` | `#FFFFFF` | Primary text, hero headlines, button labels (on dark bg) |
| `--text-head` | `#E2E8F0` | Section headings, card titles, high-emphasis text |
| `--text-sec` | `#94A3B8` | Body copy, descriptions, card subtext |
| `--text-muted` | `#475569` | Placeholder text, muted metadata |

### Semantic Palette

| Token | Hex | Usage |
|---|---|---|
| `--success` / `brand-success` | `#22C55E` | In-stock, fitment verified (✅), confirmed |
| `--warning` | `#F59E0B` | Low stock, fitment unverified (⚠️), pending |
| `--danger` | `#EF4444` | Out of stock, error, blocked |

### Border Tokens

| Token | Value | Usage |
|---|---|---|
| `--border` | `rgba(148,163,184,0.12)` | Default card/input border |
| `--border-hover` | `rgba(14,165,233,0.30)` | Hover/interactive border |
| `--border-blue` | `rgba(14,165,233,0.20)` | AI card, subtle blue border |

### FORBIDDEN COLORS (never use in new work)

- ❌ `#2563eb` / `#1d4ed8` — Google blue (currently in LandingPage — must be migrated)
- ❌ `#f4f7fd` / `#f8fafc` — light page backgrounds
- ❌ `#00ccff` / `#00A3FF` / `#1ca7ff` — wrong blues (legacy, replaced)
- ❌ Any green as primary/accent color
- ❌ White or off-white card backgrounds
- ❌ Colorful gradient backgrounds (gradients are allowed ONLY on primary buttons)

---

## 5. TYPOGRAPHY

### Font Families

| Family | Load Method | Usage |
|---|---|---|
| **Inter** | Google Fonts (in `index.html`) | All UI — headings, body, labels, buttons |
| **JetBrains Mono** | ⚠️ MISSING from index.html — must be added | OEM numbers, VIN codes, SKUs, part references, prices |

**Action required:** Add JetBrains Mono to `index.html`:
```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
```

### Type Scale

| Name | Size | Weight | Tracking | Color | Usage |
|---|---|---|---|---|---|
| Display | 3.75rem / 60px | 800 | −0.04em | `#FFFFFF` | Hero marketing headline |
| Hero | 3rem / 48px | 800 | −0.04em | `#FFFFFF` | Page-level H1 |
| Page Title | 1.875rem / 30px | 700 | −0.02em | `#E2E8F0` | Page H2 |
| Section Heading | 1.5rem / 24px | 600 | normal | `#E2E8F0` | Section headers |
| Card Title | 1rem / 16px | 600 | normal | `#E2E8F0` | Part name, product title |
| Body | 1rem / 16px | 400 | normal | `#94A3B8` | Body copy, descriptions |
| Secondary | 0.875rem / 14px | 400 | normal | `#94A3B8` | Table body, helper text |
| Label / Badge | 0.75rem / 12px | 600 | 0.12em | UPPERCASE | Status badges, tags |
| OEM / Code | 0.875rem / 14px | 500 | 0.03em | `#38BDF8` | JetBrains Mono — OEM/VIN/SKU |

### Price Display

- **Card price (inline):** 1.25rem / 700 weight / Halo Edge Blue `#38BDF8` / tabular-nums / `font-variant-numeric: tabular-nums`
- **Featured price (product page):** 2.25rem / 800 weight / same color
- Always prefix with ₪ (shekel)
- VAT display: smaller, secondary `+מע"מ ₪XX.XX`

### RTL Rules

- `<html dir="rtl">` when `lang` is `he` or `ar`
- i18n.js already detects RTL — the `dir` attribute on `<html>` must update dynamically (currently only applied as a class within components, not on `<html>` itself — **action required**)
- Price alignment: right-aligned in RTL, left-aligned in LTR
- Icon positions mirror: `ltr:right-N rtl:left-N` pattern (already used in some places ✅)

---

## 6. SPACING SYSTEM

**Base unit:** 8px  
**Scale:** 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 · 96px

| Token | px | Tailwind | Usage |
|---|---|---|---|
| `space-1` | 4px | `p-1` | Tight inline gap |
| `space-2` | 8px | `p-2` | Icon-text gap, badge padding |
| `space-3` | 12px | `p-3` | Small component internal padding |
| `space-4` | 16px | `p-4` | Standard component padding, mobile side gutter |
| `space-6` | 24px | `p-6` | Card internal padding |
| `space-8` | 32px | `p-8` | Section internal spacing |
| `space-12` | 48px | `p-12` | Between major sections |
| `space-16` | 64px | `p-16` | Large hero padding |
| `space-24` | 96px | `p-24` | Top/bottom of full-page sections |

**Card internal padding:** always 24px (`p-6`)  
**Section rhythm:** major sections separated by speed-line divider  
**Side gutter:** 16px mobile, 24px tablet+, `max-w-[1280px]` centered

---

## 7. LAYOUT

### Grid System

- **Desktop:** 12-column, 24px gutter, `max-w-[1280px]` centered, 24px side padding
- **Tablet:** 2-column product cards, 16px gap
- **Mobile:** single column, 16px side padding

### Page Structure

```
┌─ Navigation (sticky, height 64px) ─────────────────────────────┐
├─ Speed-line divider ────────────────────────────────────────────┤
├─ Page content ──────────────────────────────────────────────────┤
│   ├─ Hero / header section                                      │
│   ├─ Speed-line divider                                         │
│   ├─ Main content                                               │
│   ├─ Speed-line divider                                         │
│   └─ Secondary content                                          │
├─ Speed-line divider ────────────────────────────────────────────┤
└─ Footer ────────────────────────────────────────────────────────┘
```

### Elevation Hierarchy (z-axis)

1. Page root → Void Black `#0F1218`
2. Content cards → Deep Navy `#151B27`
3. Hover/elevated → Steel Navy `#1E2535`
4. Modal/drawer → `#252D3D` content on `#0A0E17` backdrop

### Speed-Line Divider (brand motif)

```css
.speed-line {
  height: 1px;
  background: linear-gradient(90deg, transparent 0%, #1D4ED8 25%, #0EA5E9 50%, #1D4ED8 75%, transparent 100%);
  opacity: 0.5;
}
```
Use between: hero/search, search/results, results/categories, major sections, before footer.

---

## 8. COMPONENT LANGUAGE

### Buttons

**Primary (`.btn-primary`):**
```css
background: linear-gradient(135deg, #38BDF8 0%, #0EA5E9 60%, #0284C7 100%);
color: #0F1218; /* DARK text, not white */
border-radius: 6px; /* geometric, NOT pill */
height: 40px (standard) / 48px (large) / 56px (hero);
font-weight: 600;
```
Hover: `filter: brightness(1.08)` + `box-shadow: 0 0 20px rgba(14,165,233,0.35)`

**Secondary (`.btn-secondary`):**
```css
background: transparent;
border: 1px solid rgba(148,163,184,0.20);
color: #FFFFFF;
border-radius: 6px;
```
Hover: border → `rgba(14,165,233,0.40)`, text → `#38BDF8`

**AI Button (for AI-trigger actions only):**
```css
background: rgba(14,165,233,0.10);
border: 1px solid rgba(14,165,233,0.35);
color: #38BDF8;
box-shadow: 0 0 12px rgba(14,165,233,0.18);
border-radius: 6px;
```

**Ghost (`.btn-ghost`):** transparent, `#94A3B8` text, hover: `rgba(148,163,184,0.08)` bg

**Forbidden button styles:**
- ❌ Pill-shaped (border-radius > 8px on buttons)
- ❌ White text on blue gradient (use dark text `#0F1218`)
- ❌ `#2563eb` background

### Cards (`.card`)

```css
background: #151B27;
border: 1px solid rgba(148,163,184,0.12);
border-radius: 12px;
padding: 24px;
box-shadow: 0 1px 3px rgba(0,0,0,0.4), 0 4px 12px rgba(0,0,0,0.3);
```
Hover: border → `rgba(14,165,233,0.20)`, subtle blue glow

### Part Result Cards

```
┌─ [80×80px white thumb] ─ [Part Name] ─────────────── [₪PRICE] ─┐
│                           [OEM: XXXXXX in JetBrains Mono]       │
│                           [✅ מאומת לרכב] [AI Match]            │
│                           [Supplier · 3-5 days · In stock]       │
│                                                     [Add to Cart]│
└─────────────────────────────────────────────────────────────────┘
```

**AI result card variant:**
```css
background: linear-gradient(135deg, rgba(14,165,233,0.06) 0%, rgba(14,165,233,0.02) 100%);
border: 1px solid rgba(14,165,233,0.20);
box-shadow: 0 0 24px rgba(14,165,233,0.12);
```

### Inputs (`.input-field`)

```css
background: #252D3D;
border: 1px solid rgba(148,163,184,0.12);
color: #FFFFFF;
border-radius: 8px;
height: 40px;
placeholder color: #475569;
```
Focus: border → `rgba(14,165,233,0.40)`, ring: `0 0 0 2px #0EA5E9`

### Search Bar (most important component)

```css
background: #1E2535;
border: 1px solid rgba(148,163,184,0.12);
border-radius: 12px;
height: 56px (desktop) / 48px (mobile);
```
Right-side slots (RTL-aware): AI Search button + VIN scanner button  
Focus: border → `rgba(14,165,233,0.40)`, glow: `0 0 0 2px #0EA5E9`  
Placeholder: italic, `#475569`

### Navigation

```css
background: rgba(15,18,24,0.88); /* Void Black with backdrop-blur */
backdrop-filter: blur(16px);
border-bottom: 1px solid rgba(148,163,184,0.08);
height: 64px;
```
Nav links: 0.875rem / 500 weight / `#94A3B8`  
Active link: `#0EA5E9` / 600 weight / `background: rgba(14,165,233,0.08)`

### Fitment Badges

```
✅ Verified Fit     → background: rgba(34,197,94,0.10)  border: rgba(34,197,94,0.25)  text: #22C55E
⚠️ Unverified      → background: rgba(245,158,11,0.10) border: rgba(245,158,11,0.25) text: #F59E0B
✦ AI Match         → background: rgba(14,165,233,0.12)  border: rgba(14,165,233,0.30) text: #38BDF8 + glow
```
Border-radius: 4px (sharp, not pill)  
Font: 0.67rem / 700 weight / 0.06em tracking / UPPERCASE

### Supplier Comparison Table

```css
header background: rgba(148,163,184,0.04);
row border: rgba(148,163,184,0.06);
header text: #475569 (muted, uppercase);
row hover: rgba(14,165,233,0.04);
price column: #38BDF8 / tabular-nums;
```
Stock indicator: colored dot (green/amber/red) + text

### Modal / Overlay

```css
backdrop: #0A0E17 (80% opacity);
panel: #151B27;
border: 1px solid rgba(148,163,184,0.12);
border-radius: 16px;
box-shadow: 0 20px 60px rgba(0,0,0,0.7);
```

### Badges

```css
border-radius: 4px; /* NOT pill */
padding: 2px 9px;
font-size: 0.67rem;
font-weight: 700;
letter-spacing: 0.06em;
text-transform: uppercase;
```

### Loading States

- **Page loader:** dark spinner on `#0F1218`, `border-top-color: #0EA5E9` (NOT `#00A3FF`)
- **Content skeleton:** `#1E2535` animated pulse — never white skeleton on dark bg
- **Button loading:** `Loader2` icon spin, opacity 0.7, cursor-not-allowed

### Empty States

Background: `#151B27` card  
Icon: 48px, `#475569` color  
Title: `#E2E8F0` / 600  
Body: `#94A3B8` / 400  
CTA: `.btn-primary`

---

## 9. ICONOGRAPHY

**Library:** Lucide React (already in dependencies) — use exclusively  
**Size convention:** 16px (inline text), 20px (button icon), 24px (nav icon), 48px (empty state hero)  
**Stroke width:** 1.5 (default), 2.0 (emphasized)  
**Color:** inherit from text (`currentColor`) — never hardcode icon colors

**Forbidden:** emoji as interface icons, mixing icon libraries, filled icons when line icons available

**Special treatment for AI elements:** AI-associated icons get `color: #38BDF8` and optionally `drop-shadow(0 0 4px rgba(14,165,233,0.6))`

---

## 10. SHADOWS

| Name | Value | Usage |
|---|---|---|
| `shadow-card` | `0 1px 3px rgba(0,0,0,0.4), 0 4px 12px rgba(0,0,0,0.3)` | Standard cards |
| `shadow-elevated` | `0 4px 16px rgba(0,0,0,0.5), 0 8px 32px rgba(0,0,0,0.4)` | Modals, floating panels |
| `shadow-modal` | `0 20px 60px rgba(0,0,0,0.7)` | Full modal dialogs |
| `shadow-electric` | `0 0 24px rgba(14,165,233,0.40)` | AI elements ONLY |
| `shadow-electric-strong` | `0 0 48px rgba(14,165,233,0.60)` | Featured AI element |

---

## 11. MOTION / ANIMATION

**Philosophy:** Motion confirms actions and guides attention. Never animate for decoration.

| Pattern | Timing | Usage |
|---|---|---|
| Hover transition | `transition-all duration-200` | All interactive elements |
| Card hover lift | `translateY(-2px)` + subtle blue glow | Category cards, feature cards |
| Button hover | `brightness(1.08)` | Primary button |
| Page loader | 0.8s linear spin | Loading state |
| Toast notification | 350ms ease — react-hot-toast default | System feedback |
| Skeleton pulse | `animate-pulse` 1.5s | Content loading |

**AI element entrance:** Optional subtle scale-in (`scale(0.97) → scale(1)`, 200ms) for AI response cards

**Forbidden:** bounce, rubber-band, slide-in from random directions, transitions > 400ms, layout-shifting animations

---

## 12. RESPONSIVE STRATEGY

### Breakpoints (Tailwind standard)

| Breakpoint | Width | Layout |
|---|---|---|
| Default (xs) | < 640px | Single column, 16px gutter, stacked nav |
| `sm` | ≥ 640px | 2-column grids, search inline |
| `md` | ≥ 768px | Full nav visible, 2-column hero |
| `lg` | ≥ 1024px | 3+ column grids, expanded sidebar |
| `xl` | ≥ 1280px | Max-width container centered |

### Mobile-Specific Rules

- Search: full width, 48px height (56px desktop)
- Navigation: hamburger menu (≤ md), all links accessible in drawer
- Part cards: full width, stacked (thumbnail left, info right)
- Price: always visible without horizontal scroll
- Touch targets: minimum 44×44px (WCAG 2.5.5)
- Bottom navigation consideration for < 640px: cart/search/account

### Mobile Anti-Patterns to Fix

- The floating WhatsApp button (fixed bottom-6 right-6) overlaps page content — add `pb-20` to page on mobile when it's present
- Sub-nav in LandingPage: `overflow-x-auto whitespace-nowrap` is a readability issue — consider collapsing to hamburger ≤ md
- Cart quantity controls: ensure +/− buttons are minimum 44px tap target

---

## 13. ACCESSIBILITY RULES

### Contrast Requirements (WCAG AA)

- Body text (`#94A3B8` on `#0F1218`): contrast ratio 4.7:1 ✅
- Headings (`#E2E8F0` on `#0F1218`): contrast ratio 12.6:1 ✅
- Price (`#38BDF8` on `#0F1218`): contrast ratio 4.8:1 ✅
- Warning Amber `#F59E0B` on `#0F1218`: 4.5:1 ✅
- **ISSUE:** `#475569` (placeholder/muted) on `#0F1218`: ratio 2.9:1 ❌ — fails AA for normal text (passes only for UI components per WCAG 1.4.11)

### Required for Every Interactive Element

- `focus-visible:ring-2 focus-visible:ring-[#0EA5E9]` — visible focus indicator
- Keyboard navigation: tab order follows visual order
- Semantic HTML: `<button>` for actions, `<a>` for navigation, `<nav>` for nav
- Icon-only controls: `aria-label` always
- Form inputs: always paired `<label>` or `aria-label`

### RTL Accessibility

- `dir="rtl"` on `<html>` element (must be dynamic, not static "en")
- Logical properties: `ltr:right-N rtl:left-N` pattern (already used ✅)
- Number/price display: use `<bdi dir="ltr">` wrapper for prices in RTL context to prevent bidi rendering issues

### Issues Found in Audit

- `<html lang="en">` is hardcoded — should update to `lang={currentLang}` + `dir={currentDir}` dynamically
- `<details>/<summary>` language selector: inconsistent screen-reader support across browsers → replace with controlled dropdown
- `aria-pressed` on search tabs ✅ (good, already implemented)
- Cart page: checkbox for "select all" needs `aria-label`
- Loading spinner: has no `aria-busy` / `role="status"` / `aria-label`

---

## 14. E-COMMERCE CONVERSION PRINCIPLES

### Information Hierarchy on Part Cards

Strict visual priority order:
1. **Part name** — largest, most prominent
2. **Fitment badge** — FIRST thing scanned (✅ or ⚠️) — user decision gate
3. **Price** — Halo Edge Blue, large, impossible to miss
4. **OEM number** — monospace, Halo Edge Blue, secondary prominence
5. **Stock/availability** — Success Green dot
6. **Add to Cart CTA** — always visible without scrolling

### Conversion Friction Points to Eliminate

- ❌ Requiring login to browse parts (already fixed — `/parts` is public ✅)
- ❌ Hiding price behind "request a quote" for stocked items
- ❌ Unclear fitment — fitment badge must be the FIRST element in the card
- ❌ Multi-step checkout with unnecessary fields
- ❌ Not pre-filling address from profile (already handled in Cart.jsx ✅)
- ❌ VAT surprise at checkout — show full total (subtotal + VAT + shipping) in cart

### Trust Signals (show on every purchase-path page)

- Verified supplier count
- Secure payment indicator (Stripe PCI-DSS)
- Return policy link
- WhatsApp support access
- Order tracking capability

### Anti-patterns to Avoid

- ❌ Fake urgency ("Only 2 left!" without real inventory)
- ❌ Countdown timers that reset
- ❌ Fake reviews or social proof
- ❌ Supplier names exposed to customers (must remain masked per business rules)
- ❌ Raw cost / margin shown anywhere customer-facing

---

## 15. ANTI-PATTERNS (design-level)

These are patterns currently in the codebase or common in AI-generated designs that must NOT be used:

| Anti-pattern | Current location | Correct approach |
|---|---|---|
| `#2563eb` Google blue | LandingPage.jsx throughout | Replace with `#0EA5E9` Electric Blue |
| `bg-[#f4f7fd]` light root | LandingPage.jsx line 116 | Replace with `bg-[#0F1218]` |
| `bg-white` sections | LandingPage "How It Works" | Replace with `bg-[#151B27]` |
| `Rubik, Heebo` in Toaster | App.jsx line 76 | Replace with `Inter, system-ui, sans-serif` |
| `#00A3FF` page loader | App.jsx line 35 | Replace with `#0EA5E9` |
| Missing JetBrains Mono | index.html | Add to Google Fonts link |
| `borderRadius: 14px` on buttons | Was in tailwind.config.js (now fixed) | 6px — already corrected ✅ |
| Static `lang="en"` | index.html | Dynamic `lang`+`dir` from i18n store |
| Hardcoded category counts | LandingPage.jsx | Wire to live API or clearly mark as estimates |
| Skeleton on light bg | New work risk | Always use `#1E2535` pulse on dark bg |

---

## 16. IMPLEMENTATION GUIDANCE (React + Tailwind v3)

### Using Design Tokens

Prefer Tailwind utilities that map to tokens:
```jsx
// ✅ Correct
<div className="bg-[#151B27] border border-[rgba(148,163,184,0.12)] rounded-xl p-6">

// ✅ Also correct (via CSS custom properties defined in index.css)
<div style={{ background: 'var(--card)' }}>

// ❌ Wrong
<div className="bg-white rounded-2xl border-gray-200">
```

### Key Classes Already Defined in index.css

- `.btn-primary` — primary CTA button ✅
- `.btn-secondary` — secondary button ✅
- `.btn-ghost` — ghost button ✅
- `.input-field` — form input ✅
- `.card` — standard card ✅
- `.badge` — badge base ✅
- `.speed-line` — section divider ✅
- `.auth-page` — auth page background ✅
- `.panel-dark` — dark dropdown/modal panel ✅
- `.section-title` — section heading style ✅

### RTL Pattern

```jsx
// ✅ Always use logical RTL/LTR Tailwind classes
<div className="ltr:right-4 rtl:left-4">
<svg className="rtl:rotate-180">→</svg>

// ✅ Dynamic direction from i18n
const { dir } = useI18n()
<div dir={dir}>...</div>
```

### Loading States Pattern

```jsx
// ✅ Dark skeleton
<div className="animate-pulse bg-[#1E2535] rounded-xl h-24 w-full" />

// ✅ Spinner
<div className="w-10 h-10 border-3 border-[#1E2535] border-t-[#0EA5E9] rounded-full animate-spin" />
```

---

## 17. FILE LOCATIONS

| File | Purpose |
|---|---|
| `/opt/autosparefinder/frontend/tailwind.config.js` | Tailwind token definitions (dark palette ✅ fixed) |
| `/opt/autosparefinder/frontend/src/index.css` | CSS custom properties + utility classes ✅ fixed |
| `/opt/autosparefinder/frontend/src/components/Layout.jsx` | App shell — nav/footer ✅ fixed |
| `/opt/autosparefinder/frontend/src/pages/LandingPage.jsx` | ⚠️ Uses different design system — needs migration |
| `/opt/autosparefinder/frontend/src/App.jsx` | ⚠️ Wrong font in Toaster, wrong color in PageLoader |
| `/opt/autosparefinder/frontend/index.html` | ⚠️ Missing JetBrains Mono, static lang="en" |
| `/opt/autosparefinder/.stitch/DESIGN.md` | Original brand tokens (authoritative for Stitch) |
| `/opt/autosparefinder/design-system/autosparefinder/MASTER.md` | This file — product-wide design system |
| `/opt/autosparefinder/design-system/autosparefinder/STITCH_BRIEF.md` | Stitch generation brief |
