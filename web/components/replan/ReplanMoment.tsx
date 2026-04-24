"use client";

import { motion, AnimatePresence, useReducedMotion } from "framer-motion";
import { useCallback, useEffect, useRef, useState } from "react";

import { PriceTimeline } from "./PriceTimeline";
import { ActionStrip } from "./ActionStrip";

const EASE = [0.16, 1, 0.3, 1] as const;

/** Total length of the animation in seconds — matches the 6s story + 2s hold. */
const TOTAL_DURATION_S = 8.0;
const LOOP_REST_S = 1.5;

export type ReplanMomentProps = {
  baseline: {
    timestamps: string[];
    actions: string[];
    import_c_kwh: number[];
    export_c_kwh: number[];
    soc: number[];
  };
  spiked: {
    timestamps: string[];
    actions: string[];
    import_c_kwh: number[];
    export_c_kwh: number[];
    soc: number[];
  };
  spike: {
    start_ts: string;
    end_ts: string;
    magnitude_c_kwh: number;
    channel: "import" | "export";
  };
  currentAction: { before: string; after: string };
  rationale: string;
  autoplay?: boolean;
  loop?: boolean;
  onComplete?: () => void;
};

type Phase =
  | "idle"
  | "baseline" // 0.0 - 1.0s
  | "waiting" // 1.0 - 2.0s
  | "spike" // 2.0 - 3.0s
  | "react" // 3.0 - 4.5s
  | "callout" // 4.5 - 6.0s
  | "summary" // 6.0 - 8.0s
  | "rest"; // 8.0 - 9.5s

/**
 * Derives the phase from the normalized playhead 0..1.
 */
function phaseFor(t: number): Phase {
  const s = t * TOTAL_DURATION_S;
  if (s < 1.0) return "baseline";
  if (s < 2.0) return "waiting";
  if (s < 3.0) return "spike";
  if (s < 4.5) return "react";
  if (s < 6.0) return "callout";
  if (s < 8.0) return "summary";
  return "rest";
}

export function ReplanMoment({
  baseline,
  spiked,
  spike,
  currentAction,
  rationale,
  autoplay = true,
  loop = true,
  onComplete,
}: ReplanMomentProps) {
  const reduce = useReducedMotion();
  const [phase, setPhase] = useState<Phase>("idle");
  const [playhead, setPlayhead] = useState(0);
  const [tick, setTick] = useState(0); // bump to restart
  const frameRef = useRef<number | null>(null);
  const startRef = useRef<number | null>(null);

  // Dimensions — keep the aspect ratio, scale inside a container
  const W = 1100;
  const H = 380;
  const STRIP_H = 28;
  const padding = { top: 32, right: 24, bottom: 28, left: 44 };
  const innerW = W - padding.left - padding.right;
  const n = baseline.timestamps.length;
  const segW = innerW / n;

  // Find spike interval indices. Use nearest-match because spike_start/end are
  // floored to minute precision while plan timestamps have sub-second precision.
  const spikeStartIdx = nearestTsIdx(baseline.timestamps, spike.start_ts);
  const spikeEndIdx = Math.max(
    spikeStartIdx,
    nearestTsIdx(baseline.timestamps, spike.end_ts)
  );

  // Start / stop / loop loop.
  const start = useCallback(() => {
    startRef.current = null;
    setPhase("baseline");
    setPlayhead(0);
  }, []);

  useEffect(() => {
    if (!autoplay) return;
    start();
  }, [autoplay, start, tick]);

  useEffect(() => {
    if (reduce) {
      // Skip straight to final summary state
      setPlayhead(1);
      setPhase("summary");
      return;
    }
    if (phase === "idle") return;

    const step = (ts: number) => {
      if (startRef.current == null) startRef.current = ts;
      const elapsed = (ts - startRef.current) / 1000;
      const t = Math.min(1, elapsed / TOTAL_DURATION_S);
      setPlayhead(t);
      setPhase(phaseFor(t));

      if (t < 1) {
        frameRef.current = requestAnimationFrame(step);
      } else {
        // Hold then loop (or complete)
        setPhase("rest");
        if (loop) {
          setTimeout(() => {
            onComplete?.();
            setTick((x) => x + 1);
          }, LOOP_REST_S * 1000);
        } else {
          onComplete?.();
        }
      }
    };

    frameRef.current = requestAnimationFrame(step);
    return () => {
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
    };
  }, [phase === "idle" ? "idle" : "run", tick, loop, onComplete, reduce]); // eslint-disable-line react-hooks/exhaustive-deps

  // Spike x / width in coords of the strip (same segW).
  const spikeX = segW * spikeStartIdx;
  const spikeW = segW * (spikeEndIdx - spikeStartIdx + 1);

  // Transition 0..1 for the action-strip recolor.
  const stripTransition = smoothstep((playhead - 0.38) / 0.22);

  // For the reduced-motion state we show the spiked plan as-is.
  const effectivePlayhead = reduce ? 1 : playhead;

  return (
    <div className="relative flex w-full flex-col items-center gap-4">
      {/* Copy above the chart */}
      <div className="relative flex h-[72px] w-full max-w-[1100px] items-start justify-between px-4">
        <div className="flex flex-col">
          <div className="text-11 uppercase tracking-widest text-ink-faint">
            Re-plan
          </div>
          <AnimatePresence mode="wait">
            {phase === "baseline" || phase === "waiting" ? (
              <motion.div
                key="copy-wait"
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.35, ease: EASE }}
                className="mt-1 text-18 text-ink-dim"
              >
                Every 30 minutes I re-plan.
              </motion.div>
            ) : phase === "spike" ? (
              <motion.div
                key="copy-spike"
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.35, ease: EASE }}
                className="mt-1 flex items-baseline gap-3 text-18 text-ink"
              >
                Amber revised export price
                <span className="num rounded-md border border-rose/50 bg-rose/10 px-2 py-0.5 text-15 text-rose">
                  {formatMagnitude(spike.magnitude_c_kwh)} c/kWh
                </span>
              </motion.div>
            ) : phase === "react" || phase === "callout" ? (
              <motion.div
                key="copy-action"
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.35, ease: EASE }}
                className="mt-1 flex items-baseline gap-3 text-18 text-ink"
              >
                <span className="text-ink-dim">Current action</span>
                <ActionPill label={currentAction.before} muted />
                <span className="num text-ink-faint">→</span>
                <ActionPill label={currentAction.after} />
              </motion.div>
            ) : phase === "summary" || phase === "rest" ? (
              <motion.div
                key="copy-summary"
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.45, ease: EASE }}
                className="mt-1 flex items-baseline gap-4 text-18 text-ink"
              >
                <span className="num">
                  {countChanged(baseline.actions, spiked.actions)}
                </span>
                <span className="text-ink-dim">intervals changed</span>
                <span className="text-ink-faint">·</span>
                <span className="num">
                  {arbKwh(baseline, spiked).toFixed(1)}
                </span>
                <span className="text-ink-dim">kWh of arbitrage captured</span>
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>

        {/* Clock, top right */}
        <Clock phase={phase} playhead={playhead} />
      </div>

      {/* Chart */}
      <div
        className="relative rounded-md border border-border bg-card/40"
        style={{ width: W, maxWidth: "100%" }}
      >
        <PriceTimeline
          width={W}
          height={H}
          padding={padding}
          baselineExport={baseline.export_c_kwh}
          spikedExport={spiked.export_c_kwh}
          baselineSoc={baseline.soc}
          spikedSoc={spiked.soc}
          baselineActions={baseline.actions}
          spikedActions={spiked.actions}
          playhead={effectivePlayhead}
          spikeStartIdx={spikeStartIdx}
          spikeEndIdx={spikeEndIdx}
          spikeLabel={`${formatMagnitude(spike.magnitude_c_kwh)} c/kWh`}
          xLabels={baseline.timestamps.map(shortTime)}
        />

        {/* Action strip docks at the bottom */}
        <div
          className="absolute left-0 right-0"
          style={{
            bottom: 4,
            paddingLeft: padding.left,
            paddingRight: padding.right,
          }}
        >
          <ActionStrip
            baseline={baseline.actions}
            spiked={spiked.actions}
            transition={reduce ? 1 : stripTransition}
            width={innerW}
            height={STRIP_H}
            spikeX={spikeX}
            spikeWidth={spikeW}
            showSpike={phase !== "idle" && phase !== "baseline" && phase !== "waiting"}
          />
        </div>
      </div>

      {/* Rationale panel below chart */}
      <div className="flex w-full max-w-[1100px] flex-col gap-2 px-4">
        <AnimatePresence>
          {(phase === "callout" || phase === "summary" || phase === "rest") && (
            <motion.div
              key="rationale"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.4, ease: EASE }}
              className="flex items-start gap-3 rounded-md border border-border bg-card/60 p-3"
            >
              <div className="mt-0.5 h-1.5 w-1.5 rounded-full bg-cyan shadow-[0_0_10px_0_rgba(0,229,255,0.8)]" />
              <div className="flex flex-col gap-1">
                <span className="text-11 uppercase tracking-widest text-ink-faint">
                  Rationale
                </span>
                <Typewriter text={rationale} charsPerSec={50} />
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

/** Small colored pill for the action names. */
function ActionPill({ label, muted = false }: { label: string; muted?: boolean }) {
  const bg: Record<string, string> = {
    IDLE: muted ? "bg-[#2A3642]/30" : "bg-[#2A3642]/60",
    HOLD_SOLAR: muted ? "bg-[#4A7A3E]/20" : "bg-[#4A7A3E]/40",
    CHARGE_GRID: muted ? "bg-violet/15" : "bg-violet/30",
    DISCHARGE_GRID: muted ? "bg-cyan/15" : "bg-cyan/25",
  };
  const border: Record<string, string> = {
    IDLE: "border-ink-faint/30",
    HOLD_SOLAR: "border-[#4A7A3E]/60",
    CHARGE_GRID: "border-violet/60",
    DISCHARGE_GRID: "border-cyan/60",
  };
  const text: Record<string, string> = {
    IDLE: "text-ink-dim",
    HOLD_SOLAR: "text-[#8BC077]",
    CHARGE_GRID: "text-violet",
    DISCHARGE_GRID: "text-cyan",
  };

  return (
    <span
      className={[
        "num inline-flex items-center rounded-md border px-2 py-0.5 text-13 tracking-wide",
        bg[label] ?? bg.IDLE,
        border[label] ?? border.IDLE,
        text[label] ?? text.IDLE,
        muted ? "opacity-60" : "",
      ].join(" ")}
    >
      {label}
    </span>
  );
}

/** A small clock that advances faintly through the "waiting" beat. */
function Clock({ phase, playhead }: { phase: Phase; playhead: number }) {
  // Start at 14:30, tick up to 14:31 during waiting phase, freeze after.
  const base = 14 * 60 + 30;
  const secs = Math.floor(playhead * TOTAL_DURATION_S);
  const min = base + Math.min(1, Math.floor(secs / 3));
  const hh = Math.floor(min / 60);
  const mm = min % 60;
  return (
    <div className="flex flex-col items-end">
      <div className="text-11 uppercase tracking-widest text-ink-faint">Now</div>
      <div className="num mt-1 text-24 tracking-tighter text-ink">
        {String(hh).padStart(2, "0")}:{String(mm).padStart(2, "0")}
      </div>
      <div className="mt-0.5 flex items-center gap-1.5">
        <span
          className={[
            "h-1.5 w-1.5 rounded-full",
            phase === "spike" || phase === "react"
              ? "bg-rose shadow-[0_0_10px_0_rgba(255,59,92,0.8)]"
              : "bg-cyan shadow-[0_0_10px_0_rgba(0,229,255,0.7)]",
            "animate-pulse-soft",
          ].join(" ")}
        />
        <span className="text-11 tracking-wider text-ink-faint">
          {phase === "spike" || phase === "react"
            ? "re-solving"
            : phase === "summary" || phase === "rest"
            ? "new plan committed"
            : "watching"}
        </span>
      </div>
    </div>
  );
}

/** Reveals text char-by-char at a fixed pace. */
function Typewriter({ text, charsPerSec }: { text: string; charsPerSec: number }) {
  const [shown, setShown] = useState("");
  useEffect(() => {
    setShown("");
    let i = 0;
    const interval = 1000 / charsPerSec;
    const id = setInterval(() => {
      i += 1;
      setShown(text.slice(0, i));
      if (i >= text.length) clearInterval(id);
    }, interval);
    return () => clearInterval(id);
  }, [text, charsPerSec]);
  return (
    <p className="text-15 leading-relaxed text-ink">
      {shown}
      <span className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-pulse-soft bg-cyan align-middle" />
    </p>
  );
}

function smoothstep(x: number): number {
  const t = Math.max(0, Math.min(1, x));
  return t * t * (3 - 2 * t);
}

function countChanged(a: string[], b: string[]): number {
  let n = 0;
  const len = Math.min(a.length, b.length);
  for (let i = 0; i < len; i++) if (a[i] !== b[i]) n += 1;
  return n;
}

/** Estimate kWh of arbitrage: sum of discharge intervals * nominal interval energy (5 min, 15 kW avg). */
function arbKwh(
  baseline: { actions: string[] },
  spiked: { actions: string[] }
): number {
  let delta = 0;
  const len = Math.min(baseline.actions.length, spiked.actions.length);
  for (let i = 0; i < len; i++) {
    const b = baseline.actions[i];
    const s = spiked.actions[i];
    if (s === "DISCHARGE_GRID" && b !== "DISCHARGE_GRID") delta += 1;
    if (s === "CHARGE_GRID" && b !== "CHARGE_GRID") delta += 1;
  }
  // Each interval = 5 min, average power 15 kW = 1.25 kWh. Halve because charges cancel discharges in count.
  return (delta * 1.25) / 2;
}

/** Return the index of the timestamp in `arr` closest to `target`. */
function nearestTsIdx(arr: string[], target: string): number {
  if (!arr.length) return 0;
  const targetMs = new Date(target).getTime();
  let best = 0;
  let bestDist = Math.abs(new Date(arr[0]).getTime() - targetMs);
  for (let i = 1; i < arr.length; i++) {
    const dist = Math.abs(new Date(arr[i]).getTime() - targetMs);
    if (dist < bestDist) {
      bestDist = dist;
      best = i;
    }
  }
  return best;
}

function shortTime(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
}

function formatMagnitude(v: number): string {
  const s = v >= 0 ? "+" : "";
  return `${s}${v.toFixed(0)}`;
}
