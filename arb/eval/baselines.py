"""Baseline battery strategies for backtest comparison.

B1: self-consume only — no grid arbitrage, solar charges, load discharges.
B2: static TOU — charge 1-5am, discharge 5-9pm, regardless of price.

Both strategies conform to the `strategy_fn(forecast, soc_now, battery) -> Plan`
signature so the backtest harness can swap them with the greedy agent.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from arb.scheduler.constants import INTERVAL_MIN, BatteryConstants
from arb.scheduler.plan import Action, Plan

# Ignore rounding noise below this threshold when deciding to charge/discharge.
_MIN_ENERGY_KWH = 1e-6


def self_consume_strategy(
    forecast: pd.DataFrame,
    soc_now: float,
    battery: BatteryConstants | None = None,
) -> Plan:
    """B1: baseline self-consume. No grid arbitrage.

    Solar surplus charges the battery, load deficit discharges it. Grid fills
    whatever the battery can't cover. Every interval is IDLE.
    """
    battery = battery or BatteryConstants()
    return Plan.from_self_consume(
        timestamps=forecast["timestamp"].values,
        import_c_kwh=forecast["import_c_kwh"].values.astype(float),
        export_c_kwh=forecast["export_c_kwh"].values.astype(float),
        load_kw=forecast["load_kw"].values.astype(float),
        solar_kw=forecast["solar_kw"].values.astype(float),
        soc_now=soc_now,
        battery=battery,
    )


def _hour_in_window(hour: int, window: tuple[int, int]) -> bool:
    """Check whether `hour` falls in [start, end) with wrap-around support.

    Example: (22, 6) means 22:00-06:00 across midnight.
    """
    start, end = window
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # wraps midnight
    return hour >= start or hour < end


def static_tou_strategy(
    forecast: pd.DataFrame,
    soc_now: float,
    battery: BatteryConstants | None = None,
    charge_hours: tuple[int, int] = (1, 5),
    discharge_hours: tuple[int, int] = (17, 21),
    tz: str = "Australia/Sydney",
) -> Plan:
    """B2: dumb static TOU schedule.

    Charge from grid at max rate during `charge_hours` (local time), discharge
    to grid at max rate during `discharge_hours`. Ignore prices entirely.
    SOC bounds are still enforced via `plan.can_charge_kwh`/`can_discharge_kwh`.
    """
    battery = battery or BatteryConstants()
    plan = self_consume_strategy(forecast, soc_now, battery=battery)

    # Convert timestamps to local time so the window bounds mean local clock time.
    ts = pd.DatetimeIndex(forecast["timestamp"].values)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    local = ts.tz_convert(tz)
    hours = local.hour.to_numpy()

    for i in range(plan.n):
        hour = int(hours[i])
        if _hour_in_window(hour, charge_hours):
            room = plan.can_charge_kwh(i)
            if room > _MIN_ENERGY_KWH:
                plan.charge(i, room)
        elif _hour_in_window(hour, discharge_hours):
            room = plan.can_discharge_kwh(i)
            if room > _MIN_ENERGY_KWH:
                plan.discharge(i, room)

    return plan
