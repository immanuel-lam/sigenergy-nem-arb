"use client";

import useSWR from "swr";
import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Shared types — loose on purpose. Backend is the source of truth.
// ---------------------------------------------------------------------------

export type Snapshot = {
  timestamp: string;
  soc_pct: number | null;
  load_kw: number | null;
  solar_kw: number | null;
  battery_power_kw: number | null;
  price_forecast: {
    n: number;
    min_import_c?: number | null;
    max_import_c?: number | null;
    mean_import_c?: number | null;
    first_ts?: string | null;
    last_ts?: string | null;
    price_column?: string;
  };
  stale_sensors: string[];
  warnings: string[];
  weather_n: number;
  error?: string;
};

export type PlanAction =
  | "IDLE"
  | "CHARGE_GRID"
  | "DISCHARGE_GRID"
  | "HOLD_SOLAR";

export type Plan = {
  timestamps: string[];
  actions: PlanAction[];
  soc: number[]; // 0..1
  import_c_kwh: number[];
  export_c_kwh: number[];
  load_kw: number[];
  solar_kw: number[];
  charge_grid_kwh: number[];
  discharge_grid_kwh: number[];
  current_idx: number | null;
  summary: Record<string, unknown>;
  created_at: string | null;
};

export type RationaleEntry = {
  timestamp: string | null;
  action: string | null;
  rationale: string;
};

export type AuditEntry = {
  timestamp?: string | null;
  register?: string | null;
  old_value?: unknown;
  new_value?: unknown;
  reason?: string | null;
  result?: string | null;
  dry_run?: boolean;
  [k: string]: unknown;
};

export type BacktestRow = {
  cost_dollars: number;
  import_kwh: number;
  export_kwh: number;
  cycles: number;
};

export type BacktestResult = {
  agent: BacktestRow;
  b1_self_consume: BacktestRow;
  b2_static_tou: BacktestRow;
  b3_amber_actual: BacktestRow;
  period: { start: string; end: string; days: number };
  computed_at: string;
  cached: boolean;
};

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

const fetcher = <T>(path: string) => api<T>(path);

// Shared SWR defaults: keep previous data during revalidation so panels don't
// flash skeletons, and never revalidate on focus (the dashboard is usually
// a pinned tab during a demo).
const SWR_COMMON = {
  revalidateOnFocus: false,
  keepPreviousData: true,
} as const;

export function useSnapshot() {
  return useSWR<Snapshot>("/snapshot", fetcher, {
    ...SWR_COMMON,
    refreshInterval: 20_000,
  });
}

export function usePlan() {
  // Plan only changes when the agent loop runs (every 30 min) or someone
  // hits /plan/refresh. Polling every minute is plenty — faster polling
  // just triggers unnecessary Recharts re-renders of 288-point series.
  return useSWR<Plan>("/plan/current", fetcher, {
    ...SWR_COMMON,
    refreshInterval: 60_000,
  });
}

export function useRationale(limit = 10) {
  return useSWR<RationaleEntry[]>(`/rationale?limit=${limit}`, fetcher, {
    ...SWR_COMMON,
    refreshInterval: 30_000,
  });
}

export function useAudit(limit = 10) {
  return useSWR<{ entries: AuditEntry[]; summary: Record<string, unknown> }>(
    `/audit?limit=${limit}`,
    fetcher,
    { ...SWR_COMMON, refreshInterval: 60_000 },
  );
}

export function useSpikeEvents(limit = 10) {
  return useSWR<{ raw: string }[]>(`/spike-events?limit=${limit}`, fetcher, {
    ...SWR_COMMON,
    refreshInterval: 60_000,
  });
}

export function useBacktest() {
  return useSWR<BacktestResult>("/backtest/latest", fetcher, {
    // backtest is expensive — don't auto-refetch.
    ...SWR_COMMON,
    revalidateIfStale: false,
    revalidateOnReconnect: false,
  });
}

export function useHealth() {
  return useSWR<{ ok: boolean; version: string }>("/health", fetcher, {
    ...SWR_COMMON,
    refreshInterval: 60_000,
  });
}
