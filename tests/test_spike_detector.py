"""Tests for spike_detector."""
from __future__ import annotations

from datetime import timezone

import numpy as np
import pandas as pd
import pytest

from arb.agent.spike_detector import (
    SpikeDirection,
    SpikeEvent,
    SpikeSeverity,
    detect_spike,
    format_spike_for_log,
    spike_reason,
)
from arb.ingest.snapshot import Snapshot
from arb.scheduler.plan import Plan


def _make_plan(n=48, import_prices=None, export_prices=None, start=None):
    start = start or pd.Timestamp.now(tz="UTC").floor("5min")
    timestamps = pd.date_range(start=start, periods=n, freq="5min", tz="UTC").values
    import_c = (
        np.full(n, 10.0, dtype=float)
        if import_prices is None
        else np.asarray(import_prices, dtype=float)
    )
    export_c = (
        np.full(n, 5.0, dtype=float)
        if export_prices is None
        else np.asarray(export_prices, dtype=float)
    )
    return Plan.from_self_consume(
        timestamps=timestamps,
        import_c_kwh=import_c,
        export_c_kwh=export_c,
        load_kw=np.zeros(n),
        solar_kw=np.zeros(n),
        soc_now=0.5,
    )


def _make_snapshot(timestamps, import_c, export_c, now=None):
    now = now or pd.Timestamp.now(tz="UTC").floor("5min").to_pydatetime()
    df = pd.DataFrame({
        "timestamp": timestamps,
        "import_c_kwh": import_c,
        "export_c_kwh": export_c,
        "price_type": ["ActualInterval"] * len(timestamps),
    })
    return Snapshot(
        timestamp=now,
        soc_pct=50.0,
        load_kw=1.0,
        solar_kw=0.0,
        battery_power_kw=0.0,
        price_forecast=df,
        weather_forecast=pd.DataFrame(),
    )


def test_no_previous_plan_returns_none():
    start = pd.Timestamp.now(tz="UTC").floor("5min")
    ts = pd.date_range(start=start, periods=24, freq="5min", tz="UTC")
    snap = _make_snapshot(ts, [10.0] * 24, [5.0] * 24, now=start.to_pydatetime())
    assert detect_spike(snap, None) is None


def test_no_spike_when_prices_match():
    start = pd.Timestamp.now(tz="UTC").floor("5min")
    plan = _make_plan(n=24, start=start)
    ts = pd.date_range(start=start, periods=24, freq="5min", tz="UTC")
    snap = _make_snapshot(ts, [10.0] * 24, [5.0] * 24, now=start.to_pydatetime())
    assert detect_spike(snap, plan) is None


def test_detects_import_spike_up():
    start = pd.Timestamp.now(tz="UTC").floor("5min")
    plan = _make_plan(n=24, start=start)  # import=10, export=5
    # spike import price at interval 3 (15 min out) to 50 c/kWh
    actual_import = [10.0] * 24
    actual_import[3] = 50.0
    ts = pd.date_range(start=start, periods=24, freq="5min", tz="UTC")
    snap = _make_snapshot(ts, actual_import, [5.0] * 24, now=start.to_pydatetime())

    event = detect_spike(snap, plan)
    assert event is not None
    assert event.direction == SpikeDirection.UP
    assert event.price_type == "import"
    assert event.planned_price_c_kwh == pytest.approx(10.0)
    assert event.actual_price_c_kwh == pytest.approx(50.0)
    assert event.magnitude_c_kwh == pytest.approx(40.0)


def test_detects_export_crash():
    start = pd.Timestamp.now(tz="UTC").floor("5min")
    plan = _make_plan(n=24, start=start)  # export=5
    actual_export = [5.0] * 24
    actual_export[4] = -10.0  # negative export
    ts = pd.date_range(start=start, periods=24, freq="5min", tz="UTC")
    snap = _make_snapshot(ts, [10.0] * 24, actual_export, now=start.to_pydatetime())

    event = detect_spike(snap, plan)
    assert event is not None
    assert event.direction == SpikeDirection.DOWN
    assert event.price_type == "export"
    assert event.actual_price_c_kwh == pytest.approx(-10.0)


def test_cap_event_severity():
    start = pd.Timestamp.now(tz="UTC").floor("5min")
    plan = _make_plan(n=24, start=start)
    actual_import = [10.0] * 24
    actual_import[2] = 180.0  # blow past cap_threshold=100
    ts = pd.date_range(start=start, periods=24, freq="5min", tz="UTC")
    snap = _make_snapshot(ts, actual_import, [5.0] * 24, now=start.to_pydatetime())

    event = detect_spike(snap, plan)
    assert event is not None
    assert event.severity == SpikeSeverity.CAP


def test_respects_lookahead_window():
    start = pd.Timestamp.now(tz="UTC").floor("5min")
    # 48 intervals = 4 hours. Put spike at interval 40 (3h20m out) — beyond 2h window.
    plan = _make_plan(n=48, start=start)
    actual_import = [10.0] * 48
    actual_import[40] = 60.0
    ts = pd.date_range(start=start, periods=48, freq="5min", tz="UTC")
    snap = _make_snapshot(ts, actual_import, [5.0] * 48, now=start.to_pydatetime())

    event = detect_spike(snap, plan, lookahead_minutes=120)
    assert event is None


def test_prefers_most_severe_when_multiple():
    start = pd.Timestamp.now(tz="UTC").floor("5min")
    plan = _make_plan(n=24, start=start)
    actual_import = [10.0] * 24
    actual_import[2] = 30.0  # delta 20
    actual_import[5] = 70.0  # delta 60 — bigger
    ts = pd.date_range(start=start, periods=24, freq="5min", tz="UTC")
    snap = _make_snapshot(ts, actual_import, [5.0] * 24, now=start.to_pydatetime())

    event = detect_spike(snap, plan)
    assert event is not None
    assert event.magnitude_c_kwh == pytest.approx(60.0)
    assert event.actual_price_c_kwh == pytest.approx(70.0)


def test_small_absolute_delta_filtered():
    start = pd.Timestamp.now(tz="UTC").floor("5min")
    # planned 1.0, actual 1.5 -> 50% relative deviation, only 0.5 c absolute.
    plan = _make_plan(
        n=24,
        import_prices=[1.0] * 24,
        export_prices=[0.5] * 24,
        start=start,
    )
    actual_import = [1.0] * 24
    actual_import[3] = 1.5
    ts = pd.date_range(start=start, periods=24, freq="5min", tz="UTC")
    snap = _make_snapshot(ts, actual_import, [0.5] * 24, now=start.to_pydatetime())

    event = detect_spike(snap, plan, min_absolute_c_kwh=5.0)
    assert event is None


def test_format_spike_for_log_one_line():
    from datetime import datetime

    event = SpikeEvent(
        detected_at=datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc),
        interval_ts=datetime(2026, 4, 24, 14, 30, tzinfo=timezone.utc),
        planned_price_c_kwh=12.3,
        actual_price_c_kwh=48.1,
        direction=SpikeDirection.UP,
        severity=SpikeSeverity.MAJOR,
        magnitude_c_kwh=35.8,
        price_type="import",
        reason="test",
    )
    line = format_spike_for_log(event)
    assert "\n" not in line
    assert "SPIKE" in line
    assert "major" in line
    assert "up" in line
    assert "import" in line
    assert "12.3" in line
    assert "48.1" in line


def test_spike_reason_mentions_direction_and_price():
    from datetime import datetime

    event = SpikeEvent(
        detected_at=datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc),
        interval_ts=datetime(2026, 4, 24, 14, 30, tzinfo=timezone.utc),
        planned_price_c_kwh=12.0,
        actual_price_c_kwh=47.0,
        direction=SpikeDirection.UP,
        severity=SpikeSeverity.MAJOR,
        magnitude_c_kwh=35.0,
        price_type="import",
        reason="",
    )
    reason = spike_reason(event)
    assert isinstance(reason, str)
    lowered = reason.lower()
    # direction word or synonym (up/peak/incoming for UP)
    assert any(w in lowered for w in ("up", "peak", "incoming"))
    # a price number — either the delta or the actual price should appear
    assert any(num in reason for num in ("35", "47"))
