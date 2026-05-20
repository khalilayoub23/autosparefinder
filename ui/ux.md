# AutoSpareFinder — UI/UX Design System

## Navigation
- Agent rules → READ: claude.md
- Which agents serve each UI surface → READ: skills.md § LAYER A
- Which pipeline phases produce the data shown → READ: phases.md

## Data Flow to UI
```
Phase 1-4 (pipeline) → Phase 5 (indexed) → Phase 6 (priced)
                                    ↓
                            Customer UI
                    NIR (search) + MAYA (pricing)
                    LIOR (orders) + DANA (support)
                    TAL (finance: invoices, VAT 18%, ILS conversion)
```

Finance layer notes:
- TAL generates invoices after LIOR confirms order
- VAT 18% applied on all Israeli transactions (claude.md § Business Rules)
- All prices displayed in ILS (₪) — TAL.CURRENCY_CONVERT handles USD→ILS via currency_rate.py
- Invoice data flows: Phase 6 pricing → LIOR creates order → TAL generates invoice → UI shows receipt

---

## Brand

**Name:** AutoSpare | SPARE FINDER
**Tagline:** Find the Right Part. Fast. Easy. Reliable.
**Sub-tagline:** Search millions of auto parts from trusted suppliers worldwide. Best prices. Fast delivery.

---

## Color System

```css
/* Primary */
--navy:          #0a1628;   /* Header, footer, dark sections */
--blue:          #1e6ff0;   /* Primary CTA, buttons, active states */
--blue-hover:    #3d8ef0;   /* Button hover */

/* Neutral */
--white:         #ffffff;
--gray-bg:       #f8f9fa;   /* Page background */
--gray-border:   #e9ecef;   /* Card borders, dividers */
--gray-text:     #6c757d;   /* Secondary text, subtitles */
--dark-text:     #212529;   /* Headings, primary text */

/* Part Origin (SEE phases.md § Layer 8) — matched to landing page palette */
--oem-blue:      #1e6ff0;   /* original — OEM genuine (matches primary --blue) */
--oe-cyan:       #17a2b8;   /* oe_equivalent — Bosch/NGK/Valeo tier */
--aftermarket:   #adb5bd;   /* aftermarket — economy (light gray, non-intrusive) */

/* Status */
--success:       #28a745;
--warning:       #ffc107;
--danger:        #dc3545;
--info:          #17a2b8;
```

---

## Typography

```
Font family: Inter, Segoe UI, sans-serif

Hero title:     48px / bold
Hero accent:    48px / bold / --blue
Section title:  28px / bold
Card title:     16px / semibold
Body:           14-16px / regular
Badge/label:    12px / medium
Secondary text: 14px / regular / --gray-text
```

---

## Layout

```
Max content width:   1280px
Container padding:   24px horizontal
Grid:                12-column, 24px gutter
Header height:       64px (navy)
Nav bar height:      44px (below header)

Breakpoints:
  Mobile:   < 768px
  Tablet:   768px – 1024px
  Desktop:  > 1024px
```

---

## Header
**Agent:** AVI routes initial request. NIR handles search. OREN handles auth.
**Background:** --navy (#0a1628)

**Structure left → right:**
```
[AutoSpare logo + SPARE FINDER]
[Search bar ▾ All Categories] [🔍]
[Support] [Track Order] [WhatsApp 🟢]
[$ USD ▾]
[👤 Sign In / My Account]
[🛒 Cart C₪ 0]
```

**Navigation bar (below header):**
```
[Home] [Categories ▾] [Catalog] [Request a Quote] [How It Works] [About Us] [Support]
```
Active item: --blue background, white text, rounded.

---

## Search Bar
**Agent:** NIR handles all search variants (SEE skills.md § NIR)
**Pipeline data source:** Meilisearch index (SEE phases.md § Layer 14)

**3 tabs:**
```
[🔍 VIN / Plate Number] [🔧 SKU / OEM / Part Number] [🚗 Vehicle Details]
```

**VIN tab (default):**
```
[Enter VIN Number (e.g., 1HGCM82633A004352)........] [Search Parts →]
🔒 100% Safe & Secure Search  •  We don't store your VIN
```

**Vehicle Details tab:**
```
[Make ▾] [Model ▾] [Year ▾] [Engine ▾] [Categories ▾] [Search Parts →]
```
Vehicle data from: vehicle_market_il (SEE phases.md § Layer 4)
Categories from: categories.py CATEGORY_MAP (SEE phases.md § Layer 10)

**Categories tab (browse by part type):**
```
[Make ▾] [Parts/Category ▾] [Model ▾] [Year ▾] [Engine ▾] [Search Parts →]
```
Start with Make + Category to narrow results, then refine by model/year/engine.

---

## Hero Section
**Background:** dark blue gradient (#0a1628 → #1a2f5a)
**Layout:** text left (55%) / product image right (45%)

```
Find the Right Part.
Fast. Easy. Reliable.          ← --blue
Search millions of auto parts from trusted suppliers worldwide.
Best prices. Fast delivery.

[Search by VIN] [OEM Number] [SKU / Part Number] [Vehicle Details]
[VIN input field.........................] [Search Parts →]
🔒 100% Safe & Secure Search  •  We don't store your VIN
```

**Product image:** brake disc + shock absorber + alternator + oil filter (photorealistic)

---

## Trust Badges
**5 columns, white background strip below hero**

| Icon | Title | Subtitle |
|------|-------|---------|
| 🛡️ | Trusted Suppliers | 1000+ verified suppliers |
| 💰 | Best Prices | Compare & save more |
| 📦 | Fast Delivery | Local & international |
| 🔒 | Secure Payments | 100% secure checkout |
| 🎧 | Expert Support | 24/7 support |

---

## How It Works
**4 steps — left section of homepage**
**Agent flow:** NIR → MAYA → LIOR → LIOR

```
① Search      Find parts by VIN, OEM, SKU or vehicle details.
② Compare     Compare prices, condition, delivery & suppliers.
③ Order/Quote Place an order or request a quote if stock is limited.
④ Track       Track your order and get support anytime.
```

Step circles: --blue background, white number, dashed connector line.

---

## Top Categories Grid
**Data source:** categories.py CATEGORY_MAP, 28 categories (SEE phases.md § Layer 10)
**Agent:** NIR filters search by category

**2 rows × 5 columns + "More Categories →"**

Row 1:
- Engine Parts (25,000+ parts)
- Brake System (18,000+ parts)
- Suspension (12,000+ parts)
- Electrical (20,000+ parts)
- Body Parts (15,000+ parts)

Row 2:
- Transmission (8,000+ parts)
- Cooling System (7,000+ parts)
- Filters (6,000+ parts)
- Exhaust System (9,000+ parts)
- More Categories → (View All)

**Card design:**
- White background, --gray-border border, 8px radius
- Product image centered (200×150px, white bg)
- Category name: 14px semibold, --dark-text
- Part count: 12px, --gray-text
- Hover: slight shadow, --blue border

**"View All Categories →"** top-right, --blue, 14px semibold

---

## Part Origin Badges
**Data source:** `part_condition` + `aftermarket_tier` fields (SEE phases.md § Layer 8)
**Agent:** NIR returns these via CLASSIFY_ORIGIN skill

Display on every part card and detail page:

```
[🔵 OEM Original]    → part_condition = 'original'      → --oem-blue   (#1e6ff0)
[🔹 OE Equivalent]   → part_condition = 'oe_equivalent' → --oe-cyan    (#17a2b8)
[◻️ Aftermarket]     → part_condition = 'aftermarket'   → --aftermarket (#adb5bd)
```

Badge style: pill shape, colored background (15% opacity), colored text + border, 12px medium
Colors match the landing page navy/blue palette — no gold or green that clashes with the dark theme.

---

## Price Display
**Data source:** supplier_parts table, min/max_price_ils (SEE phases.md § Layer 20)
**Agent:** MAYA handles pricing (SEE skills.md § MAYA)
**Currency:** Always ILS (₪) — SEE claude.md § Business Rules

```
Best Price:       ₪ 249     [Add to Cart] ← --blue button
─────────────────────────────────────────
OEM Original:     ₪ 890
OE Equivalent:    ₪ 320     Bosch  [🟢]
Economy:          ₪ 149     Generic [⚪]

💚 Save 72% vs OEM
```

"Best Price" row: --oe-green left border, light green background
"Save X%" badge: --success color

---

## Supplier Price Comparison Table
**Data source:** supplier_parts table (SEE phases.md § Layer 20)
**Agent:** MAYA returns sorted by total_cost. BOAZ handles B2B quotes.

```
Supplier       | Price   | Shipping | Total  | Condition  | Action
---------------|---------|----------|--------|------------|------------------
eBay Motors    | ₪ 180   | ₪ 25     | ₪ 205  | New [⚪]   | [Buy Now]
AliExpress     | ₪ 145   | Free     | ₪ 145  | New [⚪]   | [Buy Now]  ← best
Motorstore IL  | ₪ 310   | Free     | ₪ 310  | New [🟢]   | [Add to Cart]
OEM (Renault)  | ₪ 890   | Free     | ₪ 890  | Original[🟡]| [Request Quote]
```

Best price row: --oe-green left border highlight.
Sorted by total_cost ascending (MAYA.BEST_PRICE logic).

---

## Part Detail Page
**Agent:** NIR serves data. MAYA shows pricing. LIOR handles cart/order.

**Sections (top → bottom):**
1. Breadcrumb: Home > Category > Part Name
2. Image gallery (from parts_images table, populated in phases.md L14/L20)
3. Part name (name field) + Hebrew name (name_he field) + OEM number + origin badge
4. Price comparison table (all suppliers from supplier_parts)
5. Compatibility checker: list of matching vehicles from part_vehicle_fitment (phases.md L6)
6. Specifications table (specifications JSONB field, extracted in phases.md L2)
7. Related parts (interchange from phases.md L9)
8. Reviews

---

## Vehicle Selector Flow
**Agent:** NIR handles FITMENT_CHECK + COMPATIBILITY_SCORE
**Data:** vehicle_market_il + part_vehicle_fitment (phases.md L4, L6)

```
VIN input:
  [Enter VIN] → [Search Parts →]
  → auto-detect Make/Model/Year → show compatible parts

Manual:
  [Make ▾] → [Model ▾] → [Year ▾] → [Engine ▾] → [Search Parts →]
  → filter parts_catalog by part_vehicle_fitment
```

---

## AI Assistant Widget
**Agent:** AVI routes to NIR initially. Escalates as needed.
**Position:** Bottom-left, dark navy card

```
🤖 Need help finding the right part?
   Our AI Assistant can help you match parts
   and check compatibility in seconds.

   [✨ Try AI Assistant]    ← --blue button
```

---

## Feature Highlights Strip
**4 columns, bottom of homepage**

| Icon | Feature | Description |
|------|---------|-------------|
| ✅ | Parts Compatibility | Guaranteed fit for your vehicle |
| ⚙️ | Multiple Conditions | New, Used, OEM, Aftermarket options |
| 🌍 | Global Shipping | We ship worldwide to most countries |
| ↩️ | Easy Returns | Hassle-free returns within 30 days |

---

## WhatsApp Integration
**Agent:** DANA handles support queries from WhatsApp
- Floating green button: bottom-right, always visible, z-index top
- Top header: "WhatsApp" text + icon
- Links to support WhatsApp (whatsapp_bridge Docker container)

---

## Responsive Rules

```
Desktop (>1024px): Full layout as reference design
Tablet (768-1024): 2-column category grid, simplified header
Mobile (<768px):   Single column, collapsed nav, VIN search primary,
                   bottom nav bar for Home/Search/Cart/Account
```

---

## RTL / LTR Rules

- Interface language: English (LTR)
- Hebrew part names (name_he field): render with `dir="auto"`
- Currency: ₪ prefix always (never suffix)
- Numbers: Western Arabic (1, 2, 3) — never Hebrew numerals
- Mixed Hebrew+Latin names: use `dir="auto"` on the container

---

## Performance Targets

| Metric | Target |
|--------|--------|
| First Contentful Paint | < 1.5s |
| Search results (Meilisearch) | < 300ms |
| Part detail page load | < 2s |
| Image format | WebP, lazy loaded |
| Search engine | Meilisearch (not PostgreSQL direct) |

---

## Component Checklist

Every new UI component must:
- [ ] Use colors from this file's Color System only
- [ ] Display part_condition badge where parts are shown
- [ ] Show price in ILS (₪) with tier breakdown
- [ ] Be tested on mobile (<768px)
- [ ] Connect to correct agent (SEE skills.md § LAYER A)
- [ ] Pull data from correct pipeline layer (SEE phases.md)
