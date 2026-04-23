"""Tests for plan_diff: structured diff between two plans."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from arb.agent.plan_diff import (
    DiffKind,
    diff_plans,
    format_diff_for_llm,
    format_diff_short,
)
from arb.scheduler.constants import INTERVAL_MIN
from arb.scheduler.plan import Action, Plan


def _make_timestamps(n: int, start: datetime | None = None) -> np.ndarray:
    # Anchor around "now" so current_interval_idx lands on an early interval.
    start = start or datetime.now(timezone.utc).replace(second=0, microsecond=0)
    ts = pd.date_range(start=start, periods=n, freq=f"{INTERVAL_MIN}min", tz="UTC")
    return ts.values


def _plan(n: int = 12, soc: float = 0.5, start: datetime | None = None) -> Plan:
    ts = _make_timestamps(n, start)
    return Plan.from_self_consume(
        timestamps=ts,
        import_c_kwh=np.full(n, 10.0),
        export_c_kwh=np.full(n, 8.0),
        load_kw=np.zeros(n),
        solar_kw=np.zeros(n),
        soc_now=soc,
    )


def test_diff_with_no_previous_plan():
    new = _plan()
    diff = diff_plans(new, None)
    assert diff.kind == DiffKind.NEW
    assert diff.changed_intervals == []
    assert diff.summary["n_changed"] == 0
    assert diff.summary["previous_total_charge_kwh"] == 0.0
    # current_interval_diff should still be populated with previous_* = None
    assert diff.current_interval_diff is not None
    assert diff.current_interval_diff.previous_action is None


def test_diff_identical_plans_no_change():
    a = _plan()
    b = _plan()
    diff = diff_plans(b, a)
    assert diff.kind == DiffKind.NO_CHANGE
    assert diff.summary["n_changed"] == 0
    assert diff.current_interval_diff is None


def test_diff_detects_action_change():
    a = _plan()
    b = _plan()
    b.charge(5, 0.5)  # flips action[5] IDLE -> CHARGE_GRID
    diff = diff_plans(b, a)
    assert diff.kind in (DiffKind.ACTION_CHANGED, DiffKind.BOTH)
    assert diff.summary["n_changed"] >= 1
    assert diff.summary["n_action_changes"] >= 1
    assert diff.summary["n_new_charge_intervals"] >= 1
    actions_at_5 = [
        d for d in diff.changed_intervals if d.new_action == "CHARGE_GRID"
    ]
    assert any(d.previous_action == "IDLE" for d in actions_at_5)


def test_diff_detects_energy_change():
    # Same action bucket, different energy in that interval.
    a = _plan()
    a.charge(5, 0.5)
    b = _plan()
    b.charge(5, 1.2)  # still CHARGE_GRID, more kWh
    diff = diff_plans(b, a)
    # action for interval 5 is same (CHARGE_GRID), energy differs
    changed = [d for d in diff.changed_intervals if d.previous_action == "CHARGE_GRID"]
    assert any(
        not d.action_changed and d.energy_changed for d in changed
    ), "expected an ENERGY_CHANGED interval"
    # Overall kind should reflect energy-only change if nothing else differs
    assert diff.kind in (DiffKind.ENERGY_CHANGED, DiffKind.BOTH)


def test_diff_summary_counts_correct():
    # Previous: 3 charge intervals. New: 2 charge intervals, 1 removed.
    a = _plan(n=20)
    a.charge(2, 0.3)
    a.charge(5, 0.3)
    a.charge(8, 0.3)
    b = _plan(n=20)
    b.charge(2, 0.3)
    b.charge(5, 0.3)
    # interval 8 is no longer a charge -> removed
    diff = diff_plans(b, a)
    assert diff.summary["n_removed_charge_intervals"] == 1
    assert diff.summary["n_new_charge_intervals"] == 0


def test_format_diff_for_llm_shape():
    a = _plan()
    b = _plan()
    b.charge(5, 0.5)
    diff = diff_plans(b, a)
    text = format_diff_for_llm(diff)
    assert isinstance(text, str)
    assert "changed" in text.lower()


def test_format_diff_short_one_line():
    a = _plan()
    b = _plan()
    b.charge(5, 0.5)
    diff = diff_plans(b, a)
    line = format_diff_short(diff)
    assert isinstance(line, str)
    assert "\n" not in line
