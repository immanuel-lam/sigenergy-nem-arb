"use client";

import { Card, CardHeader } from "@/components/ui/Card";
import { Gauge } from "@/components/ui/Gauge";
import { Skeleton } from "@/components/ui/Skeleton";
import { usePlan, useSnapshot } from "@/hooks/useLiveData";
import { fmt } from "@/lib/utils";

const FLOOR = 0.1;
const CEILING = 0.95;

export function SOCPanel() {
  const { data: snap, error: snapErr, isLoading } = useSnapshot();
  const { data: plan } = usePlan();

  if (isLoading && !snap) {
    return (
      <Card className="flex h-full flex-col">
        <CardHeader eyebrow="Battery" title="State of charge" />
        <div className="flex flex-1 items-center justify-center">
          <Skeleton className="h-[220px] w-[220px] rounded-full" />
        </div>
      </Card>
    );
  }

  if (snapErr) {
    return (
      <Card className="flex h-full flex-col">
        <CardHeader eyebrow="Battery" title="State of charge" />
        <ErrorState endpoint="/snapshot" />
      </Card>
    );
  }

  const socPct =
    snap?.soc_pct != null && Number.isFinite(snap.soc_pct) ? snap.soc_pct : null;
  const socFrac = socPct != null ? socPct / 100 : 0.5;

  // Next-interval SOC from plan, if aligned.
  let nextSocFrac: number | null = null;
  let nextDeltaPct: number | null = null;
  if (plan && plan.soc.length > 0) {
    const idx = Math.max(0, plan.current_idx ?? 0);
    const nextIdx = Math.min(plan.soc.length - 1, idx + 1);
    if (Number.isFinite(plan.soc[nextIdx])) {
      nextSocFrac = plan.soc[nextIdx];
      if (socPct != null) {
        nextDeltaPct = nextSocFrac * 100 - socPct;
      }
    }
  }

  const batPower = snap?.battery_power_kw ?? null;
  const batLabel =
    batPower == null
      ? "—"
      : batPower > 0.05
        ? "Charging"
        : batPower < -0.05
          ? "Discharging"
          : "Idle";
  const batTone =
    batPower == null
      ? "text-ink-dim"
      : batPower > 0.05
        ? "text-cyan"
        : batPower < -0.05
          ? "text-rose"
          : "text-ink-dim";

  return (
    <Card className="flex h-full flex-col">
      <CardHeader eyebrow="Battery" title="State of charge" />

      <div className="flex flex-1 items-center justify-center py-2">
        <Gauge
          value={socFrac}
          floor={FLOOR}
          ceiling={CEILING}
          next={nextSocFrac}
          size={220}
        >
          <div className="flex flex-col items-center">
            <div className="num text-48 font-medium leading-none tracking-tightest text-ink">
              {socPct == null ? "—" : fmt(socPct, 1)}
              <span className="ml-1 text-18 text-ink-dim">%</span>
            </div>
            <div className="mt-1 text-11 uppercase tracking-widest text-ink-faint">
              SOC
            </div>
          </div>
        </Gauge>
      </div>

      {/* Footer — power + planned delta */}
      <div className="mt-3 grid grid-cols-2 gap-3 border-t border-border pt-3">
        <div>
          <div className="text-11 uppercase tracking-wider text-ink-faint">
            Power
          </div>
          <div className={`num mt-0.5 text-13 ${batTone}`}>
            {batPower == null ? "—" : `${fmt(Math.abs(batPower), 2)} kW`}
            <span className="ml-1 text-11 text-ink-faint uppercase">
              {batLabel}
            </span>
          </div>
        </div>
        <div className="text-right">
          <div className="text-11 uppercase tracking-wider text-ink-faint">
            Next interval
          </div>
          <div className="num mt-0.5 text-13 text-ink">
            {nextSocFrac == null ? "—" : `${fmt(nextSocFrac * 100, 1)}%`}
            {nextDeltaPct != null && Math.abs(nextDeltaPct) >= 0.05 && (
              <span
                className={`ml-2 text-11 ${nextDeltaPct >= 0 ? "text-cyan" : "text-rose"}`}
              >
                {nextDeltaPct >= 0 ? "+" : ""}
                {fmt(nextDeltaPct, 1)}
              </span>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}

function ErrorState({ endpoint }: { endpoint: string }) {
  return (
    <div className="flex flex-1 items-center justify-center px-4 py-6">
      <div className="text-center">
        <div className="text-12 text-amber">Data source unreachable</div>
        <div className="num mt-1 text-11 text-ink-faint">{endpoint}</div>
      </div>
    </div>
  );
}
