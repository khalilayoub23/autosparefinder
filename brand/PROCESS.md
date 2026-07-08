# AutoSpare Brand System — Master Process Tracker

> Every file in this directory is derived from the logo. Nothing is invented.
> This tracker is the single source of truth for what's done, in-progress, and next.

---

## Phases

| # | Phase | File | Status |
|---|-------|------|--------|
| 1 | Logo DNA Analysis | `LOGO_DNA.md` | ✅ Complete |
| 2 | Color System | `COLOR.md` | ✅ Complete |
| 3 | Typography | `TYPOGRAPHY.md` | ✅ Complete |
| 4 | Design Tokens | `TOKENS.md` | ✅ Complete |
| 5 | Iconography | `ICONOGRAPHY.md` | ✅ Complete |
| 6 | Component System | `COMPONENTS.md` | ✅ Complete |
| 7 | Motion System | `MOTION.md` | ✅ Complete |
| 8 | Illustration Language | `ILLUSTRATIONS.md` | ✅ Complete |
| 9 | Brand Voice | `BRAND_VOICE.md` | ✅ Complete |
| 10 | Implementation Guide | `IMPLEMENTATION.md` | ✅ Complete |
| 11 | HTML Artifact — Token Preview | artifact | ✅ Complete — verified 1234 lines, published |
| 12 | HTML Artifact — Component Demo | artifact | ✅ Complete — verified 1003 lines, published |
| 13 | HTML Artifact — Full Landing Page | artifact | ✅ Complete — verified 785 lines 47KB, published https://claude.ai/code/artifact/10bdb764-ef1f-4e2d-b4c1-ab40cfe347eb |
| 14 | HTML Artifact — Dashboard Demo | artifact | ✅ Complete — verified 785 lines 44KB, published https://claude.ai/code/artifact/f70eb204-fa9a-4d94-91df-549d9ce9c3ac |

---

## Verification Protocol (MANDATORY)

> Added after session 1 mistake: steps were marked ✅ Complete before the file existed
> or before the artifact was visually confirmed. This section prevents that.

**A step is only ✅ Complete when ALL of the following are true:**

| Check | How to verify |
|-------|--------------|
| File exists on disk | `ls /opt/autosparefinder/brand/<file>.md` returns the file |
| File has real content | `wc -l` shows > 50 lines (not a placeholder) |
| No section is empty | Every `##` heading has content beneath it |
| For HTML artifacts: renders correctly | Open artifact URL and confirm visually before marking done |
| Tracker updated immediately | PROCESS.md updated in the same session step was completed |

**Never mark a step complete based on memory. Always confirm with a tool call.**

---

## Decision Log

> Every major design choice is recorded here so future sessions don't re-derive it.

| Decision | Rationale | Source |
|----------|-----------|--------|
| Dark-first design | Logo background is near-black; enterprise automotive = night ops | LOGO_DNA §Background |
| Primary blue = #0EA5E9 | Extracted from blue halo in logo | LOGO_DNA §Colors |
| Border-radius base = 6px | Derived from gear tooth chamfer angle | LOGO_DNA §Geometry |
| Icon stroke = 1.5px | Matches gear ring stroke weight ratio | LOGO_DNA §Stroke |
| Font = Inter + JetBrains Mono | Inter matches logo text proportion; Mono for OEM/VIN/SKU | TYPOGRAPHY.md |
| Magnifying glass = universal search metaphor | The lens IS the brand; search = core product | LOGO_DNA §Lens |
| Blue horizontal lines = data flow | Speed lines in logo = network/API connections | LOGO_DNA §Lines |
| Concentric rings = elevation system | Rings = depth layers; more rings = higher elevation | LOGO_DNA §Rings |
| Metallic surface = premium tier | Chrome in logo = enterprise-grade; never cheap plastic | LOGO_DNA §Metallic |

---

## Rules (Never Break These)

1. Every design decision must trace back to a logo element
2. Dark mode is primary; light mode is a variant
3. The blue glow is reserved for AI, active states, and focus — never decorative
4. The magnifying glass metaphor must appear in all search interactions
5. Speed lines are used for loading, data flow, and global network graphics only
6. Metallic surfaces signal premium/enterprise — not used in basic customer UI
7. Gear geometry (rings, teeth, radial symmetry) informs all circular components
8. The vehicle silhouette sets the perspective angle for all vehicle illustrations

---

## File Map

```
brand/
├── PROCESS.md          ← You are here — master tracker
├── LOGO_DNA.md         ← Foundation: every design rule derived from logo
├── COLOR.md            ← Full color system (extracted from logo)
├── TYPOGRAPHY.md       ← Fonts, scale, hierarchy
├── TOKENS.md           ← Complete design token system (CSS variables + Tailwind)
├── ICONOGRAPHY.md      ← Icon philosophy and construction rules
├── COMPONENTS.md       ← Enterprise UI component specs
├── MOTION.md           ← Animation system (all derived from logo motion)
├── ILLUSTRATIONS.md    ← Technical illustration language
├── BRAND_VOICE.md      ← Tone, messaging, AI personality
└── IMPLEMENTATION.md   ← React + Tailwind + Figma guidelines
```
