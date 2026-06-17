# AutoSpareFinder UI/UX Design System

## Design Philosophy
Professional auto parts marketplace — clean, trustworthy, fast. Modeled after leading global automotive e-commerce platforms. English-first with bilingual support.

## Color Palette (updated 2026-06-11 to match reference SVG)

| Token | Value | Usage |
|-------|-------|-------|
| `--topbar-bg` | `#080e1c` | Top announcement bar |
| `--header-bg` | `#0d1524` | Main nav + sub-nav background |
| `--hero-bg` | `#070e1d` | Hero section background |
| `--hero-gradient` | `#0a1525 → #0d1b35 → #091226` | Hero overlay gradient |
| `--trust-bg` | `#0d1524` | Trust bar background |
| `--blue-primary` | `#2563eb` | CTAs, active states, badges |
| `--blue-hover` | `#1d4ed8` | Hover state on primary buttons |
| `--blue-highlight` | `#3b82f6` | Hero text highlights ("Fast.", "Reliable.") |
| `--text-primary` | `#ffffff` | Headings on dark bg |
| `--text-secondary` | `#cbd5e1` | Body on dark bg |
| `--text-muted` | `#94a3b8` | Labels, hints on dark |
| `--page-bg` | `#ffffff` | Light section backgrounds |
| `--card-border` | `#f1f5f9` | Card borders on white |
| `--green-whatsapp` | `#25D366` | Floating WhatsApp button |
| `--footer-bg` | `#021737` | Footer / about section |

## Typography
- **Font**: Inter (system fallback: -apple-system, BlinkMacSystemFont, Segoe UI)
- **Weights**: 400 body, 600 semi-bold, 700 bold, 800 extra-bold, 900 black
- **Hero title**: clamp(28px, 4vw, 46px), weight 900
- **Section title**: 22px, weight 800
- **Body**: 13.5–15px, line-height 1.6–1.7
- **Labels/small**: 11–12px

## Page Structure (Landing Page)

### 1. Top Info Bar (`.asf-topbar`)
- Dark navy `#0d1b2a`
- Left: promotional message
- Right: Support | Track Order | WhatsApp badge | Currency selector
- Height: ~30px

### 2. Main Navigation (`.asf-mainnav`)
- Logo: gear icon + "AutoSpare / SPARE FINDER" two-line text
- Center: search bar with category dropdown + search button
- Right: Chat-online indicator | Sign In | Cart
- Dark navy background

### 3. Sub Navigation (`.asf-subnav`)
- Home | Categories | Catalog | Request a Quote | How It Works | About Us | Support
- Slightly lighter than main nav
- Active state: white text + blue bottom border

### 4. Hero Section (`.asf-hero`)
- Two-column layout: text left, image right
- Headline: "Find the Right Part. Fast. Easy. Reliable."
- Search modes: VIN | OEM Number | SKU/Part Number | Vehicle Details
- Trust disclaimer below search input

### 5. Trust Bar (`.asf-trust`)
- 5 columns: Trusted Suppliers | Best Prices | Fast Delivery | Secure Payments | Expert Support
- Icon + heading + sub-text per column
- Dark background, subtle icon circles

### 6. How It Works + Categories (`.asf-main-section`)
- **White background** `#ffffff`
- Left: 4 **numbered** steps — blue circle with number (1,2,3,4), separator ":", gray square icon, title + desc
- Dashed vertical connector line between numbered circles
- Right: 5×2 category grid with images + "View All" link
- Category cards: white bg, light border, `bg-slate-50` image area, hover lift + shadow

### 7. AI Assistant CTA (`.asf-ai-section`)
- Dark navy `#021737`
- Left: Custom **BotIcon** SVG (robot with antenna, arms, glowing eyes) + copy + CTA button
- Right: 2×2 feature icons grid (Parts Compatibility, Multiple Conditions, Global Shipping, Easy Returns)

### 8. Floating WhatsApp Button
- Fixed position: `bottom-6 right-6` z-50
- `#25D366` green circle, 56×56px, `w-7 h-7` icon
- Glowing shadow: `rgba(37,211,102,0.45)`
- Scale to 110% on hover

## Component Patterns

### Buttons
```css
/* Primary */
background: linear-gradient(135deg, #1565c0, #0288d1);
border-radius: 10px; padding: 11–13px 22–24px;
font-weight: 700–800; box-shadow: 0 4px 14px rgba(21,101,192,0.35);

/* Ghost */
border: 1px solid rgba(255,255,255,0.1);
background: rgba(255,255,255,0.05); border-radius: 8–12px;
```

### Cards
```css
/* Dark card */
background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1);
border-radius: 12–14px;

/* Light card */
background: #fff; border: 1px solid #e0e6ef; border-radius: 12px;
hover: box-shadow 0 8px 24px rgba(21,101,192,0.12); transform: translateY(-3px);
```

### Search Tabs
- Inactive: `rgba(255,255,255,0.05)` bg, `#90a4ae` text
- Active: `rgba(21,101,192,0.25)` bg, `#1565c0` border, white text

## Responsive Breakpoints
- `1100px`: Single column hero, hide parts image, 3-col trust bar
- `768px`: Wrap nav, full-width search bar, hide nav-right, 2-col categories
- `480px`: 2-col trust bar, smaller sub-nav text

## Key CSS Classes (LandingPage)
| Class | Description |
|-------|-------------|
| `.asf-root` | Root wrapper, white background |
| `.asf-topbar` | Top announcement bar |
| `.asf-mainnav` | Primary nav with logo + search |
| `.asf-subnav` | Category navigation links |
| `.asf-hero` | Dark hero with search form |
| `.asf-search-box` | Search form container |
| `.asf-mode-tab` | VIN/OEM/SKU/Vehicle tab buttons |
| `.asf-trust` | 5-column trust badges bar |
| `.asf-main-section` | Light gray how-it-works + categories |
| `.asf-cats-grid` | 5-column category cards grid |
| `.asf-ai-section` | Dark AI assistant CTA section |

## Logo Spec
- Icon: ⚙️ gear emoji (to be replaced with SVG logo)
- Primary text: "AutoSpare" — bold, 16px, white
- Sub text: "SPARE FINDER" — uppercase, 10px, blue, letter-spacing 1.5px
- Container: 44×44px rounded-10, blue gradient, subtle glow

## Search UX
1. Default mode: VIN search
2. Placeholder updates per mode
3. Both hero search and nav search bar trigger `/parts?search=X&mode=Y`
4. Nav search = quick search; Hero search = primary conversion

## Pages Map
| Route | Component | Description |
|-------|-----------|-------------|
| `/` | LandingPage | Homepage marketplace |
| `/parts` | Parts | Search results + filters |
| `/chat` | Chat | AI assistant |
| `/login` | Login | Authentication |
| `/register` | Register | Sign up |
| `/account` | Profile | User account |
| `/orders` | Orders | Order tracking |
| `/cart` | Cart | Shopping cart |
| `/admin` | Admin | Admin dashboard |
