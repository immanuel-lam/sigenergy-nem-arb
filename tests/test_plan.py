"""Tests for the Plan dataclass — SOC trajectory, mutations, bounds."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from arb.scheduler.constants import INTERVAL_MIN, BatteryConstants
from arb.scheduler.plan import Action, Plan


def _make_timestamps(n: int) -> np.ndarray:
    start = datetime(2026, 4, 23, 0, 0, tzinfo=timezone.utc)
    ts = pd.date_range(start=start, periods=n, freq=f"{INTERVAL_MIN}min", tz="UTC")
    return ts.values


def _flat_plan(n: int = 12, soc: float = 0.5, load: float = 0.0, solar: float = 0.0) -> Plan:
    """Helper: plan with flat inputs, no load, no solar."""
    ts = _make_timestamps(n)
    return Plan.from_self_consume(
        timestamps=ts,
        import_c_kwh=np.full(n, 10.0),
        export_c_kwh=np.full(n, 10.0),
        load_kw=np.full(n, load),
        solar_kw=np.full(n, solar),
        soc_now=soc,
    )


def test_from_self_consume_no_load_no_solar():
    """With zero load and zero solar, SOC stays constant."""
    plan = _flat_plan(n=12, soc=0.5)
    assert all(abs(s - 0.5) < 1e-9 for s in plan.soc)


def test_from_self_consume_solar_charges():
    """Solar surplus should charge the battery."""
    plan = _flat_plan(n=12, soc=0.5, load=0.0, solar=10.0)
    # SOC should increase
    assert plan.soc[-1] > plan.soc[0]


def test_from_self_consume_load_discharges():
    """Load deficit should discharge the battery."""
    plan = _flat_plan(n=12, soc=0.5, load=10.0, solar=0.0)
    assert plan.soc[-1] < plan.soc[0]


def test_from_self_consume_soc_clamps_at_floor():
    """SOC should never go below soc_floor."""
    bc = BatteryConstants()
    plan = _flat_plan(n=100, soc=0.15, load=30.0, solar=0.0)
    assert all(s >= bc.soc_floor - 1e-9 for s in plan.soc)


def test_from_self_consume_soc_clamps_at_ceiling():
    """SOC should never exceed soc_ceiling."""
    bc = BatteryConstants()
    plan = _flat_plan(n=100, soc=0.90, load=0.0, solar=30.0)
    assert all(s <= bc.soc_ceiling + 1e-9 for s in plan.soc)


def test_charge_raises_soc():
    plan = _flat_plan(n=12, soc=0.5)
    soc_before = plan.soc.copy()
    plan.charge(3, 1.0)  # 1 kWh grid-side
    # SOC should be higher from index 4 onward
    assert plan.soc[4] > soc_before[4]
    # SOC before charge interval should be unchanged
    assert plan.soc[2] == soc_before[2]


def test_discharge_lowers_soc():
    plan = _flat_plan(n=12, soc=0.5)
    soc_before = plan.soc.copy()
    plan.discharge(5, 1.0)
    assert plan.soc[6] < soc_before[6]
    assert plan.soc[4] == soc_before[4]


def test_charge_energy_accounting():
    """Verify grid-side vs battery-side math."""
    bc = BatteryConstants()
    plan = _flat_plan(n=12, soc=0.5)
    plan.charge(0, 1.0)
    # Battery receives 1.0 * 0.95 = 0.95 kWh
    # Delta SOC = 0.95 / 64 = 0.01484375
    expected_delta = 1.0 * bc.charge_efficiency / bc.capacity_kwh
    actual_delta = plan.soc[1] - 0.5
    assert abs(actual_delta - expected_delta) < 1e-9


def test_discharge_energy_accounting():
    bc = BatteryConstants()
    plan = _flat_plan(n=12, soc=0.5)
    plan.discharge(0, 1.0)
    # Battery loses 1.0 / 0.95 = 1.0526... kWh
    expected_delta = 1.0 / bc.discharge_efficiency / bc.capacity_kwh
    actual_delta = 0.5 - plan.soc[1]
    assert abs(actual_delta - expected_delta) < 1e-9


def test_can_charge_respects_ceiling():
    """Near ceiling, charge room should be limited."""
    plan = _flat_plan(n=12, soc=0.94)
    room = plan.can_charge_kwh(0)
    # SOC headroom = 0.95 - 0.94 = 0.01
    # grid kWh = 0.01 * 64 / 0.95 = 0.6736...
    bc = BatteryConstants()
    expected = (bc.soc_ceiling - 0.94) * bc.capacity_kwh / bc.charge_efficiency
    assert abs(room - expected) < 0.01


def test_can_discharge_respects_floor():
    plan = _flat_plan(n=12, soc=0.12)
    room = plan.can_discharge_kwh(0)
    bc = BatteryConstants()
    expected = (0.12 - bc.soc_floor) * bc.capacity_kwh * bc.discharge_efficiency
    assert abs(room - expected) < 0.01


def test_actions_default_idle():
    plan = _flat_plan(n=12, soc=0.5)
    assert all(a == Action.IDLE for a in plan.actions)


def test_charge_sets_action():
    plan = _flat_plan(n=12, soc=0.5)
    plan.charge(3, 0.5)
    assert plan.actions[3] == Action.CHARGE_GRID
    assert plan.actions[2] == Action.IDLE


def test_hold_solar_sets_action():
    plan = _flat_plan(n=12, soc=0.5)
    plan.hold_solar(7)
    assert plan.actions[7] == Action.HOLD_SOLAR


def test_to_dataframe_shape():
    plan = _flat_plan(n=12, soc=0.5)
    df = plan.to_dataframe()
    assert len(df) == 12
    assert "action" in df.columns
    assert "soc_after" in df.columns


def test_summary_not_empty():
    plan = _flat_plan(n=12, soc=0.5)
    assert len(plan.summary()) > 0
