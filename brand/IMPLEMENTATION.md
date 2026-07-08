# AutoSpare Implementation Guide

> How to apply the design system in React + Tailwind. One source of truth.

---

## Project Setup

### 1. Install fonts
```bash
npm install @fontsource/inter @fontsource/jetbrains-mono
```
```tsx
// app/layout.tsx or _app.tsx
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import '@fontsource/inter/800.css';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/500.css';
```

### 2. Load CSS tokens
```css
/* globals.css — import first, before Tailwind */
@import './brand/tokens.css';   /* paste TOKENS.md CSS block here */
```

### 3. Tailwind config
```js
// tailwind.config.js — paste from TOKENS.md Tailwind Config section
```

---

## Component Patterns

### Search Bar (Primary CTA)
```tsx
function SearchBar() {
  const [focused, setFocused] = useState(false);
  const [searching, setSearching] = useState(false);

  return (
    <div
      className={`
        flex items-center gap-3 px-4
        bg-bg-surface-2 rounded-lg
        border transition-all duration-200
        ${focused
          ? 'border-primary-500 shadow-glow-focus'
          : 'border-metal-800 hover:border-metal-600'}
        h-14
      `}
    >
      <Icon
        name={searching ? 'search-ai' : 'search'}
        size="md"
        className={`
          text-metal-400 transition-colors duration-200
          ${searching ? 'text-primary-400 animate-scan-pulse' : ''}
        `}
      />
      <input
        className="flex-1 bg-transparent text-white placeholder-metal-600
                   font-medium text-base outline-none"
        placeholder="Search by part name, OEM number, or describe your vehicle..."
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
      />
      <div className="flex gap-2">
        <Button variant="ai" size="sm">Ask AI</Button>
        <Button variant="ghost" size="sm">
          <Icon name="scan" size="sm" />
          VIN
        </Button>
      </div>
    </div>
  );
}
```

### Part Card
```tsx
function PartCard({ part }: { part: Part }) {
  return (
    <div className="
      group relative
      bg-bg-surface border border-metal-800
      rounded-lg p-6
      transition-all duration-200
      hover:-translate-y-0.5 hover:shadow-z3 hover:shadow-glow-hover
      hover:border-primary-700
    ">
      {part.ai_match && (
        <span className="absolute top-3 right-3
                         bg-primary-900 border border-primary-700
                         text-primary-400 text-xs font-semibold
                         tracking-badge uppercase px-2 py-0.5 rounded-full">
          AI Match
        </span>
      )}

      <div className="flex justify-between items-start mb-3">
        <div>
          <h3 className="text-white font-semibold text-base">{part.name}</h3>
          <p className="text-metal-400 text-sm mt-0.5">
            {part.manufacturer} · {part.category}
          </p>
        </div>
        <PartTypeTag type={part.part_condition} />
      </div>

      <code className="text-primary-400 font-mono text-sm tracking-wide">
        {part.oem_number}
      </code>

      <div className="mt-4 flex items-center justify-between">
        <div>
          <span className="text-white font-bold text-xl tabular-nums">
            ₪ {part.base_price.toLocaleString()}
          </span>
          <span className="text-metal-500 text-xs ml-2">
            Max ₪ {part.max_price_ils?.toLocaleString()}
          </span>
        </div>
        <Button variant="secondary" size="sm">
          Compare Prices
        </Button>
      </div>
    </div>
  );
}
```

### AI Container
```tsx
function AIAssistant() {
  const [thinking, setThinking] = useState(false);

  return (
    <div className={`
      rounded-lg p-6
      bg-gradient-to-br from-primary-900/20 to-primary-900/5
      border border-primary-700/60
      transition-all duration-300
      ${thinking ? 'shadow-glow-scan animate-glow-pulse' : 'shadow-glow-ai'}
    `}>
      <div className="flex items-center gap-2 mb-4">
        <Icon name="ai" size="md" className="text-primary-400" />
        <span className="text-primary-400 font-semibold text-sm tracking-badge uppercase">
          AutoSpare AI
        </span>
        {thinking && (
          <span className="text-metal-400 text-xs animate-pulse">
            Searching 4.1M parts...
          </span>
        )}
      </div>
      {/* Chat messages */}
    </div>
  );
}
```

### OEM / VIN Display
```tsx
// Always mono, always blue-link color
function OEMNumber({ value }: { value: string }) {
  return (
    <code className="font-mono text-primary-400 text-sm tracking-wide">
      {value}
    </code>
  );
}

function VINDisplay({ vin }: { vin: string }) {
  return (
    <code className="font-mono text-metal-200 text-base tracking-widest
                     bg-bg-surface-2 px-3 py-1.5 rounded border border-metal-700">
      {vin}
    </code>
  );
}
```

---

## File Structure (Next.js)

```
src/
├── app/                    — Next.js 14 app router
├── components/
│   ├── brand/
│   │   ├── Logo.tsx
│   │   ├── Icon.tsx
│   │   └── GlowRing.tsx
│   ├── search/
│   │   ├── SearchBar.tsx
│   │   ├── SearchResults.tsx
│   │   └── VINScanner.tsx
│   ├── parts/
│   │   ├── PartCard.tsx
│   │   ├── PartTable.tsx
│   │   └── PriceCompare.tsx
│   ├── ai/
│   │   ├── AIAssistant.tsx
│   │   └── AIBadge.tsx
│   ├── ui/
│   │   ├── Button.tsx
│   │   ├── Input.tsx
│   │   ├── Badge.tsx
│   │   ├── Card.tsx
│   │   ├── Table.tsx
│   │   ├── Select.tsx
│   │   ├── Dialog.tsx
│   │   ├── Toast.tsx
│   │   └── Skeleton.tsx
│   ├── layout/
│   │   ├── TopNav.tsx
│   │   ├── Sidebar.tsx
│   │   └── PageHeader.tsx
│   └── vehicle/
│       ├── VehicleSelector.tsx
│       └── VehicleSilhouette.tsx
├── styles/
│   ├── globals.css         — token imports + base resets
│   └── animations.css      — keyframe definitions from MOTION.md
└── lib/
    └── brand/
        └── tokens.ts       — typed token exports for JS use
```

---

## Figma Structure

```
AutoSpare Design System (Figma File)
├── 🎨 Foundations
│   ├── Colors
│   ├── Typography
│   ├── Spacing & Grid
│   ├── Shadows & Glow
│   └── Logo & Brand
├── 🔤 Icons
│   ├── Search family
│   ├── Automotive
│   ├── AI
│   ├── Navigation
│   └── Status
├── 🧩 Components
│   ├── Inputs
│   ├── Buttons
│   ├── Cards
│   ├── Tables
│   ├── Navigation
│   ├── Badges & Tags
│   ├── Dialogs
│   └── AI Components
├── 📱 Templates
│   ├── Homepage
│   ├── Search Results
│   ├── Part Detail
│   ├── Dashboard
│   ├── Supplier Portal
│   └── Mobile App
└── 📐 Specs
    └── Redlines & annotations
```

---

## QA Checklist (Before Any UI Shipping)

### Visual
- [ ] Dark background everywhere (no white pages unless light-mode forced)
- [ ] All interactive elements have focus-visible state (`shadow-glow-focus`)
- [ ] No blue glow on non-AI, non-active elements
- [ ] OEM/VIN numbers always in `font-mono`, `text-primary-400`
- [ ] Supplier names masked from customer views
- [ ] Part condition badges always lowercase in DB; display casing via CSS

### Accessibility
- [ ] All icons used standalone have `aria-label`
- [ ] Color contrast ≥ 4.5:1 for all body text
- [ ] No information conveyed by color alone (always pair with icon or text)
- [ ] `prefers-reduced-motion` respected (animations disabled)
- [ ] All interactive elements reachable by keyboard

### Performance
- [ ] No animation that triggers layout reflow (use `transform` + `opacity` only)
- [ ] Skeleton loaders present before any async data
- [ ] Images have `width` + `height` to prevent CLS
- [ ] OEM/VIN columns use `font-variant-numeric: tabular-nums`

### Data
- [ ] Prices display with `toLocaleString()` for thousands separator
- [ ] OEM numbers display normalized (no extra spaces)
- [ ] Empty states present when lists return 0 results
- [ ] Error states present for all async operations
