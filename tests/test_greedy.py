"""Tests for the greedy scheduler."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from arb.scheduler.constants import INTERVAL_MIN, BatteryConstants
from arb.scheduler.greedy import schedule
from arb.scheduler.plan import Action


def _make_forecast(
    n: int = 24,
    import_prices: float | np.ndarray = 10.0,
    export_prices: float | np.ndarray = 10.0,
    load: float = 2.0,
    solar: float = 0.0,
) -> pd.DataFrame:
    start = datetime(2026, 4, 23, 0, 0, tzinfo=timezone.utc)
    ts = [start + timedelta(minutes=i * INTERVAL_MIN) for i in range(n)]

    if isinstance(import_prices, (int, float)):
        import_prices = np.full(n, import_prices)
    if isinstance(export_prices, (int, float)):
        export_prices = np.full(n, export_prices)

    return pd.DataFrame({
        "timestamp": ts,
        "import_c_kwh": import_prices,
        "export_c_kwh": export_prices,
        "load_kw": load,
        "solar_kw": solar,
    })


def test_flat_price_no_arbitrage():
    """If import == export, no pairs have positive net value after cycle cost."""
    forecast = _make_forecast(n=48, import_prices=10.0, export_prices=10.0)
    plan = schedule(forecast, soc_now=0.5)
    # Everything should be IDLE (self-consume handles load)
    assert all(plan.charge_grid_kwh[i] == 0 for i in range(plan.n))
    assert all(plan.discharge_grid_kwh[i] == 0 for i in range(plan.n))


def test_obvious_arbitrage():
    """Low price at start, high price at end -> should charge early, discharge late."""
    n = 48
    import_p = np.full(n, 10.0)
    export_p = np.full(n, 10.0)
    # Make first 6 intervals cheap (2 c/kWh) and last 6 intervals expensive (30 c/kWh)
    import_p[:6] = 2.0
    export_p[-6:] = 30.0

    forecast = _make_forecast(n=n, import_prices=import_p, export_prices=export_p, load=0.0)
    plan = schedule(forecast, soc_now=0.5)

    # Should have some charging in first 6 intervals
    early_charge = plan.charge_grid_kwh[:6].sum()
    assert early_charge > 0

    # Should have some discharging in last 6 intervals
    late_discharge = plan.discharge_grid_kwh[-6:].sum()
    assert late_discharge > 0


def test_respects_soc_ceiling():
    """Starting at 90% SOC should limit charging."""
    bc = BatteryConstants()
    n = 24
    import_p = np.full(n, 1.0)  # very cheap
    export_p = np.full(n, 50.0)  # very expensive

    forecast = _make_forecast(n=n, import_prices=import_p, export_prices=export_p, load=0.0)
    plan = schedule(forecast, soc_now=0.90, battery=bc)

    # SOC should never exceed ceiling
    assert all(s <= bc.soc_ceiling + 1e-6 for s in plan.soc)


def test_respects_soc_floor():
    """Starting at 15% SOC should limit discharging."""
    bc = BatteryConstants()
    n = 24
    import_p = np.full(n, 50.0)
    export_p = np.full(n, 50.0)

    forecast = _make_forecast(n=n, import_prices=import_p, export_prices=export_p, load=0.0)
    plan = schedule(forecast, soc_now=0.15, battery=bc)

    assert all(s >= bc.soc_floor - 1e-6 for s in plan.soc)


def test_negative_export_holds_solar():
    """Negative export price + solar surplus -> HOLD_SOLAR."""
    n = 24
    export_p = np.full(n, 5.0)
    export_p[6:12] = -5.0  # negative export mid-day

    forecast = _make_forecast(
        n=n,
        import_prices=10.0,
        export_prices=export_p,
        load=2.0,
        solar=10.0,  # surplus solar
    )
    plan = schedule(forecast, soc_now=0.5)

    # At least some of the negative-export intervals should be HOLD_SOLAR
    hold_count = sum(1 for i in range(6, 12) if plan.actions[i] == Action.HOLD_SOLAR)
    assert hold_count > 0


def test_cycle_cost_filters_marginal():
    """A spread below the cycle cost threshold should not trigger arbitrage."""
    bc = BatteryConstants()  # cycle_cost = 2 c/kWh, RTE = 0.9025
    n = 24

    # Spread of 2 c/kWh: net = 2 * 0.9025 - 2 = -0.195 -> should NOT trade
    import_p = np.full(n, 10.0)
    export_p = np.full(n, 12.0)

    forecast = _make_forecast(n=n, import_prices=import_p, export_prices=export_p, load=0.0)
    plan = schedule(forecast, soc_now=0.5, battery=bc)

    assert plan.charge_grid_kwh.sum() == 0
    assert plan.discharge_grid_kwh.sum() == 0
