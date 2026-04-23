/**
 * Design tokens — single source of truth for JS/TS consumers.
 * Tailwind reads the same values via CSS variables in globals.css.
 * Keep this file and globals.css in lockstep.
 */

export const color = {
  // Backgrounds
  bg: "#0A0E14",
  surface: "#0E141C",
  card: "#111821",
  border: "#1A242F",
  borderStrong: "#243040",

  // Ink
  ink: "#E6EDF3",
  inkDim: "#8B949E",
  inkFaint: "#6E7681",

  // Accents
  cyan: "#00E5FF",
  amber: "#FFB020",
  rose: "#FF3B5C",
  violet: "#8B5CF6",
} as const;

/** Chart palette — ordered so recharts gets consistent role colors. */
export const chart = {
  soc: color.cyan,
  price: color.amber,
  discharge: color.rose,
  action: color.violet,
  load: color.inkDim,
  solar: "#FFD666", // warmer than amber, for solar specifically
  gridLine: color.border,
  gridLineStrong: color.borderStrong,
} as const;

/** Motion tokens — import in any framer-motion component. */
export const motion = {
  ease: {
    out: [0.16, 1, 0.3, 1] as [number, number, number, number],
    inOut: [0.65, 0, 0.35, 1] as [number, number, number, number],
  },
  duration: {
    fast: 0.15,
    base: 0.25,
    slow: 0.4,
    slower: 0.8,
  },
  spring: {
    soft: { type: "spring" as const, stiffness: 140, damping: 22, mass: 0.9 },
    snappy: { type: "spring" as const, stiffness: 360, damping: 28, mass: 0.8 },
  },
} as const;

export const radius = {
  xs: 4,
  sm: 6,
  md: 10,
  lg: 14,
} as const;

export const space = {
  1: 4,
  2: 8,
  3: 12,
  4: 16,
  6: 24,
  8: 32,
  12: 48,
  16: 64,
} as const;

export const type = {
  sizes: [11, 12, 13, 15, 18, 24, 32, 48] as const,
};
