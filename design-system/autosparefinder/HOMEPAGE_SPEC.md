# AutoSpareFinder — Homepage Design Specification
**Version:** 1.0.0 | **Created:** 2026-10-05 | **Status:** Design phase — approved for implementation only after owner sign-off
**Phase:** Design only. No implementation. `LandingPage.jsx` is NOT to be touched until this spec is approved.

> Authoritative inputs (read in this order, all three are binding):
> 1. [`.stitch/DESIGN.md`](../../.stitch/DESIGN.md) — brand tokens
> 2. [`design-system/autosparefinder/MASTER.md`](MASTER.md) — product-wide design system
> 3. [`design-system/autosparefinder/STITCH_BRIEF.md`](STITCH_BRIEF.md) — Stitch generation brief
>
> Supporting (binding where cited): [`brand/MOTION.md`](../../brand/MOTION.md), [`brand/LOGO_DNA.md`](../../brand/LOGO_DNA.md)
>
> Visual reference: `frontend/public/reference-landing.png` (IA reference only — its light lower half and Google-blue accents are rejected)
>
> **This spec never overrides MASTER.md.** Where it is silent, MASTER.md governs.

---

## 1. Why the previous concept was rejected

The prior proposal (`.stitch/designs/homepage.html`) was structurally complete but visually generic. Fourteen concrete defects, grouped by cause:

### 1A. Direct design-system violations
| # | Defect | Rule broken |
|---|---|---|
| 1 | **Emoji used as interface icons** throughout — categories (🛑⚙️🔧), why-cards (🎯💰🌍🤖), trust bar (✅🔒🚚🏆) | MASTER.md §9: *"Forbidden: emoji as interface icons"* — Lucide React is the exclusive icon library |
| 2 | **Zero motion** — `brand/MOTION.md` defines a complete authoritative motion system (easings, catalog, state map) that went entirely unused | MOTION.md is binding and was ignored |
| 3 | **No `<bdi dir="ltr">` on prices/OEM** — relied on a CSS `unicode-bidi` shim instead | MASTER.md §13: prices in RTL require `<bdi>` |
| 4 | **Hardcoded category counts** baked into markup with no path to live data | MASTER.md §15 anti-pattern table |

### 1B. Wrong information architecture
| # | Defect | Why it matters |
|---|---|---|
| 5 | **Search lived only in the hero.** Scroll past it and the product's core function disappears | LOGO_DNA.md §1.3: *"The search bar is the primary CTA on every page — not a button, not a hero text"* |
| 6 | **Only 2 search modes** (By Vehicle / By Part Number). The platform actually supports four entry paths, and **plate lookup — the key Israeli differentiator — was absent entirely** | CLAUDE.md §3 names 3 search paths incl. plate→gov API |
| 7 | **No "How It Works."** We buy from the supplier and ship to the customer — an unfamiliar model presented with no explanation | MASTER.md §14: trust through clarity |
| 8 | **AI buried at position 7 of 8**, below the fold on every device | AI fitment verification is differentiator #1 (MASTER.md §1) |

### 1C. Weak visual execution
| # | Defect | Why it matters |
|---|---|---|
| 9 | **Hero art was a flat outline-SVG car** — reads as clipart, not premium automotive | The reference leads with photographic *parts*, which is also more honest: we sell parts, not cars |
| 10 | **The same generic car silhouette repeated 4× in "Popular in Israel"** — Corolla, Picanto, Mazda 3 and Sportage were visually identical | Lazy; destroys the credibility of a fitment product |
| 11 | **No part imagery anywhere.** A parts marketplace with no photography of parts | Commercial credibility |
| 12 | **Monotonous rhythm** — every section was `heading + sub + equal-card grid`, six times in a row | Reads as a template; nothing earns attention |
| 13 | **Hero headline broke awkwardly** at 1440 ("Compare the right / price.") | Uncontrolled wrapping on the single most important line |
| 14 | **Comparison table lacked fitment-first hierarchy** — fitment badge sat in the header, not per row | MASTER.md §14: fitment badge is the user's decision gate and must be scanned first |

**Root cause:** the design treated the homepage as a brochure describing a marketplace. It must *be* a search instrument.

---

## 2. Design direction

### Thesis
**Search is the product.** The homepage is an instrument, not a brochure. The logo is a lens over a gear ring — the page should feel like looking through that lens: dark, precise, with one blue light source.

### Character
Per MASTER.md §3, unchanged: *Authoritative · Precise · Luminous · Technical · Global · Trustworthy.* Bloomberg Terminal density meets Stripe clarity, in an automotive register.

### What changes vs. the rejected concept
1. **Search becomes persistent** — in the header on every scroll position, expanded into a console in the hero.
2. **Four search modes**, matching real backend capability: **Vehicle · Plate · OEM/Part № · VIN**.
3. **Photographic parts, not line-art cars** — a rim-lit parts cluster, which is what we actually sell.
4. **Rhythm is broken deliberately** — a two-column band (How It Works rail beside the category grid) interrupts the full-width grid cadence.
5. **Motion is introduced, sparingly**, drawn from `brand/MOTION.md` and grounded in logo elements.
6. **Every emoji replaced with a Lucide icon.**
7. **AI promoted** from position 7 to position 3-adjacent (immediately after the category/how-it-works band).

### What is preserved
Dark foundation `#0F1218`, single Electric Blue accent, 6px geometric buttons, speed-line divider motif, AI glow reserved for AI only, Inter + JetBrains Mono, 8px spacing grid, `max-w-1280px`.

> **Noted conflict, resolved:** the `autosparefinder-brand-design` skill recommends avoiding Inter and suggests Syne/Barlow/Outfit. **The repository's authoritative tokens win** — MASTER.md §5 mandates Inter + JetBrains Mono, both already loaded, and the brief explicitly says not to invent a separate design system. No font change.

---

## 3. Information architecture

```
S0  Utility bar          28px   value prop · support · track order · WhatsApp · lang/currency
S1  Header               64px   sticky · logo · PERSISTENT SEARCH · nav · cart · account
    ── speed-line ──
S2  Hero console                headline · 4-mode search · trust strip · parts imagery
S3  Trust band                  5 columns, full-width, surface #151B27
    ── speed-line ──
S4  Browse band                 [How It Works rail 4 steps] | [Top Categories grid 10+1]
    ── speed-line ──
S5  Popular in Israel           4 vehicle cards, real IL registry counts
    ── speed-line ──
S6  Price comparison proof      one part, three suppliers, fitment-first
    ── speed-line ──
S7  AI assistant                glow card, tri-lingual proof
    ── speed-line ──
S8  Footer                      4 columns + language switcher
```

Rationale for the order: **search → navigate (S4, S5) → differentiate (S6) → assist (S7)**. A visitor who does not search immediately is given two navigation paths, then the reason we are worth using, then help.

---

## 4. Section specifications

All colors, spacing, type and component rules come from MASTER.md. Only deltas and composition are specified here.

### S0 — Utility bar
- Height 28px (hidden below `md`), background `#0A0E17`, text `#475569` 12px.
- Start: `Millions of parts · Multiple suppliers · Verified to fit your car`
- End: `Support` · `Track Order` · `WhatsApp` (Lucide `MessageCircle`, `#22C55E`) · language `EN|עברית|العربية` · currency `₪ ILS`
- Separator before header: 1px `rgba(148,163,184,0.08)`.

### S1 — Header (sticky, 64px)
Per MASTER.md §8 Navigation: `rgba(15,18,24,0.88)` + `backdrop-filter: blur(16px)`, bottom border `rgba(148,163,184,0.08)`.

Desktop grid: `logo (auto) | search (1fr, max 520px) | nav (auto) | actions (auto)`

- **Logo** — gear-ring + lens mark, 32px, + wordmark. Never animated.
- **Persistent search** — height 40px, bg `#252D3D`, radius 8px, Lucide `Search` in the **inline-start** slot (LOGO_DNA.md §1.3: lens icon always present). Placeholder italic `#475569`: *"VIN, OEM number, SKU or part name"*. Focus → border `rgba(14,165,233,0.40)` + ring `0 0 0 2px #0EA5E9`.
- **Nav** — `Catalog · Categories ▾ · How It Works · Support`. 14px/500 `#94A3B8`; active `#0EA5E9`/600.
- **Actions** — cart (badge), account, both ≥40px hit area, `aria-label` required.
- **Scroll behaviour:** header is always visible. Below 120px scroll the hero console's active mode label is mirrored into the header search placeholder. No hide-on-scroll (it removes the primary CTA).

### S2 — Hero console
- Padding `64px 0 52px` desktop / `36px 0 32px` mobile.
- Background: `#0F1218` base + `radial-gradient(900px 420px at 78% 12%, rgba(14,165,233,0.16), transparent 62%)` + a 52px technical grid at 5% opacity, masked radially. **No photographic background image** — the imagery is a foreground object.
- Desktop: 2 columns `1.05fr / 0.95fr`, 48px gap, vertically centered.

**Left column**
- Eyebrow pill — Lucide `BadgeCheck` 14px + `FITMENT-VERIFIED MARKETPLACE`, 11px/700/0.12em, `#38BDF8` on `rgba(14,165,233,0.10)`, border `rgba(14,165,233,0.20)`.
- H1 — 60px/800/−0.04em (MASTER.md Display). Two lines, **hard-broken with `<br>`, never left to wrap**:
  > **Find the right part.**
  > *Get the right price.* ← `#38BDF8`
  `text-wrap: balance` as a safety net. 44px at `md`, 32px at `xs`.
- Lede — 16px `#94A3B8`, max 50ch: *"4.1M parts from 50+ suppliers, matched to your exact vehicle and priced side by side."*
- **Search console** — card `#1E2535`, border `rgba(148,163,184,0.12)`, radius 12px, padding 24px.
  - **Four mode tabs**, `role="tablist"`, `aria-selected`, Lucide icons:
    | Mode | Icon | Fields | Backend path |
    |---|---|---|---|
    | **Vehicle** (default) | `Car` | Make · Model · Year selects | fitment-first search |
    | **Plate** | `RectangleEllipsis` | IL plate input, numeric, 7–8 digits | plate → gov registry → fitment |
    | **OEM / Part №** | `Hash` | free text, JetBrains Mono input | normalized OEM match |
    | **VIN** | `ScanLine` | 17-char input + scan affordance | VIN decode |
  - Active tab: white text + 2px `#0EA5E9` underline. Inactive `#94A3B8`.
  - Inputs/selects per MASTER.md §8 — `#252D3D`, radius 8px, 44px min height.
  - Submit — `.btn-primary`, full width, **56px** (hero size), label changes per mode (`Search parts` / `Find my car` / `Look up OEM` / `Decode VIN`).
  - Below console: micro-trust line, 12px `#475569` — *"Free to search · No account needed"*.

**Right column — the parts cluster**
- A single photographic/3D-rendered cluster: **brake disc + coilover + alternator + oil filter + spark plugs**, arranged on a transparent background, rim-lit in `#38BDF8` from the lower-left, on `#0F1218`.
- Container: radius 14px, `aspect-ratio: 4/3`, `max-width: 100%`, soft blue floor-glow beneath (blurred ellipse, `#0EA5E9`, 13px blur, 0.85 opacity).
- **Asset requirement:** must be supplied as WebP + PNG fallback, ≤180KB, 2× for retina, `loading="eager"` + `fetchpriority="high"` (it is the LCP element), explicit `width`/`height` to reserve space.
- **Interim fallback if no asset exists at build time:** use the four highest-quality existing Stitch part renders already in the project (brake pads `4b76e479…`, oil filter `f32975b0…`, spark plugs `c940159e…`, coilover `03967b41…`) composited on `#0F1218`. Do **not** ship the outline-SVG car.
- RTL: container swaps to the inline-start column; the image itself gets `transform: scaleX(-1)` so the lighting still falls away from the text.

### S3 — Trust band
Full-width `#151B27`, 1px top/bottom borders, 5 equal columns, 32px vertical padding. Lucide icons 22px `#0EA5E9`, title 14px/600 `#E2E8F0`, sub 12px `#475569`.

| Icon | Title | Sub |
|---|---|---|
| `ShieldCheck` | Verified suppliers | Every seller vetted |
| `Tags` | Best price | Compared across suppliers |
| `Truck` | Fast delivery | Local & international |
| `Lock` | Secure payment | Stripe · PCI-DSS |
| `Headset` | Expert support | WhatsApp, 7 days |

Mobile: 2 columns, then 1 below 360px. No horizontal scroll.

### S4 — Browse band (the rhythm break)
Desktop grid `340px | 1fr`, 48px gap.

**Left rail — How It Works** (sticky within the band, `position: sticky; top: 96px`)
Four numbered steps, vertical, connected by a 1px `rgba(14,165,233,0.20)` line. Numbers in 28px circles, `rgba(14,165,233,0.10)` bg, `#38BDF8` text, JetBrains Mono.
1. **Search** — by vehicle, plate, OEM or VIN
2. **Compare** — every supplier's price, shipping and stock in one view
3. **Order** — pay once on AutoSpareFinder, in shekels
4. **Receive** — we buy from the supplier and ship it to you, tracked

> Numbering is justified here: this is a genuine sequence, which is the only condition under which numbered markers are legitimate.

**Right — Top Categories**
- Heading `Top Categories` + `View all →` link, right-aligned (inline-end).
- Grid: 5 cols desktop / 3 `md` / 2 `xs`, 16px gap. **10 category cards + 1 "More categories" card.**
- Card: `#151B27`, border `rgba(148,163,184,0.12)`, radius 12px, padding 20px, centered. Hover → border `rgba(14,165,233,0.20)`, `translateY(-2px)`, glow.
- Card anatomy: **animated SVG glyph (48px)** → name 13.5px/600 `#E2E8F0` → count 11px `#475569` JetBrains Mono tabular.
- **Data is dynamic, never hardcoded.** See §5.

### S5 — Popular in Israel
- Heading + sub: *"Ranked by vehicles registered on Israeli roads"*.
- 4 cards, desktop 4-up; **mobile = horizontal snap-scroll carousel** (`scroll-snap-type: x mandatory`), not a stack.
- Card: vehicle image (distinct per model — **four different images, not one silhouette repeated**), name 15px/700 white, year range 12px `#94A3B8`, registry count 11px `#475569` JetBrains Mono, `Find parts →` 12.5px `#0EA5E9` (arrow gets `rtl:rotate-180`).
- Data from `harvest_queue.il_vehicle_count` (verified live 2026-10-05): Toyota Corolla 132,079 · Kia Picanto 125,267 · Mazda 3 94,708 · Kia Sportage 91,152.

### S6 — Price comparison proof
The differentiator. One real part, three supplier offers.

- Card `#151B27`, radius 12px, `overflow: hidden`.
- **Header row:** part name 15.5px/700 white · OEM in JetBrains Mono `#38BDF8` wrapped in `<bdi dir="ltr">` · fitment badge inline-end.
- **Per-row fitment badge is mandatory** (fixes defect #14): each supplier row carries its own `✅ Fits` / `⚠️ Unverified` chip as the **first scannable element**, per MASTER.md §14.
- Columns: Supplier · Condition (`OEM` / `OE-equivalent` / `Aftermarket`) · Shipping · Availability · **Total** · Select.
- Price `#38BDF8`/800/tabular-nums, inside `<bdi dir="ltr">`. Lowest total gets a `LOWEST` chip (`#38BDF8` bg, `#0F1218` text, radius 4px).
- Availability: colored dot + text (`#22C55E` / `#F59E0B` / `#EF4444`).
- Footnote 11.5px `#475569`: *"OEM, OE-equivalent and aftermarket offers are listed together and always labelled — never hidden or substituted."*
- **Supplier identity must be masked** — render `Marketplace A` / `EU Distributor` style labels. Real names are forbidden customer-side (CLAUDE.md §5, `_mask_supplier`). Never show cost or margin.
- **Mobile: the table becomes stacked cards**, one per supplier — never a horizontally scrolling table.

### S7 — AI assistant
- Card: `linear-gradient(180deg, rgba(14,165,233,0.08), rgba(14,165,233,0.02))`, border `rgba(14,165,233,0.20)`, `box-shadow: 0 0 24px rgba(14,165,233,0.40)`, radius 16px, padding 44px. **This is the only glowing element on the page** (MASTER.md §2.8).
- 2 columns desktop, stacked mobile.
- Left: label `AI-POWERED` 10.5px/800/0.14em `#38BDF8` with `text-shadow: 0 0 12px rgba(56,189,248,0.65)` + Lucide `Sparkles`; H2 30px/800 *"Ask in Hebrew, Arabic or English"*; body; buttons `Start chat →` (`.btn-primary`) and `WhatsApp` (`.btn-secondary`).
- Right: three chat bubbles proving tri-lingual fitment — user `#252D3D`, AI `rgba(14,165,233,0.10)` + border + soft glow. Hebrew content, `dir="rtl"` on the bubbles themselves.
- **No robot mascot.** The reference's cartoon robot is generic AI slop and is rejected.

### S8 — Footer
`#0A0E17`, 4 columns (brand + Marketplace + Support + Company), social icon buttons 34px, bottom bar with `© 2026 AutoSpareFinder` and a **functional** language switcher (EN / עברית / العربية) that sets `lang` + `dir` on `<html>`.

---

## 5. Dynamic category system

The category grid must consume live data. **No hardcoded counts** (MASTER.md §15).

### Data contract
```
GET /api/v1/parts/categories?limit=10
→ [{ slug, name_en, name_he, name_ar, count }]
```
`parts_catalog.category` holds **English slugs** plus `כללי` (CLAUDE.md §9). Display names come from `category_map.DISPLAY` and are **never** read from the column.

> **Caching is mandatory.** This is a `SELECT … GROUP BY` over 4.1M+ rows. It MUST use the 10-minute in-process cache + `asyncio.Lock` single-flight pattern already used by `/parts/manufacturers`. A cold-cache stampede on this endpoint previously took the box down (CLAUDE.md §5). If the endpoint does not yet exist, it is a backend task — **not** a reason to hardcode counts.

### Live top-10 (verified 2026-10-05, for design mockups only)
`body-exterior` 905,751 · `electrical` 397,732 · `interior-comfort` 357,120 · `engine` 324,093 · `suspension-steering` 303,122 · `air-conditioning-heating` 224,912 · `wheels-bearings` 218,514 · `brakes` 171,387 · `cooling` 143,325 · `fuel-air` 141,076

### Glyph mapping
Each slug maps to one inline SVG glyph in a registry (`categoryGlyphs.jsx`). Unknown slug → neutral gear glyph. Adding a category requires no layout change.

### Loading & empty states
- Loading: 11 skeleton cards, `#1E2535` pulse (never white), `aria-busy="true"`.
- Failure: render the grid with names and **omit counts** — never invent a number, never show `0`.

---

## 6. Motion strategy

Derived from `brand/MOTION.md` (binding) and grounded in logo elements per its opening rule: *"Every animation originates from a logo element. Nothing is decorative."*

### Easings (from MOTION.md, use verbatim)
```css
--ease-gear:    cubic-bezier(0.25, 0.46, 0.45, 0.94);  /* weighted, mechanical */
--ease-default: cubic-bezier(0.4, 0, 0.2, 1);
--ease-scan:    cubic-bezier(0.45, 0, 0.55, 1);
--ease-flow:    linear;
```

### Category glyph motion
Each glyph is an inline SVG with exactly **one** animated `<g>`. Mechanism mirrors the real part:

| Category | Animated element | Motion | Timing |
|---|---|---|---|
| brakes | disc | `rotate(360deg)` | 9s `--ease-flow` |
| engine | piston group | `translateY(±2px)` | 1.4s `--ease-gear` alternate |
| suspension-steering | coil | `scaleY(0.94↔1)` | 1.6s `--ease-gear` alternate |
| cooling | fan blades | `rotate(360deg)` | 2.4s `--ease-flow` |
| electrical | pulse dot on a path | `offset-distance 0→100%` | 1.8s `--ease-flow` |
| fuel-air | flow arrows | `translateX` + fade | 2.2s `--ease-flow` |
| wheels-bearings | bearing ring | `rotate(360deg)` | 7s `--ease-flow` |
| filters | pleat ring | `scale(1↔1.03)` | 2s `--ease-scan` alternate |
| body-exterior | sheen band | gradient `translateX(-120%→120%)` | 2.6s `--ease-scan` |
| interior-comfort | seat recline | `rotate(±1.5deg)` | 2.4s `--ease-gear` alternate |
| air-conditioning-heating | vent louvres | `translateY` stagger | 2s `--ease-gear` alternate |

**Governing rules — these keep it premium rather than gimmicky:**
1. **Desktop:** motion runs on `:hover` / `:focus-within` only. **At most one card animates at a time** (the pointer enforces this naturally). No ambient looping grid.
2. **Mobile/touch:** no hover. Each glyph plays **once** when it first enters the viewport (IntersectionObserver, `threshold: 0.4`), then rests. Never loops on battery.
3. **Transform and opacity only.** Never animate width/height/top/left/margin — no layout thrash, no CLS.
4. Rotation is **clockwise** (MOTION.md: gear turning forward).
5. Continuous rotations are **slow** (7–9s). Fast spin reads as a loading spinner and is wrong here.
6. Each glyph has a deliberate **rest frame** that looks correct frozen.
7. Total animated SVG payload budget: **≤24KB** inline for all 11 glyphs.

### Page-level motion (restrained)
| Moment | Motion | Duration |
|---|---|---|
| Section enter | fade + `translateY(8px)` | 300ms `--ease-default`, once |
| Card hover | `translateY(-2px)` + border + glow | 200ms |
| Button hover | `brightness(1.08)` + glow | 200ms |
| Button press | `scale(0.98)` | 100ms |
| Input focus | ring `0 0 0 2px #0EA5E9` | 100ms |
| Search submit | lens-scan on the search icon | 1.5s `--ease-scan`, loop until response |
| AI card | **static** glow at rest; `glow-pulse` **only** while a response streams | 2s |
| Speed-line | `speed-line-extend` width 0→100% on first view | 600ms, once |
| Count-up | category counts ease-out to final value | 800ms |

**Forbidden** (MOTION.md §11 + MASTER.md): bounce, rubber-band, random-direction slide-ins, anything >400ms for a UI transition, parallax on the hero, auto-playing carousels, layout-shifting animation.

**Critical:** every section must be **fully visible and readable at rest**. Entrance animations start from a visible state — nothing may be left at `opacity: 0` waiting for an observer, or the page is blank for anyone whose observer never fires.

### Reduced motion — full strategy
Beyond the global suppression in MOTION.md §Accessibility, each animated thing needs a **meaningful static state** (MOTION.md's closing rule):

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```
Plus, explicitly:
| Element | Reduced-motion state |
|---|---|
| Category glyphs | render the rest frame; IntersectionObserver not attached at all |
| Section entrances | rendered visible, no transform |
| Speed-lines | drawn at full width immediately |
| AI glow | static `0 0 24px rgba(14,165,233,0.40)`, never pulsing |
| Count-up | final value rendered immediately |
| Search "scanning" | static lens icon + visible text label `Searching…` + `aria-busy="true"` |
| Skeletons | flat `#1E2535`, no shimmer |
| Vehicle carousel | `scroll-behavior: auto`, arrows still functional |

The preference must also be read in JS (`matchMedia('(prefers-reduced-motion: reduce)')`) so observers and timers are never attached, not merely visually suppressed.

---

## 7. Responsive specification

Breakpoints per MASTER.md §12 (Tailwind standard). **Mobile is designed, not collapsed.**

| Section | Desktop ≥1024 | Tablet 640–1023 | Mobile <640 |
|---|---|---|---|
| S0 utility | visible | hidden | hidden (contents move to footer + drawer) |
| S1 header | logo + search + nav + actions | logo + search + burger | logo + **search icon** + cart + burger |
| S2 hero | 2-col, art inline-end | 1-col, art below console | **headline → console → art**; art is a slim 16:9 band, never a tall block pushing content down |
| S2 modes | 4 tabs inline | 4 tabs inline | horizontal **snap-scroll chips**, 44px tall |
| S3 trust | 5 cols | 3 cols | 2 cols (1 below 360) |
| S4 band | `340px \| 1fr` sticky rail | stacked, rail first | stacked; How It Works becomes a 4-step vertical list |
| S4 categories | 5 cols | 3 cols | 2 cols |
| S5 vehicles | 4-up grid | 2-up | **snap-scroll carousel** |
| S6 compare | table | table | **stacked cards, one per supplier** |
| S7 AI | 2-col | stacked | stacked, chat bubbles above fold of the card |
| S8 footer | 4 cols | 2 cols | 1 col, accordions |

**Mobile-specific requirements**
- Header search icon opens a **full-screen search sheet** with the 4 modes — not a cramped inline input.
- **Sticky bottom CTA bar** (`Search parts`, 56px) appears after the hero scrolls out. It is the thumb-reachable primary action.
- Bottom padding `pb-24` on the page so the sticky bar and the floating WhatsApp button never overlap content (MASTER.md §12 flags this existing bug).
- All touch targets ≥44×44px (WCAG 2.5.5).
- Side gutter 16px, set once on one wrapper via `padding-inline`.
- **No horizontal page scroll at any width down to 320px.** Only the vehicle carousel and (tablet+) the comparison table may scroll, each in its own `overflow-x: auto` container with `min-width: 0` on the grid child.

---

## 8. RTL specification

RTL is first-class (MASTER.md §2.5), not a mirrored afterthought.

1. **`dir` and `lang` on `<html>`, driven by i18n.** `index.html` currently hardcodes `lang="en"` — MASTER.md §15 lists this as an action item and it must be fixed as part of this work.
2. **CSS logical properties only** — `margin-inline`, `padding-inline`, `inset-inline-start/end`, `border-start-*`. No `left`/`right` in new code.
3. **`<bdi dir="ltr">` around every price, OEM, VIN, SKU and plate.** Mandatory (MASTER.md §13). The CSS `unicode-bidi` shim used in the rejected concept is not sufficient.
4. **Numerals stay Western Arabic** (0-9) with `font-variant-numeric: tabular-nums`, in all three locales.
5. **Directional icons mirror** — arrows, chevrons, `Find parts →` get `rtl:rotate-180`. **Do not mirror:** the lens/search icon, the logo, the WhatsApp mark, part photography (except the hero cluster, below).
6. **Hero cluster** gets `transform: scaleX(-1)` in RTL so the rim-light continues to fall away from the text block.
7. **Speed-line gradient is symmetrical** — no RTL change needed.
8. **Search console field order reverses naturally** via flex/grid. Verify Make/Model/Year reads right-to-left as Make first.
9. **Snap-scroll carousels** must start at the inline-start edge in RTL (`scroll-padding-inline-start`); verify the first card is Corolla in both directions.
10. **Hebrew/Arabic copy is authored, not machine-translated**, and the hero H1 must be hand-set in all three languages — the Hebrew line break will not match the English one.
11. Verify at **1440 and 390, in both directions** — four viewport/direction combinations minimum.

---

## 9. Accessibility requirements

- Contrast per MASTER.md §13. **`#475569` on `#0F1218` is 2.9:1 and fails AA for body text** — it is permitted only for non-essential metadata (category counts, footnotes), never for instructions, labels or placeholders that carry meaning. Where a placeholder conveys required information, use `#94A3B8`.
- `:focus-visible` ring `0 0 0 2px #0F1218, 0 0 0 4px #0EA5E9` on every interactive element.
- Search modes: `role="tablist"` / `role="tab"` / `aria-selected`, arrow-key navigation between tabs.
- Every icon-only control has `aria-label`. Every input has a `<label>` or `aria-label`.
- Loading states: `role="status"` + `aria-busy` + a text label, not motion alone.
- Tab order follows visual order in **both** directions.
- Semantic landmarks: `<header>`, `<nav>`, `<main>`, `<footer>`; one `<h1>`.

---

## 10. Acceptance criteria

Implementation is complete only when all of these pass:

**Structure**
- [ ] All 9 bands (S0–S8) render at every breakpoint
- [ ] Four search modes present and switchable, including **Plate**
- [ ] Persistent header search present and functional at all scroll positions

**Design system**
- [ ] **Zero emoji** used as interface icons — Lucide only (`grep` the diff)
- [ ] Zero forbidden colors: `#2563eb`, `#1d4ed8`, `#f4f7fd`, `#f8fafc`, `#00ccff`, `#00A3FF`, `bg-white`
- [ ] All buttons `border-radius: 6px`, dark `#0F1218` text on the blue gradient
- [ ] AI glow appears on **exactly one** element
- [ ] Inter + JetBrains Mono loaded; JetBrains Mono added to `index.html`

**Data**
- [ ] Category counts come from a cached API, not markup
- [ ] Category endpoint uses the 10-min cache + single-flight lock
- [ ] No supplier name, raw cost, margin or `base_price` is customer-visible
- [ ] Count failure degrades to names-without-counts, never `0`

**Responsive**
- [ ] No horizontal page scroll at 1440, 1280, 1024, 834, 768, 390, 360, 320
- [ ] Mobile uses the search sheet, snap carousel, stacked compare cards and sticky CTA
- [ ] Touch targets ≥44px

**RTL**
- [ ] `dir`/`lang` set dynamically on `<html>`
- [ ] Prices/OEM/VIN wrapped in `<bdi dir="ltr">`
- [ ] Verified at 1440 and 390 in both LTR and RTL

**Motion**
- [ ] Every section fully readable at rest with JS disabled
- [ ] Category motion: hover-only on desktop, once-on-view on touch
- [ ] `prefers-reduced-motion` verified — observers not attached, static states correct
- [ ] No animation of layout properties; CLS ≈ 0

**Performance**
- [ ] Hero cluster is the LCP element, preloaded, `fetchpriority="high"`, LCP < 2.5s
- [ ] Inline SVG glyph payload ≤24KB total

---

## 11. Open items for the owner

1. **Hero parts-cluster asset** — needs a real photographic/3D render. The interim composite of existing Stitch part renders is acceptable for preview but not for production.
2. **Four distinct vehicle images** for S5 (Corolla, Picanto, Mazda 3, Sportage).
3. **`GET /api/v1/parts/categories`** — confirm whether it exists; if not it is a backend task before implementation.
4. **Hebrew and Arabic hero copy** — needs authoring, not translation.

---

## 12. Status

**Design phase complete. Not implemented.** `LandingPage.jsx` is untouched. Next phase is a browser-rendered preview of this direction for owner approval; implementation follows approval only.
