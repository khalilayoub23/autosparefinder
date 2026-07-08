# AutoSpare Motion System

> Every animation originates from a logo element. Nothing is decorative.

---

## Core Animation Principles

| Principle | Rule | Source |
|-----------|------|--------|
| Weight | Gear motion = deliberate, not snappy | Gear ring mass |
| Precision | Animations complete fully — no interruption bounce | Engineering accuracy |
| Purpose | Every animation communicates state change | No decorative motion |
| Direction | Rotation = clockwise (gear turning forward) | Logo gear orientation |
| Glow | Only AI/active states glow — nothing else | Blue halo = AI |

---

## Easing Definitions

```css
/* Gear turn — weighted, decelerating */
--ease-gear: cubic-bezier(0.25, 0.46, 0.45, 0.94);

/* UI standard — Material-like */
--ease-default: cubic-bezier(0.4, 0, 0.2, 1);

/* Elastic settle — for gear lock-in animations */
--ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);

/* Data flow — linear, constant speed */
--ease-flow: linear;

/* Scan — slow in, slow out */
--ease-scan: cubic-bezier(0.45, 0, 0.55, 1);
```

---

## Animation Catalog

### 1. Gear Spin (Loading)
Used in: page load, background process running, system check.
```css
@keyframes gear-spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}
.gear-loading {
  animation: gear-spin 2s var(--ease-gear) infinite;
}
/* Inner ring rotates slower (like real gear system) */
.gear-loading .ring-inner {
  animation: gear-spin 3s var(--ease-gear) infinite reverse;
}
```

### 2. Lens Scan (AI Search)
Used in: search submission, AI processing, VIN scan.
```css
@keyframes lens-scan {
  0%   { transform: scale(1);    opacity: 1; }
  40%  { transform: scale(1.25); opacity: 0.8; }
  70%  { transform: scale(1.1);  opacity: 0.9; }
  100% { transform: scale(1);    opacity: 1; }
}
.lens-scanning {
  animation: lens-scan 1.5s var(--ease-scan) infinite;
}
```

### 3. Glow Pulse (AI Active)
Used in: AI assistant container, AI result cards, processing state.
```css
@keyframes glow-pulse {
  0%,100% { box-shadow: 0 0 24px rgba(14,165,233,0.40); }
  50%     { box-shadow: 0 0 48px rgba(14,165,233,0.70); }
}
.ai-active {
  animation: glow-pulse 2s var(--ease-scan) infinite;
}
```

### 4. Speed Line Shimmer (Loading / Skeleton)
Used in: skeleton loaders, data-fetching state, speed line backgrounds.
```css
@keyframes shimmer {
  0%   { background-position: -200% 0; }
  100% { background-position:  200% 0; }
}
.skeleton {
  background: linear-gradient(
    90deg,
    var(--color-bg-surface-2) 25%,
    rgba(14,165,233,0.15)     50%,
    var(--color-bg-surface-2) 75%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s var(--ease-flow) infinite;
}
```

### 5. Radar Sweep (Scanning / Discovery)
Used in: VIN lookup in progress, AI matching across catalog.
```css
@keyframes radar-sweep {
  0%   { transform: rotate(0deg);   opacity: 1; }
  100% { transform: rotate(360deg); opacity: 1; }
}
@keyframes radar-fade {
  0%   { opacity: 0.8; }
  100% { opacity: 0; }
}
/* The visual: a conic gradient that rotates */
.radar-container {
  background: conic-gradient(
    rgba(14,165,233,0.5) 0deg,
    transparent 60deg,
    transparent 360deg
  );
  animation: radar-sweep 2s var(--ease-flow) infinite;
  border-radius: 50%;
}
```

### 6. Speed Lines Appear (Data Flow)
Used in: connecting supplier nodes, API flow diagrams, network animations.
```css
@keyframes speed-line-extend {
  from { width: 0%;     opacity: 0; }
  to   { width: 100%;   opacity: 1; }
}
.speed-line {
  height: 1px;
  background: linear-gradient(90deg, transparent, #1D4ED8, #0EA5E9, #1D4ED8, transparent);
  animation: speed-line-extend 0.6s var(--ease-out) forwards;
}
```

### 7. Card Hover Lift
Used in: all interactive cards.
```css
.card {
  transition:
    transform    var(--duration-normal) var(--ease-out),
    box-shadow   var(--duration-normal) var(--ease-out),
    border-color var(--duration-normal) var(--ease-out);
}
.card:hover {
  transform:    translateY(-2px);
  box-shadow:   var(--shadow-z3), var(--glow-hover);
  border-color: var(--color-border-primary);
}
```

### 8. Focus Ring Appear
Used in: all focusable interactive elements.
```css
:focus-visible {
  outline: none;
  box-shadow: var(--glow-focus-ring);
  transition: box-shadow var(--duration-fast) var(--ease-out);
}
```

### 9. Page Transition
Used in: route changes in the app.
```css
@keyframes page-enter {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
.page-enter {
  animation: page-enter var(--duration-slow) var(--ease-out);
}
```

### 10. Toast Notification
Used in: success/error/info toasts.
```css
@keyframes toast-in {
  from {
    opacity: 0;
    transform: translateX(100%);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}
@keyframes toast-out {
  from {
    opacity: 1;
    transform: translateX(0);
  }
  to {
    opacity: 0;
    transform: translateX(100%);
  }
}
```

### 11. Number Count Up (Analytics)
Used in: dashboard KPI tiles on mount.
```css
/* JS implementation: countUp(from, to, duration, element) */
/* CSS easing: ease-out (front-weighted, slows to final value) */
/* Duration: 800ms for numbers < 10K; 1200ms for numbers > 1M */
```

---

## Animation State Map

| State | Animation | Duration |
|-------|-----------|----------|
| Page loading | Gear spin (full ring) | Loop |
| AI searching | Lens scan + glow pulse | Loop until done |
| AI result found | Glow brightens → settles | 300ms |
| Data fetching | Speed line shimmer (skeleton) | Loop |
| VIN scanning | Radar sweep | Loop until decoded |
| Card hover | Lift + glow hover | 200ms |
| Button click | Scale 0.98 | 100ms |
| Input focus | Glow focus ring | 100ms |
| Modal open | Fade + scale 0.95→1 | 200ms |
| Modal close | Fade + scale 1→0.95 | 150ms |
| Toast enter | Slide in from right | 300ms |
| Toast exit | Slide out right | 200ms |
| Sidebar collapse | Width 260→64 | 300ms gear ease |
| Route change | Fade + slide up 8px | 300ms |
| Number count | Count up, ease-out | 800–1200ms |
| Sync indicator | Rotating arc | Loop |

---

## Accessibility

```css
/* Respect user preference — no animations for reduced-motion users */
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration:   0.01ms !important;
    animation-iteration-count: 1  !important;
    transition-duration:  0.01ms !important;
  }
  .skeleton {
    animation: none;
    background: var(--color-bg-surface-2);
  }
  .gear-loading {
    animation: none;
    opacity: 0.5;
  }
}
```

**Rule:** Every animated loading state must also have a static fallback that
communicates the same state without motion (text label, opacity change, etc.).
