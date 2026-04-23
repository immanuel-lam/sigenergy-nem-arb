"use client";

import { useMemo } from "react";
import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardHeader } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { type BacktestResult, useBacktest } from "@/hooks/useLiveData";
import { color } from "@/lib/design-tokens";
import { fmt, fmtTime } from "@/lib/utils";

type Row = {
  key: keyof Pick<BacktestResult, "agent" | "b1_self_consume" | "b2_static_tou" | "b3_amber_actual">;
  label: string;
  cost: number;
  imp: number;
  exp: number;
  cycles: number;
  agent?: boolean;
};

export function BacktestTable() {
  const { data, error, isLoading } = useBacktest();

  const rows: Row[] = useMemo(() => {
    if (!data) return [];
    return [
      { key: "agent", label: "Agent", cost: data.agent.cost_dollars, imp: data.agent.import_kwh, exp: data.agent.export_kwh, cycles: data.agent.cycles, agent: true },
      { key: "b1_self_consume", label: "Self-consume", cost: data.b1_self_consume.cost_dollars, imp: data.b1_self_consume.import_kwh, exp: data.b1_self_consume.export_kwh, cycles: data.b1_self_consume.cycles },
      { key: "b2_static_tou", label: "Static TOU", cost: data.b2_static_tou.cost_dollars, imp: data.b2_static_tou.import_kwh, exp: data.b2_static_tou.export_kwh, cycles: data.b2_static_tou.cycles },
      { key: "b3_amber_actual", label: "Amber actual", cost: data.b3_amber_actual.cost_dollars, imp: data.b3_amber_actual.import_kwh, exp: data.b3_amber_actual.export_kwh, cycles: data.b3_amber_actual.cycles },
    ];
  }, [data]);

  const computedAt = data?.computed_at ? fmtTime(data.computed_at) : "—";

  return (
    <Card className="flex h-full flex-col">
      <CardHeader
        eyebrow="Backtest"
        title="7-day comparison"
        right={
          <span className="num text-11 text-ink-faint">
            Computed {computedAt}
          </span>
        }
      />

      {isLoading && !data ? (
        <div className="space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-7 w-full" />
          ))}
        </div>
      ) : error ? (
        <div className="flex flex-1 items-center justify-center py-8">
          <div className="text-center">
            <div className="text-12 text-amber">Data source unreachable</div>
            <div className="num mt-1 text-11 text-ink-faint">
              /backtest/latest
            </div>
          </div>
        </div>
      ) : (
        <>
          <div className="overflow-hidden rounded-sm border border-border">
            <table className="w-full border-collapse">
              <thead>
                <tr className="bg-surface/60 text-11 uppercase tracking-wider text-ink-faint">
                  <th className="px-3 py-2 text-left font-medium">Strategy</th>
                  <th className="px-3 py-2 text-right font-medium">Cost $</th>
                  <th className="px-3 py-2 text-right font-medium">Import</th>
                  <th className="px-3 py-2 text-right font-medium">Export</th>
                  <th className="px-3 py-2 text-right font-medium">Cycles</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.key}
                    className={
                      r.agent
                        ? "border-t border-cyan/20 bg-cyan/[0.05] shadow-glow-cyan"
                        : "border-t border-border"
                    }
                  >
                    <td className="px-3 py-2 text-13">
                      <span
                        className={
                          r.agent
                            ? "font-medium text-cyan"
                            : "text-ink-dim"
                        }
                      >
                        {r.label}
                      </span>
                    </td>
                    <td className="num px-3 py-2 text-right text-13 text-ink">
                      {fmt(r.cost, 2)}
                    </td>
                    <td className="num px-3 py-2 text-right text-12 text-ink-dim">
                      {fmt(r.imp, 1)}
                    </td>
                    <td className="num px-3 py-2 text-right text-12 text-ink-dim">
                      {fmt(r.exp, 1)}
                    </td>
                    <td className="num px-3 py-2 text-right text-12 text-ink-dim">
                      {fmt(r.cycles, 2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-4 min-h-[120px] flex-1">
            <ResponsiveContainer width="100%" height={120}>
              <BarChart
                data={rows}
                margin={{ top: 4, right: 8, left: 0, bottom: 4 }}
              >
                <XAxis
                  dataKey="label"
                  tick={{
                    fill: color.inkFaint,
                    fontSize: 11,
                  }}
                  axisLine={{ stroke: color.border }}
                  tickLine={false}
                />
                <YAxis
                  tick={{
                    fill: color.inkFaint,
                    fontSize: 11,
                    fontFamily: "var(--font-mono)",
                  }}
                  axisLine={{ stroke: "transparent" }}
                  tickLine={false}
                  width={34}
                />
                <Tooltip
                  cursor={{ fill: color.border, fillOpacity: 0.4 }}
                  contentStyle={{
                    background: "rgba(10,14,20,0.95)",
                    border: `1px solid ${color.borderStrong}`,
                    borderRadius: 6,
                    fontSize: 12,
                  }}
                  labelStyle={{ color: color.inkDim }}
                />
                <Bar dataKey="cost" radius={[3, 3, 0, 0]}>
                  {rows.map((r) => (
                    <Cell
                      key={r.key}
                      fill={r.agent ? color.cyan : color.border}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="mt-2 text-11 text-ink-faint">
            {data
              ? `${data.period.days}-day window, perfect-foresight replay`
              : ""}
          </div>
        </>
      )}
    </Card>
  );
}
