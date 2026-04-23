"use client";

import { useMemo } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Badge, actionVariant } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { usePlan } from "@/hooks/useLiveData";
import { chart as chartTokens, color } from "@/lib/design-tokens";
import { fmt, fmtTime } from "@/lib/utils";

// Flat row shape consumed by recharts.
type Row = {
  ts: number;
  hour: string;
  importC: number;
  exportC: number;
  exportBelowZero: number | null;
  socPct: number;
  action: string;
};

const ACTION_COLORS: Record<string, string> = {
  CHARGE_GRID: color.cyan,
  DISCHARGE_GRID: "#4ade80",
  HOLD_SOLAR: color.violet,
  IDLE: "transparent",
};

export function PricePanel() {
  const { data: plan, error, isLoading } = usePlan();

  const rows: Row[] = useMemo(() => {
    if (!plan) return [];
    return plan.timestamps.map((ts, i) => {
      const t = new Date(ts).getTime();
      const e = plan.export_c_kwh[i];
      return {
        ts: t,
        hour: new Date(ts).toLocaleTimeString("en-AU", {
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
          timeZone: "Australia/Sydney",
        }),
        importC: plan.import_c_kwh[i],
        exportC: e,
        exportBelowZero: e < 0 ? e : null,
        socPct: plan.soc[i] * 100,
        action: plan.actions[i],
      };
    });
  }, [plan]);

  // Convert runs of identical action into ReferenceArea bands.
  const bands = useMemo(() => {
    if (rows.length === 0) return [] as { x1: number; x2: number; action: string }[];
    const out: { x1: number; x2: number; action: string }[] = [];
    let start = 0;
    for (let i = 1; i <= rows.length; i++) {
      if (i === rows.length || rows[i].action !== rows[start].action) {
        const action = rows[start].action;
        if (action && action !== "IDLE") {
          out.push({ x1: rows[start].ts, x2: rows[i - 1].ts, action });
        }
        start = i;
      }
    }
    return out;
  }, [rows]);

  // Hour ticks — every 3 hours so the axis doesn't get crowded.
  const hourTicks = useMemo(() => {
    if (rows.length === 0) return [] as number[];
    const out: number[] = [];
    let lastHour = -1;
    for (const r of rows) {
      const d = new Date(r.ts);
      const h = d.getHours();
      if (h !== lastHour && h % 3 === 0 && d.getMinutes() === 0) {
        out.push(r.ts);
        lastHour = h;
      }
    }
    return out;
  }, [rows]);

  const currentTs = plan && plan.current_idx >= 0 && plan.current_idx < rows.length
    ? rows[plan.current_idx].ts
    : null;

  const currentAction = plan?.actions[plan?.current_idx ?? 0] ?? null;

  if (isLoading && !plan) {
    return (
      <Card className="flex h-full flex-col">
        <CardHeader eyebrow="Forecast" title="24h price & plan" />
        <Skeleton className="flex-1 min-h-[340px]" />
      </Card>
    );
  }

  if (error) {
    return (
      <Card className="flex h-full flex-col">
        <CardHeader eyebrow="Forecast" title="24h price & plan" />
        <div className="flex flex-1 items-center justify-center">
          <div className="text-center">
            <div className="text-12 text-amber">
              Data source unreachable
            </div>
            <div className="num mt-1 text-11 text-ink-faint">/plan/current</div>
          </div>
        </div>
      </Card>
    );
  }

  return (
    <Card className="flex h-full flex-col">
      <CardHeader
        eyebrow="Forecast"
        title="24h price & plan"
        right={
          <div className="flex items-center gap-2">
            <LegendDot color={chartTokens.price} label="Import" />
            <LegendDot color={chartTokens.discharge} label="Export" />
            <span className="mx-1 h-3 w-px bg-border" />
            <ActionLegend color={color.cyan} label="Charge" />
            <ActionLegend color="#4ade80" label="Discharge" />
            <ActionLegend color={color.violet} label="Hold" />
          </div>
        }
      />

      <div className="flex-1" style={{ minHeight: 340 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={rows}
            margin={{ top: 8, right: 12, left: 4, bottom: 4 }}
          >
            <defs>
              <linearGradient id="priceImport" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color.amber} stopOpacity={0.25} />
                <stop offset="100%" stopColor={color.amber} stopOpacity={0} />
              </linearGradient>
              <linearGradient id="priceExportNeg" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color.rose} stopOpacity={0} />
                <stop offset="100%" stopColor={color.rose} stopOpacity={0.3} />
              </linearGradient>
            </defs>

            <CartesianGrid stroke="transparent" />

            {/* Action bands — beneath the lines */}
            {bands.map((b, i) => (
              <ReferenceArea
                key={`${b.action}-${i}`}
                x1={b.x1}
                x2={b.x2}
                fill={ACTION_COLORS[b.action] || "transparent"}
                fillOpacity={0.1}
                stroke="none"
                ifOverflow="visible"
              />
            ))}

            {/* Zero line — a subtle rule */}
            <ReferenceLine
              y={0}
              stroke={color.border}
              strokeWidth={1}
              strokeDasharray="2 3"
            />

            {currentTs != null && (
              <ReferenceLine
                x={currentTs}
                stroke={color.cyan}
                strokeWidth={1}
                strokeOpacity={0.5}
              />
            )}

            <XAxis
              type="number"
              dataKey="ts"
              domain={["dataMin", "dataMax"]}
              scale="time"
              ticks={hourTicks}
              tickFormatter={(v) => fmtTime(new Date(v as number))}
              tick={{ fill: color.inkFaint, fontSize: 11, fontFamily: "var(--font-mono)" }}
              axisLine={{ stroke: color.border }}
              tickLine={{ stroke: color.border }}
            />
            <YAxis
              yAxisId="price"
              tick={{ fill: color.inkFaint, fontSize: 11, fontFamily: "var(--font-mono)" }}
              axisLine={{ stroke: "transparent" }}
              tickLine={{ stroke: "transparent" }}
              width={38}
              tickFormatter={(v) => `${v}`}
            />

            {/* Import — amber area */}
            <Area
              yAxisId="price"
              type="monotone"
              dataKey="importC"
              stroke={color.amber}
              strokeWidth={1.6}
              fill="url(#priceImport)"
              dot={false}
              isAnimationActive={false}
            />
            {/* Export — rose line */}
            <Line
              yAxisId="price"
              type="monotone"
              dataKey="exportC"
              stroke={color.rose}
              strokeWidth={1.6}
              dot={false}
              isAnimationActive={false}
            />
            {/* Negative-export fill — darker rose below 0 */}
            <Area
              yAxisId="price"
              type="monotone"
              dataKey="exportBelowZero"
              stroke="transparent"
              fill="url(#priceExportNeg)"
              dot={false}
              isAnimationActive={false}
              connectNulls={false}
            />

            <Tooltip
              content={<PriceTooltip />}
              cursor={{ stroke: color.border, strokeWidth: 1 }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {currentAction && (
        <div className="mt-3 flex items-center gap-2 border-t border-border pt-3">
          <span className="text-11 uppercase tracking-wider text-ink-faint">
            Now
          </span>
          <Badge variant={actionVariant(currentAction)} dot>
            {currentAction.replace("_", " ")}
          </Badge>
          <span className="text-11 text-ink-faint">c/kWh shown</span>
        </div>
      )}
    </Card>
  );
}

function LegendDot({ color: c, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="h-1.5 w-3 rounded-full" style={{ background: c }} />
      <span className="text-11 uppercase tracking-wider text-ink-faint">
        {label}
      </span>
    </span>
  );
}

function ActionLegend({ color: c, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span
        className="h-2 w-2 rounded-xs"
        style={{ background: c, opacity: 0.4 }}
      />
      <span className="text-11 uppercase tracking-wider text-ink-faint">
        {label}
      </span>
    </span>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function PriceTooltip({ active, payload }: any) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0].payload as Row;
  return (
    <div className="rounded-sm border border-border-strong bg-bg/95 px-3 py-2 text-12 shadow-card backdrop-blur-sm">
      <div className="num mb-1 text-11 text-ink-dim">{row.hour}</div>
      <div className="flex items-center justify-between gap-4">
        <span className="text-11 uppercase tracking-wider text-ink-faint">
          Import
        </span>
        <span className="num text-12 text-amber">{fmt(row.importC, 1)} c</span>
      </div>
      <div className="flex items-center justify-between gap-4">
        <span className="text-11 uppercase tracking-wider text-ink-faint">
          Export
        </span>
        <span className="num text-12 text-rose">{fmt(row.exportC, 1)} c</span>
      </div>
      <div className="flex items-center justify-between gap-4">
        <span className="text-11 uppercase tracking-wider text-ink-faint">
          SOC
        </span>
        <span className="num text-12 text-ink">{fmt(row.socPct, 1)}%</span>
      </div>
      <div className="mt-1 border-t border-border pt-1 text-11 uppercase tracking-wider text-ink-dim">
        {row.action?.replace("_", " ")}
      </div>
    </div>
  );
}
