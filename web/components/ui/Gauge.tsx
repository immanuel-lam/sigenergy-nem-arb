"use client";

import { motion } from "framer-motion";
import { useMemo } from "react";
import { cn } from "@/lib/utils";

interface GaugeProps {
  /** Current SOC 0..1 (not percentage). */
  value: number;
  /** Safety floor (0..1). Arc starts here. */
  floor?: number;
  /** Safety ceiling (0..1). Arc ends here. */
  ceiling?: number;
  /** Planned next value (0..1). Shown as a ghost marker. */
  next?: number | null;
  size?: number;
  /** Center overlay override. */
  children?: React.ReactNode;
  className?: string;
}

/**
 * Circular SOC gauge — a wide arc from the safety floor to the ceiling,
 * with a glowing dot at the current value and a faint tick for `next`.
 *
 * The arc is drawn from 135deg sweep around the bottom half, which reads
 * better than a full circle for battery state. Math is:
 *   angle 0 at bottom (270deg), swept -135deg to +135deg.
 */
export function Gauge({
  value,
  floor = 0.1,
  ceiling = 0.95,
  next = null,
  size = 220,
  children,
  className,
}: GaugeProps) {
  const stroke = 14;
  const radius = size / 2 - stroke / 2 - 2;
  const center = size / 2;

  // Arc geometry — 270deg sweep, starting at -225deg (lower-left).
  const startAngle = -225;
  const endAngle = 45;
  const totalSweep = endAngle - startAngle; // 270

  const clampedValue = Math.max(floor, Math.min(ceiling, value));
  const valueFrac = (clampedValue - floor) / (ceiling - floor);
  const nextFrac =
    next == null
      ? null
      : (Math.max(floor, Math.min(ceiling, next)) - floor) / (ceiling - floor);

  const polarToCartesian = (angleDeg: number) => {
    const rad = (angleDeg * Math.PI) / 180;
    return {
      x: center + radius * Math.cos(rad),
      y: center + radius * Math.sin(rad),
    };
  };

  const arcPath = (a: number, b: number) => {
    const start = polarToCartesian(a);
    const end = polarToCartesian(b);
    const large = b - a > 180 ? 1 : 0;
    return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${large} 1 ${end.x} ${end.y}`;
  };

  const trackPath = arcPath(startAngle, endAngle);
  const progressEndAngle = startAngle + totalSweep * valueFrac;
  const progressPath = arcPath(startAngle, progressEndAngle);

  const dotAngle = progressEndAngle;
  const dotPos = polarToCartesian(dotAngle);

  const nextMarker = useMemo(() => {
    if (nextFrac == null) return null;
    const a = startAngle + totalSweep * nextFrac;
    const inner = {
      x: center + (radius - stroke / 2 - 3) * Math.cos((a * Math.PI) / 180),
      y: center + (radius - stroke / 2 - 3) * Math.sin((a * Math.PI) / 180),
    };
    const outer = {
      x: center + (radius + stroke / 2 + 3) * Math.cos((a * Math.PI) / 180),
      y: center + (radius + stroke / 2 + 3) * Math.sin((a * Math.PI) / 180),
    };
    return { inner, outer };
  }, [nextFrac, center, radius, stroke, startAngle, totalSweep]);

  return (
    <div
      className={cn("relative inline-flex items-center justify-center", className)}
      style={{ width: size, height: size }}
    >
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className="overflow-visible"
      >
        <defs>
          <linearGradient id="gauge-grad" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="#FF3B5C" />
            <stop offset="15%" stopColor="#FFB020" />
            <stop offset="45%" stopColor="#00E5FF" />
            <stop offset="80%" stopColor="#00E5FF" />
            <stop offset="100%" stopColor="#FFB020" />
          </linearGradient>
          <filter id="gauge-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="4" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Track */}
        <path
          d={trackPath}
          fill="none"
          stroke="var(--border)"
          strokeWidth={stroke}
          strokeLinecap="round"
        />

        {/* Progress */}
        <motion.path
          d={progressPath}
          fill="none"
          stroke="url(#gauge-grad)"
          strokeWidth={stroke}
          strokeLinecap="round"
          initial={{ pathLength: 0, opacity: 0.4 }}
          animate={{ pathLength: 1, opacity: 1 }}
          transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
        />

        {/* Next marker — thin tick at planned SOC */}
        {nextMarker && (
          <line
            x1={nextMarker.inner.x}
            y1={nextMarker.inner.y}
            x2={nextMarker.outer.x}
            y2={nextMarker.outer.y}
            stroke="var(--ink-dim)"
            strokeWidth={1.5}
            strokeLinecap="round"
            opacity={0.7}
          />
        )}

        {/* Current value dot — glowing */}
        <motion.circle
          cx={dotPos.x}
          cy={dotPos.y}
          r={6}
          fill="#00E5FF"
          filter="url(#gauge-glow)"
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          transition={{ duration: 0.5, delay: 0.3 }}
        />
        <motion.circle
          cx={dotPos.x}
          cy={dotPos.y}
          r={3}
          fill="#FFFFFF"
          initial={{ opacity: 0.6 }}
          animate={{ opacity: [0.6, 1, 0.6] }}
          transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
        />
      </svg>

      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        {children}
      </div>
    </div>
  );
}
