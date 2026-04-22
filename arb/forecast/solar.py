"""Solar PV forecast — clear-sky model derated by cloud cover."""
from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd

from arb.scheduler.constants import INTERVAL_MIN

log = logging.getLogger(__name__)

SYSTEM_SIZE_KWP = 24.0
CLOUD_DERATING = 0.75  # 100% cloud -> 75% reduction


def forecast_solar(
    weather: pd.DataFrame,
    start: datetime,
    hours: int = 48,
    system_kwp: float = SYSTEM_SIZE_KWP,
) -> pd.DataFrame:
    """Forecast solar generation using weather forecast and simple clear-sky model.

    Args:
        weather: Open-Meteo hourly data with columns
                 [timestamp, cloud_cover_pct, shortwave_radiation_wm2, is_day].
        start: Forecast start time (UTC).
        hours: Forecast horizon.
        system_kwp: System size in kWp.

    Returns:
        DataFrame with columns [timestamp, solar_kw] at 5-min intervals.
    """
    n_intervals = int(hours * 60 / INTERVAL_MIN)
    target_ts = pd.date_range(start=start, periods=n_intervals, freq=f"{INTERVAL_MIN}min", tz="UTC")

    if weather is None or weather.empty:
        log.warning("No weather data, returning zero solar")
        return pd.DataFrame({"timestamp": target_ts, "solar_kw": 0.0})

    # Ensure timezone-aware
    wx = weather.copy()
    wx["timestamp"] = pd.to_datetime(wx["timestamp"], utc=True)
    wx = wx.set_index("timestamp").sort_index()

    # Fill missing columns with safe defaults
    for col, default in [("cloud_cover_pct", 50.0), ("shortwave_radiation_wm2", 0.0), ("is_day", 0)]:
        if col not in wx.columns:
            wx[col] = default

    # Upsample hourly to 5-min via linear interpolation
    wx_5min = wx.resample(f"{INTERVAL_MIN}min").interpolate(method="linear")
    wx_5min["is_day"] = wx_5min["is_day"].round()  # keep as 0/1 after interpolation

    # Align to target timestamps
    wx_5min = wx_5min.reindex(target_ts, method="nearest", tolerance=pd.Timedelta(minutes=10))

    # Compute solar output
    # shortwave_radiation: W/m^2. At 1000 W/m^2 (1 sun), system_kwp produces nameplate.
    clear_sky_kw = (wx_5min["shortwave_radiation_wm2"].fillna(0) / 1000.0) * system_kwp
    cloud_factor = 1.0 - (wx_5min["cloud_cover_pct"].fillna(50) / 100.0) * CLOUD_DERATING
    solar_kw = clear_sky_kw * cloud_factor

    # Clamp: no negative, no more than nameplate, zero at night
    solar_kw = solar_kw.clip(lower=0, upper=system_kwp)
    night_mask = wx_5min["is_day"].fillna(0) == 0
    solar_kw[night_mask] = 0.0

    result = pd.DataFrame({"timestamp": target_ts, "solar_kw": solar_kw.values})
    log.info("Solar forecast: %d intervals, peak %.1f kW, mean %.1f kW",
             n_intervals, result["solar_kw"].max(), result["solar_kw"].mean())
    return result
