/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0a0a0f",
        card: "#181820",
        accent: "#ff1e3c",
        gold: "#ffc23c",
        success: "#22c55e",
        warn: "#f59e0b",
        danger: "#ef4444",
        muted: "#888",
      },
    },
  },
  plugins: [],
};
