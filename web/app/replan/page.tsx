"use client";

import { motion, AnimatePresence } from "framer-motion";
import { useEffect, useRef, useState, useCallback } from "react";

import { ReplanMoment, type ReplanMomentProps } from "@/components/replan";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * Standalone recording page. Fills the viewport, dark background, autoplays,
 * loops with a 1.5s rest. Space key triggers replay. Attempts to fetch live
 * spike data from POST /spike-demo, otherwise uses the hardcoded story.
 */
export default function ReplanRecordingPage() {
  const [data, setData] = useState<ReplanMomentProps | null>(null);
  const [hintVisible, setHintVisible] = useState(true);
  const [replayTick, setReplayTick] = useState(0);

  // Try the live endpoint, fall back to the hardcoded story.
  useEffect(() => {
    let alive = true;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 1200);
    (async () => {
      try {
        const base = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
        const r = await fetch(`${base}/spike-demo`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          signal: ctrl.signal,
        });
        clearTimeout(timer);
        if (!r.ok) throw new Error(String(r.status));
        const body = await r.json();
        if (alive) setData(body);
      } catch {
        if (alive) setData(DEMO_DATA);
      }
    })();
    return () => {
      alive = false;
      clearTimeout(timer);
      ctrl.abort();
    };
  }, []);

  // Fade the hint after 3 seconds.
  useEffect(() => {
    const id = setTimeout(() => setHintVisible(false), 3000);
    return () => clearTimeout(id);
  }, []);

  const replay = useCallback(() => {
    setReplayTick((t) => t + 1);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code === "Space") {
        e.preventDefault();
        replay();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [replay]);

  return (
    <main className="fixed inset-0 flex items-center justify-center overflow-hidden bg-bg">
      {/* Backdrop — mirrors the main app's atmosphere */}
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10 overflow-hidden">
        <div className="atmosphere-grid" />
        <div className="atmosphere-blob atmosphere-blob-1" />
        <div className="atmosphere-blob atmosphere-blob-2" />
      </div>

      {/* Centered animation. key forces remount when replayed. */}
      {data ? (
        <div className="flex w-full items-center justify-center px-8">
          <ReplanMoment key={replayTick} {...data} autoplay loop />
        </div>
      ) : (
        <div className="text-13 tracking-widest text-ink-faint">loading</div>
      )}

      {/* Replay button */}
      <button
        onClick={replay}
        className="absolute right-4 top-4 rounded-md border border-border bg-card/60 px-3 py-1.5 text-11 uppercase tracking-widest text-ink-dim backdrop-blur-sm transition-colors hover:border-cyan/40 hover:text-ink"
        aria-label="Replay animation"
      >
        Replay
      </button>

      {/* Keyboard hint — fades after 3s */}
      <AnimatePresence>
        {hintVisible ? (
          <motion.div
            key="hint"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.6, ease: EASE }}
            className="pointer-events-none absolute bottom-6 left-1/2 -translate-x-1/2 text-11 tracking-widest text-ink-faint"
          >
            press{" "}
            <span className="num mx-1 rounded-sm border border-border bg-card px-1.5 py-0.5 text-ink-dim">
              space
            </span>{" "}
            to replay
          </motion.div>
        ) : null}
      </AnimatePresence>
    </main>
  );
}

/* ------------------------------------------------------------------------- */
/* Hardcoded story — 60 intervals, 5-min each, 14:30 → 19:30.                */
/* Baseline plan is IDLE → HOLD_SOLAR midday, no arbitrage.                  */
/* Spiked plan charges hard now, discharges at 14:40–14:55.                  */
/* ------------------------------------------------------------------------- */

function buildDemoData(): ReplanMomentProps {
  const n = 60;
  const start = new Date("2026-04-23T04:30:00Z"); // 14:30 AEST
  const timestamps: string[] = [];
  for (let i = 0; i < n; i++) {
    const d = new Date(start.getTime() + i * 5 * 60_000);
    timestamps.push(d.toISOString());
  }

  // Baseline export price — modest solar-flooded afternoon, hovers 4–12 c/kWh.
  const baselineExport: number[] = [];
  const baselineImport: number[] = [];
  for (let i = 0; i < n; i++) {
    // Smooth curve, dips midday, rises toward peak 17:30.
    const h = i / n;
    const midDip = -3 * Math.exp(-Math.pow((h - 0.15) / 0.1, 2));
    const evenPeak = 26 * Math.exp(-Math.pow((h - 0.65) / 0.12, 2));
    baselineExport.push(8 + midDip + evenPeak);
    baselineImport.push(12 + midDip + evenPeak * 1.3);
  }

  // Spiked export: 14:40–14:55 (intervals 2–5) get +120 c/kWh kick.
  const spikeStartIdx = 2;
  const spikeEndIdx = 5;
  const spikedExport = baselineExport.slice();
  const spikedImport = baselineImport.slice();
  for (let i = spikeStartIdx; i <= spikeEndIdx; i++) {
    spikedExport[i] = baselineExport[i] + 120;
  }

  // Baseline actions: IDLE now, HOLD_SOLAR midday, IDLE evening, bit of discharge at peak.
  const baselineActions: string[] = [];
  for (let i = 0; i < n; i++) {
    if (i < 20) baselineActions.push(i < 4 ? "IDLE" : "HOLD_SOLAR");
    else if (i >= 38 && i <= 48) baselineActions.push("DISCHARGE_GRID");
    else baselineActions.push("IDLE");
  }

  // Spiked actions: CHARGE_GRID now (intervals 0-1), DISCHARGE_GRID at spike (2-5),
  // rest largely same except we pull some discharge forward.
  const spikedActions = baselineActions.slice();
  spikedActions[0] = "CHARGE_GRID";
  spikedActions[1] = "CHARGE_GRID";
  spikedActions[spikeStartIdx] = "DISCHARGE_GRID";
  spikedActions[spikeStartIdx + 1] = "DISCHARGE_GRID";
  spikedActions[spikeStartIdx + 2] = "DISCHARGE_GRID";
  spikedActions[spikeStartIdx + 3] = "DISCHARGE_GRID";
  // A couple of follow-through charge intervals to refill.
  spikedActions[10] = "CHARGE_GRID";
  spikedActions[11] = "CHARGE_GRID";
  spikedActions[12] = "CHARGE_GRID";

  // SOC trajectories — length n+1.
  const baselineSoc = traceSoc(baselineActions, 0.45);
  const spikedSoc = traceSoc(spikedActions, 0.45);

  return {
    baseline: {
      timestamps,
      actions: baselineActions,
      import_c_kwh: baselineImport,
      export_c_kwh: baselineExport,
      soc: baselineSoc,
    },
    spiked: {
      timestamps,
      actions: spikedActions,
      import_c_kwh: spikedImport,
      export_c_kwh: spikedExport,
      soc: spikedSoc,
    },
    spike: {
      start_ts: timestamps[spikeStartIdx],
      end_ts: timestamps[spikeEndIdx],
      magnitude_c_kwh: 120,
      channel: "export",
    },
    currentAction: { before: "HOLD_SOLAR", after: "CHARGE_GRID" },
    rationale:
      "Amber lifted the 14:40 export price by 120 c/kWh. Charging now at 11 c/kWh to discharge into the window nets a 118 c/kWh spread after efficiency.",
  };
}

function traceSoc(actions: string[], start: number): number[] {
  const soc = [start];
  // 5 min at 15 kW = 1.25 kWh. Capacity 64 kWh. Delta = ~0.0195 per interval.
  const delta = 0.02;
  for (let i = 0; i < actions.length; i++) {
    const a = actions[i];
    let s = soc[i];
    if (a === "CHARGE_GRID") s += delta;
    else if (a === "DISCHARGE_GRID") s -= delta;
    else if (a === "HOLD_SOLAR") s += delta * 0.5;
    s = Math.max(0.1, Math.min(0.95, s));
    soc.push(s);
  }
  return soc;
}

const DEMO_DATA: ReplanMomentProps = buildDemoData();
