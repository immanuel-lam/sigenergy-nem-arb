import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Backgrounds
        bg: "var(--bg)",
        surface: "var(--surface)",
        card: "var(--card)",
        border: "var(--border)",
        "border-strong": "var(--border-strong)",

        // Ink (text)
        ink: "var(--ink)",
        "ink-dim": "var(--ink-dim)",
        "ink-faint": "var(--ink-faint)",

        // Accents
        cyan: {
          DEFAULT: "var(--cyan)",
          dim: "var(--cyan-dim)",
        },
        amber: {
          DEFAULT: "var(--amber)",
          dim: "var(--amber-dim)",
        },
        rose: {
          DEFAULT: "var(--rose)",
          dim: "var(--rose-dim)",
        },
        violet: {
          DEFAULT: "var(--violet)",
          dim: "var(--violet-dim)",
        },

        // Semantic
        ok: "var(--cyan)",
        warn: "var(--amber)",
        crit: "var(--rose)",
        opus: "var(--violet)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      fontSize: {
        "11": ["11px", { lineHeight: "14px", letterSpacing: "0.02em" }],
        "12": ["12px", { lineHeight: "16px", letterSpacing: "0.01em" }],
        "13": ["13px", { lineHeight: "18px", letterSpacing: "0" }],
        "15": ["15px", { lineHeight: "22px", letterSpacing: "-0.005em" }],
        "18": ["18px", { lineHeight: "26px", letterSpacing: "-0.01em" }],
        "24": ["24px", { lineHeight: "30px", letterSpacing: "-0.02em" }],
        "32": ["32px", { lineHeight: "38px", letterSpacing: "-0.025em" }],
        "48": ["48px", { lineHeight: "52px", letterSpacing: "-0.03em" }],
      },
      spacing: {
        "1": "4px",
        "2": "8px",
        "3": "12px",
        "4": "16px",
        "6": "24px",
        "8": "32px",
        "12": "48px",
        "16": "64px",
      },
      borderRadius: {
        xs: "4px",
        sm: "6px",
        md: "10px",
        lg: "14px",
      },
      letterSpacing: {
        tightest: "-0.03em",
        tighter: "-0.02em",
        tight: "-0.01em",
        normal: "0",
        wide: "0.02em",
        wider: "0.05em",
        widest: "0.12em",
      },
      boxShadow: {
        "glow-cyan": "0 0 0 1px rgba(0, 229, 255, 0.35), 0 0 24px -4px rgba(0, 229, 255, 0.25)",
        "glow-amber": "0 0 0 1px rgba(255, 176, 32, 0.35), 0 0 24px -4px rgba(255, 176, 32, 0.2)",
        "glow-rose": "0 0 0 1px rgba(255, 59, 92, 0.35), 0 0 24px -4px rgba(255, 59, 92, 0.2)",
        card: "0 1px 0 rgba(255,255,255,0.02) inset, 0 8px 24px -12px rgba(0,0,0,0.6)",
      },
      transitionTimingFunction: {
        out: "cubic-bezier(0.16, 1, 0.3, 1)",
      },
      transitionDuration: {
        "150": "150ms",
        "250": "250ms",
        "400": "400ms",
        "800": "800ms",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "pulse-soft": {
          "0%, 100%": { opacity: "0.6" },
          "50%": { opacity: "1" },
        },
        "drift-1": {
          "0%, 100%": { transform: "translate(0, 0) scale(1)" },
          "50%": { transform: "translate(5%, -3%) scale(1.08)" },
        },
        "drift-2": {
          "0%, 100%": { transform: "translate(0, 0) scale(1)" },
          "50%": { transform: "translate(-4%, 4%) scale(0.95)" },
        },
      },
      animation: {
        "fade-in": "fade-in 400ms cubic-bezier(0.16, 1, 0.3, 1) both",
        "pulse-soft": "pulse-soft 2.4s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "drift-1": "drift-1 22s ease-in-out infinite",
        "drift-2": "drift-2 28s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
