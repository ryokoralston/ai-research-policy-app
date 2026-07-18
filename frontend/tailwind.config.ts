import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  // Persona avatar/card colors (built-in + custom) come from the backend
  // as dynamic strings (backend/services/persona_service.py's
  // BUILTIN_COLORS + CUSTOM_PALETTE) and never appear as literal text in
  // this repo, so Tailwind's content scanner can't discover them on its
  // own — safelist the exact bounded set it can emit.
  safelist: [
    "bg-violet-600", "text-violet-100",
    "bg-blue-600", "text-blue-100",
    "bg-slate-600", "text-slate-100",
    "bg-rose-600", "text-rose-100",
    "bg-teal-600", "text-teal-100",
    "bg-amber-600", "text-amber-100",
    "bg-emerald-600", "text-emerald-100",
    "bg-orange-600", "text-orange-100",
    "bg-cyan-600", "text-cyan-100",
    "bg-red-600", "text-red-100",
    "bg-indigo-600", "text-indigo-100",
    "bg-pink-600", "text-pink-100",
    "bg-lime-600", "text-lime-100",
    "bg-sky-600", "text-sky-100",
    "bg-fuchsia-600", "text-fuchsia-100",
    "bg-yellow-600", "text-yellow-100",
  ],
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
      },
    },
  },
  plugins: [require("@tailwindcss/typography"), require("tailwind-scrollbar")],
};
export default config;
