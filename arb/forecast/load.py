"""Household load forecast — day-of-week rolling average from HA history."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from arb.scheduler.constants import INTERVAL_MIN

log = logging.getLogger(__name__)

ROLLING_WEEKS = 4
FALLBACK_LOAD_KW = 1.0


def forecast_load(
    history: pd.DataFrame,
    start: datetime,
    hours: int = 48,
) -> pd.DataFrame:
    """Forecast load for the next `hours` hours at 5-min resolution.

    Args:
        history: HA history with columns [timestamp, load_kw].
                 Should have at least a few days for useful results.
        start: Forecast start time (UTC).
        hours: Forecast horizon.

    Returns:
        DataFrame with columns [timestamp, load_kw] at 5-min intervals.
    """
    # Generate target timestamps
    n_intervals = int(hours * 60 / INTERVAL_MIN)
    target_ts = pd.date_range(start=start, periods=n_intervals, freq=f"{INTERVAL_MIN}min", tz="UTC")

    if history is None or history.empty or "load_kw" not in history.columns:
        log.warning("No load history, returning flat %.1f kW", FALLBACK_LOAD_KW)
        return pd.DataFrame({"timestamp": target_ts, "load_kw": FALLBACK_LOAD_KW})

    df = history[["timestamp", "load_kw"]].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.dropna(subset=["load_kw"])

    if len(df) < 10:
        log.warning("Sparse load history (%d points), returning flat", len(df))
        return pd.DataFrame({"timestamp": target_ts, "load_kw": FALLBACK_LOAD_KW})

    # Filter to last ROLLING_WEEKS weeks
    cutoff = start - timedelta(weeks=ROLLING_WEEKS)
    df = df[df["timestamp"] >= cutoff]

    # Compute day-of-week and time-of-day bucket
    df["dow"] = df["timestamp"].dt.dayofweek  # 0=Mon
    df["tod"] = (df["timestamp"].dt.hour * 60 + df["timestamp"].dt.minute) // INTERVAL_MIN * INTERVAL_MIN

    has_enough_days = df["dow"].nunique() >= 3

    if has_enough_days:
        # Group by (day_of_week, time_of_day), take mean
        profile = df.groupby(["dow", "tod"])["load_kw"].mean()
    else:
        # Not enough days, ignore day_of_week
        profile = df.groupby("tod")["load_kw"].mean()

    # Map onto target timestamps
    result_load = np.full(n_intervals, FALLBACK_LOAD_KW)
    for i, ts in enumerate(target_ts):
        dow = ts.dayofweek
        tod = (ts.hour * 60 + ts.minute) // INTERVAL_MIN * INTERVAL_MIN

        if has_enough_days:
            if (dow, tod) in profile.index:
                result_load[i] = profile.loc[(dow, tod)]
            elif tod in profile.index.get_level_values("tod"):
                # Fall back to all-days average for this time slot
                result_load[i] = profile.xs(tod, level="tod").mean()
        else:
            if tod in profile.index:
                result_load[i] = profile.loc[tod]

    log.info("Load forecast: %d intervals, mean %.1f kW", n_intervals, result_load.mean())
    return pd.DataFrame({"timestamp": target_ts, "load_kw": result_load})
