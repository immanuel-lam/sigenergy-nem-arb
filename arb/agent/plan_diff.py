"""Structured diff between two Plan objects.

Feeds explain.py with a richer picture than "action changed / didn't".
The diff is per-interval, aligned by timestamp, so horizon shifts between
cycles don't register as false changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import numpy as np
import pandas as pd

from arb.scheduler.plan import Action, Plan

# Energy changes under this many kWh are noise, not a real revision.
ENERGY_EPSILON_KWH = 1e-6
# Price revisions under this many c/kWh are rounding, not news.
PRICE_EPSILON_C = 1e-6


class DiffKind(str, Enum):
    NEW = "NEW"
    NO_CHANGE = "NO_CHANGE"
    ACTION_CHANGED = "ACTION_CHANGED"
    ENERGY_CHANGED = "ENERGY_CHANGED"
    BOTH = "BOTH"


@dataclass
class IntervalDiff:
    """What changed for a specific interval."""

    timestamp: datetime
    previous_action: str | None
    new_action: str
    previous_charge_kwh: float
    new_charge_kwh: float
    previous_discharge_kwh: float
    new_discharge_kwh: float
    previous_import_c: float | None
    new_import_c: float
    previous_export_c: float | None
    new_export_c: float

    @property
    def action_changed(self) -> bool:
        return self.previous_action is not None and self.previous_action != self.new_action

    @property
    def energy_changed(self) -> bool:
        dc = abs(self.new_charge_kwh - self.previous_charge_kwh)
        dd = abs(self.new_discharge_kwh - self.previous_discharge_kwh)
        return dc > ENERGY_EPSILON_KWH or dd > ENERGY_EPSILON_KWH


@dataclass
class PlanDiff:
    """Structured comparison of two plans."""

    kind: DiffKind
    current_interval_diff: IntervalDiff | None
    changed_intervals: list[IntervalDiff] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def _action_str(a) -> str:
    return a.value if isinstance(a, Action) else str(a)


def _timestamp_to_datetime(ts) -> datetime:
    """Convert numpy datetime64 / pandas Timestamp to a python datetime in UTC."""
    return pd.Timestamp(ts).to_pydatetime()


def _build_interval_diff(
    new: Plan,
    previous: Plan | None,
    new_idx: int,
    prev_idx: int | None,
) -> IntervalDiff:
    new_action = _action_str(new.actions[new_idx])
    new_charge = float(new.charge_grid_kwh[new_idx])
    new_discharge = float(new.discharge_grid_kwh[new_idx])
    new_import = float(new.import_c_kwh[new_idx])
    new_export = float(new.export_c_kwh[new_idx])

    if previous is None or prev_idx is None:
        return IntervalDiff(
            timestamp=_timestamp_to_datetime(new.timestamps[new_idx]),
            previous_action=None,
            new_action=new_action,
            previous_charge_kwh=0.0,
            new_charge_kwh=new_charge,
            previous_discharge_kwh=0.0,
            new_discharge_kwh=new_discharge,
            previous_import_c=None,
            new_import_c=new_import,
            previous_export_c=None,
            new_export_c=new_export,
        )

    return IntervalDiff(
        timestamp=_timestamp_to_datetime(new.timestamps[new_idx]),
        previous_action=_action_str(previous.actions[prev_idx]),
        new_action=new_action,
        previous_charge_kwh=float(previous.charge_grid_kwh[prev_idx]),
        new_charge_kwh=new_charge,
        previous_discharge_kwh=float(previous.discharge_grid_kwh[prev_idx]),
        new_discharge_kwh=new_discharge,
        previous_import_c=float(previous.import_c_kwh[prev_idx]),
        new_import_c=new_import,
        previous_export_c=float(previous.export_c_kwh[prev_idx]),
        new_export_c=new_export,
    )


def _timestamp_index(plan: Plan) -> dict:
    """Map ns-since-epoch -> interval idx, for timestamp alignment across plans."""
    index = {}
    for i in range(plan.n):
        key = int(pd.Timestamp(plan.timestamps[i]).value)
        index[key] = i
    return index


def diff_plans(new: Plan, previous: Plan | None) -> PlanDiff:
    """Produce a structured diff between new and previous plan.

    Aligns by timestamp so a rolling horizon doesn't trigger false positives.
    """
    if previous is None:
        cur_idx = new.current_interval_idx
        if cur_idx is None:
            cur_idx = 0
        cur_diff = _build_interval_diff(new, None, cur_idx, None)
        summary = {
            "n_changed": 0,
            "n_action_changes": 0,
            "n_new_charge_intervals": 0,
            "n_new_discharge_intervals": 0,
            "n_removed_charge_intervals": 0,
            "n_removed_discharge_intervals": 0,
            "max_import_price_revision_c": 0.0,
            "max_export_price_revision_c": 0.0,
            "previous_total_charge_kwh": 0.0,
            "new_total_charge_kwh": float(new.charge_grid_kwh.sum()),
            "previous_total_discharge_kwh": 0.0,
            "new_total_discharge_kwh": float(new.discharge_grid_kwh.sum()),
        }
        return PlanDiff(
            kind=DiffKind.NEW,
            current_interval_diff=cur_diff,
            changed_intervals=[],
            summary=summary,
        )

    prev_index = _timestamp_index(previous)
    changed_intervals: list[IntervalDiff] = []
    n_action_changes = 0
    n_new_charge = 0
    n_new_discharge = 0
    n_removed_charge = 0
    n_removed_discharge = 0
    max_import_rev = 0.0
    max_export_rev = 0.0

    for i in range(new.n):
        key = int(pd.Timestamp(new.timestamps[i]).value)
        prev_idx = prev_index.get(key)
        if prev_idx is None:
            continue

        diff = _build_interval_diff(new, previous, i, prev_idx)

        if diff.previous_import_c is not None:
            max_import_rev = max(
                max_import_rev, abs(diff.new_import_c - diff.previous_import_c)
            )
            max_export_rev = max(
                max_export_rev, abs(diff.new_export_c - diff.previous_export_c)
            )

        if diff.action_changed or diff.energy_changed:
            changed_intervals.append(diff)

        if diff.action_changed:
            n_action_changes += 1
            prev_a = diff.previous_action
            new_a = diff.new_action
            if prev_a != "CHARGE_GRID" and new_a == "CHARGE_GRID":
                n_new_charge += 1
            if prev_a != "DISCHARGE_GRID" and new_a == "DISCHARGE_GRID":
                n_new_discharge += 1
            if prev_a == "CHARGE_GRID" and new_a != "CHARGE_GRID":
                n_removed_charge += 1
            if prev_a == "DISCHARGE_GRID" and new_a != "DISCHARGE_GRID":
                n_removed_discharge += 1

    # Filter out price-only changes that don't affect action or energy
    # (they're already excluded above — changed_intervals only has real changes).

    # Overall kind
    any_action = any(d.action_changed for d in changed_intervals)
    any_energy = any(d.energy_changed for d in changed_intervals)
    if any_action and any_energy:
        kind = DiffKind.BOTH
    elif any_action:
        kind = DiffKind.ACTION_CHANGED
    elif any_energy:
        kind = DiffKind.ENERGY_CHANGED
    else:
        kind = DiffKind.NO_CHANGE

    # Current interval diff
    cur_idx = new.current_interval_idx
    if cur_idx is None:
        cur_idx = 0
    cur_key = int(pd.Timestamp(new.timestamps[cur_idx]).value)
    cur_prev_idx = prev_index.get(cur_key)
    current_interval_diff: IntervalDiff | None = None
    if cur_prev_idx is not None:
        candidate = _build_interval_diff(new, previous, cur_idx, cur_prev_idx)
        if candidate.action_changed or candidate.energy_changed:
            current_interval_diff = candidate
    else:
        # Current interval not in previous plan — treat as first look for this slot
        current_interval_diff = _build_interval_diff(new, None, cur_idx, None)

    summary = {
        "n_changed": len(changed_intervals),
        "n_action_changes": n_action_changes,
        "n_new_charge_intervals": n_new_charge,
        "n_new_discharge_intervals": n_new_discharge,
        "n_removed_charge_intervals": n_removed_charge,
        "n_removed_discharge_intervals": n_removed_discharge,
        "max_import_price_revision_c": float(max_import_rev),
        "max_export_price_revision_c": float(max_export_rev),
        "previous_total_charge_kwh": float(previous.charge_grid_kwh.sum()),
        "new_total_charge_kwh": float(new.charge_grid_kwh.sum()),
        "previous_total_discharge_kwh": float(previous.discharge_grid_kwh.sum()),
        "new_total_discharge_kwh": float(new.discharge_grid_kwh.sum()),
    }

    return PlanDiff(
        kind=kind,
        current_interval_diff=current_interval_diff,
        changed_intervals=changed_intervals,
        summary=summary,
    )


def _fmt_ts(ts: datetime) -> str:
    """HH:MM in the timestamp's own tz (usually UTC)."""
    return ts.strftime("%H:%M")


def _describe_interval(d: IntervalDiff) -> str:
    """One-line description of a single interval diff."""
    if d.action_changed:
        bits = [f"{_fmt_ts(d.timestamp)} {d.previous_action} -> {d.new_action}"]
        if d.new_action == "CHARGE_GRID" and d.new_charge_kwh > 0:
            bits.append(f"({d.new_charge_kwh:.2f} kWh)")
        elif d.new_action == "DISCHARGE_GRID" and d.new_discharge_kwh > 0:
            bits.append(f"({d.new_discharge_kwh:.2f} kWh)")
        if d.previous_import_c is not None:
            dimp = d.new_import_c - d.previous_import_c
            if abs(dimp) > 0.1:
                bits.append(f"import {dimp:+.1f}c")
        return " ".join(bits)
    # energy-only change
    dc = d.new_charge_kwh - d.previous_charge_kwh
    dd = d.new_discharge_kwh - d.previous_discharge_kwh
    if abs(dc) > abs(dd):
        return f"{_fmt_ts(d.timestamp)} charge {d.previous_charge_kwh:.2f} -> {d.new_charge_kwh:.2f} kWh"
    return f"{_fmt_ts(d.timestamp)} discharge {d.previous_discharge_kwh:.2f} -> {d.new_discharge_kwh:.2f} kWh"


def format_diff_for_llm(diff: PlanDiff, max_intervals: int = 5) -> str:
    """Terse, human-readable diff for the LLM prompt."""
    if diff.kind == DiffKind.NEW:
        return "First plan — no previous to compare."
    if diff.kind == DiffKind.NO_CHANGE:
        return "No material change from previous plan."

    s = diff.summary
    lines = [f"Plan revised. {s['n_changed']} intervals changed."]

    if diff.changed_intervals:
        lines.append("Key changes:")
        for d in diff.changed_intervals[:max_intervals]:
            lines.append(f"- {_describe_interval(d)}")
        extra = len(diff.changed_intervals) - max_intervals
        if extra > 0:
            lines.append(f"- ... and {extra} more")

    lines.append(
        f"Total planned charge: {s['previous_total_charge_kwh']:.1f} -> "
        f"{s['new_total_charge_kwh']:.1f} kWh"
    )
    lines.append(
        f"Total planned discharge: {s['previous_total_discharge_kwh']:.1f} -> "
        f"{s['new_total_discharge_kwh']:.1f} kWh"
    )
    if s["max_import_price_revision_c"] > 0.1:
        lines.append(
            f"Max import price revision: {s['max_import_price_revision_c']:.1f} c/kWh"
        )
    if s["max_export_price_revision_c"] > 0.1:
        lines.append(
            f"Max export price revision: {s['max_export_price_revision_c']:.1f} c/kWh"
        )
    return "\n".join(lines)


def format_diff_short(diff: PlanDiff) -> str:
    """One-line diff summary for logs."""
    if diff.kind == DiffKind.NEW:
        return "plan_diff: NEW (first plan)"
    if diff.kind == DiffKind.NO_CHANGE:
        return "plan_diff: NO_CHANGE"
    s = diff.summary
    return (
        f"plan_diff: {diff.kind.value} "
        f"n_changed={s['n_changed']} "
        f"actions={s['n_action_changes']} "
        f"+ch={s['n_new_charge_intervals']} -ch={s['n_removed_charge_intervals']} "
        f"+dis={s['n_new_discharge_intervals']} -dis={s['n_removed_discharge_intervals']} "
        f"charge_kwh={s['previous_total_charge_kwh']:.1f}->{s['new_total_charge_kwh']:.1f}"
    )


__all__ = [
    "Action",
    "DiffKind",
    "IntervalDiff",
    "PlanDiff",
    "diff_plans",
    "format_diff_for_llm",
    "format_diff_short",
]
