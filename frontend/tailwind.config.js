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
          50: '#edf4ff',
          100: '#dce7f5',
          200: '#c2d4ea',
          300: '#9cb4d3',
          400: '#5f87b8',
          500: '#2d5b8e',
          600: '#1f3f6b',
          700: '#183356',
          800: '#122845',
          900: '#0b1f3a',
          blue: '#1ca7ff',
          navy: '#0b1f3a',
          surface: '#f4f8fd',
          border: '#d7e3f2',
          success: '#16a34a',
        },
      },
      borderRadius: {
        brand: '14px',
      },
      boxShadow: {
        electric: '0 12px 32px rgba(28, 167, 255, 0.22)',
      },
      fontFamily: {
        sans: ['Rubik', 'Heebo', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
