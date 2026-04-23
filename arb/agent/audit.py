"""Post-interval execution audit.

Advisory-mode means Amber SmartShift actually controls the battery; our plan
is a recommendation. This module checks, after each interval, how far the
actual SOC trajectory drifted from what we planned — useful both for trust
(are we giving good advice?) and for the backtest comparison story.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from arb.scheduler.plan import Action, Plan

log = logging.getLogger(__name__)

AUDIT_LOG_PATH = Path(os.getenv("ARB_EXECUTION_AUDIT_LOG", "execution_audit.log"))


@dataclass
class AuditEntry:
    """One post-interval comparison of planned vs actual state."""

    timestamp: datetime
    plan_created_at: datetime
    planned_action: str
    planned_soc_before: float
    planned_soc_after: float
    actual_soc_before: float | None
    actual_soc_after: float | None
    actual_battery_power_kw: float | None
    planned_charge_kwh: float
    planned_discharge_kwh: float
    soc_delta_pct: float | None
    status: str
    notes: str


def _action_str(a) -> str:
    return a.value if isinstance(a, Action) else str(a)


def _entry_to_jsonable(entry: AuditEntry) -> dict:
    d = asdict(entry)
    # datetimes -> iso strings
    if entry.timestamp is not None:
        d["timestamp"] = entry.timestamp.isoformat()
    if entry.plan_created_at is not None:
        d["plan_created_at"] = entry.plan_created_at.isoformat()
    return d


def _entry_from_jsonable(d: dict) -> AuditEntry:
    ts = d.get("timestamp")
    pc = d.get("plan_created_at")
    return AuditEntry(
        timestamp=datetime.fromisoformat(ts) if ts else None,  # type: ignore[arg-type]
        plan_created_at=datetime.fromisoformat(pc) if pc else None,  # type: ignore[arg-type]
        planned_action=d.get("planned_action", ""),
        planned_soc_before=float(d.get("planned_soc_before", 0.0)),
        planned_soc_after=float(d.get("planned_soc_after", 0.0)),
        actual_soc_before=d.get("actual_soc_before"),
        actual_soc_after=d.get("actual_soc_after"),
        actual_battery_power_kw=d.get("actual_battery_power_kw"),
        planned_charge_kwh=float(d.get("planned_charge_kwh", 0.0)),
        planned_discharge_kwh=float(d.get("planned_discharge_kwh", 0.0)),
        soc_delta_pct=d.get("soc_delta_pct"),
        status=d.get("status", ""),
        notes=d.get("notes", ""),
    )


def _no_data_entry(
    plan: Plan, notes: str, current_state: dict | None = None
) -> AuditEntry:
    ha = current_state or {}
    now = datetime.now(timezone.utc)
    return AuditEntry(
        timestamp=now,
        plan_created_at=plan.created_at,
        planned_action="",
        planned_soc_before=0.0,
        planned_soc_after=0.0,
        actual_soc_before=None,
        actual_soc_after=ha.get("soc_pct"),
        actual_battery_power_kw=ha.get("battery_power_kw"),
        planned_charge_kwh=0.0,
        planned_discharge_kwh=0.0,
        soc_delta_pct=None,
        status="no_data",
        notes=notes,
    )


def audit_current_interval(
    plan: Plan,
    current_ha_state: dict,
    prior_soc_pct: float | None,
    tolerance_pct: float = 5.0,
) -> AuditEntry:
    """Audit the current interval against fresh HA state.

    Compares planned SOC delta vs actual SOC delta across the current interval.
    Writes the entry to AUDIT_LOG_PATH and returns it.
    """
    idx = plan.current_interval_idx
    if idx is None:
        entry = _no_data_entry(plan, "current time outside plan horizon", current_ha_state)
        write_audit_entry(entry)
        return entry

    actual_soc_after = current_ha_state.get("soc_pct")
    actual_battery_power = current_ha_state.get("battery_power_kw")

    planned_soc_before = float(plan.soc[idx])
    planned_soc_after = float(plan.soc[idx + 1])
    planned_action = _action_str(plan.actions[idx])
    planned_charge = float(plan.charge_grid_kwh[idx])
    planned_discharge = float(plan.discharge_grid_kwh[idx])

    now = datetime.now(timezone.utc)

    if prior_soc_pct is None or actual_soc_after is None:
        reason = "prior_soc missing" if prior_soc_pct is None else "actual_soc missing"
        entry = AuditEntry(
            timestamp=now,
            plan_created_at=plan.created_at,
            planned_action=planned_action,
            planned_soc_before=planned_soc_before,
            planned_soc_after=planned_soc_after,
            actual_soc_before=prior_soc_pct,
            actual_soc_after=actual_soc_after,
            actual_battery_power_kw=actual_battery_power,
            planned_charge_kwh=planned_charge,
            planned_discharge_kwh=planned_discharge,
            soc_delta_pct=None,
            status="no_data",
            notes=reason,
        )
        write_audit_entry(entry)
        return entry

    planned_delta_pct = (planned_soc_after - planned_soc_before) * 100.0
    actual_delta_pct = float(actual_soc_after) - float(prior_soc_pct)
    drift = actual_delta_pct - planned_delta_pct  # signed: +ve = battery higher than plan

    abs_drift = abs(drift)
    if abs_drift > tolerance_pct:
        status = "major_drift"
    elif abs_drift > tolerance_pct / 2.0:
        status = "minor_drift"
    else:
        status = "ok"

    notes = (
        f"planned_delta={planned_delta_pct:+.2f}% "
        f"actual_delta={actual_delta_pct:+.2f}% "
        f"drift={drift:+.2f}%"
    )

    entry = AuditEntry(
        timestamp=now,
        plan_created_at=plan.created_at,
        planned_action=planned_action,
        planned_soc_before=planned_soc_before,
        planned_soc_after=planned_soc_after,
        actual_soc_before=float(prior_soc_pct),
        actual_soc_after=float(actual_soc_after),
        actual_battery_power_kw=actual_battery_power,
        planned_charge_kwh=planned_charge,
        planned_discharge_kwh=planned_discharge,
        soc_delta_pct=float(drift),
        status=status,
        notes=notes,
    )
    write_audit_entry(entry)
    if status == "major_drift":
        log.warning("audit: major drift %+.2f%% (%s)", drift, notes)
    elif status == "minor_drift":
        log.info("audit: minor drift %+.2f%% (%s)", drift, notes)
    return entry


def write_audit_entry(entry: AuditEntry) -> None:
    """Append an AuditEntry to AUDIT_LOG_PATH as one JSON line."""
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_entry_to_jsonable(entry)) + "\n")


def read_audit_log(n_recent: int = 100) -> list[AuditEntry]:
    """Read the last n_recent entries from AUDIT_LOG_PATH."""
    if not AUDIT_LOG_PATH.exists():
        return []
    with AUDIT_LOG_PATH.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    tail = lines[-n_recent:] if n_recent > 0 else lines
    entries: list[AuditEntry] = []
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(_entry_from_jsonable(json.loads(line)))
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            log.warning("skipping malformed audit line: %s", e)
    return entries


def summarize_recent_audits(n: int = 48) -> dict:
    """Summary stats over the last n audit entries."""
    entries = read_audit_log(n)
    n_total = len(entries)
    n_ok = sum(1 for e in entries if e.status == "ok")
    n_minor = sum(1 for e in entries if e.status == "minor_drift")
    n_major = sum(1 for e in entries if e.status == "major_drift")
    n_no_data = sum(1 for e in entries if e.status == "no_data")

    drifts = [e.soc_delta_pct for e in entries if e.soc_delta_pct is not None]
    mean_drift = float(sum(drifts) / len(drifts)) if drifts else 0.0
    max_drift = float(max((abs(d) for d in drifts), default=0.0))

    return {
        "n_total": n_total,
        "n_ok": n_ok,
        "n_minor_drift": n_minor,
        "n_major_drift": n_major,
        "n_no_data": n_no_data,
        "mean_drift_pct": mean_drift,
        "max_drift_pct": max_drift,
    }


__all__ = [
    "AUDIT_LOG_PATH",
    "AuditEntry",
    "audit_current_interval",
    "read_audit_log",
    "summarize_recent_audits",
    "write_audit_entry",
]
