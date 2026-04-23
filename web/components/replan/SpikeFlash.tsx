"use client";

import { motion, AnimatePresence } from "framer-motion";

const EASE = [0.16, 1, 0.3, 1] as const;

type SpikeFlashProps = {
  /** x pixel position in chart coordinates */
  x: number;
  /** width of the spike band in pixels */
  width: number;
  /** chart height */
  height: number;
  /** whether the spike is currently visible */
  active: boolean;
  /** magnitude text e.g. "+120 c/kWh" */
  label: string;
};

/**
 * A vertical band overlaid on the chart marking where the price spike occurred.
 * Two layered motion elements — one flashing pulse, one sustained band.
 */
export function SpikeFlash({ x, width, height, active, label }: SpikeFlashProps) {
  return (
    <AnimatePresence>
      {active ? (
        <motion.g
          key="spike"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.25, ease: EASE }}
        >
          {/* Flashing pulse — quick attack, slow decay */}
          <motion.rect
            x={x - 2}
            y={0}
            width={width + 4}
            height={height}
            fill="#FF3B5C"
            initial={{ opacity: 0.85 }}
            animate={{ opacity: 0 }}
            transition={{ duration: 0.6, ease: [0.4, 0, 0.2, 1] }}
          />

          {/* Sustained gradient band */}
          <rect
            x={x}
            y={0}
            width={width}
            height={height}
            fill="url(#spike-gradient)"
          />

          {/* Edge lines */}
          <motion.line
            x1={x}
            y1={0}
            x2={x}
            y2={height}
            stroke="#FF3B5C"
            strokeWidth={1}
            strokeDasharray="2 3"
            initial={{ opacity: 0 }}
            animate={{ opacity: 0.6 }}
            transition={{ duration: 0.4, delay: 0.15, ease: EASE }}
          />
          <motion.line
            x1={x + width}
            y1={0}
            x2={x + width}
            y2={height}
            stroke="#FF3B5C"
            strokeWidth={1}
            strokeDasharray="2 3"
            initial={{ opacity: 0 }}
            animate={{ opacity: 0.6 }}
            transition={{ duration: 0.4, delay: 0.15, ease: EASE }}
          />

          {/* Label pill, floats above the band */}
          <motion.g
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, delay: 0.2, ease: EASE }}
          >
            <rect
              x={x + width / 2 - 58}
              y={10}
              width={116}
              height={22}
              rx={11}
              fill="#0A0E14"
              stroke="#FF3B5C"
              strokeWidth={1}
            />
            <text
              x={x + width / 2}
              y={25}
              textAnchor="middle"
              fontSize={11}
              fontFamily="var(--font-mono), ui-monospace, monospace"
              fontWeight={500}
              fill="#FF3B5C"
              style={{ letterSpacing: "0.02em" }}
            >
              {label}
            </text>
          </motion.g>
        </motion.g>
      ) : null}
    </AnimatePresence>
  );
}
