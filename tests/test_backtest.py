"""Tests for the backtest replay engine."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from arb.eval.backtest import (
    BacktestResult,
    idle_strategy,
    run_backtest,
    _step_battery,
)
from arb.scheduler.constants import INTERVAL_MIN, BatteryConstants
from arb.scheduler.plan import Action, Plan


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_history(
    start: datetime,
    hours: int = 48,
    load_kw: float = 1.0,
    solar_kw: float = 0.0,
) -> pd.DataFrame:
    n = int(hours * 60 / INTERVAL_MIN)
    ts = pd.date_range(start=start, periods=n, freq=f"{INTERVAL_MIN}min", tz="UTC")
    return pd.DataFrame({
        "timestamp": ts,
        "load_kw": np.full(n, load_kw),
        "solar_kw": np.full(n, solar_kw),
        "soc_pct": np.full(n, 50.0),
        "battery_power_kw": np.zeros(n),
    })


def _make_prices(start: datetime, hours: int = 48, price: float = 10.0) -> pd.DataFrame:
    n = int(hours * 60 / INTERVAL_MIN)
    ts = pd.date_range(start=start, periods=n, freq=f"{INTERVAL_MIN}min", tz="UTC")
    if isinstance(price, (int, float)):
        price = np.full(n, price, dtype=float)
    return pd.DataFrame({
        "timestamp": ts,
        "rrp_c_kwh": price,
        "region": "NSW1",
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_flat_price_idle_strategy_zero_cost():
    """Constant price, zero load, zero solar, idle strategy -> zero cost."""
    start = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=4)
    hist = _make_history(start - timedelta(days=30), hours=30 * 24 + 4, load_kw=0.0, solar_kw=0.0)
    prices = _make_prices(start - timedelta(days=30), hours=30 * 24 + 4, price=10.0)

    result = run_backtest(
        history=hist,
        prices=prices,
        start=start,
        end=end,
        strategy_fn=idle_strategy,
        initial_soc=0.5,
    )

    assert isinstance(result, BacktestResult)
    assert result.total_cost_dollars == pytest.approx(0.0, abs=1e-6)
    assert result.total_import_kwh == pytest.approx(0.0, abs=1e-6)
    assert result.total_export_kwh == pytest.approx(0.0, abs=1e-6)


def test_backtest_handles_sparse_history():
    """Gaps in HA history should not crash the backtest."""
    start = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=2)

    # Very sparse history: only 3 rows
    hist = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2026-03-01 00:00",
            "2026-03-10 12:00",
            "2026-03-20 18:00",
        ], utc=True),
        "load_kw": [1.0, 2.0, 1.5],
        "solar_kw": [0.0, 5.0, 3.0],
    })
    prices = _make_prices(start - timedelta(hours=1), hours=4, price=10.0)

    result = run_backtest(
        history=hist,
        prices=prices,
        start=start,
        end=end,
        strategy_fn=idle_strategy,
        initial_soc=0.5,
    )
    assert isinstance(result, BacktestResult)
    # With no history near the decision point, the sim falls back to defaults (0).
    # The sim should still produce a log with some rows.
    assert len(result.interval_log) > 0


def test_backtest_output_shape():
    """BacktestResult has all expected fields populated."""
    start = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=6)
    hist = _make_history(start - timedelta(days=30), hours=30 * 24 + 6, load_kw=1.0)
    prices = _make_prices(start - timedelta(days=30), hours=30 * 24 + 6, price=10.0)

    result = run_backtest(
        history=hist,
        prices=prices,
        start=start,
        end=end,
        strategy_fn=idle_strategy,
        initial_soc=0.5,
        strategy_name="idle-test",
    )

    assert result.strategy_name == "idle-test"
    assert isinstance(result.total_cost_dollars, float)
    assert isinstance(result.total_import_kwh, float)
    assert isinstance(result.total_export_kwh, float)
    assert isinstance(result.total_charge_cycles, float)
    assert isinstance(result.daily_breakdown, pd.DataFrame)
    assert isinstance(result.interval_log, pd.DataFrame)

    # Daily breakdown columns
    for col in ("date", "cost_dollars", "import_kwh", "export_kwh"):
        assert col in result.daily_breakdown.columns

    # Interval log columns
    for col in (
        "timestamp", "soc_before", "soc_after", "action",
        "price_c_kwh", "cost_delta_dollars", "net_grid_kwh",
    ):
        assert col in result.interval_log.columns

    # 6 hours / 5-min = 72 interval rows
    assert len(result.interval_log) == 72


def test_soc_bounds_enforced_in_sim():
    """A strategy that commands wild charge/discharge can't push SOC out of bounds."""
    battery = BatteryConstants()
    start = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=4)

    hist = _make_history(start - timedelta(days=30), hours=30 * 24 + 4)
    prices = _make_prices(start - timedelta(days=30), hours=30 * 24 + 4, price=10.0)

    def always_max_charge(forecast: pd.DataFrame, soc_now: float) -> Plan:
        # Build a plan, manually stamp max charge on every interval.
        ts = forecast["timestamp"].values
        plan = Plan.from_self_consume(
            timestamps=ts,
            import_c_kwh=forecast["import_c_kwh"].values.astype(float),
            export_c_kwh=forecast["export_c_kwh"].values.astype(float),
            load_kw=forecast["load_kw"].values.astype(float),
            solar_kw=forecast["solar_kw"].values.astype(float),
            soc_now=soc_now,
        )
        # Force commanded charge huge on every interval, bypassing plan.charge()
        interval_h = INTERVAL_MIN / 60.0
        plan.charge_grid_kwh[:] = battery.max_charge_kw * interval_h * 10  # 10x over the rate limit
        plan.actions[:] = Action.CHARGE_GRID
        return plan

    result = run_backtest(
        history=hist,
        prices=prices,
        start=start,
        end=end,
        strategy_fn=always_max_charge,
        initial_soc=0.5,
        battery=battery,
    )

    # SOC must stay in [floor, ceiling]
    soc_trace = result.interval_log["soc_after"].values
    assert (soc_trace >= battery.soc_floor - 1e-9).all()
    assert (soc_trace <= battery.soc_ceiling + 1e-9).all()

    # And a pure-discharge strategy must not go below floor
    def always_max_discharge(forecast: pd.DataFrame, soc_now: float) -> Plan:
        ts = forecast["timestamp"].values
        plan = Plan.from_self_consume(
            timestamps=ts,
            import_c_kwh=forecast["import_c_kwh"].values.astype(float),
            export_c_kwh=forecast["export_c_kwh"].values.astype(float),
            load_kw=forecast["load_kw"].values.astype(float),
            solar_kw=forecast["solar_kw"].values.astype(float),
            soc_now=soc_now,
        )
        interval_h = INTERVAL_MIN / 60.0
        plan.discharge_grid_kwh[:] = battery.max_discharge_kw * interval_h * 10
        plan.actions[:] = Action.DISCHARGE_GRID
        return plan

    result2 = run_backtest(
        history=hist,
        prices=prices,
        start=start,
        end=end,
        strategy_fn=always_max_discharge,
        initial_soc=0.5,
        battery=battery,
    )
    soc_trace2 = result2.interval_log["soc_after"].values
    assert (soc_trace2 >= battery.soc_floor - 1e-9).all()
    assert (soc_trace2 <= battery.soc_ceiling + 1e-9).all()


def test_cost_accounting_import():
    """Importing 1 kWh at 10 c/kWh costs $0.10.

    Battery at floor so self-consume can't cover the load.
    """
    start = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5)

    hist = _make_history(start - timedelta(days=30), hours=30 * 24 + 1, load_kw=12.0, solar_kw=0.0)
    prices = _make_prices(start - timedelta(days=30), hours=30 * 24 + 1, price=10.0)

    result = run_backtest(
        history=hist,
        prices=prices,
        start=start,
        end=end,
        strategy_fn=idle_strategy,
        initial_soc=BatteryConstants().soc_floor,
    )
    assert result.total_import_kwh == pytest.approx(1.0, abs=1e-6)
    assert result.total_export_kwh == pytest.approx(0.0, abs=1e-6)
    assert result.total_cost_dollars == pytest.approx(0.10, abs=1e-6)


def test_cost_accounting_export():
    """Exporting 1 kWh at 10 c/kWh earns $0.10.

    Battery at ceiling so self-consume can't absorb the solar surplus.
    """
    start = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5)

    hist = _make_history(start - timedelta(days=30), hours=30 * 24 + 1, load_kw=0.0, solar_kw=12.0)
    prices = _make_prices(start - timedelta(days=30), hours=30 * 24 + 1, price=10.0)

    result = run_backtest(
        history=hist,
        prices=prices,
        start=start,
        end=end,
        strategy_fn=idle_strategy,
        initial_soc=BatteryConstants().soc_ceiling,
    )
    assert result.total_export_kwh == pytest.approx(1.0, abs=1e-6)
    assert result.total_import_kwh == pytest.approx(0.0, abs=1e-6)
    assert result.total_cost_dollars == pytest.approx(-0.10, abs=1e-6)


def test_step_battery_respects_ceiling():
    """Direct unit test on _step_battery: charge clamped at ceiling."""
    battery = BatteryConstants()
    # Start at 94%, ceiling 95%. Commanding huge charge should land exactly at ceiling.
    new_soc, actual_charge, actual_discharge = _step_battery(
        soc=0.94,
        charge_grid_kwh=100.0,
        discharge_grid_kwh=0.0,
        battery=battery,
    )
    assert new_soc == pytest.approx(battery.soc_ceiling, abs=1e-9)
    assert actual_discharge == 0.0
    # Actual charge must be less than the commanded 100 kWh
    assert actual_charge < 100.0


def test_step_battery_respects_floor():
    battery = BatteryConstants()
    new_soc, actual_charge, actual_discharge = _step_battery(
        soc=0.11,
        charge_grid_kwh=0.0,
        discharge_grid_kwh=100.0,
        battery=battery,
    )
    assert new_soc == pytest.approx(battery.soc_floor, abs=1e-9)
    assert actual_charge == 0.0
    assert actual_discharge < 100.0
