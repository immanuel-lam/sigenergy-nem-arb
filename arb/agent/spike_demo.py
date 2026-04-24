"""Demonstrate mid-interval re-plan in response to an injected price spike.

Not for production. Stand-in for a real price cap event we may or may not
see during the demo window. The point is the reproducible before/after:
agent runs on the live forecast, we graft a spike into the forecast
DataFrame, agent re-runs, we diff the two plans.
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import pandas as pd

from arb.agent.explain import explain_plan
from arb.agent.plan_diff import diff_plans, format_diff_for_llm
from arb.forecast.builder import build_forecast
from arb.ingest import ha
from arb.ingest.snapshot import Snapshot, take_snapshot
from arb.scheduler.constants import INTERVAL_MIN
from arb.scheduler.greedy import schedule
from arb.scheduler.plan import Action, Plan

log = logging.getLogger(__name__)


@dataclass
class SpikeDemoResult:
    baseline_plan: Plan
    spiked_plan: Plan
    diff_summary: str
    baseline_rationale: str
    spiked_rationale: str
    spike_start: datetime
    spike_end: datetime
    spike_c_kwh: float
    action_changed: bool


def inject_spike(
    price_df: pd.DataFrame,
    start_offset_min: int = 10,
    duration_min: int = 15,
    magnitude_c_kwh: float = 120.0,
    channel: Literal["import", "export"] = "import",
    now: datetime | None = None,
) -> pd.DataFrame:
    """Return a copy of price_df with a synthetic spike grafted in.

    start_offset_min: spike begins this many minutes after `now`.
    For import spikes, magnitude is ADDED. For export spikes, use negative
    magnitude to demonstrate an export-crash scenario.
    """
    if price_df is None or price_df.empty:
        return price_df.copy() if price_df is not None else pd.DataFrame()

    out = price_df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)

    if now is None:
        now = datetime.now(timezone.utc)
    # Snap `now` to the 5-min grid so the spike lines up with the forecast's
    # bucket boundaries.
    now_ts = pd.Timestamp(now)
    if now_ts.tz is None:
        now_ts = now_ts.tz_localize("UTC")
    else:
        now_ts = now_ts.tz_convert("UTC")
    now_ts = now_ts.floor(f"{INTERVAL_MIN}min")

    start = now_ts + pd.Timedelta(minutes=start_offset_min)
    end = start + pd.Timedelta(minutes=duration_min)

    mask = (out["timestamp"] >= start) & (out["timestamp"] < end)

    col = "import_c_kwh" if channel == "import" else "export_c_kwh"
    if col not in out.columns:
        # Amber-style rows without the matching column get a fresh one based on
        # whichever price column we do have.
        if "import_c_kwh" in out.columns:
            out[col] = out["import_c_kwh"]
        elif "rrp_c_kwh" in out.columns:
            out[col] = out["rrp_c_kwh"]
        else:
            out[col] = 0.0

    out.loc[mask, col] = out.loc[mask, col].astype(float) + magnitude_c_kwh
    # Mirror the change onto rrp_c_kwh if present so AEMO-shaped consumers see it.
    if "rrp_c_kwh" in out.columns and channel == "import":
        out.loc[mask, "rrp_c_kwh"] = out.loc[mask, "rrp_c_kwh"].astype(float) + magnitude_c_kwh

    return out


def _fmt_hhmm(ts) -> str:
    """HH:MM in UTC for compact terminal output."""
    t = pd.Timestamp(ts)
    if t.tz is None:
        t = t.tz_localize("UTC")
    return t.strftime("%H:%M")


def _next_action_of(plan: Plan, target: Action, start_idx: int = 0) -> int | None:
    """Index of first interval at or after start_idx with the given action."""
    for i in range(start_idx, plan.n):
        a = plan.actions[i]
        if (a.value if isinstance(a, Action) else a) == target.value:
            return i
    return None


def _describe_next(plan: Plan, target: Action, label: str, start_idx: int = 0) -> str:
    idx = _next_action_of(plan, target, start_idx)
    if idx is None:
        return f"  Next {label}: none"
    price = plan.export_c_kwh[idx] if target == Action.DISCHARGE_GRID else plan.import_c_kwh[idx]
    return f"  Next {label}: {_fmt_hhmm(plan.timestamps[idx])} ({price:.1f}c/kWh)"


def _print_side_by_side(
    baseline: Plan,
    spiked: Plan,
    baseline_rationale: str,
    spiked_rationale: str,
    spike_start: datetime,
    spike_end: datetime,
    spike_c_kwh: float,
    channel: str,
) -> None:
    now_ts = baseline.timestamps[0]
    cur_baseline = baseline.actions[0]
    cur_spiked = spiked.actions[0]
    cur_baseline_str = cur_baseline.value if isinstance(cur_baseline, Action) else str(cur_baseline)
    cur_spiked_str = cur_spiked.value if isinstance(cur_spiked, Action) else str(cur_spiked)

    print("=== Spike Demo ===")
    print(f"Current time: {pd.Timestamp(now_ts).isoformat()}")
    print(
        f"Injected spike ({channel}): {spike_c_kwh:+.1f} c/kWh from "
        f"{_fmt_hhmm(spike_start)} to {_fmt_hhmm(spike_end)}"
    )
    print()
    print("BASELINE PLAN (before spike):")
    print(f"  Current action: {cur_baseline_str}")
    print(_describe_next(baseline, Action.CHARGE_GRID, "charge"))
    print(_describe_next(baseline, Action.DISCHARGE_GRID, "discharge"))
    print(f"  Rationale: \"{baseline_rationale}\"")
    print()

    change_marker = f"(was {cur_baseline_str})" if cur_spiked_str != cur_baseline_str else ""
    print(f"SPIKED PLAN (after {spike_c_kwh:+.1f} c/kWh {channel} injection):")
    print(f"  Current action: {cur_spiked_str}  {change_marker}".rstrip())
    print(_describe_next(spiked, Action.CHARGE_GRID, "charge"))
    # Point out whether next discharge actually landed inside the spike window.
    dis_idx = _next_action_of(spiked, Action.DISCHARGE_GRID)
    if dis_idx is not None:
        dis_ts = pd.Timestamp(spiked.timestamps[dis_idx])
        if dis_ts.tz is None:
            dis_ts = dis_ts.tz_localize("UTC")
        ss = pd.Timestamp(spike_start).tz_convert("UTC") if pd.Timestamp(spike_start).tz else pd.Timestamp(spike_start).tz_localize("UTC")
        se = pd.Timestamp(spike_end).tz_convert("UTC") if pd.Timestamp(spike_end).tz else pd.Timestamp(spike_end).tz_localize("UTC")
        marker = "  <-- targets spike!" if ss <= dis_ts < se else ""
        price = spiked.export_c_kwh[dis_idx]
        print(f"  Next discharge: {_fmt_hhmm(spiked.timestamps[dis_idx])} ({price:.1f}c/kWh){marker}")
    else:
        print("  Next discharge: none")
    print(f"  Rationale: \"{spiked_rationale}\"")


def _templated_rationale(plan: Plan, context: str = "") -> str:
    """Deterministic rationale used with --skip-llm."""
    idx = 0
    action = plan.actions[idx]
    action_str = action.value if isinstance(action, Action) else str(action)
    soc = plan.soc[idx + 1] * 100
    imp = plan.import_c_kwh[idx]
    exp = plan.export_c_kwh[idx]
    charge_total = float(plan.charge_grid_kwh.sum())
    discharge_total = float(plan.discharge_grid_kwh.sum())
    base = (
        f"{action_str} at {_fmt_hhmm(plan.timestamps[idx])}, "
        f"SOC tracking to {soc:.0f}%, "
        f"import {imp:.1f}c/kWh export {exp:.1f}c/kWh. "
        f"Plan totals {charge_total:.1f} kWh charge / {discharge_total:.1f} kWh discharge."
    )
    return f"{base} {context}".strip()


def run_spike_demo(
    magnitude_c_kwh: float = 120.0,
    minutes_ahead: int = 10,
    duration_min: int = 15,
    channel: Literal["import", "export"] = "import",
    skip_llm: bool = False,
    snapshot: Snapshot | None = None,
    history: pd.DataFrame | None = None,
) -> SpikeDemoResult:
    """Run the full demo: baseline plan, inject spike, re-plan, diff.

    If snapshot and history are passed in, the slow ingest steps are skipped —
    the caller is responsible for freshness. Used by the API to reuse the
    cache primed by /plan/refresh so the demo button feels snappy.
    """
    if snapshot is None:
        log.info("Taking live snapshot")
        snapshot = take_snapshot()
        log.info("Snapshot:\n%s", snapshot.summary())

    if history is None:
        # Pull history for the load forecaster. Failure here is fine — builder
        # has a fallback, we just lose some accuracy.
        try:
            history = ha.fetch_history(days=14)
        except Exception as e:  # noqa: BLE001
            log.warning("HA history fetch failed (%s); proceeding without", e)
            history = pd.DataFrame()

    # --- Baseline plan ---
    baseline_forecast = build_forecast(snapshot, history)
    soc_now = (snapshot.soc_pct or 50.0) / 100.0
    baseline_plan = schedule(baseline_forecast, soc_now=soc_now)

    # --- Inject the spike into the snapshot's price forecast ---
    spike_start_ts = pd.Timestamp(snapshot.timestamp).floor(f"{INTERVAL_MIN}min") + pd.Timedelta(
        minutes=minutes_ahead
    )
    spike_end_ts = spike_start_ts + pd.Timedelta(minutes=duration_min)

    spiked_prices = inject_spike(
        snapshot.price_forecast,
        start_offset_min=minutes_ahead,
        duration_min=duration_min,
        magnitude_c_kwh=magnitude_c_kwh,
        channel=channel,
        now=snapshot.timestamp,
    )
    spiked_snapshot = Snapshot(
        timestamp=snapshot.timestamp,
        soc_pct=snapshot.soc_pct,
        load_kw=snapshot.load_kw,
        solar_kw=snapshot.solar_kw,
        battery_power_kw=snapshot.battery_power_kw,
        price_forecast=spiked_prices,
        weather_forecast=snapshot.weather_forecast,
        stale_sensors=list(snapshot.stale_sensors),
        warnings=list(snapshot.warnings),
    )

    spiked_forecast = build_forecast(spiked_snapshot, history)
    spiked_plan = schedule(spiked_forecast, soc_now=soc_now)

    # --- Rationales ---
    if skip_llm:
        baseline_rationale = _templated_rationale(baseline_plan)
        spiked_rationale = _templated_rationale(
            spiked_plan,
            context=f"Re-plan triggered by {channel} spike {magnitude_c_kwh:+.0f}c/kWh at {_fmt_hhmm(spike_start_ts)}.",
        )
    else:
        baseline_rationale = explain_plan(baseline_plan, snapshot, previous_plan=None)
        spiked_rationale = explain_plan(spiked_plan, spiked_snapshot, previous_plan=baseline_plan)

    # --- Diff + action-change flag ---
    diff = diff_plans(spiked_plan, baseline_plan)
    diff_summary = format_diff_for_llm(diff)

    baseline_cur = baseline_plan.actions[0]
    spiked_cur = spiked_plan.actions[0]
    baseline_cur_str = baseline_cur.value if isinstance(baseline_cur, Action) else str(baseline_cur)
    spiked_cur_str = spiked_cur.value if isinstance(spiked_cur, Action) else str(spiked_cur)
    action_changed = baseline_cur_str != spiked_cur_str

    _print_side_by_side(
        baseline_plan,
        spiked_plan,
        baseline_rationale,
        spiked_rationale,
        spike_start_ts.to_pydatetime(),
        spike_end_ts.to_pydatetime(),
        magnitude_c_kwh,
        channel,
    )
    print()
    print("DIFF:")
    for line in diff_summary.splitlines():
        print(f"  {line}")

    return SpikeDemoResult(
        baseline_plan=baseline_plan,
        spiked_plan=spiked_plan,
        diff_summary=diff_summary,
        baseline_rationale=baseline_rationale,
        spiked_rationale=spiked_rationale,
        spike_start=spike_start_ts.to_pydatetime(),
        spike_end=spike_end_ts.to_pydatetime(),
        spike_c_kwh=magnitude_c_kwh,
        action_changed=action_changed,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Demo a mid-interval re-plan from an injected price spike."
    )
    parser.add_argument(
        "--magnitude",
        type=float,
        default=120.0,
        help="Spike magnitude in c/kWh (default 120 = near cap event)",
    )
    parser.add_argument("--minutes-ahead", type=int, default=10)
    parser.add_argument("--duration-min", type=int, default=15)
    parser.add_argument("--channel", choices=["import", "export"], default="import")
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Skip Claude API calls, use templated rationale",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    result = run_spike_demo(
        magnitude_c_kwh=args.magnitude,
        minutes_ahead=args.minutes_ahead,
        duration_min=args.duration_min,
        channel=args.channel,
        skip_llm=args.skip_llm,
    )
    print()
    print(f"Action changed: {result.action_changed}")
    print(f"Spike window: {result.spike_start} to {result.spike_end}")
    print(f"Magnitude: {result.spike_c_kwh:+.1f} c/kWh")


if __name__ == "__main__":
    main()
