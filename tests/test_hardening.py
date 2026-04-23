"""Hardening tests — edge cases that must not crash the demo.

Covers the failure modes listed in the audit mandate. Each test is
single-assertion where practical. Network calls are monkeypatched; no real
HA/AEMO/Amber traffic.
"""
from __future__ import annotations

import io
import json
import os
import pickle
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mk_hist(n: int = 48) -> pd.DataFrame:
    ts = pd.date_range("2026-04-20", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({
        "timestamp": ts,
        "load_kw": [1.5] * n,
        "solar_kw": [0.5] * n,
        "soc_pct": [55.0] * n,
        "battery_power_kw": [0.0] * n,
    })


def _mk_prices(n: int = 48, value: float = 12.0) -> pd.DataFrame:
    ts = pd.date_range("2026-04-20", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({
        "timestamp": ts,
        "import_c_kwh": [value] * n,
        "export_c_kwh": [value] * n,
    })


def _mk_forecast(n: int = 12, import_c: float = 12.0, export_c: float = 12.0) -> pd.DataFrame:
    ts = pd.date_range("2026-04-20", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({
        "timestamp": ts,
        "import_c_kwh": [import_c] * n,
        "export_c_kwh": [export_c] * n,
        "load_kw": [1.0] * n,
        "solar_kw": [0.0] * n,
    })


# ---------------------------------------------------------------------------
# Ingest — HA
# ---------------------------------------------------------------------------


def test_ha_get_current_state_no_url_env(monkeypatch):
    """get_current_state when HA network fails must return dict with Nones, not crash.

    We monkeypatch requests.get to raise ConnectionError (dotenv may have set HA_URL
    but the host is unreachable in test environments).
    """
    import requests
    from arb.ingest import ha

    with patch("requests.get", side_effect=requests.ConnectionError("unreachable")):
        result = ha.get_current_state()

    assert isinstance(result, dict)
    assert len(result) == 4  # load_kw, solar_kw, soc_pct, battery_power_kw
    assert all(v is None for v in result.values())


def test_ha_get_current_state_401(monkeypatch):
    """get_current_state with HA returning 401 must return Nones, not raise."""
    monkeypatch.setenv("HA_URL", "http://fake.local:8123")
    monkeypatch.setenv("HA_TOKEN", "bad-token")

    import requests
    from arb.ingest import ha

    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.HTTPError("401 Unauthorized")

    with patch("requests.get", return_value=mock_resp):
        result = ha.get_current_state()

    assert isinstance(result, dict)
    assert all(v is None for v in result.values())


def test_ha_fetch_history_one_sensor_missing(monkeypatch):
    """fetch_history where one entity returns no data — other sensors still populate."""
    from arb.ingest import ha

    # Use the actual sensor IDs from _sensor_ids() so the entity_map lookup works.
    sensors = ha._sensor_ids()
    ts_str = "2026-04-20T00:00:00+00:00"
    fake_response = [
        # solar sensor — has data
        [{"entity_id": sensors["solar"], "state": "2.5", "last_changed": ts_str}],
        # SOC sensor — has data
        [{"entity_id": sensors["soc"], "state": "60", "last_changed": ts_str}],
        # load and battery_power — empty lists (entity returned no states)
        [],
        [],
    ]
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = fake_response

    with patch("requests.get", return_value=mock_resp):
        df = ha.fetch_history(days=1)

    assert not df.empty
    # At least solar_kw column should exist (got data for it)
    assert "solar_kw" in df.columns


# ---------------------------------------------------------------------------
# Ingest — AEMO
# ---------------------------------------------------------------------------


def test_aemo_fetch_5mpd_empty_zip(monkeypatch):
    """fetch_5mpd_forecast with an empty (no CSV) zip must return empty df, not crash."""
    from arb.ingest import aemo

    # Build a zip with no CSV files
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("README.txt", "not a csv")
    buf.seek(0)

    mock_index_resp = MagicMock()
    mock_index_resp.raise_for_status.return_value = None
    mock_index_resp.text = '<A HREF="/Reports/Current/P5_Reports/fake.zip">fake.zip</A>'

    mock_zip_resp = MagicMock()
    mock_zip_resp.raise_for_status.return_value = None
    mock_zip_resp.content = buf.read()

    with patch("requests.get", side_effect=[mock_index_resp, mock_zip_resp]):
        result = aemo.fetch_5mpd_forecast()

    assert result.empty


def test_aemo_parse_5mpd_csv_missing_regionsolution():
    """_parse_5mpd_csv with CSV that has no REGIONSOLUTION table returns empty df."""
    from arb.ingest.aemo import _parse_5mpd_csv
    bad_csv = "I,DISPATCH,PRICE,1,SETTLEMENTDATE,REGIONID,RRP\n"
    result = _parse_5mpd_csv(bad_csv)
    assert result.empty


def test_aemo_parse_5mpd_csv_unicode_garbage():
    """_parse_5mpd_csv with unparseable date in INTERVAL_DATETIME doesn't raise."""
    from arb.ingest.aemo import _parse_5mpd_csv
    # Valid CSV structure but with garbage where INTERVAL_DATETIME should be
    garbage = (
        "I,P5MIN,REGIONSOLUTION,1,INTERVAL_DATETIME,REGIONID,RRP,INTERVENTION\n"
        "D,P5MIN,REGIONSOLUTION,1,NOT_A_DATE,NSW1,100,0\n"
    )
    # Should not raise — bad date → dropna removes the row → returns empty or partial df
    result = _parse_5mpd_csv(garbage)
    assert isinstance(result, pd.DataFrame)


def test_aemo_parse_5mpd_csv_wrong_column_order():
    """_parse_5mpd_csv with wrong column mapping returns empty (no valid rows)."""
    from arb.ingest.aemo import _parse_5mpd_csv
    # Valid header format but columns swapped: RRP where INTERVAL_DATETIME should be
    csv = (
        "I,P5MIN,REGIONSOLUTION,1,RRP,REGIONID,INTERVAL_DATETIME,INTERVENTION\n"
        "D,P5MIN,REGIONSOLUTION,1,100,NSW1,2026-04-20 10:00:00,0\n"
    )
    # Parser looks for INTERVAL_DATETIME column by name, so this should fail gracefully
    # (no exception, may be empty or have parse errors)
    result = _parse_5mpd_csv(csv)
    assert isinstance(result, pd.DataFrame)


# ---------------------------------------------------------------------------
# Ingest — Amber
# ---------------------------------------------------------------------------


def test_amber_fetch_prices_no_api_key(monkeypatch):
    """fetch_prices with no AMBER_API_KEY returns empty df."""
    from arb.ingest import amber
    # Patch _api_key() directly since dotenv may have loaded the key already
    with patch.object(amber, "_api_key", return_value=None):
        result = amber.fetch_prices()
    assert result.empty


def test_amber_fetch_historical_422(monkeypatch):
    """fetch_historical_prices with 422 response must not raise uncaught.

    Note: the current implementation does not catch HTTPError from the prices
    endpoint — it propagates. This test documents that the 422 *is* raised, so
    callers (like run_backtest.py main()) must handle it. If we ever add
    graceful handling, update this test to assert result.empty instead.
    """
    import requests
    from arb.ingest import amber

    site_resp = MagicMock()
    site_resp.raise_for_status.return_value = None
    site_resp.json.return_value = [{"id": "site-123"}]

    price_resp = MagicMock()
    price_resp.raise_for_status.side_effect = requests.HTTPError(
        response=MagicMock(status_code=422)
    )

    with patch.object(amber, "_api_key", return_value="fake-key"), \
         patch("requests.get", side_effect=[site_resp, price_resp]):
        # Document current behavior: HTTPError propagates. This is a known gap —
        # callers should wrap in try/except. Test ensures no silent data corruption.
        with pytest.raises(requests.HTTPError):
            amber.fetch_historical_prices(days=1)


# ---------------------------------------------------------------------------
# Ingest — Snapshot
# ---------------------------------------------------------------------------


def test_take_snapshot_all_sources_fail(monkeypatch):
    """take_snapshot when every source fails still returns a Snapshot with stale flags."""
    import requests
    from arb.ingest import snapshot as snap_mod

    with patch("arb.ingest.ha.get_current_state", side_effect=Exception("HA down")), \
         patch("arb.ingest.amber.fetch_prices", side_effect=Exception("Amber down")), \
         patch("arb.ingest.aemo.fetch_5mpd_forecast", side_effect=Exception("AEMO down")), \
         patch("arb.ingest.bom.fetch_weather_forecast", side_effect=Exception("BOM down")):
        snap = snap_mod.take_snapshot()

    assert snap is not None
    assert snap.soc_pct is None
    assert len(snap.stale_sensors) > 0
    assert snap.price_forecast.empty


# ---------------------------------------------------------------------------
# Forecast layer
# ---------------------------------------------------------------------------


def test_build_forecast_empty_price_forecast(monkeypatch):
    """build_forecast with empty price_forecast falls back to flat 10 c/kWh."""
    from arb.forecast.builder import build_forecast
    from arb.ingest.snapshot import Snapshot

    snap = Snapshot(
        timestamp=datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc),
        soc_pct=50.0,
        load_kw=1.5,
        solar_kw=0.5,
        battery_power_kw=0.0,
        price_forecast=pd.DataFrame(),
        weather_forecast=pd.DataFrame(),
    )

    with patch("arb.ingest.ha.fetch_history", return_value=pd.DataFrame()):
        df = build_forecast(snap, ha_history=None, horizon_h=1)

    assert not df.empty
    assert (df["import_c_kwh"] == 10.0).all()


def test_build_forecast_ha_history_none(monkeypatch):
    """build_forecast with ha_history=None must not crash."""
    from arb.forecast.builder import build_forecast
    from arb.ingest.snapshot import Snapshot

    snap = Snapshot(
        timestamp=datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc),
        soc_pct=50.0,
        load_kw=1.5,
        solar_kw=0.5,
        battery_power_kw=0.0,
        price_forecast=_mk_prices(12),
        weather_forecast=pd.DataFrame(),
    )

    df = build_forecast(snap, ha_history=None, horizon_h=1)
    assert not df.empty
    assert "load_kw" in df.columns


def test_forecast_load_all_nan():
    """forecast_load with all-NaN load_kw falls back to FALLBACK_LOAD_KW."""
    from arb.forecast.load import FALLBACK_LOAD_KW, forecast_load

    ts = pd.date_range("2026-04-01", periods=50, freq="5min", tz="UTC")
    hist = pd.DataFrame({
        "timestamp": ts,
        "load_kw": [np.nan] * 50,
    })
    start = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
    df = forecast_load(hist, start, hours=1)
    assert not df.empty
    # Should fall back to flat value since all history is NaN
    assert (df["load_kw"] == FALLBACK_LOAD_KW).all()


def test_forecast_solar_nan_shortwave():
    """forecast_solar with NaN in shortwave_radiation must not crash."""
    from arb.forecast.solar import forecast_solar

    ts = pd.date_range("2026-04-20", periods=24, freq="h", tz="UTC")
    weather = pd.DataFrame({
        "timestamp": ts,
        "cloud_cover_pct": [30.0] * 24,
        "shortwave_radiation_wm2": [np.nan] * 24,
        "is_day": [1] * 12 + [0] * 12,
    })
    start = datetime(2026, 4, 20, 6, 0, tzinfo=timezone.utc)
    df = forecast_solar(weather, start, hours=6)
    assert not df.empty
    assert not df["solar_kw"].isna().any()


# ---------------------------------------------------------------------------
# Scheduler — greedy
# ---------------------------------------------------------------------------


def test_greedy_all_zero_prices():
    """schedule() with all-zero prices — no arbitrage, returns IDLE plan."""
    from arb.scheduler.greedy import schedule
    from arb.scheduler.plan import Action

    fc = _mk_forecast(n=12, import_c=0.0, export_c=0.0)
    plan = schedule(fc, soc_now=0.5)
    assert plan.n == 12
    # No profitable pairs at zero prices — should be all IDLE
    assert all(a == Action.IDLE for a in plan.actions)


def test_greedy_soc_at_ceiling():
    """schedule() with soc_now at ceiling — SOC must never exceed ceiling."""
    from arb.scheduler.greedy import schedule
    from arb.scheduler.constants import BatteryConstants

    bc = BatteryConstants()
    # Set load > solar so self-consume discharge drops SOC, potentially creating
    # charge headroom. The key invariant is SOC never exceeds ceiling.
    fc = _mk_forecast(n=12, import_c=5.0, export_c=50.0)
    plan = schedule(fc, soc_now=bc.soc_ceiling)

    # SOC must stay at or below ceiling throughout
    assert plan.soc.max() <= bc.soc_ceiling + 1e-9


def test_greedy_soc_at_floor():
    """schedule() with soc_now at floor — must not discharge."""
    from arb.scheduler.greedy import schedule
    from arb.scheduler.constants import BatteryConstants

    bc = BatteryConstants()
    fc = _mk_forecast(n=12, import_c=5.0, export_c=50.0)
    plan = schedule(fc, soc_now=bc.soc_floor)

    # No discharge when at floor
    assert plan.discharge_grid_kwh.sum() == 0.0


def test_greedy_soc_above_ceiling_clamped():
    """schedule() with soc_now=0.99 (above ceiling) — must clamp, not crash."""
    from arb.scheduler.greedy import schedule

    fc = _mk_forecast(n=12)
    plan = schedule(fc, soc_now=0.99)
    assert plan is not None
    # SOC must stay within bounds
    assert plan.soc.max() <= 1.01  # small float tolerance


def test_greedy_soc_below_floor_clamped():
    """schedule() with soc_now=0.05 (below floor) — must not crash; no discharge added."""
    from arb.scheduler.greedy import schedule
    from arb.scheduler.constants import BatteryConstants

    bc = BatteryConstants()
    fc = _mk_forecast(n=12, import_c=5.0, export_c=50.0)
    plan = schedule(fc, soc_now=0.05)
    assert plan is not None
    # Greedy must not add grid discharge when SOC is below floor
    # (self-consume discharge may still drop SOC to serve load, but the
    # greedy layer adds no additional discharge_grid_kwh)
    assert plan.discharge_grid_kwh.sum() == 0.0


def test_greedy_single_interval():
    """schedule() with forecast of length 1 — minimum viable input."""
    from arb.scheduler.greedy import schedule

    fc = _mk_forecast(n=1, import_c=5.0, export_c=20.0)
    plan = schedule(fc, soc_now=0.5)
    assert plan.n == 1


def test_plan_from_self_consume_all_nan():
    """Plan.from_self_consume with all-NaN load/solar — must produce valid plan."""
    from arb.scheduler.plan import Plan

    ts = pd.date_range("2026-04-20", periods=5, freq="5min", tz="UTC").values
    nan_arr = np.full(5, np.nan)
    zeros = np.zeros(5)

    # NaN load/solar — numpy ops produce NaN SOC; from_self_consume should survive
    # (it uses numpy arithmetic directly). Clamp ensures no crash.
    try:
        plan = Plan.from_self_consume(
            timestamps=ts,
            import_c_kwh=np.full(5, 10.0),
            export_c_kwh=np.full(5, 10.0),
            load_kw=nan_arr,
            solar_kw=nan_arr,
            soc_now=0.5,
        )
        # If it returns without crashing, SOC array length is correct
        assert len(plan.soc) == 6
    except Exception as exc:
        pytest.fail(f"from_self_consume raised with all-NaN: {exc}")


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


def test_run_once_kill_switch(monkeypatch, tmp_path):
    """run_once with KILL_SWITCH active returns immediately, calls no side effects."""
    monkeypatch.setenv("ARB_KILL", "1")
    monkeypatch.setenv("ARB_RATIONALE_LOG", str(tmp_path / "rationale.log"))
    monkeypatch.setenv("ARB_PREVIOUS_PLAN", str(tmp_path / "plan.pkl"))
    monkeypatch.setenv("ARB_PREVIOUS_SOC", str(tmp_path / "soc.txt"))

    # Reload the module so KILL_SWITCH re-reads the env
    import importlib
    import arb.agent.loop as loop_mod
    importlib.reload(loop_mod)

    called = []
    with patch("arb.ingest.snapshot.take_snapshot", side_effect=lambda: called.append("snap")):
        loop_mod.run_once(dry_run=True)

    # take_snapshot should NOT have been called
    assert "snap" not in called

    # Clean up reload side effect
    importlib.reload(loop_mod)
    monkeypatch.delenv("ARB_KILL", raising=False)


def test_run_once_corrupted_pickle(monkeypatch, tmp_path):
    """run_once with corrupted .previous_plan.pkl must load as None, not crash."""
    pkl_path = tmp_path / "plan.pkl"
    pkl_path.write_bytes(b"garbage bytes not valid pickle")

    monkeypatch.setenv("ARB_PREVIOUS_PLAN", str(pkl_path))
    monkeypatch.setenv("ARB_KILL", "0")

    import arb.agent.loop as loop_mod
    result = loop_mod._load_previous_plan()
    assert result is None


def test_run_once_soc_none_no_force(monkeypatch, tmp_path):
    """run_once with snapshot.soc_pct=None and no --force must skip cleanly."""
    monkeypatch.setenv("ARB_KILL", "0")
    monkeypatch.setenv("ARB_PREVIOUS_PLAN", str(tmp_path / "plan.pkl"))
    monkeypatch.setenv("ARB_PREVIOUS_SOC", str(tmp_path / "soc.txt"))
    monkeypatch.setenv("ARB_RATIONALE_LOG", str(tmp_path / "rationale.log"))

    from arb.ingest.snapshot import Snapshot

    stale_snap = Snapshot(
        timestamp=datetime.now(timezone.utc),
        soc_pct=None,  # SOC unknown
        load_kw=None,
        solar_kw=None,
        battery_power_kw=None,
        price_forecast=pd.DataFrame(),
        weather_forecast=pd.DataFrame(),
        stale_sensors=["soc_pct"],
    )

    schedule_called = []
    import arb.agent.loop as loop_mod

    with patch("arb.ingest.snapshot.take_snapshot", return_value=stale_snap), \
         patch("arb.scheduler.greedy.schedule", side_effect=lambda *a, **k: schedule_called.append(1)):
        loop_mod.run_once(dry_run=True, force=False)

    # Scheduler must not have been called — we skipped the cycle
    assert len(schedule_called) == 0


# ---------------------------------------------------------------------------
# Agent — spike_detector
# ---------------------------------------------------------------------------


def test_detect_spike_plan_current_interval_idx_none():
    """detect_spike with plan.current_interval_idx=None must not crash."""
    from arb.agent.spike_detector import detect_spike
    from arb.scheduler.plan import Plan

    fc = _mk_forecast(n=6)
    plan = Plan.from_self_consume(
        timestamps=fc["timestamp"].values,
        import_c_kwh=fc["import_c_kwh"].values.astype(float),
        export_c_kwh=fc["export_c_kwh"].values.astype(float),
        load_kw=fc["load_kw"].values.astype(float),
        solar_kw=fc["solar_kw"].values.astype(float),
        soc_now=0.5,
    )

    # Snapshot with price_forecast matching the plan timestamps (so spike check runs)
    from arb.ingest.snapshot import Snapshot
    snap = Snapshot(
        timestamp=datetime.now(timezone.utc),
        soc_pct=50.0,
        load_kw=1.0,
        solar_kw=0.5,
        battery_power_kw=0.0,
        price_forecast=_mk_prices(12, value=200.0),  # big spike vs plan's 12c
        weather_forecast=pd.DataFrame(),
    )

    # Should not crash regardless of current_interval_idx
    result = detect_spike(snap, plan)
    # result is either None or a SpikeEvent — both are fine
    assert result is None or hasattr(result, "magnitude_c_kwh")


# ---------------------------------------------------------------------------
# Agent — audit
# ---------------------------------------------------------------------------


def test_audit_prior_soc_none():
    """audit_current_interval with prior_soc_pct=None must return status='no_data'."""
    from arb.agent.audit import audit_current_interval
    from arb.scheduler.plan import Plan

    fc = _mk_forecast(n=6)
    # Pin timestamps to now so current_interval_idx finds interval 0
    now = pd.Timestamp.now(tz="UTC")
    ts = pd.date_range(start=now, periods=6, freq="5min", tz="UTC")
    fc["timestamp"] = ts

    plan = Plan.from_self_consume(
        timestamps=fc["timestamp"].values,
        import_c_kwh=fc["import_c_kwh"].values.astype(float),
        export_c_kwh=fc["export_c_kwh"].values.astype(float),
        load_kw=fc["load_kw"].values.astype(float),
        solar_kw=fc["solar_kw"].values.astype(float),
        soc_now=0.5,
    )

    ha_state = {"soc_pct": 55.0, "load_kw": 1.0, "solar_kw": 0.5, "battery_power_kw": 0.0}

    with patch("arb.agent.audit.write_audit_entry"):  # don't write to disk
        entry = audit_current_interval(plan, ha_state, prior_soc_pct=None)

    assert entry.status == "no_data"


# ---------------------------------------------------------------------------
# Actuator — ha_control
# ---------------------------------------------------------------------------


def test_actuator_soc_below_floor_discharge_refused():
    """apply_action with SOC 9.5% requesting DISCHARGE_GRID must return False."""
    from arb.actuator.ha_control import apply_action
    from arb.scheduler.plan import Action

    result = apply_action(
        action=Action.DISCHARGE_GRID,
        discharge_kw=10.0,
        soc_pct=9.5,
        reason="test",
    )
    assert result is False


def test_actuator_soc_below_floor_charge_allowed():
    """apply_action with SOC 9.5% requesting CHARGE_GRID must be allowed (charging is fine)."""
    from arb.actuator.ha_control import apply_action
    from arb.scheduler.plan import Action

    # DRY_RUN is True by default in tests — so set_ems_mode won't touch hardware.
    result = apply_action(
        action=Action.CHARGE_GRID,
        charge_kw=10.0,
        soc_pct=9.5,
        reason="test",
    )
    assert result is True


def test_actuator_soc_above_ceiling_charge_refused():
    """apply_action with SOC 95.5% requesting CHARGE_GRID must return False."""
    from arb.actuator.ha_control import apply_action
    from arb.scheduler.plan import Action

    result = apply_action(
        action=Action.CHARGE_GRID,
        charge_kw=10.0,
        soc_pct=95.5,
        reason="test",
    )
    assert result is False


def test_actuator_rate_limiter_returns_false(monkeypatch):
    """set_ems_mode with rate limiter hit must return False without writing."""
    import arb.actuator.ha_control as ha_ctrl
    monkeypatch.setattr(ha_ctrl, "DRY_RUN", False)
    monkeypatch.setattr(ha_ctrl, "KILL_SWITCH", False)
    # Fill the write timestamps to hit the limit
    from datetime import timezone
    now = datetime.now(timezone.utc)
    ha_ctrl._write_timestamps.clear()
    ha_ctrl._write_timestamps.extend([now] * ha_ctrl.MAX_WRITES_PER_HOUR)

    result = ha_ctrl.set_ems_mode("Maximum Self Consumption", "test")
    assert result is False

    # Clean up
    ha_ctrl._write_timestamps.clear()
    monkeypatch.setattr(ha_ctrl, "DRY_RUN", True)


# ---------------------------------------------------------------------------
# Eval — backtest
# ---------------------------------------------------------------------------


def test_run_backtest_empty_history():
    """run_backtest with empty history must produce BacktestResult, not crash."""
    from arb.eval.backtest import idle_strategy, run_backtest

    start = datetime(2026, 4, 20, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 20, 1, 0, tzinfo=timezone.utc)

    result = run_backtest(
        history=pd.DataFrame(),
        prices=_mk_prices(n=12),
        start=start,
        end=end,
        strategy_fn=idle_strategy,
        strategy_name="idle",
    )
    assert result is not None
    assert result.strategy_name == "idle"
    # No crash — cost may be 0 since no load/solar data
    assert isinstance(result.total_cost_dollars, float)


def test_amber_replay_compute_cost_all_nan_solar():
    """compute_amber_cost with solar_kw column all NaN must not crash."""
    from arb.eval.amber_replay import compute_amber_cost

    ts = pd.date_range("2026-04-20", periods=10, freq="5min", tz="UTC")
    hist = pd.DataFrame({
        "timestamp": ts,
        "load_kw": [1.5] * 10,
        "solar_kw": [np.nan] * 10,
        "soc_pct": [55.0] * 10,
        "battery_power_kw": [0.0] * 10,
    })
    result = compute_amber_cost(hist, _mk_prices(10))
    assert isinstance(result["total_cost_dollars"], float)
    assert result["total_import_kwh"] >= 0.0


def test_offline_dryrun_hours_1(monkeypatch, tmp_path):
    """run_offline_dryrun with hours=1 must not crash (minimum meaningful run)."""
    from arb.eval.offline_dryrun import run_offline_dryrun

    hist = _mk_hist(n=288)  # 24h of history
    hist["timestamp"] = pd.date_range(
        end=datetime.now(timezone.utc), periods=288, freq="5min", tz="UTC"
    )
    prices = _mk_prices(n=288, value=15.0)
    prices["timestamp"] = hist["timestamp"]
    prices["rrp_c_kwh"] = prices["import_c_kwh"]

    with patch("arb.ingest.ha.fetch_history", return_value=hist), \
         patch("arb.ingest.amber.fetch_historical_prices", return_value=prices), \
         patch("arb.ingest.amber.fetch_prices", return_value=prices), \
         patch("arb.ingest.bom.fetch_weather_forecast", return_value=pd.DataFrame()):
        summary = run_offline_dryrun(
            hours=1,
            rationale_log_path=str(tmp_path / "rationale.log"),
            plan_log_path=str(tmp_path / "plans.jsonl"),
            skip_llm=True,
        )

    assert "n_decisions" in summary
    assert summary["n_decisions"] >= 1


# ---------------------------------------------------------------------------
# API — server
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    from arb.api.server import app
    return TestClient(app, raise_server_exceptions=False)


def test_api_snapshot_returns_json_on_failure(client):
    """GET /snapshot when take_snapshot raises must return JSON (not HTML), status 200."""
    with patch("arb.api.server.take_snapshot", side_effect=RuntimeError("exploded")):
        resp = client.get("/snapshot")

    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data or "stale_sensors" in data


def test_api_spike_demo_invalid_channel(client):
    """POST /spike-demo with channel='bad' must return 400."""
    resp = client.post("/spike-demo", json={"channel": "bad"})
    assert resp.status_code == 400


def test_api_spike_demo_missing_required_fields_uses_defaults(client):
    """POST /spike-demo with empty body — all fields have defaults, should not 422."""
    with patch("arb.api.server.run_spike_demo", side_effect=RuntimeError("spike fail")):
        resp = client.post("/spike-demo", json={})
    # 422 means pydantic validation failed — that's a bug; 503 is acceptable (spike fail)
    assert resp.status_code != 422


def test_api_backtest_latest_run_fails_returns_503(client):
    """GET /backtest/latest when run_backtest raises must return 503, not crash."""
    # Clear cache so it tries to run
    import arb.api.server as server_mod
    server_mod._BACKTEST_CACHE["data"] = None
    server_mod._BACKTEST_CACHE["computed_at"] = None

    with patch("arb.api.server._run_backtest_7d", side_effect=RuntimeError("bt fail")):
        resp = client.get("/backtest/latest?refresh=true")

    assert resp.status_code == 503
    data = resp.json()
    assert "detail" in data


def test_api_websocket_disconnect_mid_send(client):
    """WebSocket disconnect must not leave server in bad state (other /health still works)."""
    with client.websocket_connect("/ws") as ws:
        # Receive the immediate tick
        msg = ws.receive_json()
        assert msg["type"] == "tick"
        # Disconnect abruptly
        ws.close()

    # Server should still respond to normal requests
    resp = client.get("/health")
    assert resp.status_code == 200


def test_api_health(client):
    """Basic sanity: GET /health returns ok=True."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
