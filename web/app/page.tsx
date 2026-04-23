"use client";

import { BacktestTable } from "@/components/dashboard/BacktestTable";
import { CurrentStatus } from "@/components/dashboard/CurrentStatus";
import { DataQuality } from "@/components/dashboard/DataQuality";
import { PricePanel } from "@/components/dashboard/PricePanel";
import { RationaleFeed } from "@/components/dashboard/RationaleFeed";
import { ReplanSection } from "@/components/dashboard/ReplanSection";
import { SOCPanel } from "@/components/dashboard/SOCPanel";
import { SpikeDemoButton } from "@/components/dashboard/SpikeDemoButton";

/**
 * Dashboard — the only page in the demo. CSS grid for precise sizing; we
 * deliberately avoid Tailwind's grid template classes so the layout stays
 * legible when tweaked.
 */
export default function Home() {
  return (
    <div className="flex flex-col gap-4 pt-2">
      {/* Top strip — eyebrow + spike demo */}
      <div className="flex items-end justify-between">
        <div>
          <div className="mb-1 flex items-center gap-2">
            <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-cyan" />
            <span className="text-11 uppercase tracking-widest text-ink-dim">
              Live
            </span>
          </div>
          <h1 className="text-24 font-semibold tracking-tight text-ink">
            Agent dashboard
          </h1>
          <p className="mt-0.5 max-w-[60ch] text-12 text-ink-faint">
            Re-plans every 30 minutes. Read the Opus rationale to see why the
            plan changes when prices or load shift.
          </p>
        </div>

        <SpikeDemoButton />
      </div>

      {/* Row 1 — SOC gauge | 24h price chart */}
      <section
        className="grid gap-4"
        style={{ gridTemplateColumns: "320px minmax(0, 1fr)" }}
      >
        <SOCPanel />
        <PricePanel />
      </section>

      {/* Row 2 — current status | rationale feed */}
      <section
        className="grid gap-4"
        style={{ gridTemplateColumns: "320px minmax(0, 1fr)" }}
      >
        <CurrentStatus />
        <RationaleFeed />
      </section>

      {/* Row 3 — Signature animation: ReplanMoment listens for spike-demo events */}
      <ReplanSection />

      {/* Row 4 — backtest table | data quality + audit */}
      <section
        className="grid gap-4"
        style={{ gridTemplateColumns: "minmax(0, 1.2fr) minmax(0, 1fr)" }}
      >
        <BacktestTable />
        <DataQuality />
      </section>

      <footer className="mt-4 pb-2 text-center text-11 text-ink-faint">
        <span className="num">NSW1 · Sigen 64 kWh · 24 kWp · AEMO 5MPD</span>
      </footer>
    </div>
  );
}
