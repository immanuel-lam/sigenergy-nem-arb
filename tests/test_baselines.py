"""Tests for baseline strategies and Amber replay reconstruction."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from arb.eval.amber_replay import (
    compute_amber_cost,
    reconstruct_amber_actions,
)
from arb.eval.baselines import self_consume_strategy, static_tou_strategy
from arb.scheduler.constants import INTERVAL_MIN, BatteryConstants
from arb.scheduler.plan import Action


# ---------- fixtures ----------

def _make_forecast(
    n: int = 288,  # 24h @ 5-min
    start: datetime | None = None,
    import_prices: float | np.ndarray = 10.0,
    export_prices: float | np.ndarray = 10.0,
    load: float | np.ndarray = 1.0,
    solar: float | np.ndarray = 0.0,
) -> pd.DataFrame:
    # Default start: midnight UTC -> 10am AEST. Use a stable reference date.
    if start is None:
        start = datetime(2026, 4, 23, 14, 0, tzinfo=timezone.utc)  # midnight AEST (UTC+10)
    ts = [start + timedelta(minutes=i * INTERVAL_MIN) for i in range(n)]

    def _arr(x, n):
        if isinstance(x, (int, float)):
            return np.full(n, float(x))
        return np.asarray(x, dtype=float)

    return pd.DataFrame({
        "timestamp": ts,
        "import_c_kwh": _arr(import_prices, n),
        "export_c_kwh": _arr(export_prices, n),
        "load_kw": _arr(load, n),
        "solar_kw": _arr(solar, n),
    })


# ---------- self-consume tests ----------

def test_self_consume_no_grid_charge():
    """B1 must not schedule any grid charge, even with enormously low prices."""
    forecast = _make_forecast(n=48, import_prices=0.1, export_prices=50.0, load=1.0)
    plan = self_consume_strategy(forecast, soc_now=0.5)
    assert plan.charge_grid_kwh.sum() == 0.0
    assert plan.discharge_grid_kwh.sum() == 0.0
    # All intervals must be IDLE
    assert all(a == Action.IDLE for a in plan.actions)


def test_self_consume_respects_soc_bounds():
    """SOC must stay inside [floor, ceiling]."""
    bc = BatteryConstants()
    # Extreme solar surplus then extreme deficit to push both ends.
    n = 48
    solar = np.concatenate([np.full(24, 20.0), np.full(24, 0.0)])
    load = np.concatenate([np.full(24, 0.5), np.full(24, 10.0)])
    forecast = _make_forecast(n=n, solar=solar, load=load)
    plan = self_consume_strategy(forecast, soc_now=0.5, battery=bc)
    assert plan.soc.min() >= bc.soc_floor - 1e-9
    assert plan.soc.max() <= bc.soc_ceiling + 1e-9


# ---------- static TOU tests ----------

def test_static_tou_charges_at_1am():
    """Intervals in the 1-5am local window should schedule grid charge."""
    # Start at midnight AEST so intervals 12-59 are 1-5am local (5-min each).
    start = datetime(2026, 4, 23, 14, 0, tzinfo=timezone.utc)  # midnight AEST
    forecast = _make_forecast(n=288, start=start, load=0.0, solar=0.0)
    plan = static_tou_strategy(forecast, soc_now=0.5)

    # Intervals 12..59 are 1:00-5:00 AEST
    charge_energy = plan.charge_grid_kwh[12:60].sum()
    assert charge_energy > 0, "expected grid charging during 1-5am window"


def test_static_tou_discharges_at_6pm():
    """Intervals in the 5-9pm local window should schedule grid discharge."""
    start = datetime(2026, 4, 23, 14, 0, tzinfo=timezone.utc)  # midnight AEST
    # Need enough SOC at 5pm to discharge. Start full.
    forecast = _make_forecast(n=288, start=start, load=0.0, solar=0.0)
    plan = static_tou_strategy(forecast, soc_now=0.9)

    # 6pm local = 17:00-18:00 AEST = intervals 204..215 (17*12=204)
    discharge_energy = plan.discharge_grid_kwh[204:216].sum()
    assert discharge_energy > 0, "expected grid discharge during 6pm window"


def test_static_tou_ignores_price():
    """Static TOU charges during its window even when prices are flipped."""
    start = datetime(2026, 4, 23, 14, 0, tzinfo=timezone.utc)
    n = 288
    # Flip prices: high during 1-5am (expensive to charge), low during 5-9pm (cheap).
    import_p = np.full(n, 10.0)
    export_p = np.full(n, 10.0)
    import_p[12:60] = 50.0   # expensive at 1-5am
    export_p[204:252] = 1.0  # cheap at 5-9pm
    forecast = _make_forecast(
        n=n, start=start, import_prices=import_p, export_prices=export_p,
        load=0.0, solar=0.0,
    )
    plan = static_tou_strategy(forecast, soc_now=0.5)
    # Still charges in 1-5am (expensive) and discharges in 5-9pm (cheap).
    assert plan.charge_grid_kwh[12:60].sum() > 0
    assert plan.discharge_grid_kwh[204:252].sum() > 0


# ---------- Amber replay tests ----------

def _make_history(
    n: int = 12,
    start: datetime | None = None,
    load_kw: float | np.ndarray = 1.0,
    solar_kw: float | np.ndarray = 0.0,
    battery_power_kw: float | np.ndarray = 0.0,
    soc_pct: float | np.ndarray = 50.0,
) -> pd.DataFrame:
    if start is None:
        start = datetime(2026, 4, 23, 0, 0, tzinfo=timezone.utc)
    ts = [start + timedelta(minutes=i * INTERVAL_MIN) for i in range(n)]

    def _arr(x, n):
        if isinstance(x, (int, float)):
            return np.full(n, float(x))
        return np.asarray(x, dtype=float)

    return pd.DataFrame({
        "timestamp": ts,
        "load_kw": _arr(load_kw, n),
        "solar_kw": _arr(solar_kw, n),
        "battery_power_kw": _arr(battery_power_kw, n),
        "soc_pct": _arr(soc_pct, n),
    })


def _make_prices(history: pd.DataFrame, import_c_kwh: float | np.ndarray,
                 export_c_kwh: float | np.ndarray | None = None) -> pd.DataFrame:
    n = len(history)
    if isinstance(import_c_kwh, (int, float)):
        import_arr = np.full(n, float(import_c_kwh))
    else:
        import_arr = np.asarray(import_c_kwh, dtype=float)
    if export_c_kwh is None:
        export_arr = import_arr
    elif isinstance(export_c_kwh, (int, float)):
        export_arr = np.full(n, float(export_c_kwh))
    else:
        export_arr = np.asarray(export_c_kwh, dtype=float)
    return pd.DataFrame({
        "timestamp": history["timestamp"].values,
        "import_c_kwh": import_arr,
        "export_c_kwh": export_arr,
    })


def test_amber_reconstruct_classifies_actions():
    """Fake history with clear patterns should classify into correct actions."""
    # Build 5 rows, each with a distinct pattern.
    start = datetime(2026, 4, 23, 0, 0, tzinfo=timezone.utc)
    ts = [start + timedelta(minutes=i * INTERVAL_MIN) for i in range(5)]
    history = pd.DataFrame({
        "timestamp": ts,
        # Row 0: charging from solar (battery_power > 1, solar >> load)
        # Row 1: charging from grid (battery_power > 1, load > solar)
        # Row 2: discharging to load (battery_power < -1, load >> solar)
        # Row 3: discharging to grid (battery_power < -1, solar > load)
        # Row 4: idle (|battery_power| < 0.5)
        "load_kw":         [0.5, 3.0, 5.0, 1.0, 1.0],
        "solar_kw":        [8.0, 0.0, 0.0, 6.0, 1.0],
        "battery_power_kw":[5.0, 2.0, -4.0, -3.0, 0.1],
        "soc_pct":         [50, 55, 50, 45, 40],
    })
    prices = _make_prices(history, import_c_kwh=10.0)

    result = reconstruct_amber_actions(history, prices)

    assert list(result["action"]) == [
        "CHARGE_FROM_SOLAR",
        "CHARGE_FROM_GRID",
        "DISCHARGE_TO_LOAD",
        "DISCHARGE_TO_GRID",
        "IDLE",
    ]


def test_amber_cost_computation():
    """Known prices and grid flows should produce expected cost."""
    # Scenario: 4 intervals of 5 min each.
    # Row 0: import 2 kW @ 20 c/kWh -> 2 * (5/60) * 20 = 3.333 c
    # Row 1: import 4 kW @ 10 c/kWh -> 4 * (5/60) * 10 = 3.333 c
    # Row 2: export 3 kW @ 15 c/kWh -> -3 * (5/60) * 15 = -3.75 c (revenue)
    # Row 3: idle (no battery, solar == load) -> 0
    start = datetime(2026, 4, 23, 0, 0, tzinfo=timezone.utc)
    ts = [start + timedelta(minutes=i * INTERVAL_MIN) for i in range(4)]
    # grid_kw = load + battery_power - solar
    # Row 0: 1 + 1 - 0 = 2 (importing)
    # Row 1: 4 + 0 - 0 = 4 (importing)
    # Row 2: 1 + 0 - 4 = -3 (exporting)
    # Row 3: 2 + 0 - 2 = 0
    history = pd.DataFrame({
        "timestamp": ts,
        "load_kw":         [1.0, 4.0, 1.0, 2.0],
        "solar_kw":        [0.0, 0.0, 4.0, 2.0],
        "battery_power_kw":[1.0, 0.0, 0.0, 0.0],
        "soc_pct":         [50, 55, 55, 55],
    })
    prices = pd.DataFrame({
        "timestamp": ts,
        "import_c_kwh": [20.0, 10.0, 15.0, 12.0],
        "export_c_kwh": [20.0, 10.0, 15.0, 12.0],
    })

    result = compute_amber_cost(history, prices)

    # Expected total cost in cents:
    # 2*(5/60)*20 + 4*(5/60)*10 + (-3)*(5/60)*15 + 0
    # = 3.3333 + 3.3333 - 3.75 + 0
    # = 2.9167 c -> $0.02917
    expected_c = (2 * 5/60 * 20) + (4 * 5/60 * 10) + (-3 * 5/60 * 15)
    assert result["total_cost_dollars"] == pytest.approx(expected_c / 100.0, rel=1e-6)

    # Import kWh: rows 0 and 1 -> 2*(5/60) + 4*(5/60) = 0.5 kWh
    assert result["total_import_kwh"] == pytest.approx(6 * 5/60, rel=1e-6)
    # Export kWh: row 2 -> 3*(5/60) = 0.25 kWh
    assert result["total_export_kwh"] == pytest.approx(3 * 5/60, rel=1e-6)

    # Daily rollup should have at least one entry
    assert not result["daily"].empty
