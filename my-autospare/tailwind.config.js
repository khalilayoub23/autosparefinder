/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brandBlue: "#2D5BE3",
        brandBlueMid: "#3F7BF0",
        brandBlueLight: "#7FB7FF"
      },
      boxShadow: {
        partCard: "0 18px 40px rgba(0,0,0,0.45)"
      }
    }
  },
  plugins: []
};
