"use client";

import * as Tooltip from "@radix-ui/react-tooltip";
import { useMemo } from "react";

import { Card, CardHeader } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { useAudit, useSnapshot } from "@/hooks/useLiveData";
import { cn, fmtTime } from "@/lib/utils";

type Source = "HA" | "Amber" | "AEMO" | "BOM" | "Modbus";
type Tone = "ok" | "warn" | "crit";

const SOURCE_MATCH: Record<Source, string[]> = {
  HA: ["ha", "home_assistant", "soc", "load", "solar"],
  Amber: ["amber"],
  AEMO: ["aemo", "nemweb", "price"],
  BOM: ["bom", "weather", "open-meteo"],
  Modbus: ["modbus", "sigen"],
};

function classifySource(
  source: Source,
  staleSensors: string[],
  warnings: string[],
): { tone: Tone; note: string } {
  const needles = SOURCE_MATCH[source];
  const lcStale = staleSensors.map((s) => String(s).toLowerCase());
  const lcWarn = warnings.map((w) => String(w).toLowerCase());

  const hasStale = lcStale.some((s) => needles.some((n) => s.includes(n)));
  const matchingWarns = warnings.filter((w) =>
    needles.some((n) => String(w).toLowerCase().includes(n)),
  );

  if (hasStale) {
    return { tone: "crit", note: `Stale: ${staleSensors.filter((s) => needles.some((n) => String(s).toLowerCase().includes(n))).join(", ")}` };
  }
  if (matchingWarns.length > 0) {
    return { tone: "warn", note: matchingWarns.join(" · ") };
  }
  // Global warnings (unmatched) weakly nudge amber
  if (lcWarn.length > 0 && source === "HA") {
    // HA is the default catch-all for ambient warnings.
    return { tone: "warn", note: warnings.join(" · ") };
  }
  return { tone: "ok", note: "Live" };
}

export function DataQuality() {
  const { data: snap, isLoading: snapLoading } = useSnapshot();
  const { data: audit, isLoading: auditLoading } = useAudit(6);

  const statuses = useMemo(() => {
    const sources: Source[] = ["HA", "Amber", "AEMO", "BOM", "Modbus"];
    const stale = snap?.stale_sensors ?? [];
    const warnings = snap?.warnings ?? [];
    return sources.map((s) => ({ source: s, ...classifySource(s, stale, warnings) }));
  }, [snap]);

  return (
    <Card className="flex h-full flex-col">
      <CardHeader
        eyebrow="Telemetry"
        title="Data quality & actuator"
        right={
          <span className="num text-11 text-ink-faint">
            {snap?.timestamp ? fmtTime(snap.timestamp) : "—"}
          </span>
        }
      />

      <div className="mb-3">
        {snapLoading && !snap ? (
          <Skeleton className="h-7 w-full" />
        ) : (
          <Tooltip.Provider delayDuration={150}>
            <div className="flex flex-wrap items-center gap-1.5">
              {statuses.map((s) => (
                <SourcePill
                  key={s.source}
                  source={s.source}
                  tone={s.tone}
                  note={s.note}
                />
              ))}
            </div>
          </Tooltip.Provider>
        )}
      </div>

      <div className="hairline mb-3" />

      <div className="mb-2 flex items-center justify-between">
        <span className="text-11 uppercase tracking-widest text-ink-faint">
          Actuator audit
        </span>
        {audit?.entries?.length != null && (
          <span className="num text-11 text-ink-faint">
            {audit.entries.length} recent
          </span>
        )}
      </div>

      {auditLoading && !audit ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-6 w-full" />
          ))}
        </div>
      ) : (audit?.entries ?? []).length === 0 ? (
        <div className="rounded-sm border border-dashed border-border/70 px-3 py-4 text-center text-11 text-ink-faint">
          No actuator writes yet.
        </div>
      ) : (
        <ul className="flex-1 space-y-1 overflow-y-auto pr-1 text-12">
          {(audit?.entries ?? []).slice(-6).reverse().map((e, i) => (
            <li
              key={i}
              className="flex items-center justify-between gap-3 rounded-sm border border-border/60 bg-surface/50 px-2.5 py-1.5"
            >
              <span className="num text-11 text-ink-faint">
                {e.timestamp ? fmtTime(String(e.timestamp)) : "--:--"}
              </span>
              <span className="flex-1 truncate text-11 text-ink-dim">
                {e.register ?? "—"}
                {e.new_value != null && (
                  <span className="num ml-2 text-ink">→ {String(e.new_value)}</span>
                )}
              </span>
              <span
                className={cn(
                  "rounded-xs border px-1.5 py-0.5 text-11 uppercase tracking-wider",
                  e.dry_run
                    ? "border-amber/30 bg-amber/10 text-amber"
                    : e.result === "ok"
                      ? "border-cyan/30 bg-cyan/10 text-cyan"
                      : "border-rose/30 bg-rose/10 text-rose",
                )}
              >
                {e.dry_run ? "dry" : (e.result ?? "ok")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function SourcePill({
  source,
  tone,
  note,
}: {
  source: string;
  tone: Tone;
  note: string;
}) {
  const styles = {
    ok: "border-cyan/30 bg-cyan/10 text-cyan",
    warn: "border-amber/30 bg-amber/10 text-amber",
    crit: "border-rose/30 bg-rose/10 text-rose",
  }[tone];

  const dotStyles = {
    ok: "bg-cyan",
    warn: "bg-amber",
    crit: "bg-rose",
  }[tone];

  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <span
          className={cn(
            "inline-flex items-center gap-1.5 rounded-xs border px-2 py-0.5",
            "text-11 font-medium uppercase tracking-wider cursor-default",
            styles,
          )}
        >
          <span className={cn("h-1.5 w-1.5 rounded-full", dotStyles)} />
          {source}
        </span>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          sideOffset={6}
          className="z-50 max-w-[280px] rounded-sm border border-border-strong bg-bg/95 px-2.5 py-1.5 text-11 text-ink-dim shadow-card backdrop-blur-sm"
        >
          {note}
          <Tooltip.Arrow className="fill-border-strong" />
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}
