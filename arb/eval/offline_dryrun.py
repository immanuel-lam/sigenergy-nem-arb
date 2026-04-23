"""Offline 24-hour dry-run — replay recent history through the agent loop.

Takes the last N hours of real HA + Amber data and simulates the agent
re-planning every 30 minutes. Produces a rationale log and a plan-diff log
that feed the Day 4 postmortem.

Upper-bound caveat: prices use Amber historical values that cover intervals
both before and after each decision timestamp. That's perfect foresight on
prices, so the plans will be better than they would have been live. The
purpose here is to generate realistic artifacts (rationale, action
transitions) from real data, not to measure strategy quality — that's what
the backtest is for.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from arb.agent.explain import _fallback_rationale, explain_plan, summarize_plan_changes
from arb.forecast.builder import build_forecast
from arb.ingest import amber, bom, ha
from arb.ingest.snapshot import Snapshot
from arb.scheduler.constants import INTERVAL_MIN, LOOP_PERIOD_MIN
from arb.scheduler.greedy import schedule
from arb.scheduler.plan import Action, Plan

log = logging.getLogger(__name__)


def _nearest_sensor(history: pd.DataFrame, ts: pd.Timestamp, col: str,
                    tolerance_min: int = 15) -> float | None:
    """Return history[col] at the sample nearest ts, or None if stale/missing."""
    if history is None or history.empty or col not in history.columns:
        return None
    series = history.dropna(subset=[col])
    if series.empty:
        return None
    diffs = (series["timestamp"] - ts).abs()
    min_idx = diffs.idxmin()
    if diffs.loc[min_idx] > pd.Timedelta(minutes=tolerance_min):
        return None
    val = series.loc[min_idx, col]
    if pd.isna(val):
        return None
    return float(val)


def _build_synthetic_snapshot(
    t: pd.Timestamp,
    history: pd.DataFrame,
    prices: pd.DataFrame,
    weather: pd.DataFrame,
) -> Snapshot:
    """Construct a Snapshot representing state-of-the-world at synthetic time t.

    - SOC/load/solar/battery_power from HA history nearest to t (marked stale if
      gap > 15 min).
    - Price forecast = all Amber prices at or after t (perfect foresight,
      documented upper bound).
    - Weather = current Open-Meteo fetch (history unavailable, acceptable given
      our simple cloud-derating solar model).
    """
    stale: list[str] = []
    warnings: list[str] = []

    soc_pct = _nearest_sensor(history, t, "soc_pct")
    load_kw = _nearest_sensor(history, t, "load_kw")
    solar_kw = _nearest_sensor(history, t, "solar_kw")
    battery_power_kw = _nearest_sensor(history, t, "battery_power_kw")

    for name, val in (
        ("soc_pct", soc_pct),
        ("load_kw", load_kw),
        ("solar_kw", solar_kw),
        ("battery_power_kw", battery_power_kw),
    ):
        if val is None:
            stale.append(name)

    # Prices: keep entries with timestamp >= t (perfect foresight from t forward).
    if prices is None or prices.empty:
        price_forecast = pd.DataFrame()
        warnings.append("no Amber historical prices")
    else:
        price_forecast = prices[prices["timestamp"] >= t].reset_index(drop=True)
        if price_forecast.empty:
            warnings.append(f"no price data at or after {t.isoformat()}")

    return Snapshot(
        timestamp=t.to_pydatetime(),
        soc_pct=soc_pct,
        load_kw=load_kw,
        solar_kw=solar_kw,
        battery_power_kw=battery_power_kw,
        price_forecast=price_forecast,
        weather_forecast=weather if weather is not None else pd.DataFrame(),
        stale_sensors=stale,
        warnings=warnings,
    )


def _plan_action_at_zero(plan: Plan) -> str:
    """First-interval action as a plain string."""
    if plan is None or plan.n == 0:
        return "NONE"
    a = plan.actions[0]
    return a.value if isinstance(a, Action) else str(a)


def run_offline_dryrun(
    hours: int = 24,
    loop_period_min: int = LOOP_PERIOD_MIN,
    rationale_log_path: str = "offline_dryrun_rationale.log",
    plan_log_path: str = "offline_dryrun_plans.jsonl",
    skip_llm: bool = False,
) -> dict:
    """Replay the last `hours` of real data at `loop_period_min` cadence.

    Returns a summary dict with decision count, action changes, log paths,
    and the simulated time window.
    """
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    # Align end to the previous 30-min boundary so decisions fall on tidy times.
    end = end - timedelta(minutes=end.minute % loop_period_min)
    start = end - timedelta(hours=hours)

    log.info("Offline dry-run: %s to %s (%d h, every %d min)",
             start.isoformat(), end.isoformat(), hours, loop_period_min)

    # --- Pull data once and reuse for every decision ---
    log.info("Pulling HA history...")
    # Need enough lookback for the load forecaster (4 weeks) plus the sim window.
    history = ha.fetch_history(days=35, end=end)
    if history is None or history.empty:
        raise RuntimeError("HA history empty — can't run offline dry-run")
    history["timestamp"] = pd.to_datetime(history["timestamp"], utc=True)
    history = history.sort_values("timestamp").reset_index(drop=True)

    log.info("Pulling Amber historical prices...")
    # Amber historical needs days >= window; fetch_historical_prices uses yesterday
    # as endDate, so grab a generous window to cover both past and future-of-t
    # for perfect-foresight forecasting.
    prices = amber.fetch_historical_prices(days=max(hours // 24 + 2, 3))
    if prices is None or prices.empty:
        log.warning("Amber historical empty — trying current fetch")
        prices = amber.fetch_prices()
    if prices is not None and not prices.empty:
        prices["timestamp"] = pd.to_datetime(prices["timestamp"], utc=True)
        prices = prices.sort_values("timestamp").reset_index(drop=True)
    else:
        prices = pd.DataFrame()
        log.warning("No Amber data available — forecast will use flat fallback")

    log.info("Pulling current weather forecast (reused for all decisions)...")
    try:
        weather = bom.fetch_weather_forecast(hours=48)
    except Exception as e:  # noqa: BLE001
        log.warning("Weather fetch failed (%s) — continuing without", e)
        weather = pd.DataFrame()

    # --- Prep output files ---
    rationale_path = Path(rationale_log_path)
    plan_path = Path(plan_log_path)
    # Overwrite previous runs so each invocation is self-contained.
    rationale_path.write_text("")
    plan_path.write_text("")

    # --- Iterate decision timestamps ---
    decision_ts_list: list[pd.Timestamp] = []
    t = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize("UTC")
    while t < end_ts:
        decision_ts_list.append(t)
        t += pd.Timedelta(minutes=loop_period_min)

    last_plan: Plan | None = None
    last_action: str | None = None
    action_changes = 0
    change_events: list[tuple[str, str, str]] = []  # (ts, prev, new)
    action_counter: Counter[str] = Counter()

    # Force the explain helpers to treat interval 0 as "current". Real
    # Plan.current_interval_idx compares plan timestamps to datetime.now(),
    # which would land on the wrong interval during a historical replay.
    original_prop = Plan.current_interval_idx
    Plan.current_interval_idx = property(lambda self: 0 if self.n > 0 else None)

    try:
        with rationale_path.open("w", encoding="utf-8") as rationale_fh, \
             plan_path.open("w", encoding="utf-8") as plan_fh:

            for i, t in enumerate(decision_ts_list):
                # 1. Synthetic snapshot from pre-t HA + at-or-after-t prices.
                snapshot = _build_synthetic_snapshot(t, history, prices, weather)

                # Can't schedule without an SOC — skip cleanly and note it.
                if snapshot.soc_pct is None:
                    line = (
                        f"{t.isoformat()}\tSKIP\tSOC unavailable at this timestamp "
                        f"(stale sensors: {','.join(snapshot.stale_sensors)})\n"
                    )
                    rationale_fh.write(line)
                    rationale_fh.flush()
                    log.warning("Decision %d/%d at %s: SOC unavailable, skipping",
                                i + 1, len(decision_ts_list), t.isoformat())
                    continue

                # 2. HA history restricted to pre-t (no look-ahead for load/solar).
                ha_pre_t = history[history["timestamp"] < t]

                # 3. Forecast.
                try:
                    forecast_df = build_forecast(snapshot, ha_history=ha_pre_t)
                except Exception as e:  # noqa: BLE001
                    log.error("build_forecast failed at %s: %s", t.isoformat(), e)
                    rationale_fh.write(
                        f"{t.isoformat()}\tERROR\tforecast build failed: {e}\n"
                    )
                    rationale_fh.flush()
                    continue

                # 4. Schedule.
                soc_frac = snapshot.soc_pct / 100.0
                try:
                    plan = schedule(forecast_df, soc_frac)
                except Exception as e:  # noqa: BLE001
                    log.error("schedule failed at %s: %s", t.isoformat(), e)
                    rationale_fh.write(f"{t.isoformat()}\tERROR\tschedule failed: {e}\n")
                    rationale_fh.flush()
                    continue

                action_str = _plan_action_at_zero(plan)
                action_counter[action_str] += 1

                changed = last_action is not None and last_action != action_str
                if changed:
                    action_changes += 1
                    change_events.append((t.isoformat(), last_action or "", action_str))

                # 5. Rationale.
                if skip_llm:
                    diff = summarize_plan_changes(plan, last_plan)
                    rationale = _fallback_rationale(diff, snapshot)
                else:
                    rationale = explain_plan(plan, snapshot, previous_plan=last_plan)

                # Single-line rationale (strip any stray newlines the LLM added).
                rationale_single = " ".join(rationale.split())

                # 6. Write logs.
                rationale_fh.write(f"{t.isoformat()}\t{action_str}\t{rationale_single}\n")
                rationale_fh.flush()

                plan_dict = plan.to_dict()
                plan_dict["decision_timestamp"] = t.isoformat()
                plan_dict["current_action"] = action_str
                plan_dict["previous_action"] = last_action
                plan_dict["action_changed"] = bool(changed)
                plan_dict["snapshot"] = {
                    "soc_pct": snapshot.soc_pct,
                    "load_kw": snapshot.load_kw,
                    "solar_kw": snapshot.solar_kw,
                    "battery_power_kw": snapshot.battery_power_kw,
                    "stale_sensors": list(snapshot.stale_sensors),
                    "warnings": list(snapshot.warnings),
                }
                plan_fh.write(json.dumps(plan_dict, default=str) + "\n")
                plan_fh.flush()

                log.info("Decision %d/%d at %s: %s (SOC=%.1f%%)%s",
                         i + 1, len(decision_ts_list), t.isoformat(),
                         action_str, snapshot.soc_pct,
                         f" CHANGED from {last_action}" if changed else "")

                last_plan = plan
                last_action = action_str
    finally:
        Plan.current_interval_idx = original_prop

    # --- Summary ---
    print("\n=== Offline dry-run complete ===")
    print(f"Decisions: {len(decision_ts_list)}")
    print(f"Action changes: {action_changes}  (interesting re-plans)")
    print(f"Period: {start.isoformat()} to {end.isoformat()}")
    print(f"Rationale log: {rationale_path}")
    print(f"Plan log: {plan_path}")
    print()
    print("Action breakdown:")
    for action, count in action_counter.most_common():
        print(f"  {action}: {count}")

    if change_events:
        print()
        print("Interesting moments:")
        # Show up to 5
        for ts_iso, prev, new in change_events[:5]:
            print(f"  {ts_iso} - action changed from {prev} to {new}")
        if len(change_events) > 5:
            print(f"  ... and {len(change_events) - 5} more")

    summary = {
        "n_decisions": len(decision_ts_list),
        "n_action_changes": action_changes,
        "rationale_log_path": str(rationale_path),
        "plan_log_path": str(plan_path),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "action_breakdown": dict(action_counter),
        "change_events": [
            {"timestamp": ts, "previous": prev, "new": new}
            for ts, prev, new in change_events
        ],
    }
    return summary


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet down the noisy scheduler/forecast INFO spam — we only want per-decision lines.
    logging.getLogger("arb.scheduler.greedy").setLevel(logging.WARNING)
    logging.getLogger("arb.forecast.builder").setLevel(logging.WARNING)
    logging.getLogger("arb.forecast.load").setLevel(logging.WARNING)
    logging.getLogger("arb.forecast.solar").setLevel(logging.WARNING)

    hours = int(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else 24
    skip_llm = "--skip-llm" in sys.argv

    result = run_offline_dryrun(hours=hours, skip_llm=skip_llm)
    print()
    print(json.dumps({k: v for k, v in result.items() if k != "change_events"}, indent=2))
