"use client";

import { ReplanMoment, type ReplanMomentProps } from "@/components/replan";
import { useEffect, useRef, useState } from "react";

/**
 * Listens for the custom "spike-demo" event dispatched by SpikeDemoButton.
 * When fired, normalises the API response into ReplanMoment's shape and
 * plays the signature animation. Until then, shows a subtle prompt.
 */
export function ReplanSection() {
  const [data, setData] = useState<ReplanMomentProps | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (!detail) return;
      const props = toReplanProps(detail);
      if (!props) return;
      setData(props);
      // Scroll into view so the animation is visible after the button click
      ref.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    };
    window.addEventListener("spike-demo", handler as EventListener);
    return () =>
      window.removeEventListener("spike-demo", handler as EventListener);
  }, []);

  return (
    <section
      ref={ref}
      data-replan-moment
      className="relative overflow-hidden rounded-md border border-border bg-card"
    >
      {data ? (
        <ReplanMoment {...data} autoplay loop={false} />
      ) : (
        <div className="flex items-center justify-center px-6 py-12 text-center">
          <div>
            <div className="mb-2 flex items-center justify-center gap-2">
              <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-violet" />
              <span className="text-11 uppercase tracking-widest text-violet">
                Signature animation
              </span>
            </div>
            <div className="text-15 font-medium text-ink">
              Inject a synthetic spike to see the agent re-plan.
            </div>
            <div className="mt-1 text-12 text-ink-faint">
              Or open{" "}
              <a href="/replan" className="underline hover:text-ink">
                /replan
              </a>{" "}
              for the standalone loop.
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

type AnyObj = Record<string, unknown>;

function toReplanProps(raw: unknown): ReplanMomentProps | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as AnyObj;
  const baseline = r.baseline_plan as AnyObj | undefined;
  const spiked = r.spiked_plan as AnyObj | undefined;
  if (!baseline || !spiked) return null;

  const spikeStart = (r.spike_start as string) ?? new Date().toISOString();
  const spikeEnd =
    (r.spike_end as string) ??
    new Date(Date.now() + 15 * 60_000).toISOString();

  const currentIdx = (baseline.current_idx as number | null) ?? 0;
  const beforeAction =
    ((baseline.actions as string[] | undefined) ?? [])[currentIdx] ?? "IDLE";
  const afterAction =
    ((spiked.actions as string[] | undefined) ?? [])[
      (spiked.current_idx as number | null) ?? currentIdx
    ] ?? "IDLE";

  const rationale =
    (r.spiked_rationale as string) ??
    (r.diff_summary as string) ??
    "Agent re-planned in response to the injected spike.";

  return {
    baseline: pickPlan(baseline),
    spiked: pickPlan(spiked),
    spike: {
      start_ts: spikeStart,
      end_ts: spikeEnd,
      magnitude_c_kwh: (r.spike_c_kwh as number) ?? 120,
      channel: ((r.channel as "import" | "export") ?? "export"),
    },
    currentAction: { before: beforeAction, after: afterAction },
    rationale,
    autoplay: true,
    loop: false,
  };
}

function pickPlan(plan: AnyObj) {
  return {
    timestamps: (plan.timestamps as string[]) ?? [],
    actions: (plan.actions as string[]) ?? [],
    import_c_kwh: (plan.import_c_kwh as number[]) ?? [],
    export_c_kwh: (plan.export_c_kwh as number[]) ?? [],
    soc: (plan.soc as number[]) ?? [],
  };
}
