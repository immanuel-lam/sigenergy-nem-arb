"use client";

import { motion } from "framer-motion";

const EASE = [0.16, 1, 0.3, 1] as const;

const ACTION_COLOR: Record<string, string> = {
  IDLE: "#2A3642",
  HOLD_SOLAR: "#4A7A3E",
  CHARGE_GRID: "#8B5CF6",
  DISCHARGE_GRID: "#00E5FF",
};

const ACTION_COLOR_GHOST: Record<string, string> = {
  IDLE: "#1A242F",
  HOLD_SOLAR: "#2C4A25",
  CHARGE_GRID: "#3E2A66",
  DISCHARGE_GRID: "#0E6673",
};

type ActionStripProps = {
  baseline: string[];
  spiked: string[];
  /** 0..1 — how far through the spike transition we are. 0 = baseline, 1 = spiked */
  transition: number;
  width: number;
  height: number;
  /** x position of the spike band on this strip, for the ring highlight */
  spikeX?: number;
  spikeWidth?: number;
  /** whether to draw the spike highlight */
  showSpike?: boolean;
};

/**
 * A thin horizontal strip along the bottom of the chart. One colored segment per interval.
 * Recolors from baseline to spiked as `transition` goes 0 → 1.
 */
export function ActionStrip({
  baseline,
  spiked,
  transition,
  width,
  height,
  spikeX,
  spikeWidth,
  showSpike,
}: ActionStripProps) {
  const n = Math.max(baseline.length, spiked.length);
  const segW = width / n;

  return (
    <svg
      width={width}
      height={height}
      className="overflow-visible"
      aria-hidden
    >
      {/* Baseline layer */}
      <g>
        {baseline.map((a, i) => (
          <rect
            key={`base-${i}`}
            x={i * segW}
            y={0}
            width={Math.max(segW - 0.5, 0.5)}
            height={height}
            fill={ACTION_COLOR[a] ?? ACTION_COLOR.IDLE}
            opacity={1 - transition * 0.85}
          />
        ))}
      </g>

      {/* Spiked layer — fades in */}
      <g>
        {spiked.map((a, i) => {
          const changed = baseline[i] !== a;
          return (
            <rect
              key={`spike-${i}`}
              x={i * segW}
              y={0}
              width={Math.max(segW - 0.5, 0.5)}
              height={height}
              fill={ACTION_COLOR[a] ?? ACTION_COLOR.IDLE}
              opacity={changed ? transition : 0}
            />
          );
        })}
      </g>

      {/* Spike window outline on the strip */}
      {showSpike && spikeX != null && spikeWidth != null ? (
        <motion.rect
          x={spikeX}
          y={-1}
          width={spikeWidth}
          height={height + 2}
          fill="none"
          stroke="#FF3B5C"
          strokeWidth={1}
          initial={{ opacity: 0 }}
          animate={{ opacity: 0.7 }}
          transition={{ duration: 0.4, ease: EASE }}
        />
      ) : null}

      {/* "now" marker on the far left */}
      <rect x={0} y={0} width={1.5} height={height} fill="#E6EDF3" opacity={0.5} />
    </svg>
  );
}

export { ACTION_COLOR, ACTION_COLOR_GHOST };
