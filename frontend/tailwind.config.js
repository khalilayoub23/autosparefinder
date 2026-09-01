/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  safelist: [
    { pattern: /^border-l-(red|orange|yellow|lime|sky|cyan|violet|emerald|blue|pink|stone|teal|gray)-(300|400)$/ },
    { pattern: /^bg-(red|orange|yellow|lime|sky|cyan|violet|emerald|blue|pink|stone|teal|gray)-(50|100)$/ },
    { pattern: /^text-(red|orange|yellow|lime|sky|cyan|violet|emerald|blue|pink|stone|teal|gray)-(600|700)$/ },
  ],
  theme: {
    extend: {
      screens: {
        xs: '400px',
      },
      colors: {
        brand: {
          // Dark design system — DESIGN.md tokens
          // Surfaces (light → dark progression)
          50:  '#252D3D',   // Surface Highlight — active states, selected rows
          100: '#1E2535',   // Steel Navy — elevated panels, modals, dropdowns
          200: '#151B27',   // Deep Navy — card level 1
          300: '#0EA5E9',   // Electric Blue — focus rings, highlights
          400: '#38BDF8',   // Halo Edge Blue — hover accent
          500: '#0EA5E9',   // Electric Blue — primary accent
          600: '#0EA5E9',   // Electric Blue (gradient compat)
          700: '#38BDF8',   // Halo Edge Blue (gradient compat — from-brand-700)
          800: '#0284C7',   // Deep Blue — pressed/active
          900: '#0F1218',   // Void Black — page root
          // Named tokens
          blue:    '#0EA5E9',   // Electric Blue (was #1ca7ff — WRONG)
          navy:    '#151B27',   // Deep Navy (was #0b1f3a — too dark)
          surface: '#0F1218',   // Void Black (was #f4f8fd — CRITICAL BUG, was light)
          border:  'rgba(148,163,184,0.12)',
          success: '#22C55E',   // Success Green (was #16a34a)
        },
      },
      borderRadius: {
        brand: '6px',   // Geometric edges — NOT pill-shaped (was 14px)
      },
      boxShadow: {
        electric: '0 0 24px rgba(14,165,233,0.40)',  // AI glow
        card:     '0 1px 3px rgba(0,0,0,0.4), 0 4px 12px rgba(0,0,0,0.3)',
        elevated: '0 4px 16px rgba(0,0,0,0.5), 0 8px 32px rgba(0,0,0,0.4)',
      },
      fontFamily: {
        // Inter already loaded via index.html Google Fonts link
        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'Cascadia Code', 'monospace'],
      },
    },
  },
  plugins: [],
}
