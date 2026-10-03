# AutoSpareFinder — Stitch Design Brief
**Version:** 1.0.0 | **Created:** 2026-09-29  
**Use this file as the system prompt for every Stitch generation.**  
**Full design system:** see `MASTER.md` in this directory and `.stitch/DESIGN.md` at project root.

---

## IDENTITY

**Product name:** AutoSpareFinder  
**Product type:** Global automotive spare-parts comparison marketplace  
**Target markets:** Israel (Hebrew RTL), Arabic-speaking markets, English global  
**Business model:** Multi-supplier price comparison + direct purchase (eBay/AliExpress model for car parts with AI fitment verification)  
**User promise:** Find the right part for your specific vehicle, verified to fit, at the best price.

---

## VISUAL PERSONALITY

**Tone words:** Authoritative · Precise · Luminous · Technical · Global · Trustworthy  
**Theme:** Premium dark-mode marketplace. Dense, information-rich, scannable.  
**NOT:** Generic SaaS, friendly pastel e-commerce, Amazon-clone.

---

## COLOR RULES (copy these tokens into every Stitch prompt)

```
Page background:    #0F1218  (Void Black)
Card surface:       #151B27  (Deep Navy)
Elevated panel:     #1E2535  (Steel Navy)
Active/selected:    #252D3D  (Surface Highlight)
Modal backdrop:     #0A0E17  (Modal Scrim)

Primary accent:     #0EA5E9  (Electric Blue)  — CTAs, nav active, AI labels
Hover accent:       #38BDF8  (Halo Edge Blue) — hover, price, OEM codes
Pressed:            #0284C7  (Deep Blue)       — button active state
Dividers only:      #1D4ED8  (Network Blue)    — speed-line section dividers

Primary text:       #FFFFFF
Section headings:   #E2E8F0
Body / subtext:     #94A3B8
Placeholder/muted:  #475569

Success:            #22C55E  — in stock, fitment verified
Warning:            #F59E0B  — low stock, unverified fitment
Error:              #EF4444  — out of stock, failed

Border default:     rgba(148,163,184,0.12)
Border hover:       rgba(14,165,233,0.30)
Border blue:        rgba(14,165,233,0.20)
```

**NEVER USE:**
- White or light backgrounds (`#f4f7fd`, `#f8fafc`, `#fff`)
- Google blue (`#2563eb`, `#1d4ed8`)
- Any green as a primary/accent color
- Colorful gradient backgrounds
- Pill-shaped buttons (border-radius > 8px)

---

## TYPOGRAPHY

- **Primary font:** Inter — all headings, body, buttons, labels
- **Monospace font:** JetBrains Mono — OEM numbers, VIN codes, SKUs, part references (always in `#38BDF8`)
- **Price display:** tabular-nums, `#38BDF8`, bold weight, ₪ prefix

---

## COMPONENT LANGUAGE

### Primary button
- Gradient: `linear-gradient(135deg, #38BDF8 0%, #0EA5E9 60%, #0284C7 100%)`
- Text color: `#0F1218` (DARK text, NOT white)
- Border-radius: 6px (geometric — never pill)
- Height: 40px standard / 48px large / 56px hero

### Search bar (most important component)
- Background: `#1E2535`
- Border: `1px solid rgba(148,163,184,0.12)` → focus: `rgba(14,165,233,0.40)` + ring `#0EA5E9`
- Border-radius: 12px
- Height: 56px desktop / 48px mobile
- Placeholder: italic, `#475569`

### Cards
- Background: `#151B27`
- Border: `1px solid rgba(148,163,184,0.12)` → hover: `rgba(14,165,233,0.20)`
- Border-radius: 12px
- Internal padding: 24px

### AI elements (glow treatment — ONLY on AI-related components)
- Background: `rgba(14,165,233,0.08)` to `rgba(14,165,233,0.02)` gradient
- Border: `1px solid rgba(14,165,233,0.20)`
- Box-shadow: `0 0 24px rgba(14,165,233,0.40)` (Electric Blue glow)

### Fitment badges
- Verified fit: bg `rgba(34,197,94,0.10)` · border `rgba(34,197,94,0.25)` · text `#22C55E`
- Unverified: bg `rgba(245,158,11,0.10)` · border `rgba(245,158,11,0.25)` · text `#F59E0B`
- AI match: bg `rgba(14,165,233,0.12)` · border `rgba(14,165,233,0.30)` · text `#38BDF8` + glow

### Speed-line divider (brand motif — between sections)
```css
height: 1px;
background: linear-gradient(90deg, transparent 0%, #1D4ED8 25%, #0EA5E9 50%, #1D4ED8 75%, transparent 100%);
opacity: 0.5;
```

---

## LAYOUT

- Max container: 1280px centered with 24px side padding
- Mobile side gutter: 16px
- Section internal spacing: 48px top/bottom padding
- Card grid gap: 16px

---

## SCREENS TO GENERATE

When generating screens for AutoSpareFinder, always include:

### Landing Page / Homepage
- Dark hero with gradient overlay and automotive image
- Large search bar with tabs: VIN · OEM · SKU · Vehicle
- Trust bar with 5 columns (verified suppliers, prices, delivery, payments, experts)
- Category grid (10 categories + "more") with part-family images
- "How It Works" 4-step guide
- AI chat call-to-action (with glow treatment)
- Footer with 3 columns

### Search Results Page
- Left sidebar: category filter, price range, brand, fitment, condition
- Main grid: part cards with thumbnail, name, OEM code, fitment badge, price, "Add to Cart"
- Header shows: result count, active filters as dismissible chips, sort dropdown
- Fitment-verified results at top (green badge)

### Product / Part Detail Page  
- Large part image left (white bg thumbnail)
- Right: part name, OEM in monospace, manufacturer, fitment badge
- Supplier comparison table: supplier · price · shipping · stock · select
- "Add to Cart" large primary button
- Part specifications accordion

### Cart Page
- Line items with thumbnail, name, OEM, quantity stepper, remove
- Order summary: subtotal, conditional VAT (18% IL only), shipping, total
- Multi-step: Cart → Address → Payment → Confirmation
- "Proceed to Checkout" large primary button

### AI Chat Interface
- Dark chat bubble layout
- User bubble: `#252D3D`
- AI bubble: Electric Blue glow card (`rgba(14,165,233,0.08)` + glow border)
- Part card results inline in chat
- Part images in cards

---

## RTL REQUIREMENTS

- All layouts must work in RTL (Hebrew / Arabic)
- Search input: icon and button swap sides
- Card content: text aligns right in RTL
- Navigation: items order reverses
- Price: always shows with ₪ symbol, `ltr` direction within RTL context

---

## WHAT STITCH MUST PRESERVE

1. The dark premium aesthetic — do NOT add light backgrounds
2. The Electric Blue / Halo Blue accent scheme — do NOT introduce other accent colors
3. Geometric (non-pill) button shapes
4. The AI glow treatment (only on AI elements)
5. Fitment badges as a core UI element
6. JetBrains Mono for OEM numbers / codes
7. Dark text on primary buttons (not white)
8. The speed-line divider as a brand motif

## WHAT STITCH SHOULD EXPLORE

- Layout variations for the search results grid (list vs. grid toggle)
- Part detail page with multiple supplier rows
- Mobile-first navigation patterns (hamburger / bottom tab bar)
- Cart with inline fitment warning for incompatible parts
- Onboarding / vehicle selection modal
- Price comparison visualization (bar chart, per-supplier breakdown)
