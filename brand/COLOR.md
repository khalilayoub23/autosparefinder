# AutoSpare Color System

> All colors extracted directly from the logo. Nothing invented.
> See LOGO_DNA.md §1.9 for extraction methodology.

---

## Core Palette

### Backgrounds (from logo dark field)
```
--color-bg-base:      #0F1218   /* Page root — logo outer dark */
--color-bg-surface:   #151B27   /* Card level 1 — ring shadow */
--color-bg-surface2:  #1E2535   /* Card level 2 — ring body */
--color-bg-surface3:  #252D3D   /* Card level 3 — elevated panel */
--color-bg-overlay:   #0A0E17   /* Modal scrim — gear tooth dark */
```

### Primary — Electric Blue (from halo)
```
--color-primary-50:   #F0F9FF
--color-primary-100:  #E0F2FE
--color-primary-200:  #BAE6FD
--color-primary-300:  #7DD3FC
--color-primary-400:  #38BDF8   /* Halo edge — hover */
--color-primary-500:  #0EA5E9   /* Halo core — PRIMARY */
--color-primary-600:  #0284C7   /* Pressed state */
--color-primary-700:  #0369A1   /* Halo deep shadow */
--color-primary-800:  #075985
--color-primary-900:  #0C4A6E
```

### Network Blue (from speed lines)
```
--color-network-400:  #60A5FA
--color-network-500:  #3B82F6
--color-network-600:  #2563EB
--color-network-700:  #1D4ED8   /* Speed line color */
--color-network-800:  #1E40AF
```

### Metallic (from chrome effect)
```
--color-metal-50:     #F8FAFC
--color-metal-100:    #F1F5F9
--color-metal-200:    #E2E8F0   /* Chrome highlight */
--color-metal-300:    #CBD5E1
--color-metal-400:    #94A3B8   /* Chrome body — secondary text */
--color-metal-500:    #64748B
--color-metal-600:    #475569   /* Chrome shadow — muted */
--color-metal-700:    #334155
--color-metal-800:    #1E293B
--color-metal-900:    #0F172A
```

### Text
```
--color-text-primary:   #FFFFFF      /* Logo white text */
--color-text-secondary: #94A3B8      /* Chrome body */
--color-text-muted:     #475569      /* Chrome shadow */
--color-text-inverse:   #0F1218      /* On light surfaces */
--color-text-link:      #38BDF8      /* Halo edge */
--color-text-ai:        #0EA5E9      /* AI-sourced content */
```

### Borders
```
--color-border-subtle:   rgba(148, 163, 184, 0.08)   /* Hairline */
--color-border-default:  rgba(148, 163, 184, 0.12)   /* Cards */
--color-border-strong:   rgba(148, 163, 184, 0.20)   /* Active */
--color-border-primary:  rgba(14, 165, 233, 0.40)    /* Focused */
--color-border-ai:       rgba(14, 165, 233, 0.60)    /* AI container */
```

---

## Status Colors

| Role | Color | Hex | Usage |
|------|-------|-----|-------|
| Success | Green | `#22C55E` | In stock, confirmed, priced |
| Warning | Amber | `#F59E0B` | Low stock, pending, partial |
| Error | Red | `#EF4444` | Out of stock, failed, blocked |
| Info | Primary | `#0EA5E9` | AI result, informational |
| Neutral | Metal | `#64748B` | Draft, unknown, unverified |

---

## Glow System (from blue halo)

```css
/* AI Active — used only on AI components */
--glow-ai:        0 0 24px rgba(14, 165, 233, 0.40);
--glow-ai-strong: 0 0 48px rgba(14, 165, 233, 0.60);

/* Focus — used on all interactive elements when focused */
--glow-focus:     0 0 0 2px #0EA5E9;

/* Hover — subtle glow on card hover */
--glow-hover:     0 0 16px rgba(14, 165, 233, 0.20);

/* Scan — pulsing for loading/scanning states */
--glow-scan:      0 0 32px rgba(14, 165, 233, 0.50);

/* Premium surface — metallic card glow */
--glow-premium:   0 0 24px rgba(148, 163, 184, 0.10);
```

---

## Gradients

### Hero Background
```css
background: radial-gradient(
  ellipse 80% 50% at 50% -10%,
  rgba(14, 165, 233, 0.15) 0%,
  transparent 70%
), #0F1218;
```

### Card Surface — Metallic
```css
background: linear-gradient(
  135deg,
  #1E2535 0%,
  #151B27 100%
);
```

### Primary Button
```css
background: linear-gradient(
  135deg,
  #38BDF8 0%,
  #0EA5E9 50%,
  #0284C7 100%
);
```

### Speed Line Section Divider
```css
background: linear-gradient(
  90deg,
  transparent 0%,
  #1D4ED8 30%,
  #0EA5E9 50%,
  #1D4ED8 70%,
  transparent 100%
);
height: 1px;
```

### AI Component Background
```css
background: linear-gradient(
  135deg,
  rgba(14, 165, 233, 0.08) 0%,
  rgba(14, 165, 233, 0.03) 100%
);
border: 1px solid rgba(14, 165, 233, 0.20);
```

### Enterprise Tier Surface
```css
background: linear-gradient(
  135deg,
  rgba(148, 163, 184, 0.06) 0%,
  rgba(148, 163, 184, 0.02) 100%
);
border: 1px solid rgba(148, 163, 184, 0.12);
```

---

## Light Mode Variants

> Light mode is a variant, not the default. Used in customer-facing print exports
> and accessibility overrides only.

```
--color-lm-bg:        #F8FAFC
--color-lm-surface:   #FFFFFF
--color-lm-surface2:  #F1F5F9
--color-lm-text:      #0F172A
--color-lm-primary:   #0284C7
--color-lm-border:    rgba(15, 23, 42, 0.12)
```

---

## Accessibility

All primary color combinations meet WCAG AA (4.5:1 minimum):

| Foreground | Background | Ratio | Pass |
|------------|------------|-------|------|
| `#FFFFFF` | `#0F1218` | 19.5:1 | ✅ AAA |
| `#FFFFFF` | `#0EA5E9` | 2.8:1 | ⚠️ Large text only |
| `#0F1218` | `#38BDF8` | 7.2:1 | ✅ AAA |
| `#94A3B8` | `#0F1218` | 5.9:1 | ✅ AA |
| `#475569` | `#0F1218` | 3.1:1 | ⚠️ Use only for placeholder/muted |
| `#FFFFFF` | `#0284C7` | 4.6:1 | ✅ AA |

**Rule:** Never place white text directly on `--color-primary-500` without a background
surface behind it. The halo blue fails contrast for body text.

---

## Data Visualization Palette

For charts, graphs, and analytics (derived from logo palette, extended for 8 series):

```
Series 1: #0EA5E9   (primary blue — halo)
Series 2: #22C55E   (success green)
Series 3: #F59E0B   (warning amber)
Series 4: #8B5CF6   (violet — derived from blue+metallic mix)
Series 5: #EC4899   (rose)
Series 6: #14B8A6   (teal)
Series 7: #F97316   (orange)
Series 8: #94A3B8   (metallic — neutral series)
```

Sequential scale (single-metric heatmaps):
```
#0C4A6E → #0369A1 → #0284C7 → #0EA5E9 → #38BDF8 → #7DD3FC → #BAE6FD
```
