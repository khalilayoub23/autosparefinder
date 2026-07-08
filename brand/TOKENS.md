# AutoSpare Design Token System

> Complete CSS custom properties + Tailwind mappings.
> Every token traces back to a logo element (see LOGO_DNA.md for sources).

---

## Full CSS Token Sheet

```css
:root {
  /* ─── COLORS — BACKGROUND ─────────────────────────────── */
  --color-bg-base:       #0F1218;
  --color-bg-surface:    #151B27;
  --color-bg-surface-2:  #1E2535;
  --color-bg-surface-3:  #252D3D;
  --color-bg-overlay:    rgba(10, 14, 23, 0.80);
  --color-bg-scrim:      rgba(10, 14, 23, 0.60);

  /* ─── COLORS — PRIMARY (BLUE HALO) ───────────────────── */
  --color-primary-50:    #F0F9FF;
  --color-primary-100:   #E0F2FE;
  --color-primary-200:   #BAE6FD;
  --color-primary-300:   #7DD3FC;
  --color-primary-400:   #38BDF8;
  --color-primary-500:   #0EA5E9;
  --color-primary-600:   #0284C7;
  --color-primary-700:   #0369A1;
  --color-primary-800:   #075985;
  --color-primary-900:   #0C4A6E;

  /* ─── COLORS — NETWORK (SPEED LINES) ─────────────────── */
  --color-network-400:   #60A5FA;
  --color-network-500:   #3B82F6;
  --color-network-600:   #2563EB;
  --color-network-700:   #1D4ED8;

  /* ─── COLORS — METALLIC (CHROME) ─────────────────────── */
  --color-metal-200:     #E2E8F0;
  --color-metal-300:     #CBD5E1;
  --color-metal-400:     #94A3B8;
  --color-metal-500:     #64748B;
  --color-metal-600:     #475569;
  --color-metal-700:     #334155;
  --color-metal-800:     #1E293B;

  /* ─── COLORS — TEXT ───────────────────────────────────── */
  --color-text-primary:  #FFFFFF;
  --color-text-secondary:#94A3B8;
  --color-text-muted:    #475569;
  --color-text-link:     #38BDF8;
  --color-text-ai:       #0EA5E9;

  /* ─── COLORS — STATUS ─────────────────────────────────── */
  --color-success:       #22C55E;
  --color-success-bg:    rgba(34, 197, 94, 0.10);
  --color-warning:       #F59E0B;
  --color-warning-bg:    rgba(245, 158, 11, 0.10);
  --color-error:         #EF4444;
  --color-error-bg:      rgba(239, 68, 68, 0.10);
  --color-info:          #0EA5E9;
  --color-info-bg:       rgba(14, 165, 233, 0.10);

  /* ─── COLORS — BORDERS ────────────────────────────────── */
  --color-border-subtle:  rgba(148, 163, 184, 0.08);
  --color-border-default: rgba(148, 163, 184, 0.12);
  --color-border-strong:  rgba(148, 163, 184, 0.20);
  --color-border-primary: rgba(14, 165, 233, 0.40);
  --color-border-ai:      rgba(14, 165, 233, 0.60);

  /* ─── SPACING (base-8 grid) ───────────────────────────── */
  /* Derived from gear ring proportions: 4px = minimum tooth gap */
  --space-0:    0px;
  --space-px:   1px;
  --space-0-5:  2px;
  --space-1:    4px;
  --space-1-5:  6px;
  --space-2:    8px;
  --space-3:    12px;
  --space-4:    16px;
  --space-5:    20px;
  --space-6:    24px;
  --space-8:    32px;
  --space-10:   40px;
  --space-12:   48px;
  --space-16:   64px;
  --space-20:   80px;
  --space-24:   96px;
  --space-32:   128px;
  --space-40:   160px;
  --space-48:   192px;

  /* ─── BORDER RADIUS ───────────────────────────────────── */
  /* Derived from gear tooth chamfer — base is 6px */
  --radius-none:   0px;
  --radius-sm:     4px;    /* Tight — badge, chip */
  --radius-base:   6px;    /* Default — button, input */
  --radius-md:     8px;    /* Card default */
  --radius-lg:     12px;   /* Large card, modal */
  --radius-xl:     16px;   /* Featured card */
  --radius-2xl:    24px;   /* Hero container */
  --radius-full:   9999px; /* Pill — badge, avatar, toggle */

  /* ─── BORDER WIDTH ────────────────────────────────────── */
  /* Gear ring stroke: ~3% of diameter. At card scale: 1px base */
  --border-thin:    1px;
  --border-base:    1px;
  --border-thick:   2px;   /* Focus, active ring */
  --border-heavy:   3px;   /* Premium tier indicator */

  /* ─── ELEVATION / SHADOWS ─────────────────────────────── */
  /* Concentric rings = depth layers */
  --shadow-z0:  none;
  --shadow-z1:  0 1px 3px rgba(0,0,0,0.40), 0 1px 2px rgba(0,0,0,0.30);
  --shadow-z2:  0 4px 6px rgba(0,0,0,0.40), 0 2px 4px rgba(0,0,0,0.30);
  --shadow-z3:  0 10px 15px rgba(0,0,0,0.50), 0 4px 6px rgba(0,0,0,0.30);
  --shadow-z4:  0 20px 25px rgba(0,0,0,0.60), 0 10px 10px rgba(0,0,0,0.30);
  --shadow-z5:  0 25px 50px rgba(0,0,0,0.70);

  /* ─── GLOW (BLUE HALO) ────────────────────────────────── */
  --glow-focus:       0 0 0 2px var(--color-primary-500);
  --glow-focus-ring:  0 0 0 2px #0F1218, 0 0 0 4px var(--color-primary-500);
  --glow-ai:          0 0 24px rgba(14, 165, 233, 0.40);
  --glow-ai-strong:   0 0 48px rgba(14, 165, 233, 0.60);
  --glow-hover:       0 0 16px rgba(14, 165, 233, 0.20);
  --glow-scan:        0 0 32px rgba(14, 165, 233, 0.50);
  --glow-premium:     0 0 24px rgba(148, 163, 184, 0.10);

  /* ─── TYPOGRAPHY ──────────────────────────────────────── */
  --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  --font-mono: 'JetBrains Mono', 'Fira Code', monospace;

  --text-xs:   0.75rem;
  --text-sm:   0.875rem;
  --text-base: 1rem;
  --text-lg:   1.125rem;
  --text-xl:   1.25rem;
  --text-2xl:  1.5rem;
  --text-3xl:  1.875rem;
  --text-4xl:  2.25rem;
  --text-5xl:  3rem;
  --text-6xl:  3.75rem;
  --text-7xl:  4.5rem;

  --leading-none:    1;
  --leading-tight:   1.2;
  --leading-snug:    1.35;
  --leading-normal:  1.5;
  --leading-relaxed: 1.65;
  --leading-loose:   2;

  --tracking-tightest: -0.04em;
  --tracking-tight:    -0.02em;
  --tracking-normal:   0;
  --tracking-wide:     0.04em;
  --tracking-wider:    0.08em;
  --tracking-badge:    0.12em;

  /* ─── ANIMATION ───────────────────────────────────────── */
  /* Gear rotation is smooth and weighted — not snappy */
  --duration-instant:  50ms;
  --duration-fast:     100ms;
  --duration-normal:   200ms;
  --duration-slow:     300ms;
  --duration-slower:   500ms;
  --duration-crawl:    800ms;
  --duration-scan:     1500ms;  /* Full lens sweep */
  --duration-spin:     2000ms;  /* Gear rotation loop */

  --ease-default:      cubic-bezier(0.4, 0, 0.2, 1);   /* Material standard */
  --ease-in:           cubic-bezier(0.4, 0, 1, 1);
  --ease-out:          cubic-bezier(0, 0, 0.2, 1);
  --ease-spring:       cubic-bezier(0.34, 1.56, 0.64, 1); /* For gear settle */
  --ease-gear:         cubic-bezier(0.25, 0.46, 0.45, 0.94); /* Smooth gear turn */

  /* ─── BLUR ────────────────────────────────────────────── */
  --blur-sm:   4px;
  --blur-base: 8px;
  --blur-md:   12px;
  --blur-lg:   24px;
  --blur-xl:   40px;

  /* ─── Z-INDEX ─────────────────────────────────────────── */
  /* Maps to concentric ring depth */
  --z-base:     0;
  --z-raised:   10;
  --z-dropdown: 100;
  --z-sticky:   200;
  --z-overlay:  300;
  --z-modal:    400;
  --z-toast:    500;
  --z-tooltip:  600;

  /* ─── GRID ────────────────────────────────────────────── */
  --grid-columns:     12;
  --grid-gap:         24px;
  --container-sm:     640px;
  --container-md:     768px;
  --container-lg:     1024px;
  --container-xl:     1280px;
  --container-2xl:    1440px;
  --container-max:    1600px;

  /* ─── COMPONENT SIZES ─────────────────────────────────── */
  /* Button heights */
  --btn-height-sm:  32px;
  --btn-height-md:  40px;
  --btn-height-lg:  48px;
  --btn-height-xl:  56px;

  /* Input heights */
  --input-height-sm:  36px;
  --input-height-md:  44px;
  --input-height-lg:  52px;

  /* Search bar (primary CTA) */
  --search-height:    56px;
  --search-height-lg: 72px;

  /* Avatar sizes */
  --avatar-xs:  24px;
  --avatar-sm:  32px;
  --avatar-md:  40px;
  --avatar-lg:  48px;
  --avatar-xl:  64px;

  /* Icon sizes */
  --icon-xs:  12px;
  --icon-sm:  16px;
  --icon-md:  20px;
  --icon-lg:  24px;
  --icon-xl:  32px;
  --icon-2xl: 48px;

  /* Sidebar width */
  --sidebar-width:          260px;
  --sidebar-width-collapsed: 64px;

  /* Top nav height */
  --nav-height: 64px;
}
```

---

## Tailwind Config

```js
// tailwind.config.js
const colors = require('./brand/colors');

module.exports = {
  darkMode: 'class',
  content: ['./src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: {
          base:      '#0F1218',
          surface:   '#151B27',
          'surface-2': '#1E2535',
          'surface-3': '#252D3D',
        },
        primary: {
          50:  '#F0F9FF',
          400: '#38BDF8',
          500: '#0EA5E9',
          600: '#0284C7',
          700: '#0369A1',
        },
        metal: {
          200: '#E2E8F0',
          400: '#94A3B8',
          600: '#475569',
          800: '#1E293B',
        },
        network: {
          700: '#1D4ED8',
        },
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      borderRadius: {
        sm:   '4px',
        DEFAULT: '6px',
        md:   '8px',
        lg:   '12px',
        xl:   '16px',
        '2xl': '24px',
      },
      boxShadow: {
        z1: '0 1px 3px rgba(0,0,0,0.40), 0 1px 2px rgba(0,0,0,0.30)',
        z2: '0 4px 6px rgba(0,0,0,0.40), 0 2px 4px rgba(0,0,0,0.30)',
        z3: '0 10px 15px rgba(0,0,0,0.50), 0 4px 6px rgba(0,0,0,0.30)',
        'glow-ai':     '0 0 24px rgba(14, 165, 233, 0.40)',
        'glow-hover':  '0 0 16px rgba(14, 165, 233, 0.20)',
        'glow-focus':  '0 0 0 2px #0F1218, 0 0 0 4px #0EA5E9',
        'glow-scan':   '0 0 32px rgba(14, 165, 233, 0.50)',
      },
      animation: {
        'gear-spin':  'spin 2s cubic-bezier(0.25, 0.46, 0.45, 0.94) infinite',
        'scan-pulse': 'pulse 1.5s ease-in-out infinite',
        'glow-pulse': 'glow-pulse 2s ease-in-out infinite',
        'speed-line': 'shimmer 1.5s linear infinite',
      },
      keyframes: {
        'glow-pulse': {
          '0%, 100%': { boxShadow: '0 0 24px rgba(14,165,233,0.40)' },
          '50%':       { boxShadow: '0 0 48px rgba(14,165,233,0.70)' },
        },
        shimmer: {
          '0%':   { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
      },
      letterSpacing: {
        badge: '0.12em',
        label: '0.08em',
      },
      transitionTimingFunction: {
        spring: 'cubic-bezier(0.34, 1.56, 0.64, 1)',
        gear:   'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
      },
    },
  },
};
```

---

## Token Usage Quick Reference

| Component | Key Tokens |
|-----------|-----------|
| Page background | `--color-bg-base` |
| Card | `--color-bg-surface` + `--color-border-default` + `--shadow-z2` |
| Elevated card | `--color-bg-surface-2` + `--color-border-strong` + `--shadow-z3` |
| Primary button | `--color-primary-500` bg + `--radius-base` + `--btn-height-md` |
| Search input | `--color-bg-surface-2` bg + `--search-height` + `--glow-focus` on focus |
| AI container | `--color-border-ai` + `--glow-ai` + AI gradient bg |
| Focus state | `--glow-focus-ring` (always) |
| OEM/VIN text | `--font-mono` + `--color-text-link` |
| Badge/tag | `--radius-full` + `--tracking-badge` + uppercase |
| Price display | `--font-sans` tabular-nums + `--color-primary-400` |
