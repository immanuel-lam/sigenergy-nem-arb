"""Tests for audit: post-interval planned-vs-actual comparison and log I/O."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from arb.agent import audit as audit_mod
from arb.agent.audit import (
    AuditEntry,
    audit_current_interval,
    read_audit_log,
    summarize_recent_audits,
    write_audit_entry,
)
from arb.scheduler.constants import INTERVAL_MIN
from arb.scheduler.plan import Action, Plan


@pytest.fixture(autouse=True)
def _redirect_audit_log(tmp_path, monkeypatch):
    log_path = tmp_path / "execution_audit.log"
    monkeypatch.setattr(audit_mod, "AUDIT_LOG_PATH", log_path)
    return log_path


def _make_timestamps(n: int, start: datetime | None = None) -> np.ndarray:
    start = start or datetime.now(timezone.utc).replace(second=0, microsecond=0)
    ts = pd.date_range(start=start, periods=n, freq=f"{INTERVAL_MIN}min", tz="UTC")
    return ts.values


def _plan(
    n: int = 12,
    soc: float = 0.5,
    start: datetime | None = None,
    load: float = 0.0,
    solar: float = 0.0,
) -> Plan:
    ts = _make_timestamps(n, start)
    return Plan.from_self_consume(
        timestamps=ts,
        import_c_kwh=np.full(n, 10.0),
        export_c_kwh=np.full(n, 8.0),
        load_kw=np.full(n, load),
        solar_kw=np.full(n, solar),
        soc_now=soc,
    )


def test_audit_no_data_when_no_current_interval():
    # Plan timestamps in the past — current_interval_idx returns None.
    past_start = datetime.now(timezone.utc) - timedelta(days=2)
    plan = _plan(n=12, start=past_start)
    entry = audit_current_interval(
        plan,
        current_ha_state={"soc_pct": 55.0, "battery_power_kw": 0.0},
        prior_soc_pct=50.0,
    )
    assert entry.status == "no_data"


def test_audit_ok_when_soc_matches_plan():
    # Flat plan with no load/solar — planned delta ~0%.
    plan = _plan(n=12, soc=0.5)
    entry = audit_current_interval(
        plan,
        current_ha_state={"soc_pct": 50.0, "battery_power_kw": 0.0},
        prior_soc_pct=50.0,
        tolerance_pct=5.0,
    )
    assert entry.status == "ok"
    assert entry.soc_delta_pct is not None
    assert abs(entry.soc_delta_pct) < 1e-6


def test_audit_detects_major_drift():
    plan = _plan(n=12, soc=0.5)
    # planned delta ~ 0, actual delta = +10% -> major drift at tolerance 5
    entry = audit_current_interval(
        plan,
        current_ha_state={"soc_pct": 60.0, "battery_power_kw": 5.0},
        prior_soc_pct=50.0,
        tolerance_pct=5.0,
    )
    assert entry.status == "major_drift"
    assert entry.soc_delta_pct is not None
    assert entry.soc_delta_pct > 5.0


def test_audit_detects_minor_drift():
    plan = _plan(n=12, soc=0.5)
    # planned ~0, actual +3% -> between tolerance/2 (2.5) and tolerance (5.0)
    entry = audit_current_interval(
        plan,
        current_ha_state={"soc_pct": 53.0, "battery_power_kw": 1.0},
        prior_soc_pct=50.0,
        tolerance_pct=5.0,
    )
    assert entry.status == "minor_drift"


def test_audit_no_data_when_prior_soc_none():
    plan = _plan(n=12, soc=0.5)
    entry = audit_current_interval(
        plan,
        current_ha_state={"soc_pct": 50.0, "battery_power_kw": 0.0},
        prior_soc_pct=None,
    )
    assert entry.status == "no_data"


def test_audit_writes_to_log(_redirect_audit_log):
    plan = _plan(n=12, soc=0.5)
    audit_current_interval(
        plan,
        current_ha_state={"soc_pct": 50.0, "battery_power_kw": 0.0},
        prior_soc_pct=50.0,
    )
    assert _redirect_audit_log.exists()
    content = _redirect_audit_log.read_text().strip().splitlines()
    assert len(content) == 1
    assert content[0].startswith("{")


def test_read_audit_log_roundtrip():
    now = datetime.now(timezone.utc)
    originals = [
        AuditEntry(
            timestamp=now + timedelta(minutes=i),
            plan_created_at=now,
            planned_action="IDLE",
            planned_soc_before=0.5,
            planned_soc_after=0.5,
            actual_soc_before=50.0,
            actual_soc_after=50.0 + i,
            actual_battery_power_kw=0.0,
            planned_charge_kwh=0.0,
            planned_discharge_kwh=0.0,
            soc_delta_pct=float(i),
            status="ok" if i == 0 else "minor_drift",
            notes=f"entry {i}",
        )
        for i in range(3)
    ]
    for e in originals:
        write_audit_entry(e)
    back = read_audit_log(10)
    assert len(back) == 3
    for orig, got in zip(originals, back):
        assert got.planned_action == orig.planned_action
        assert got.soc_delta_pct == orig.soc_delta_pct
        assert got.status == orig.status
        assert got.notes == orig.notes
        # timestamps round-trip via isoformat
        assert got.timestamp == orig.timestamp


def test_summarize_recent_audits_counts():
    now = datetime.now(timezone.utc)
    statuses = ["ok", "ok", "minor_drift", "major_drift", "no_data"]
    for i, st in enumerate(statuses):
        write_audit_entry(
            AuditEntry(
                timestamp=now + timedelta(minutes=i),
                plan_created_at=now,
                planned_action="IDLE",
                planned_soc_before=0.5,
                planned_soc_after=0.5,
                actual_soc_before=50.0 if st != "no_data" else None,
                actual_soc_after=50.0 if st != "no_data" else None,
                actual_battery_power_kw=0.0,
                planned_charge_kwh=0.0,
                planned_discharge_kwh=0.0,
                soc_delta_pct=(None if st == "no_data" else float(i)),
                status=st,
                notes=st,
            )
        )
    summary = summarize_recent_audits(10)
    assert summary["n_total"] == 5
    assert summary["n_ok"] == 2
    assert summary["n_minor_drift"] == 1
    assert summary["n_major_drift"] == 1
    assert summary["n_no_data"] == 1
    # max_drift_pct should be abs-max of drift values recorded (1,2,3)
    assert summary["max_drift_pct"] == 3.0
