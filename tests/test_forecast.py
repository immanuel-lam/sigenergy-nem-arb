"""Tests for load and solar forecasters."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from arb.forecast.load import forecast_load
from arb.forecast.solar import forecast_solar
from arb.scheduler.constants import INTERVAL_MIN


def test_load_empty_history_returns_flat():
    start = datetime(2026, 4, 23, 0, 0, tzinfo=timezone.utc)
    result = forecast_load(pd.DataFrame(), start, hours=24)
    assert len(result) == 24 * 60 // INTERVAL_MIN
    assert "load_kw" in result.columns


def test_load_correct_shape():
    start = datetime(2026, 4, 23, 0, 0, tzinfo=timezone.utc)
    # Create some fake history
    history_ts = pd.date_range(
        start - timedelta(days=14), periods=14 * 24 * 12, freq="5min", tz="UTC"
    )
    history = pd.DataFrame({
        "timestamp": history_ts,
        "load_kw": np.random.uniform(0.5, 5.0, len(history_ts)),
    })

    result = forecast_load(history, start, hours=24)
    assert len(result) == 288  # 24h * 12 intervals/h


def test_load_none_history():
    start = datetime(2026, 4, 23, 0, 0, tzinfo=timezone.utc)
    result = forecast_load(None, start, hours=24)
    assert len(result) == 288
    assert result["load_kw"].iloc[0] == 1.0  # fallback


def test_solar_nighttime_is_zero():
    start = datetime(2026, 4, 22, 10, 0, tzinfo=timezone.utc)  # 10pm AEST = night
    weather = pd.DataFrame({
        "timestamp": pd.date_range(start, periods=12, freq="1h", tz="UTC"),
        "cloud_cover_pct": 0.0,
        "shortwave_radiation_wm2": 0.0,
        "is_day": 0,
    })
    result = forecast_solar(weather, start, hours=12)
    assert result["solar_kw"].max() == 0.0


def test_solar_clear_sky_proportional():
    start = datetime(2026, 4, 23, 0, 0, tzinfo=timezone.utc)  # 10am AEST = daytime
    weather = pd.DataFrame({
        "timestamp": pd.date_range(start, periods=6, freq="1h", tz="UTC"),
        "cloud_cover_pct": 0.0,
        "shortwave_radiation_wm2": 1000.0,  # 1 sun
        "is_day": 1,
    })
    result = forecast_solar(weather, start, hours=6, system_kwp=24.0)
    # At 1000 W/m2, 0% cloud, system should produce ~24 kW
    assert result["solar_kw"].max() == pytest.approx(24.0, abs=0.5)


def test_solar_cloud_derating():
    start = datetime(2026, 4, 23, 0, 0, tzinfo=timezone.utc)
    weather = pd.DataFrame({
        "timestamp": pd.date_range(start, periods=6, freq="1h", tz="UTC"),
        "cloud_cover_pct": 100.0,
        "shortwave_radiation_wm2": 1000.0,
        "is_day": 1,
    })
    result = forecast_solar(weather, start, hours=6, system_kwp=24.0)
    # 100% cloud * 0.75 derating = 25% output = 6 kW
    assert result["solar_kw"].max() == pytest.approx(6.0, abs=0.5)


def test_solar_correct_shape():
    start = datetime(2026, 4, 23, 0, 0, tzinfo=timezone.utc)
    weather = pd.DataFrame({
        "timestamp": pd.date_range(start, periods=48, freq="1h", tz="UTC"),
        "cloud_cover_pct": 50.0,
        "shortwave_radiation_wm2": 500.0,
        "is_day": 1,
    })
    result = forecast_solar(weather, start, hours=24)
    assert len(result) == 288
