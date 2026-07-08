# AutoSpare Logo DNA Analysis

> The logo is not decoration. It is the blueprint. Every visual rule in this system
> is derived from an element that already exists in the logo.

---

## 1. Geometric Breakdown

### 1.1 The Outer Gear Ring
**What it is:** A circular frame with radial notches/teeth at the top and bottom,
resembling a steering wheel crossed with a precision gear.

**Why it exists:** Automotive precision + mechanical trust. Gears = engineered,
reliable, exact. The ring is also a lens frame — it frames the subject (the car),
just as the platform frames and contains automotive knowledge.

**Design rules derived:**
- All card containers use a `border` derived from this ring's stroke weight
- Circular progress indicators rotate like this ring
- Avatar containers, badges, and status rings share its proportions
- `border-radius` on sharp components comes from the chamfer of the teeth

---

### 1.2 Concentric Metallic Rings
**What it is:** Multiple rings of increasing diameter, stacked with depth and
metallic shading, creating a 3D cylinder/barrel effect.

**Why it exists:** Depth + precision. Like a rifle scope or a telescope — tools
of precise targeting and discovery. Also maps to the concept of "zooming in"
on the right part from a universe of millions.

**Design rules derived:**
- **Elevation system**: each z-level adds one visual ring — shadow depth corresponds
  to ring depth. z1=subtle, z2=card, z3=modal, z4=overlay, z5=tooltip
- **Loading rings**: animated concentric ring spinner
- **Radar/scan animations**: pulse outward from center
- **Dashboard widget borders**: double-ring border for premium widgets
- **Focus rings**: 2px solid primary + 2px gap, simulating a ring around the focused element

---

### 1.3 The Magnifying Glass (Search Lens)
**What it is:** A metallic magnifying glass overlaid at the bottom-right of
the gear ring, partially overlapping the vehicle.

**Why it exists:** Search is the product. The platform's entire value is finding
the right part. The lens is the most important element in the logo after the ring.

**Design rules derived:**
- The search bar is the primary CTA on every page — not a button, not a hero text
- All search inputs display a lens icon in the left slot (not a placeholder text alone)
- AI scanning animations use a circular lens sweep over content
- Hover states on part cards trigger a subtle lens-glow effect
- The loading skeleton has a lens pulse animation
- Focus on any input = blue glow matching the lens halo
- "Searching" state = animated lens rotation (not a generic spinner)
- Product discovery = visual lens metaphor (zoom in, frame, confirm)

---

### 1.4 The Vehicle Silhouette
**What it is:** A classic sedan drawn in a technical illustration style —
clean stroke lines, side profile, simplified but detailed. Centered inside the rings.

**Why it exists:** The subject of the entire platform. The car is at the center
of everything — literally and conceptually.

**Design rules derived:**
- All vehicle illustrations use **side profile, 3/4 front angle, or top-down** — never
  arbitrary angles. Same perspective language as the logo.
- Stroke weight for vehicle illustrations: 1.5px (matches logo ratio)
- No photorealistic vehicles in UI — only technical/illustrated style
- Vehicle cards show the silhouette at 40% opacity as background layer
- Empty states use a minimal line-art vehicle illustration
- Vehicle selector component uses the silhouette as the selection indicator

---

### 1.5 Blue Halo / Glow
**What it is:** An electric blue circular glow emanating from behind the vehicle,
filling the inner ring space. Soft, radial, luminous.

**Why it exists:** The AI core. The platform's intelligence is invisible but
always present. The glow represents the system thinking, scanning, processing.

**Design rules derived:**
- Blue glow = AI is active / the system is processing your query
- `box-shadow: 0 0 24px rgba(14, 165, 233, 0.4)` on AI components
- Hero background: radial gradient from `#0EA5E9` at 8% opacity to transparent
- Active state on nav items: left border + subtle blue glow
- AI assistant container: always has the halo as background
- Meilisearch / vector search "thinking" state: pulsing halo animation
- The glow must NEVER appear on static, decorative, or non-interactive elements
- Glow intensity scales with confidence score (dim = uncertain, bright = exact match)

---

### 1.6 Horizontal Blue Speed Lines
**What it is:** Multiple parallel horizontal lines extending left and right
from the gear ring, in electric blue, suggesting speed, data, connectivity.

**Why it exists:** Global reach. Data in motion. API calls. Supplier connections.
The platform is a network platform — these lines are the network.

**Design rules derived:**
- Section dividers: thin horizontal rule with gradient from blue to transparent
- Loading bars: horizontal blue fill with shimmer sweep
- Data flow diagrams: horizontal connectors between nodes
- "Global network" hero graphic: speed lines with node dots
- Skeleton loaders: horizontal bands with animated shimmer
- Table row hover: left-border highlight + subtle horizontal line extends right
- Background texture for enterprise/supplier sections: fine parallel horizontal lines
  at 3% opacity
- API documentation pages: use speed line motif as section markers

---

### 1.7 Metallic / Chrome Effect
**What it is:** Gradient rendering on all metal elements — rings, text,
magnifying glass — creating a brushed chrome / polished steel appearance.

**Why it exists:** Premium engineering. Automotive grade. The platform handles
enterprise contracts — it must feel like it costs what it charges.

**Design rules derived:**
- Premium cards: `background: linear-gradient(135deg, #1e2535 0%, #252d3d 100%)`
  with `border: 1px solid rgba(148, 163, 184, 0.12)`
- Enterprise/supplier portal surfaces are slightly more metallic than customer portal
- Glossy highlight: `::after` pseudo with `linear-gradient(to bottom, rgba(255,255,255,0.05), transparent)`
  on card top edge
- Button metallic variant: silver gradient used for secondary actions
- Logo lockup on dark: full metallic treatment
- Logo lockup on light: flatten to single color (no gradient on light background)
- Never use real metallic textures in UI — simulate with gradient only

---

### 1.8 Typography in the Logo
**What it is:** "AutoSpare" in bold italic with metallic fill + "SPARE FINDER"
in a smaller all-caps badge underneath, on a blue rectangle.

**Why it exists:** Two-tier naming — product name + descriptor. The bold italic
suggests speed and confidence. The badge descriptor grounds it in function.

**Design rules derived:**
- Product name: always bold, never light weight
- Product descriptor tags/badges: all-caps, tight letter-spacing (0.12em)
- Hero headlines: bold italic allowed only for brand headlines; all other headlines
  are upright bold
- Blue badge convention: small all-caps labels on blue pill background for
  product categories, status indicators, feature tags

---

### 1.9 Color Palette (Extracted Directly)

| Role | Extracted | Hex | Usage |
|------|-----------|-----|-------|
| Background | Logo dark base | `#0F1218` | Page background |
| Surface | Ring shadow area | `#151B27` | Card background |
| Surface 2 | Lighter ring layer | `#1E2535` | Elevated card |
| Primary Blue | Halo core | `#0EA5E9` | Actions, AI, active |
| Blue Light | Halo edge | `#38BDF8` | Hover, highlights |
| Blue Dark | Halo deep | `#0369A1` | Pressed, deep shadow |
| Speed Blue | Horizontal lines | `#1D4ED8` | Network/data visuals |
| Metallic High | Chrome highlight | `#E2E8F0` | Metallic top edge |
| Metallic Mid | Chrome body | `#94A3B8` | Secondary text |
| Metallic Low | Chrome shadow | `#475569` | Muted elements |
| White | Logo text fill | `#FFFFFF` | Primary text |
| Gear Dark | Teeth shadow | `#0A0E17` | Deepest shadow |

---

### 1.10 Spatial Ratios and Proportions

Measured from the logo geometry:

| Token | Value | Source |
|-------|-------|--------|
| Ring stroke weight | ~3% of ring diameter | Outer gear ring border |
| Gear tooth depth | ~8% of ring radius | Notch depth on gear |
| Halo radius | 60% of ring radius | Blue inner glow extent |
| Lens handle angle | 135° from center | Magnifying glass rotation |
| Vehicle width | 75% of inner ring diameter | Car silhouette proportion |
| Speed line length | 2× ring diameter | Horizontal line extension |
| Speed line gap | ~5px per line | Spacing between parallel lines |

These ratios become spacing and sizing tokens (see `TOKENS.md`).

---

## 2. Psychological & Emotional Reading

| Element | Psychological Signal | Brand Perception |
|---------|---------------------|-----------------|
| Gear ring | Precision, engineering, trust | "This platform is built for professionals" |
| Magnifying glass | Discovery, clarity, intelligence | "It finds what you can't find yourself" |
| Blue halo | Technology, AI, energy | "There is intelligence behind every result" |
| Speed lines | Network, speed, global | "It's connected to everything, everywhere" |
| Metallic surface | Premium, durability, quality | "This is enterprise-grade, not a toy" |
| Dark background | Confidence, authority, depth | "It operates in the background — always on" |
| Centered vehicle | Purpose, focus, subject clarity | "Cars are at the center of everything we do" |

---

## 3. Transformation Map — Logo Element → Design Language

```
GEAR RING
    → card border
    → circular progress
    → avatar ring
    → step indicator
    → nav active ring

CONCENTRIC RINGS
    → elevation / z-index system
    → loading spinner
    → radar scan animation
    → modal depth layers

MAGNIFYING GLASS
    → search input design
    → AI scanning animation
    → hover zoom effect
    → discovery empty state
    → "no results" illustration

BLUE HALO
    → AI component glow
    → focus ring
    → hero background
    → active state
    → confidence indicator

SPEED LINES
    → section dividers
    → skeleton loaders
    → data flow diagrams
    → background texture
    → loading bars

METALLIC SURFACE
    → premium card surface
    → enterprise tier badge
    → button gradients
    → sidebar surface

VEHICLE SILHOUETTE
    → vehicle card illustration
    → selector state indicator
    → empty state art
    → 404 page illustration

DARK BACKGROUND
    → primary design mode
    → page background
    → all enterprise surfaces

TYPOGRAPHY BADGE
    → status pill
    → category tag
    → feature badge
    → supplier tier indicator
```

---

## 4. What NOT to Do (Anti-Patterns)

| Wrong | Why | Right |
|-------|-----|-------|
| Blue glow on decorative elements | Glow = AI active only | Reserve for active/AI states |
| Bright white background pages | Logo = dark; light mode is a variant | Dark-first; light as override |
| Multiple competing ring animations | One gear, one center — hierarchy matters | One dominant animation per view |
| Generic spinner (3 arc dots) | Logo has a specific ring language | Use concentric ring spinner |
| Photorealistic car images in UI | Logo = technical illustration style | Line-art / technical illustration |
| Random corner radii | Radii come from gear chamfer geometry | Use token system only |
| Speed lines as decoration | Lines = data/network meaning only | Never decorative; always semantic |
