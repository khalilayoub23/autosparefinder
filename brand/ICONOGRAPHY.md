# AutoSpare Iconography System

> Icon construction rules derived entirely from logo geometry.
> Every icon must look like it was drawn by the same hand that drew the logo.

---

## Construction Rules

### Grid
All icons are drawn on a **24×24px grid** with a **2px safe margin** on all sides,
giving a **20×20 active area**. This matches the logo's proportional margins
(the gear ring has ~10% padding from the outer edge).

```
┌─────────────────────────────┐  24px
│  ╔═══════════════════════╗  │  ← 2px margin
│  ║                       ║  │
│  ║    20px active area   ║  │
│  ║                       ║  │
│  ╚═══════════════════════╝  │  ← 2px margin
└─────────────────────────────┘
```

### Stroke
- **Weight**: 1.5px — derived from gear ring stroke ratio (~3% of 48px diameter)
- **Cap**: `round` — matches logo curve terminations
- **Join**: `round` — no sharp miter joins; gear teeth have chamfered corners
- **Color**: always `currentColor` — inherits from component context

### Corner Radius
- Inner corners: **2px** (from gear tooth chamfer)
- Path curves: prefer arcs over sharp bezier corners
- Never use right angles without a 2px radius

### Style
- **Outline only** — no filled icons in the primary set
- Exception: status icons (success checkmark, error X) may use filled circle + inverted stroke
- Duotone variant: second layer at 20% opacity for premium components

### Perspective
Icons are strictly **front-facing or isometric-flat** — matching the logo's
direct, un-rotated geometry. No 3D perspective distortion.

---

## Icon Categories

### Search & Discovery (Magnifying Glass family)
Derived from the logo lens.

| Icon | Description | Key Geometry |
|------|-------------|--------------|
| `search` | Standard search | Circle + handle at 135° |
| `search-ai` | AI-powered search | Standard + pulse ring |
| `scan` | VIN/barcode scan | Rectangle + corner marks |
| `match` | Compatibility match | Two overlapping circles |
| `cross-ref` | Cross-reference | Two arrows in a circle |
| `verify` | Verification | Search + checkmark |
| `discover` | Discovery mode | Search + sparkle |

### Navigation & Structure
| Icon | Description |
|------|-------------|
| `home` | Base grid, not a house |
| `dashboard` | 4 equal rectangles |
| `catalog` | Stacked horizontal bars |
| `marketplace` | Grid of dots |
| `orders` | Receipt with lines |
| `inventory` | 3 stacked boxes |
| `warehouse` | Building outline |
| `reports` | Bar chart + line |
| `analytics` | Rising line chart |
| `settings` | Gear (single ring, 8 teeth) |
| `api` | `</>` brackets |

### Automotive
| Icon | Description |
|------|-------------|
| `vehicle` | Side-profile car outline |
| `vin` | Barcode + letters |
| `oem` | Part with number label |
| `engine` | Block with 4 cylinders |
| `brake` | Disc + caliper outline |
| `wheel` | Circle with 5 spokes |
| `battery` | Rect with +/- poles |
| `filter` | Cylinder with ridges |
| `suspension` | Spring coil |
| `exhaust` | Pipe with flow |

### AI & Intelligence
| Icon | Description |
|------|-------------|
| `ai` | Spark/lightning in circle |
| `ai-search` | Lens + neural dot |
| `recommend` | Star in lens |
| `confidence` | Signal bars |
| `nlp` | Chat bubble + circuit |
| `price-intel` | Chart + AI spark |

### Status & Feedback
| Icon | Description |
|------|-------------|
| `check` | Simple check, 1.5px stroke |
| `check-circle` | Filled circle, inverted check |
| `x` | × at 45° |
| `x-circle` | Filled circle, inverted × |
| `warning` | Triangle + ! |
| `info` | Circle + i |
| `loading` | Concentric arc (3 rings) |

### Supplier & Commerce
| Icon | Description |
|------|-------------|
| `supplier` | Factory outline |
| `price` | Tag with $ or ₪ |
| `shipping` | Truck outline |
| `tracking` | Map pin + path |
| `invoice` | Document + $ |
| `quote` | Document + ? |
| `compare` | Two columns |
| `cart` | Shopping cart |
| `checkout` | Cart + arrow |

---

## Sizes

| Size | px | Usage |
|------|----|-------|
| `icon-xs` | 12 | Inline text icons, table indicators |
| `icon-sm` | 16 | Button icons, input icons |
| `icon-md` | 20 | Default — nav, cards |
| `icon-lg` | 24 | Feature icons, section headings |
| `icon-xl` | 32 | Empty states, illustrations |
| `icon-2xl` | 48 | Hero icons, onboarding |

---

## Color Rules

| Context | Icon Color |
|---------|-----------|
| Default | `--color-metal-400` (#94A3B8) |
| Active / selected | `--color-primary-500` (#0EA5E9) |
| Hover | `--color-metal-200` (#E2E8F0) |
| Disabled | `--color-metal-600` (#475569) |
| AI context | `--color-primary-400` (#38BDF8) |
| Success | `--color-success` (#22C55E) |
| Error | `--color-error` (#EF4444) |
| Warning | `--color-warning` (#F59E0B) |

---

## Forbidden Patterns

- No two-tone icons outside the duotone variant set
- No drop shadows on icons
- No icon with stroke width above 2px or below 1px
- No filled non-status icons
- No 3D/perspective icons
- No pixel-art or raster-style icons
- Gear-shaped settings icon: use single ring with 8 evenly-spaced teeth only
  (never a 4-tooth or 6-tooth gear — the logo has more)

---

## React Component Interface

```tsx
// Icon component contract
interface IconProps {
  name: IconName;           // keyof icon registry
  size?: 'xs'|'sm'|'md'|'lg'|'xl'|'2xl';
  color?: string;           // defaults to currentColor
  className?: string;
  'aria-label'?: string;    // required for standalone icons
  'aria-hidden'?: boolean;  // use when icon is decorative
}

// Usage
<Icon name="search-ai" size="lg" aria-label="AI-powered search" />
<Icon name="check-circle" size="sm" color="var(--color-success)" aria-hidden />
```
