"""Replay historical HA + AEMO data through a strategy, track $ vs baselines.

The backtest loops at 30-min granularity. At each decision point:
  1. Build a forecast from data available BEFORE the decision timestamp.
     (Persistence for prices, history-lookback for load/solar.)
  2. Call the strategy to get a Plan covering the next horizon.
  3. Step the simulator forward by 30 min (6 x 5-min intervals) using the
     ACTUAL load, solar, and prices from the history — NOT the forecast.
  4. Apply the Plan's commanded charge/discharge on top of the actual
     net load to compute grid flow and $ impact.

The simulator has a hard SOC clamp so a buggy schedule cannot violate
physical bounds. When a command gets truncated by the clamp, it's logged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

import numpy as np
import pandas as pd

from arb.scheduler.constants import INTERVAL_MIN, LOOP_PERIOD_MIN, BatteryConstants
from arb.scheduler.plan import Action, Plan

log = logging.getLogger(__name__)

# Forecast horizon handed to the strategy at every decision point.
FORECAST_HORIZON_H = 6
# Strategy re-plans every LOOP_PERIOD_MIN (30 min). That's 6 five-min slices.
INTERVALS_PER_DECISION = LOOP_PERIOD_MIN // INTERVAL_MIN


@dataclass
class BacktestResult:
    """Output of a single strategy replay."""

    strategy_name: str
    total_cost_dollars: float  # negative = net revenue
    total_import_kwh: float
    total_export_kwh: float
    total_charge_cycles: float  # sum of |delta_soc| / 2 across the sim
    daily_breakdown: pd.DataFrame  # date, cost_dollars, import_kwh, export_kwh
    interval_log: pd.DataFrame  # per 5-min sim step: timestamp, soc, action, price, cost_delta
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Forecast construction (no look-ahead)
# ---------------------------------------------------------------------------


def _build_forecast_at(
    decision_ts: pd.Timestamp,
    history: pd.DataFrame,
    prices: pd.DataFrame,
    horizon_h: int = FORECAST_HORIZON_H,
    perfect_foresight: bool = False,
) -> pd.DataFrame:
    """Build a forecast DataFrame for the strategy at a given decision time.

    By default: only uses data with timestamp strictly before decision_ts.
    Uses price persistence (last known price carried forward) and a
    day-of-week+time-of-day lookback for load/solar.

    If perfect_foresight=True: uses actual future prices as the forecast.
    This shows the upper-bound arbitrage value a perfect forecaster could
    capture. Load/solar still use no-look-ahead forecasts (realistic).
    """
    n_intervals = int(horizon_h * 60 / INTERVAL_MIN)
    target_ts = pd.date_range(
        start=decision_ts,
        periods=n_intervals,
        freq=f"{INTERVAL_MIN}min",
        tz="UTC",
    )

    # --- Prices ---
    if perfect_foresight:
        # Use actual prices at/after decision_ts as the forecast.
        px = prices.copy()
        px["timestamp"] = pd.to_datetime(px["timestamp"], utc=True)
        price_col = "rrp_c_kwh" if "rrp_c_kwh" in px.columns else "import_c_kwh"
        px = px.set_index("timestamp").sort_index()
        # Reindex to target timestamps with nearest match (5-min tolerance)
        future_prices = px.reindex(target_ts, method="nearest", tolerance=pd.Timedelta(minutes=5))
        import_forecast = future_prices[price_col].ffill().bfill().fillna(10.0).values
        export_col = "export_c_kwh" if "export_c_kwh" in px.columns else price_col
        if export_col in future_prices.columns:
            export_forecast = future_prices[export_col].ffill().bfill().fillna(10.0).values
        else:
            export_forecast = import_forecast
    else:
        # Persistence: last known price before decision_ts.
        price_history = prices[prices["timestamp"] < decision_ts]
        if price_history.empty:
            last_price = 10.0
        else:
            price_col = "rrp_c_kwh" if "rrp_c_kwh" in price_history.columns else "import_c_kwh"
            last_price = float(price_history[price_col].iloc[-1])
        import_forecast = np.full(n_intervals, last_price)
        export_forecast = np.full(n_intervals, last_price)

    # --- Load / solar: look back 4 weeks and average same-time samples. ---
    past_hist = history[history["timestamp"] < decision_ts]
    load_forecast = np.full(n_intervals, np.nan)
    solar_forecast = np.full(n_intervals, np.nan)

    if not past_hist.empty:
        hist_indexed = past_hist.set_index("timestamp").sort_index()
        for i, ts in enumerate(target_ts):
            # Sample the same time-of-day at 1, 2, 3, 4 weeks prior.
            samples_load = []
            samples_solar = []
            for weeks_back in (1, 2, 3, 4):
                sample_ts = ts - pd.Timedelta(weeks=weeks_back)
                if sample_ts < hist_indexed.index[0]:
                    continue
                # Nearest-5min match
                idx = hist_indexed.index.get_indexer([sample_ts], method="nearest")[0]
                if idx < 0 or idx >= len(hist_indexed):
                    continue
                row = hist_indexed.iloc[idx]
                if "load_kw" in row and pd.notna(row["load_kw"]):
                    samples_load.append(float(row["load_kw"]))
                if "solar_kw" in row and pd.notna(row["solar_kw"]):
                    samples_solar.append(float(row["solar_kw"]))
            if samples_load:
                load_forecast[i] = np.mean(samples_load)
            if samples_solar:
                solar_forecast[i] = np.mean(samples_solar)

    # Fall back to recent mean for any gaps.
    if not past_hist.empty:
        fallback_load = float(past_hist["load_kw"].tail(288).mean()) if "load_kw" in past_hist else 1.0
        fallback_solar = float(past_hist["solar_kw"].tail(288).mean()) if "solar_kw" in past_hist else 0.0
    else:
        fallback_load = 1.0
        fallback_solar = 0.0
    if np.isnan(fallback_load):
        fallback_load = 1.0
    if np.isnan(fallback_solar):
        fallback_solar = 0.0

    load_forecast = np.where(np.isnan(load_forecast), fallback_load, load_forecast)
    solar_forecast = np.where(np.isnan(solar_forecast), fallback_solar, solar_forecast)

    return pd.DataFrame({
        "timestamp": target_ts,
        "import_c_kwh": import_forecast,
        "export_c_kwh": export_forecast,
        "load_kw": load_forecast,
        "solar_kw": solar_forecast,
    })


# ---------------------------------------------------------------------------
# Actual-data lookup helpers
# ---------------------------------------------------------------------------


def _actual_at(df: pd.DataFrame, ts: pd.Timestamp, col: str, default: float = 0.0) -> float:
    """Return the value of `col` in `df` at the 5-min bucket containing ts.

    Uses nearest-match with a 5-min tolerance. Returns default on miss.
    """
    if df is None or df.empty or col not in df.columns:
        return default
    idx = df["timestamp"].searchsorted(ts, side="right") - 1
    if idx < 0 or idx >= len(df):
        return default
    row_ts = df["timestamp"].iloc[idx]
    if abs((row_ts - ts).total_seconds()) > INTERVAL_MIN * 60:
        return default
    val = df[col].iloc[idx]
    if pd.isna(val):
        return default
    return float(val)


# ---------------------------------------------------------------------------
# Battery step simulator
# ---------------------------------------------------------------------------


def _step_battery(
    soc: float,
    charge_grid_kwh: float,
    discharge_grid_kwh: float,
    battery: BatteryConstants,
) -> tuple[float, float, float]:
    """Advance SOC by one 5-min step given commanded grid-side charge/discharge.

    Returns (new_soc, actual_charge_grid_kwh, actual_discharge_grid_kwh) — the
    "actual" values reflect truncation against SOC bounds and rate limits.
    """
    interval_h = INTERVAL_MIN / 60.0
    max_energy = battery.max_charge_kw * interval_h

    charge_grid_kwh = max(0.0, min(charge_grid_kwh, max_energy))
    discharge_grid_kwh = max(0.0, min(discharge_grid_kwh, max_energy))

    # Apply charge first
    if charge_grid_kwh > 0:
        battery_kwh = charge_grid_kwh * battery.charge_efficiency
        delta_soc = battery_kwh / battery.capacity_kwh
        new_soc = soc + delta_soc
        if new_soc > battery.soc_ceiling:
            # Truncate charge so SOC lands exactly at ceiling
            allowed_delta = battery.soc_ceiling - soc
            allowed_battery_kwh = max(0.0, allowed_delta * battery.capacity_kwh)
            charge_grid_kwh = allowed_battery_kwh / battery.charge_efficiency
            new_soc = battery.soc_ceiling
        soc = new_soc

    if discharge_grid_kwh > 0:
        battery_kwh = discharge_grid_kwh / battery.discharge_efficiency
        delta_soc = battery_kwh / battery.capacity_kwh
        new_soc = soc - delta_soc
        if new_soc < battery.soc_floor:
            allowed_delta = soc - battery.soc_floor
            allowed_battery_kwh = max(0.0, allowed_delta * battery.capacity_kwh)
            discharge_grid_kwh = allowed_battery_kwh * battery.discharge_efficiency
            new_soc = battery.soc_floor
        soc = new_soc

    # Numeric safety clamp
    soc = float(np.clip(soc, battery.soc_floor, battery.soc_ceiling))
    return soc, charge_grid_kwh, discharge_grid_kwh


def _step_with_self_consume(
    soc: float,
    load_kw: float,
    solar_kw: float,
    cmd_charge_grid_kwh: float,
    cmd_discharge_grid_kwh: float,
    battery: BatteryConstants,
) -> tuple[float, float, float, float]:
    """Simulate one 5-min interval with self-consume on top of agent commands.

    The real inverter in MAX_SELF_CONSUMPTION mode (or IDLE) will automatically:
      - Charge battery from solar surplus (solar > load) up to max_charge_kw
      - Discharge battery to serve load deficit (load > solar) up to max_discharge_kw
    The agent's commands stack on top:
      - cmd_charge_grid_kwh: pull this much extra from grid into battery
      - cmd_discharge_grid_kwh: push this much extra from battery to grid

    Returns (new_soc, net_grid_kwh, actual_charge_grid_kwh, actual_discharge_grid_kwh).
    net_grid_kwh positive = import, negative = export.
    """
    interval_h = INTERVAL_MIN / 60.0
    max_energy = battery.max_charge_kw * interval_h

    # Start with self-consume: battery offsets net load where possible
    net_load_kwh = (load_kw - solar_kw) * interval_h  # + = deficit, - = surplus

    # Self-consume charge from surplus (if any, and room in battery)
    sc_charge_battery_kwh = 0.0
    sc_discharge_battery_kwh = 0.0
    if net_load_kwh < 0:
        # Surplus available to charge
        surplus_kwh = -net_load_kwh
        room_soc = battery.soc_ceiling - soc
        max_battery_kwh = room_soc * battery.capacity_kwh
        # Solar-side charge (no efficiency loss on grid since it's local)
        sc_charge_battery_kwh = min(surplus_kwh * battery.charge_efficiency,
                                     max_energy * battery.charge_efficiency,
                                     max_battery_kwh)
    elif net_load_kwh > 0:
        # Deficit — discharge battery to serve load
        deficit_kwh = net_load_kwh
        room_soc = soc - battery.soc_floor
        max_battery_kwh = room_soc * battery.capacity_kwh
        sc_discharge_battery_kwh = min(deficit_kwh / battery.discharge_efficiency,
                                        max_energy / battery.discharge_efficiency,
                                        max_battery_kwh)

    # Apply self-consume effect on SOC
    soc += sc_charge_battery_kwh / battery.capacity_kwh
    soc -= sc_discharge_battery_kwh / battery.capacity_kwh

    # Now apply agent commands (stacked on top)
    actual_charge_grid = 0.0
    actual_discharge_grid = 0.0

    cmd_charge_grid_kwh = max(0.0, min(cmd_charge_grid_kwh, max_energy))
    cmd_discharge_grid_kwh = max(0.0, min(cmd_discharge_grid_kwh, max_energy))

    if cmd_charge_grid_kwh > 0:
        battery_kwh = cmd_charge_grid_kwh * battery.charge_efficiency
        room_battery_kwh = (battery.soc_ceiling - soc) * battery.capacity_kwh
        applied = min(battery_kwh, room_battery_kwh)
        actual_charge_grid = applied / battery.charge_efficiency
        soc += applied / battery.capacity_kwh

    if cmd_discharge_grid_kwh > 0:
        battery_kwh = cmd_discharge_grid_kwh / battery.discharge_efficiency
        avail_battery_kwh = (soc - battery.soc_floor) * battery.capacity_kwh
        applied = min(battery_kwh, avail_battery_kwh)
        actual_discharge_grid = applied * battery.discharge_efficiency
        soc -= applied / battery.capacity_kwh

    soc = float(np.clip(soc, battery.soc_floor, battery.soc_ceiling))

    # Grid flow: load, minus solar served by solar (surplus went to battery or export),
    # minus battery serving load, plus agent charge from grid, minus agent discharge to grid
    # Simplified:
    #   natural_net_load = load - solar (kWh over interval)
    #   after self-consume charge: surplus beyond battery exports to grid
    #   after self-consume discharge: deficit beyond battery imports from grid
    # Compute natural grid flow (before agent commands):
    # solar absorbed by self-consume charge (battery_kwh / eff to get solar-side)
    sc_charge_solar_kwh = sc_charge_battery_kwh / battery.charge_efficiency if battery.charge_efficiency > 0 else 0
    # load served by self-consume discharge (battery_kwh * eff to get load-side)
    sc_discharge_load_kwh = sc_discharge_battery_kwh * battery.discharge_efficiency

    # Remaining surplus solar (exports) or remaining deficit (imports)
    if net_load_kwh < 0:
        remaining_surplus = -net_load_kwh - sc_charge_solar_kwh
        natural_grid_kwh = -remaining_surplus  # negative = export
    else:
        remaining_deficit = net_load_kwh - sc_discharge_load_kwh
        natural_grid_kwh = remaining_deficit  # positive = import

    net_grid_kwh = natural_grid_kwh + actual_charge_grid - actual_discharge_grid
    return soc, net_grid_kwh, actual_charge_grid, actual_discharge_grid


# ---------------------------------------------------------------------------
# Main backtest loop
# ---------------------------------------------------------------------------


def run_backtest(
    history: pd.DataFrame,
    prices: pd.DataFrame,
    start: datetime,
    end: datetime,
    strategy_fn: Callable[[pd.DataFrame, float], Plan],
    initial_soc: float = 0.5,
    battery: BatteryConstants | None = None,
    strategy_name: str = "strategy",
    perfect_foresight: bool = False,
) -> BacktestResult:
    """Replay `history` + `prices` through `strategy_fn` between start and end.

    Args:
        history: 5-min HA history with columns timestamp, load_kw, solar_kw,
            soc_pct (optional), battery_power_kw (optional).
        prices: actual cleared prices with columns timestamp, rrp_c_kwh.
        start: UTC datetime to start the backtest.
        end: UTC datetime to end the backtest.
        strategy_fn: (forecast_df, soc_now) -> Plan. Receives only data
            available before the decision timestamp.
        initial_soc: SOC at start as fraction (0-1).
        battery: Battery constants. Defaults to BatteryConstants().
        strategy_name: label for the result.

    Returns:
        BacktestResult with cost, energy, and per-interval log.
    """
    battery = battery or BatteryConstants()
    interval_h = INTERVAL_MIN / 60.0

    # Normalise input frames
    hist = history.copy() if history is not None else pd.DataFrame()
    if not hist.empty:
        hist["timestamp"] = pd.to_datetime(hist["timestamp"], utc=True)
        hist = hist.sort_values("timestamp").reset_index(drop=True)

    px = prices.copy() if prices is not None else pd.DataFrame()
    if not px.empty:
        px["timestamp"] = pd.to_datetime(px["timestamp"], utc=True)
        px = px.sort_values("timestamp").reset_index(drop=True)

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize("UTC")
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize("UTC")

    soc = float(np.clip(initial_soc, battery.soc_floor, battery.soc_ceiling))
    total_cost = 0.0
    total_import_kwh = 0.0
    total_export_kwh = 0.0
    total_cycles = 0.0

    interval_rows: list[dict] = []

    decision_ts = start_ts
    while decision_ts < end_ts:
        # 1. Build a forecast from only data available before decision_ts.
        forecast = _build_forecast_at(decision_ts, hist, px, horizon_h=FORECAST_HORIZON_H,
                                       perfect_foresight=perfect_foresight)

        # 2. Run the strategy.
        try:
            plan = strategy_fn(forecast, soc)
        except Exception as e:
            log.warning("strategy_fn raised at %s: %s — defaulting to idle", decision_ts, e)
            plan = None

        # 3. Step the sim forward by LOOP_PERIOD_MIN, applying plan commands
        #    (for the first INTERVALS_PER_DECISION intervals only) on top of
        #    the ACTUAL load/solar/price.
        for k in range(INTERVALS_PER_DECISION):
            ts = decision_ts + pd.Timedelta(minutes=k * INTERVAL_MIN)
            if ts >= end_ts:
                break

            # Pull commanded grid-side charge/discharge from the plan.
            cmd_charge = 0.0
            cmd_discharge = 0.0
            action = Action.IDLE
            if plan is not None and k < plan.n:
                cmd_charge = float(plan.charge_grid_kwh[k])
                cmd_discharge = float(plan.discharge_grid_kwh[k])
                action = plan.actions[k]

            # Actual observed values at this ts.
            actual_load = _actual_at(hist, ts, "load_kw", default=0.0)
            actual_solar = _actual_at(hist, ts, "solar_kw", default=0.0)
            # Use Amber-style separate import/export if available, fall back to rrp.
            if "import_c_kwh" in px.columns:
                actual_import_price = _actual_at(px, ts, "import_c_kwh", default=0.0)
                actual_export_price = _actual_at(px, ts, "export_c_kwh", default=actual_import_price)
            else:
                actual_import_price = _actual_at(px, ts, "rrp_c_kwh", default=0.0)
                actual_export_price = actual_import_price
            actual_price = actual_import_price  # kept for log compat

            soc_before = soc
            soc, net_grid_kwh, actual_charge, actual_discharge = _step_with_self_consume(
                soc, actual_load, actual_solar, cmd_charge, cmd_discharge, battery
            )

            if net_grid_kwh >= 0:
                interval_cost = net_grid_kwh * actual_import_price / 100.0
                total_import_kwh += net_grid_kwh
            else:
                interval_cost = net_grid_kwh * actual_export_price / 100.0
                total_export_kwh += -net_grid_kwh

            total_cost += interval_cost
            total_cycles += abs(soc - soc_before) / 2.0

            interval_rows.append({
                "timestamp": ts,
                "soc_before": soc_before,
                "soc_after": soc,
                "action": action.value if hasattr(action, "value") else str(action),
                "price_c_kwh": actual_price,
                "load_kw": actual_load,
                "solar_kw": actual_solar,
                "cmd_charge_kwh": cmd_charge,
                "cmd_discharge_kwh": cmd_discharge,
                "actual_charge_kwh": actual_charge,
                "actual_discharge_kwh": actual_discharge,
                "net_grid_kwh": net_grid_kwh,
                "cost_delta_dollars": interval_cost,
            })

        decision_ts = decision_ts + pd.Timedelta(minutes=LOOP_PERIOD_MIN)

    interval_log = pd.DataFrame(interval_rows)

    if interval_log.empty:
        daily = pd.DataFrame(columns=["date", "cost_dollars", "import_kwh", "export_kwh"])
    else:
        log_copy = interval_log.copy()
        log_copy["date"] = log_copy["timestamp"].dt.tz_convert("UTC").dt.date
        log_copy["import_kwh_bucket"] = log_copy["net_grid_kwh"].clip(lower=0)
        log_copy["export_kwh_bucket"] = (-log_copy["net_grid_kwh"]).clip(lower=0)
        daily = (
            log_copy.groupby("date")
            .agg(
                cost_dollars=("cost_delta_dollars", "sum"),
                import_kwh=("import_kwh_bucket", "sum"),
                export_kwh=("export_kwh_bucket", "sum"),
            )
            .reset_index()
        )

    return BacktestResult(
        strategy_name=strategy_name,
        total_cost_dollars=total_cost,
        total_import_kwh=total_import_kwh,
        total_export_kwh=total_export_kwh,
        total_charge_cycles=total_cycles,
        daily_breakdown=daily,
        interval_log=interval_log,
        meta={
            "start": start_ts.isoformat(),
            "end": end_ts.isoformat(),
            "initial_soc": initial_soc,
            "final_soc": soc,
            "n_decisions": int((end_ts - start_ts).total_seconds() // (LOOP_PERIOD_MIN * 60)),
        },
    )


# ---------------------------------------------------------------------------
# Convenience strategies for tests
# ---------------------------------------------------------------------------


def idle_strategy(forecast: pd.DataFrame, soc_now: float) -> Plan:
    """Baseline: do nothing (self-consume only, no grid arbitrage)."""
    timestamps = forecast["timestamp"].values
    return Plan.from_self_consume(
        timestamps=timestamps,
        import_c_kwh=forecast["import_c_kwh"].values.astype(float),
        export_c_kwh=forecast["export_c_kwh"].values.astype(float),
        load_kw=forecast["load_kw"].values.astype(float),
        solar_kw=forecast["solar_kw"].values.astype(float),
        soc_now=soc_now,
    )
