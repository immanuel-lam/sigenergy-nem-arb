"use client";

import { useEffect, useState } from "react";

import { Badge, actionVariant } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { usePlan, useSnapshot } from "@/hooks/useLiveData";
import { fmt } from "@/lib/utils";

/** Rounded-down 30-min slot boundary. */
function nextReplanAt(now: Date): Date {
  const d = new Date(now);
  const m = d.getMinutes();
  const next = m < 30 ? 30 : 60;
  d.setMinutes(next, 0, 0);
  return d;
}

function formatCountdown(ms: number): string {
  if (ms < 0) return "00:00";
  const s = Math.floor(ms / 1000);
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

export function CurrentStatus() {
  const { data: plan, isLoading: planLoading } = usePlan();
  const { data: snap } = useSnapshot();
  const [countdown, setCountdown] = useState<string>("--:--");

  useEffect(() => {
    const tick = () => {
      const now = new Date();
      const target = nextReplanAt(now);
      setCountdown(formatCountdown(target.getTime() - now.getTime()));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  if (planLoading && !plan) {
    return (
      <Card className="flex h-full flex-col">
        <CardHeader eyebrow="Now" title="Current interval" />
        <Skeleton className="h-10 w-32" />
        <div className="mt-4 space-y-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
        </div>
      </Card>
    );
  }

  const idx = plan?.current_idx ?? 0;
  const action = (plan?.actions?.[idx] ?? "IDLE") as string;
  const chargeKwh = plan?.charge_grid_kwh?.[idx] ?? 0;
  const dischargeKwh = plan?.discharge_grid_kwh?.[idx] ?? 0;
  // Scheduler uses 5-min intervals, so kWh * 12 = kW.
  const chargeKw = chargeKwh * 12;
  const dischargeKw = dischargeKwh * 12;

  const importC = plan?.import_c_kwh?.[idx];
  const exportC = plan?.export_c_kwh?.[idx];

  const actionLabel = action.replace("_", " ");

  return (
    <Card className="flex h-full flex-col">
      <CardHeader
        eyebrow="Now"
        title="Current interval"
        right={
          <div className="flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-cyan" />
            <span className="num text-11 text-ink-faint">{countdown}</span>
          </div>
        }
      />

      <div className="mt-1">
        <Badge variant={actionVariant(action)} dot className="text-12 px-2.5 py-1">
          {actionLabel}
        </Badge>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3">
        <Stat
          label="Charge"
          value={chargeKw > 0 ? fmt(chargeKw, 2) : "—"}
          unit={chargeKw > 0 ? "kW" : ""}
          tone="cyan"
        />
        <Stat
          label="Discharge"
          value={dischargeKw > 0 ? fmt(dischargeKw, 2) : "—"}
          unit={dischargeKw > 0 ? "kW" : ""}
          tone="rose"
        />
        <Stat
          label="Import price"
          value={importC != null ? fmt(importC, 1) : "—"}
          unit={importC != null ? "c" : ""}
          tone="amber"
        />
        <Stat
          label="Export price"
          value={exportC != null ? fmt(exportC, 1) : "—"}
          unit={exportC != null ? "c" : ""}
          tone={exportC != null && exportC < 0 ? "rose" : "default"}
        />
      </div>

      <div className="mt-4 border-t border-border pt-3">
        <div className="flex items-center justify-between text-11 uppercase tracking-wider text-ink-faint">
          <span>House load</span>
          <span className="num text-ink-dim">
            {snap?.load_kw != null ? `${fmt(snap.load_kw, 2)} kW` : "—"}
          </span>
        </div>
        <div className="mt-1 flex items-center justify-between text-11 uppercase tracking-wider text-ink-faint">
          <span>Solar</span>
          <span className="num text-ink-dim">
            {snap?.solar_kw != null ? `${fmt(snap.solar_kw, 2)} kW` : "—"}
          </span>
        </div>
      </div>

      <div className="mt-auto pt-4 text-11 text-ink-faint">
        Next re-plan in <span className="num text-ink-dim">{countdown}</span>
      </div>
    </Card>
  );
}

function Stat({
  label,
  value,
  unit,
  tone,
}: {
  label: string;
  value: string;
  unit?: string;
  tone: "default" | "cyan" | "amber" | "rose";
}) {
  const toneCls = {
    default: "text-ink",
    cyan: "text-cyan",
    amber: "text-amber",
    rose: "text-rose",
  }[tone];
  return (
    <div>
      <div className="text-11 uppercase tracking-wider text-ink-faint">
        {label}
      </div>
      <div className={`num mt-0.5 text-15 ${toneCls}`}>
        {value}
        {unit && <span className="ml-1 text-11 text-ink-faint">{unit}</span>}
      </div>
    </div>
  );
}
