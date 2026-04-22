"""Combine price, load, solar forecasts into a scheduler-ready DataFrame."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from arb.forecast import load as load_mod
from arb.forecast import solar as solar_mod
from arb.ingest.snapshot import Snapshot
from arb.scheduler.constants import HORIZON_H, INTERVAL_MIN

log = logging.getLogger(__name__)


def _normalize_prices(price_df: pd.DataFrame) -> pd.DataFrame:
    """Normalize price DataFrame to have import_c_kwh and export_c_kwh columns."""
    if price_df is None or price_df.empty:
        return pd.DataFrame()

    df = price_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    # Amber format has import_c_kwh / export_c_kwh
    if "import_c_kwh" in df.columns:
        out = df[["timestamp"]].copy()
        out["import_c_kwh"] = df["import_c_kwh"]
        out["export_c_kwh"] = df.get("export_c_kwh", df["import_c_kwh"])
        return out.dropna(subset=["import_c_kwh"])

    # AEMO format has rrp_c_kwh — use as both import and export
    # (on Amber pass-through they're close; export is slightly less but this is fine for Day 2)
    if "rrp_c_kwh" in df.columns:
        out = df[["timestamp"]].copy()
        out["import_c_kwh"] = df["rrp_c_kwh"]
        out["export_c_kwh"] = df["rrp_c_kwh"]
        return out

    log.warning("Price DataFrame has no recognized price columns: %s", list(df.columns))
    return pd.DataFrame()


def build_forecast(
    snapshot: Snapshot,
    ha_history: pd.DataFrame | None = None,
    horizon_h: int = HORIZON_H,
) -> pd.DataFrame:
    """Build a unified forecast DataFrame for the scheduler.

    Returns DataFrame with columns:
        timestamp, import_c_kwh, export_c_kwh, load_kw, solar_kw, net_load_kw
    at 5-min intervals from snapshot.timestamp to snapshot.timestamp + horizon_h.
    """
    start = snapshot.timestamp
    n_intervals = int(horizon_h * 60 / INTERVAL_MIN)
    target_ts = pd.date_range(start=start, periods=n_intervals, freq=f"{INTERVAL_MIN}min", tz="UTC")

    # --- Prices ---
    prices = _normalize_prices(snapshot.price_forecast)
    if prices.empty:
        log.warning("No price data, using flat 10 c/kWh")
        price_df = pd.DataFrame({
            "timestamp": target_ts,
            "import_c_kwh": 10.0,
            "export_c_kwh": 10.0,
        })
    else:
        price_df = pd.DataFrame({"timestamp": target_ts})
        prices = prices.set_index("timestamp").sort_index()
        # Reindex to target, forward-fill beyond forecast horizon
        prices_reindexed = prices.reindex(target_ts, method="ffill")
        # Also backfill in case forecast starts after our start time
        prices_reindexed = prices_reindexed.bfill()

        n_forecast = prices.index.isin(target_ts).sum()
        n_filled = n_intervals - n_forecast
        if n_filled > 0:
            log.info("Price forecast covers %d intervals, %d intervals forward-filled", n_forecast, n_filled)

        price_df["import_c_kwh"] = prices_reindexed["import_c_kwh"].values
        price_df["export_c_kwh"] = prices_reindexed["export_c_kwh"].values

        # If still NaN (no price data at all overlapped), use 10 c/kWh
        price_df = price_df.fillna(10.0)

    # --- Load ---
    load_df = load_mod.forecast_load(ha_history, start, hours=horizon_h)
    # Align to target timestamps
    load_aligned = load_df.set_index("timestamp").reindex(target_ts, method="nearest",
                                                          tolerance=pd.Timedelta(minutes=10))
    load_aligned = load_aligned.fillna(1.0)  # fallback

    # --- Solar ---
    solar_df = solar_mod.forecast_solar(snapshot.weather_forecast, start, hours=horizon_h)
    solar_aligned = solar_df.set_index("timestamp").reindex(target_ts, method="nearest",
                                                            tolerance=pd.Timedelta(minutes=10))
    solar_aligned = solar_aligned.fillna(0.0)

    # --- Combine ---
    result = pd.DataFrame({
        "timestamp": target_ts,
        "import_c_kwh": price_df["import_c_kwh"].values,
        "export_c_kwh": price_df["export_c_kwh"].values,
        "load_kw": load_aligned["load_kw"].values,
        "solar_kw": solar_aligned["solar_kw"].values,
    })
    result["net_load_kw"] = result["load_kw"] - result["solar_kw"]

    log.info(
        "Forecast built: %d intervals, price %.1f-%.1f c/kWh, load mean %.1f kW, solar mean %.1f kW",
        len(result),
        result["import_c_kwh"].min(), result["import_c_kwh"].max(),
        result["load_kw"].mean(), result["solar_kw"].mean(),
    )
    return result
