# AutoSpare Component System

> Enterprise UI components. Every spec traces to a logo token.

---

## 1. Search Bar (Primary CTA — most important component)

The search bar IS the product. It appears at the top of every page except auth.

```
┌──────────────────────────────────────────────────────────────────┐
│ 🔍  Search by part name, OEM number, or describe your vehicle...  │
│                                               [AI Search]  [VIN]  │
└──────────────────────────────────────────────────────────────────┘
```

**Specs:**
- Height: `--search-height` (56px) on desktop; 48px on mobile
- Background: `--color-bg-surface-2`
- Border: `1px solid --color-border-default`
- Border-radius: `--radius-lg` (12px)
- On focus: border becomes `--color-border-primary` + `--glow-focus-ring`
- Left icon: lens icon, `--icon-md`, `--color-metal-400`
- Placeholder: italic, `--color-text-muted`
- Typed text: `--color-text-primary`, `font-medium`
- Right slot: AI Search button + VIN scanner button
- Scan animation on submit: lens icon rotates 360° once (`--duration-spin`)

**States:**
- `default` — standard border
- `focus` — primary border + glow
- `searching` — lens icon animates + speed line shimmer on input bg
- `has-results` — drops suggestion panel below with `--shadow-z3`
- `error` — border becomes `--color-error`, error text below

---

## 2. Buttons

### Primary
```css
background: linear-gradient(135deg, #38BDF8 0%, #0EA5E9 60%, #0284C7 100%);
color: #0F1218;
font-weight: 600;
border-radius: var(--radius-base);          /* 6px */
height: var(--btn-height-md);               /* 40px */
padding: 0 20px;
transition: all var(--duration-normal) var(--ease-out);
```
Hover: `brightness(1.1)` + `--glow-hover`
Active: `scale(0.98)` + `brightness(0.95)`
Focus: `--glow-focus-ring`

### Secondary
```css
background: transparent;
border: 1px solid var(--color-border-strong);
color: var(--color-text-primary);
```
Hover: border → `--color-border-primary`, text → `--color-primary-400`

### Ghost
```css
background: transparent;
border: none;
color: var(--color-text-secondary);
```
Hover: background `rgba(148,163,184,0.08)`

### Danger
```css
background: var(--color-error-bg);
border: 1px solid var(--color-error);
color: var(--color-error);
```

### AI Button (special)
```css
background: rgba(14, 165, 233, 0.10);
border: 1px solid var(--color-border-ai);
color: var(--color-primary-400);
box-shadow: var(--glow-ai);
```
Has pulsing animation on hover: `--glow-ai` → `--glow-ai-strong` oscillation

### Sizes
| Size | Height | Padding | Font |
|------|--------|---------|------|
| sm | 32px | 0 12px | text-sm |
| md | 40px | 0 20px | text-sm |
| lg | 48px | 0 24px | text-base |
| xl | 56px | 0 32px | text-lg |

---

## 3. Cards

### Base Card
```css
background: var(--color-bg-surface);
border: 1px solid var(--color-border-default);
border-radius: var(--radius-lg);
box-shadow: var(--shadow-z2);
padding: var(--space-6);             /* 24px */
```

### Elevated Card
```css
background: var(--color-bg-surface-2);
border: 1px solid var(--color-border-strong);
box-shadow: var(--shadow-z3);
```

### AI Result Card
```css
background: linear-gradient(135deg, rgba(14,165,233,0.08) 0%, rgba(14,165,233,0.02) 100%);
border: 1px solid var(--color-border-ai);
box-shadow: var(--glow-ai);
```
Includes AI badge (blue pill, "AI Match") top-right corner.

### Part Card (Marketplace)
```
┌─────────────────────────────────────────────────────┐
│  [BRAND LOGO]   Brake Pad Set — Front Axle          │
│                 Toyota Corolla 2018-2023             │
│  [Part image]   OEM: 04465-02250                    │
│                 ✅ In Stock  [New] [OEM]             │
│                                                     │
│                 ₪ 189.00          [Compare Prices]  │
│                 Base: ₪ 189 / Max: ₪ 245            │
└─────────────────────────────────────────────────────┘
```
- Vehicle compatibility shown as small pills
- OEM number in `--font-mono`, `--color-text-link`
- Price in `--text-xl`, bold, `--color-text-primary`
- "Compare Prices" opens price comparison panel
- Supplier name MASKED — shown as "Source A" to customers
- Hover: `--glow-hover` + card lifts `translateY(-2px)`

### Supplier Card (Supplier Portal)
```
┌─────────────────────────────────────────────────────┐
│  ●  SUPPLIER NAME      [Enterprise]                 │
│     152,430 parts  •  47 brands  •  IL + Global     │
│                                                     │
│     Last sync: 2h ago    Avg price match: 94%       │
│     [View Catalog]  [Sync Now]  [Settings]          │
└─────────────────────────────────────────────────────┘
```
- Status indicator (green/amber/red dot) = live availability check
- Tier badge: Starter / Pro / Enterprise (metallic pill)

### Analytics Widget Card
```
┌───────────────────────────────┐
│  Total Parts              [↗] │
│                               │
│  4,171,856                    │
│                               │
│  ↑ 12.4% vs last week        │
│  ████████████████░░░░  82%    │
└───────────────────────────────┘
```
- Number: `--text-4xl`, `font-extrabold`, `--color-text-primary`
- Trend: `--text-sm`, color = status color
- Progress bar: primary blue fill, metallic track

---

## 4. Data Tables

Enterprise tables — dense, scannable, sortable.

```
┌─────┬──────────────┬─────────────┬────────┬──────────┬────────┐
│  ✓  │ OEM Number   │ Name        │ Brand  │ Price    │ Stock  │
├─────┼──────────────┼─────────────┼────────┼──────────┼────────┤
│  □  │ 04465-02250  │ Brake Pad.. │ Toyota │ ₪ 189   │  ✅   │
│  □  │ 517592B300   │ Bearing..   │ Kia    │ ₪ 245   │  ✅   │
│  ✓  │ 1J0698451G   │ Brake Kit   │ VW     │ ₪ 312   │  ⚠️   │
└─────┴──────────────┴─────────────┴────────┴──────────┴────────┘
```

**Specs:**
- Row height: 48px (`--leading-loose` = 2)
- Header: `--text-xs`, uppercase, `--tracking-badge`, `--color-metal-400`
- Body text: `--text-sm`, `--color-text-secondary`
- OEM column: `--font-mono`, `--color-text-link`
- Price column: `--font-sans`, tabular-nums, right-aligned, `--color-text-primary`
- Row hover: `background: rgba(148,163,184,0.04)` + left border 2px primary
- Selected row: `background: rgba(14,165,233,0.08)`
- Sort indicator: chevron icon, `--color-primary-500` when active

---

## 5. Navigation

### Top Nav (Desktop)
```
[LOGO]  Marketplace  Dashboard  Suppliers  API  Docs  |  [Search]  [Notifications]  [Avatar]
```
- Height: `--nav-height` (64px)
- Background: `--color-bg-surface` + bottom border `--color-border-subtle`
- Active item: `--color-primary-500`, font-semibold, no underline
- Nav item hover: `--color-text-primary`
- Backdrop blur: `blur(12px)` + `background-opacity: 0.9` (glass effect)

### Sidebar (Dashboard)
```
[LOGO]
──────
🔍 Search
📊 Dashboard
🛒 Marketplace
📦 Inventory
🏭 Suppliers
📈 Analytics
🤖 AI Assistant
──────
⚙️ Settings
❓ Help
[Avatar] Khalil
```
- Width: `--sidebar-width` (260px), collapsed: `--sidebar-width-collapsed` (64px)
- Background: `--color-bg-surface`
- Right border: `--color-border-subtle`
- Active item: `--color-bg-surface-2` bg + left border 2px `--color-primary-500`
- Item height: 40px
- Icon: `--icon-md` (20px), left slot
- Collapse transition: `--duration-slow`, `--ease-gear`

---

## 6. Badges & Tags

### Status Badge
```css
display: inline-flex;
align-items: center;
padding: 2px 8px;
border-radius: var(--radius-full);
font-size: var(--text-xs);
font-weight: 600;
letter-spacing: var(--tracking-badge);
text-transform: uppercase;
```

| Variant | Background | Color |
|---------|------------|-------|
| success | `--color-success-bg` | `--color-success` |
| warning | `--color-warning-bg` | `--color-warning` |
| error | `--color-error-bg` | `--color-error` |
| info | `--color-info-bg` | `--color-info` |
| ai | `rgba(14,165,233,0.15)` | `#38BDF8` |
| enterprise | `rgba(148,163,184,0.10)` | `#E2E8F0` |

### Part Type Tag
- `[OEM]` — info blue
- `[Aftermarket]` — warning amber
- `[Used]` — metal neutral
- `[New]` — success green

---

## 7. Forms

### Input Field
```css
height: var(--input-height-md);       /* 44px */
background: var(--color-bg-surface-2);
border: 1px solid var(--color-border-default);
border-radius: var(--radius-base);
padding: 0 var(--space-4);
color: var(--color-text-primary);
font-size: var(--text-base);
```
Focus: `border-color: --color-primary-500` + `box-shadow: --glow-focus-ring`
Error: `border-color: --color-error` + error message below in red
Label: above input, `--text-sm`, `font-medium`, `--color-metal-200`

### Select / Dropdown
Same as input + chevron-down icon right slot
Open: renders floating panel with `--shadow-z4`, `--radius-md`
Options: 36px height, hover `rgba(148,163,184,0.08)`

### Vehicle Selector (Special Component)
```
┌─────────────────────────────────────────────────────┐
│  Make      │  Model     │  Year      │  [Find Parts] │
│  Toyota ▾  │  Corolla ▾ │  2020  ▾   │               │
└─────────────────────────────────────────────────────┘
```
- Three chained selects, each populates next
- Vehicle silhouette appears to the right when year is selected (line art, 40% opacity)
- "Find Parts" = primary button, triggers search with fitment filter

---

## 8. AI Assistant

```
┌─────────────────────────────────────────────────────┐
│  ⚡ AI Assistant                         [glow ring] │
│  ─────────────────────────────────────────────────  │
│  Ask about any part, OEM number, or vehicle issue   │
│                                                     │
│  [User]: I need front brake pads for my 2020        │
│           Toyota Corolla                            │
│                                                     │
│  [AI]: Found 23 compatible brake pads for your      │
│        Corolla (ZRE172, 2019-2023). Top match:      │
│        Toyota OEM 04465-02250 — ₪ 189               │
│                                                     │
│  [Type your question...]              [Send ⚡]     │
└─────────────────────────────────────────────────────┘
```
- Container: AI card style (blue glow border)
- AI responses: text color `--color-metal-200`, first keyword in `--color-primary-400`
- AI label: `[AI]` badge in top-left, blue pill
- Pulsing glow during "thinking" state
- Send button: primary with AI button style

---

## 9. Price Comparison Panel

Opens inline below part card when "Compare Prices" is clicked.

```
┌─────────────────────────────────────────────────────────────┐
│  Comparing prices for: Toyota Brake Pad Set 04465-02250     │
├──────────┬──────────┬───────────┬───────────┬──────────────┤
│ Source   │ Price    │ Shipping  │ In Stock  │              │
├──────────┼──────────┼───────────┼───────────┼──────────────┤
│ Source A │ ₪ 189   │ ₪ 25      │ ✅ 5 left │ [Buy]        │
│ Source B │ ₪ 201   │ Free       │ ✅ 12     │ [Buy]        │
│ Source C │ ₪ 178   │ ₪ 35      │ ⚠️ 2 left │ [Buy]        │
└──────────┴──────────┴───────────┴───────────┴──────────────┘
│  ⚡ AI Recommendation: Source C cheapest total if >2 items   │
└─────────────────────────────────────────────────────────────┘
```
- Supplier names ALWAYS masked from customer view
- AI recommendation row: blue background, AI badge
- Best total price row: subtle green highlight

---

## 10. Loading States

### Page Loading
- Concentric ring spinner (3 rings, animated rotation, inner rings slower)
- Center: logo mark (gear ring only, no text)

### Skeleton Loader
- Background: `--color-bg-surface-2`
- Shimmer: horizontal speed-line animation, `--duration-scan`
- Shape: matches exact layout of loaded content

### Scanning State (AI / Search)
- Lens icon animates: expands and contracts (scale 1 → 1.2 → 1)
- Blue glow pulses: `--glow-scan`
- Text: "Scanning 4.1M parts..." with dot animation
