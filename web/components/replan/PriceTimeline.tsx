"use client";

import { motion } from "framer-motion";
import { SpikeFlash } from "./SpikeFlash";

const EASE = [0.16, 1, 0.3, 1] as const;

type PriceTimelineProps = {
  width: number;
  height: number;
  padding?: { top: number; right: number; bottom: number; left: number };

  /** Price series — export price in c/kWh for each interval. */
  baselineExport: number[];
  spikedExport: number[];

  /** SOC trajectories, length = intervals + 1, values 0..1 */
  baselineSoc: number[];
  spikedSoc: number[];

  /** Action arrays, used to identify charge vs discharge intervals for color-by-segment */
  baselineActions: string[];
  spikedActions: string[];

  /** 0..1 master playhead driving everything */
  playhead: number;

  /** Spike timing — interval indices */
  spikeStartIdx: number;
  spikeEndIdx: number;
  spikeLabel: string;

  /** Time labels for the x axis */
  xLabels?: string[];
};

/**
 * The bespoke SVG chart. Price line (amber) + SOC line (cyan).
 * Old plan fades to a ghost; new plan draws in on top.
 * No Recharts — too opinionated for this animation.
 */
export function PriceTimeline({
  width,
  height,
  padding = { top: 32, right: 24, bottom: 28, left: 44 },
  baselineExport,
  spikedExport,
  baselineSoc,
  spikedSoc,
  baselineActions,
  spikedActions,
  playhead,
  spikeStartIdx,
  spikeEndIdx,
  spikeLabel,
  xLabels,
}: PriceTimelineProps) {
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;

  const n = baselineExport.length;
  const segW = innerW / n;

  // Y scale for price — pad to show the spike magnitude comfortably.
  const maxPrice = Math.max(
    ...baselineExport,
    ...spikedExport,
    50
  );
  const minPrice = Math.min(...baselineExport, ...spikedExport, 0);
  const pricePad = (maxPrice - minPrice) * 0.12;
  const pYMin = minPrice - pricePad;
  const pYMax = maxPrice + pricePad;

  const priceY = (v: number) =>
    padding.top + innerH * (1 - (v - pYMin) / (pYMax - pYMin));

  // SOC axis — fixed 0..1 with some headroom.
  const socY = (v: number) => padding.top + innerH * (1 - v);

  // Build point series for price (mid of each interval).
  const priceX = (i: number) => padding.left + segW * (i + 0.5);
  const socX = (i: number) => padding.left + segW * i;

  // Stepped path for price lines (each interval is a flat step).
  const priceStepPath = (arr: number[]): string => {
    if (!arr.length) return "";
    const parts: string[] = [];
    arr.forEach((v, i) => {
      const x0 = padding.left + segW * i;
      const x1 = padding.left + segW * (i + 1);
      const y = priceY(v);
      if (i === 0) parts.push(`M ${x0.toFixed(2)} ${y.toFixed(2)}`);
      else {
        const prevY = priceY(arr[i - 1]);
        parts.push(`L ${x0.toFixed(2)} ${prevY.toFixed(2)}`);
        parts.push(`L ${x0.toFixed(2)} ${y.toFixed(2)}`);
      }
      parts.push(`L ${x1.toFixed(2)} ${y.toFixed(2)}`);
    });
    return parts.join(" ");
  };

  const socPath = (arr: number[]): string => {
    if (!arr.length) return "";
    const parts: string[] = [];
    arr.forEach((v, i) => {
      const x = padding.left + segW * i;
      const y = socY(v);
      parts.push(i === 0 ? `M ${x.toFixed(2)} ${y.toFixed(2)}` : `L ${x.toFixed(2)} ${y.toFixed(2)}`);
    });
    return parts.join(" ");
  };

  const pricePathBaseline = priceStepPath(baselineExport);
  const pricePathSpiked = priceStepPath(spikedExport);
  const socPathBaseline = socPath(baselineSoc);
  const socPathSpiked = socPath(spikedSoc);

  // Playhead-derived progress values
  const baselineDraw = clamp01((playhead - 0.0) / 0.25); // 0 → 0.25
  const spikeActive = playhead >= 0.28;
  const newPlanDraw = clamp01((playhead - 0.38) / 0.22); // 0.38 → 0.60
  const oldPlanFade = clamp01((playhead - 0.38) / 0.25);

  // Y-axis ticks for price
  const priceTicks = niceTicks(pYMin, pYMax, 4);

  // Spike geometry in chart coords
  const spikeX = padding.left + segW * spikeStartIdx;
  const spikeW = segW * (spikeEndIdx - spikeStartIdx + 1);

  // Highlight "current interval" (i=0)
  const nowX = padding.left;

  return (
    <svg
      width={width}
      height={height}
      className="overflow-visible"
      aria-label="Price and state-of-charge timeline"
    >
      <defs>
        <linearGradient id="spike-gradient" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#FF3B5C" stopOpacity={0.02} />
          <stop offset="40%" stopColor="#FF3B5C" stopOpacity={0.14} />
          <stop offset="100%" stopColor="#FF3B5C" stopOpacity={0.32} />
        </linearGradient>
        <linearGradient id="price-baseline-stroke" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#FFB020" stopOpacity={0.2} />
          <stop offset="12%" stopColor="#FFB020" stopOpacity={1} />
          <stop offset="100%" stopColor="#FFB020" stopOpacity={1} />
        </linearGradient>
        <linearGradient id="soc-stroke" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#00E5FF" stopOpacity={0.2} />
          <stop offset="12%" stopColor="#00E5FF" stopOpacity={1} />
          <stop offset="100%" stopColor="#00E5FF" stopOpacity={1} />
        </linearGradient>
      </defs>

      {/* Chart frame */}
      <rect
        x={padding.left}
        y={padding.top}
        width={innerW}
        height={innerH}
        fill="transparent"
        stroke="#1A242F"
        strokeWidth={1}
      />

      {/* Horizontal gridlines */}
      {priceTicks.map((t) => (
        <g key={`grid-${t}`}>
          <line
            x1={padding.left}
            x2={padding.left + innerW}
            y1={priceY(t)}
            y2={priceY(t)}
            stroke="#1A242F"
            strokeWidth={1}
            strokeDasharray="2 4"
          />
          <text
            x={padding.left - 8}
            y={priceY(t) + 3}
            textAnchor="end"
            fontSize={10}
            fill="#6E7681"
            fontFamily="var(--font-mono), ui-monospace, monospace"
          >
            {formatPrice(t)}
          </text>
        </g>
      ))}

      {/* Y axis label */}
      <text
        x={padding.left - 30}
        y={padding.top - 12}
        fontSize={10}
        fill="#8B949E"
        fontFamily="var(--font-mono), ui-monospace, monospace"
        style={{ letterSpacing: "0.05em" }}
      >
        c/kWh
      </text>

      {/* X axis labels (sparse) */}
      {xLabels?.map((lbl, i) => {
        if (i % Math.max(1, Math.floor(n / 6)) !== 0) return null;
        return (
          <text
            key={`xl-${i}`}
            x={padding.left + segW * i}
            y={padding.top + innerH + 16}
            fontSize={10}
            fill="#6E7681"
            fontFamily="var(--font-mono), ui-monospace, monospace"
          >
            {lbl}
          </text>
        );
      })}

      {/* Spike flash — sits behind the lines */}
      <SpikeFlash
        x={spikeX}
        width={spikeW}
        height={innerH + padding.top}
        active={spikeActive}
        label={spikeLabel}
      />

      {/* Baseline price (amber) — fades when new plan draws in */}
      <motion.path
        d={pricePathBaseline}
        fill="none"
        stroke="url(#price-baseline-stroke)"
        strokeWidth={2}
        strokeLinejoin="round"
        strokeLinecap="round"
        initial={{ pathLength: 0, opacity: 0 }}
        animate={{
          pathLength: baselineDraw,
          opacity: 1 - oldPlanFade * 0.82,
        }}
        transition={{ duration: 0 }}
      />

      {/* Baseline SOC (cyan) — also fades */}
      <motion.path
        d={socPathBaseline}
        fill="none"
        stroke="#00E5FF"
        strokeWidth={1.5}
        strokeDasharray="3 3"
        strokeLinejoin="round"
        initial={{ pathLength: 0, opacity: 0 }}
        animate={{
          pathLength: baselineDraw,
          opacity: 0.7 * (1 - oldPlanFade * 0.8),
        }}
        transition={{ duration: 0 }}
      />

      {/* New plan price (amber spike) — draws in from left */}
      <motion.path
        d={pricePathSpiked}
        fill="none"
        stroke="#FFB020"
        strokeWidth={2.25}
        strokeLinejoin="round"
        strokeLinecap="round"
        initial={{ pathLength: 0, opacity: 0 }}
        animate={{
          pathLength: newPlanDraw,
          opacity: newPlanDraw > 0 ? 1 : 0,
        }}
        transition={{ duration: 0 }}
        style={{ filter: "drop-shadow(0 0 6px rgba(255,176,32,0.45))" }}
      />

      {/* New SOC path (cyan) — draws in */}
      <motion.path
        d={socPathSpiked}
        fill="none"
        stroke="#00E5FF"
        strokeWidth={2}
        strokeLinejoin="round"
        initial={{ pathLength: 0, opacity: 0 }}
        animate={{
          pathLength: newPlanDraw,
          opacity: newPlanDraw > 0 ? 1 : 0,
        }}
        transition={{ duration: 0 }}
        style={{ filter: "drop-shadow(0 0 8px rgba(0,229,255,0.6))" }}
      />

      {/* Action-bar markers on the price curve — only for spiked plan */}
      {spikedActions.map((a, i) => {
        if (a !== "CHARGE_GRID" && a !== "DISCHARGE_GRID") return null;
        const isCharge = a === "CHARGE_GRID";
        const color = isCharge ? "#8B5CF6" : "#00E5FF";
        const cx = priceX(i);
        const cy = priceY(spikedExport[i]);
        const visible = newPlanDraw > i / n;
        return (
          <motion.circle
            key={`act-${i}`}
            cx={cx}
            cy={cy}
            r={3.5}
            fill={color}
            initial={{ opacity: 0, scale: 0 }}
            animate={{ opacity: visible ? 1 : 0, scale: visible ? 1 : 0 }}
            transition={{ duration: 0.3, ease: EASE }}
            style={{ filter: `drop-shadow(0 0 4px ${color})` }}
          />
        );
      })}

      {/* "now" vertical marker */}
      <line
        x1={nowX}
        x2={nowX}
        y1={padding.top}
        y2={padding.top + innerH}
        stroke="#E6EDF3"
        strokeWidth={1}
        strokeOpacity={0.35}
      />
      <text
        x={nowX + 4}
        y={padding.top + 12}
        fontSize={10}
        fill="#E6EDF3"
        fillOpacity={0.5}
        fontFamily="var(--font-mono), ui-monospace, monospace"
        style={{ letterSpacing: "0.05em" }}
      >
        now
      </text>

      {/* Legend — bottom right */}
      <g transform={`translate(${padding.left + innerW - 180}, ${padding.top - 18})`}>
        <rect x={0} y={0} width={8} height={2} fill="#FFB020" />
        <text x={12} y={4} fontSize={10} fill="#8B949E" fontFamily="var(--font-mono), ui-monospace, monospace">
          export
        </text>
        <rect x={60} y={0} width={8} height={2} fill="#00E5FF" />
        <text x={72} y={4} fontSize={10} fill="#8B949E" fontFamily="var(--font-mono), ui-monospace, monospace">
          soc
        </text>
        <circle cx={108} cy={1} r={2.5} fill="#8B5CF6" />
        <text x={114} y={4} fontSize={10} fill="#8B949E" fontFamily="var(--font-mono), ui-monospace, monospace">
          charge
        </text>
        <circle cx={154} cy={1} r={2.5} fill="#00E5FF" />
        <text x={160} y={4} fontSize={10} fill="#8B949E" fontFamily="var(--font-mono), ui-monospace, monospace">
          discharge
        </text>
      </g>
    </svg>
  );
}

function clamp01(v: number): number {
  return Math.max(0, Math.min(1, v));
}

function formatPrice(v: number): string {
  if (Math.abs(v) >= 100) return v.toFixed(0);
  return v.toFixed(1);
}

function niceTicks(min: number, max: number, count: number): number[] {
  const span = max - min;
  const step = niceStep(span / count);
  const first = Math.ceil(min / step) * step;
  const out: number[] = [];
  for (let v = first; v <= max; v += step) out.push(Number(v.toFixed(6)));
  return out;
}

function niceStep(raw: number): number {
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / pow;
  let step;
  if (norm < 1.5) step = 1;
  else if (norm < 3) step = 2;
  else if (norm < 7) step = 5;
  else step = 10;
  return step * pow;
}
