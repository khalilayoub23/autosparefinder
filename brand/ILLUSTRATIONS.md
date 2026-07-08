# AutoSpare Illustration Language

> All illustration rules derived from the vehicle silhouette and gear ring in the logo.
> Every illustration produced for AutoSpare must look like it belongs to the same family.

---

## Core Style: Technical Blueprint

The logo vehicle is drawn in a **technical illustration style** — not cartoon,
not photorealistic, not isometric 3D. It is a precision line drawing: clean strokes,
controlled curves, simplified but not naive.

Think: automotive engineering diagram meets premium editorial illustration.

### Style reference anchors
- Automotive patent drawings (USPTO technical vehicle illustrations)
- Bentley repair manual diagrams
- Porsche internal CAD exploded views
- NASA technical illustration style guide

---

## Construction Rules

### Stroke
- Weight: **1.5px** — identical to icon stroke (logo consistency)
- Cap: `round`
- Join: `round`
- Color: always single-color line art — never gradient strokes
- Primary color: `--color-metal-200` (#E2E8F0) on dark backgrounds
- Accent stroke: `--color-primary-500` (#0EA5E9) for highlighted parts or AI callouts

### Fill
- **No solid fills** on main subjects — line art only
- Exception: background planes use 4–8% opacity fills to suggest depth
- Accent fills: `rgba(14, 165, 233, 0.08)` for highlighted components

### Perspective
Three permitted perspectives — all derived from the logo vehicle angle:

| Perspective | Use case |
|-------------|----------|
| **Side profile** (exact logo angle) | Vehicle selector, part category headers |
| **3/4 front** (≈30° rotation from side) | Hero sections, feature banners |
| **Top-down / exploded** | Warehouse, inventory, parts diagram views |

**Never use**: rear-only view, extreme low-angle, fisheye, or random perspective.
All illustrations in the same view must share the same vanishing point.

### Simplification rules
- Remove detail that doesn't read at target size
- At 200px wide: 3 levels of detail (body, wheels, windows)
- At 400px wide: 5 levels (body, wheels, windows, lights, grille)
- At 800px+: full detail (body, wheels, windows, lights, grille, door lines, mirrors)
- Never add detail that wasn't in the logo vehicle (no texture mapping, no reflections)

---

## Illustration Categories

### 1. Vehicle Illustrations
Used in: vehicle selector, search results header, part detail page, empty states.

**Rules:**
- Side profile default
- Wheels use the gear ring proportions: thick outer ring, thin spokes
- No brand-specific styling — generic premium sedan/SUV/truck silhouette
- Vehicle type variants: sedan, SUV, pickup, van, hatchback, coupe
- Highlight version: specific part location highlighted in `--color-primary-500`
  with a pulsing dot + connecting line to part name

```
Example — Part Location Callout:
[Vehicle side profile]
         ↑
    [●]──────── Brake Pad (Front Axle)
    pulse dot    label in --color-primary-400
```

### 2. Parts Illustrations
Used in: part cards when no photo available, category icons at large scale, exploded diagrams.

**Rules:**
- Top-down or 3/4 perspective
- Same 1.5px stroke
- OEM number can be embedded as a label line (mono font, blue)
- Show mounting points as dashed lines
- Cross-section style for internal components (filters, bearings, pistons)

### 3. Empty States
Used in: zero search results, empty cart, empty order history, first-time screens.

**Template:**
```
[Illustration — 160px wide, centered]
[Heading — --text-xl, font-semibold]
[Subtext — --text-base, --color-text-muted]
[Optional CTA button]
```

| Empty state | Illustration | Heading |
|-------------|-------------|---------|
| No search results | Vehicle with magnifying glass, question mark | "No parts found" |
| Empty cart | Empty parts shelf | "Your order is empty" |
| No orders | Delivery truck, no packages | "No orders yet" |
| No suppliers | Factory outline, disconnected | "No suppliers connected" |
| 404 page | Vehicle with flat tyre | "This page doesn't exist" |
| 500 error | Engine with smoke | "Something went wrong" |
| Offline | Vehicle with no signal bars | "No connection" |

### 4. Hero / Feature Illustrations
Used in: landing page sections, feature banners, marketing materials.

**Rules:**
- Larger scale (400–800px)
- Layered composition: background speed lines + foreground vehicle/part
- Blue halo glow behind primary subject
- Can include abstract data elements: floating OEM number tags, price tags,
  network connection lines between parts

### 5. AI / Data Visualizations
Used in: AI assistant section, analytics pages, price intelligence feature.

**Style:**
- Abstract, geometric — no literal objects
- Radar sweep circle (from logo gear ring)
- Node-and-connection graphs (from speed lines)
- Glowing blue center point (from halo)
- Data points as small filled circles, `--color-primary-500`
- Connection lines as speed lines (horizontal preferred, diagonal allowed)

### 6. Warehouse / Logistics Illustrations
Used in: supplier portal, inventory pages, warehouse management section.

**Style:**
- Top-down or isometric flat (not 3D render)
- Shelf racks as grid of rectangles
- Parts as small labeled boxes
- Forklift, conveyor belt as line art
- Same stroke/color rules as vehicle illustrations

### 7. Global Network / API Illustrations
Used in: supplier network page, API docs, enterprise pitch.

**Style:**
- World map outline (minimal, wire-frame level)
- Node dots at supplier/market locations
- Speed lines connecting nodes (horizontal or arc)
- Blue halo glow on primary node (the platform center)
- Animated version: nodes light up sequentially

---

## Color Usage in Illustrations

| Element | Color | Token |
|---------|-------|-------|
| Main strokes | Light silver | `--color-metal-200` |
| Secondary/detail strokes | Medium metal | `--color-metal-400` |
| Background plane fills | Near-invisible | `rgba(148,163,184,0.05)` |
| Highlighted part | Electric blue | `--color-primary-500` |
| AI callout / pulse | Halo blue | `--color-primary-400` |
| Dashed mounting lines | Muted blue | `--color-primary-700` |
| OEM label text | Link blue | `--color-text-link` |
| Error state strokes | Red | `--color-error` |

---

## What NOT to Do

| Wrong | Why | Right |
|-------|-----|-------|
| Photorealistic renders | Logo = technical illustration, not photography | Line art only |
| Cartoon/flat color shapes | Too playful for enterprise platform | Stroke-based technical style |
| Random perspective angles | Logo sets the angle; consistency = brand | Side / 3/4 / top-down only |
| Gradient strokes | Logo strokes are uniform weight | Single-weight strokes |
| Brand-specific vehicles (BMW grille, VW logo) | Platform is brand-neutral | Generic vehicle silhouettes |
| Thick 3px+ strokes | Logo ratio = 1.5px | 1.5px always |
| Drop shadows on illustrations | Illustrations live on dark surfaces | Use glow, not shadow |
| Busy/complex scenes | Logo is clean, geometric, purposeful | Max 3 focal elements per illustration |
