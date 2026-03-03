/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  safelist: [
    // Category accent colours used dynamically in PartCard
    { pattern: /^border-l-(red|orange|yellow|lime|sky|cyan|violet|emerald|blue|pink|stone|teal|gray)-(300|400)$/ },
    { pattern: /^bg-(red|orange|yellow|lime|sky|cyan|violet|emerald|blue|pink|stone|teal|gray)-(50|100)$/ },
    { pattern: /^text-(red|orange|yellow|lime|sky|cyan|violet|emerald|blue|pink|stone|teal|gray)-(600|700)$/ },
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50:  '#fff7ed',
          100: '#ffedd5',
          200: '#fed7aa',
          300: '#fdba74',
          400: '#fb923c',
          500: '#f97316',
          600: '#ea580c',
          700: '#c2410c',
          800: '#9a3412',
          900: '#7c2d12',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
