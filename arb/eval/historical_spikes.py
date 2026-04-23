"""Scan price history for real spike events.

A spike is defined as a price that deviates from a rolling median by more
than `threshold_c_kwh` within a short window. If we find one in Immanuel's
last 30 days of Amber data we can use it as the demo moment instead of
the synthetic injection.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class HistoricalSpike:
    start_ts: datetime
    end_ts: datetime
    peak_c_kwh: float
    delta_from_median_c_kwh: float
    direction: str  # "up" or "down"
    channel: str    # "import" or "export"


def _rolling_median(series: pd.Series, window_min: int) -> pd.Series:
    """Rolling median centred on each point, in clock-time terms.

    Uses pandas' time-based rolling so 5-min gaps in the data don't throw the
    window off.
    """
    # Window is symmetrical-ish: pandas rolling is trailing, but for spike
    # detection what matters is that the baseline is stable. Use a trailing
    # window — spikes are short enough that a trailing median of 2h is still
    # close to the background level.
    return series.rolling(f"{window_min}min", min_periods=3).median()


def _spikes_for_channel(
    prices: pd.DataFrame,
    col: str,
    channel: str,
    threshold_c_kwh: float,
    median_window_min: int,
    min_peak_c_kwh: float | None,
) -> list[HistoricalSpike]:
    if col not in prices.columns:
        return []

    df = prices[["timestamp", col]].dropna(subset=[col]).copy()
    if df.empty:
        return []

    df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
    df = df.set_index("timestamp")

    values = df[col].astype(float)
    median = _rolling_median(values, median_window_min)
    delta = values - median

    # Each row independently gets classified as a spike candidate. We then
    # merge consecutive candidates (same direction, gap <= 10 min) into a
    # single spike event.
    up_mask = delta > threshold_c_kwh
    down_mask = delta < -threshold_c_kwh

    events: list[HistoricalSpike] = []

    for direction, mask in (("up", up_mask), ("down", down_mask)):
        if not mask.any():
            continue
        idx = np.where(mask.values)[0]
        # Group consecutive indices (in terms of timestamp proximity).
        groups: list[list[int]] = []
        current: list[int] = []
        prev_ts: pd.Timestamp | None = None
        timestamps = values.index
        for i in idx:
            ts = timestamps[i]
            if current and prev_ts is not None and (ts - prev_ts) > pd.Timedelta(minutes=10):
                groups.append(current)
                current = []
            current.append(i)
            prev_ts = ts
        if current:
            groups.append(current)

        for group in groups:
            group_vals = values.iloc[group]
            group_deltas = delta.iloc[group]
            if direction == "up":
                pick = group_deltas.idxmax()
                peak = float(group_vals.loc[pick])
                peak_delta = float(group_deltas.loc[pick])
            else:
                pick = group_deltas.idxmin()
                peak = float(group_vals.loc[pick])
                peak_delta = float(group_deltas.loc[pick])

            if min_peak_c_kwh is not None and abs(peak) < min_peak_c_kwh:
                continue

            start_ts = timestamps[group[0]]
            end_ts = timestamps[group[-1]]
            events.append(
                HistoricalSpike(
                    start_ts=pd.Timestamp(start_ts).to_pydatetime(),
                    end_ts=pd.Timestamp(end_ts).to_pydatetime(),
                    peak_c_kwh=peak,
                    delta_from_median_c_kwh=peak_delta,
                    direction=direction,
                    channel=channel,
                )
            )

    return events


def find_spikes(
    prices: pd.DataFrame,
    threshold_c_kwh: float = 20.0,
    median_window_min: int = 120,
    min_peak_c_kwh: float | None = None,
) -> list[HistoricalSpike]:
    """Find price spikes in the history.

    prices: must have timestamp and at least one of (import_c_kwh,
        export_c_kwh, rrp_c_kwh).
    threshold_c_kwh: delta from rolling median to count as a spike.
    median_window_min: window over which to compute the rolling median.
    min_peak_c_kwh: optional absolute floor for the peak value.

    Returns list sorted by delta_from_median_c_kwh descending.
    """
    if prices is None or prices.empty:
        return []

    df = prices.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    all_events: list[HistoricalSpike] = []

    if "import_c_kwh" in df.columns:
        all_events.extend(
            _spikes_for_channel(
                df, "import_c_kwh", "import",
                threshold_c_kwh, median_window_min, min_peak_c_kwh,
            )
        )
    if "export_c_kwh" in df.columns:
        all_events.extend(
            _spikes_for_channel(
                df, "export_c_kwh", "export",
                threshold_c_kwh, median_window_min, min_peak_c_kwh,
            )
        )
    if (
        "rrp_c_kwh" in df.columns
        and "import_c_kwh" not in df.columns
        and "export_c_kwh" not in df.columns
    ):
        all_events.extend(
            _spikes_for_channel(
                df, "rrp_c_kwh", "rrp",
                threshold_c_kwh, median_window_min, min_peak_c_kwh,
            )
        )

    all_events.sort(key=lambda s: abs(s.delta_from_median_c_kwh), reverse=True)
    return all_events


def _fetch_chunk(site_id: str, start_date, end_date) -> pd.DataFrame:
    """Raw Amber /prices call for a single date range."""
    import requests

    from arb.ingest import amber

    url = f"{amber.AMBER_API_BASE}/sites/{site_id}/prices"
    params = {
        "resolution": 5,
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
    }
    resp = requests.get(url, headers=amber._headers(), params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return pd.DataFrame()

    rows = []
    for entry in data:
        ts = pd.to_datetime(entry.get("startTime"), utc=True)
        channel = entry.get("channelType", "")
        price = entry.get("perKwh", 0.0)
        if channel == "general":
            rows.append({"timestamp": ts, "import_c_kwh": price})
        elif channel == "feedIn":
            rows.append({"timestamp": ts, "export_c_kwh": price})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    imports = df[df.get("import_c_kwh").notna()][["timestamp", "import_c_kwh"]] if "import_c_kwh" in df.columns else pd.DataFrame()
    exports = df[df.get("export_c_kwh").notna()][["timestamp", "export_c_kwh"]] if "export_c_kwh" in df.columns else pd.DataFrame()
    if imports.empty and exports.empty:
        return pd.DataFrame()
    if imports.empty:
        return exports.reset_index(drop=True)
    if exports.empty:
        return imports.reset_index(drop=True)
    return imports.merge(exports, on="timestamp", how="outer").reset_index(drop=True)


def _fetch_in_chunks(days: int, chunk_days: int = 7) -> pd.DataFrame:
    """Amber's /prices rejects long date ranges. Fetch in chunks and stitch."""
    from datetime import date, timedelta

    from arb.ingest import amber

    if amber._api_key() is None:
        log.info("No Amber API key, skipping historical")
        return pd.DataFrame()
    site_id = amber.get_site_id()
    if site_id is None:
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    end = date.today() - timedelta(days=1)
    days_left = days
    while days_left > 0:
        take = min(chunk_days, days_left)
        start = end - timedelta(days=take - 1)
        log.info("Amber chunk: %s to %s", start, end)
        try:
            df = _fetch_chunk(site_id, start, end)
        except Exception as e:  # noqa: BLE001
            log.warning("Amber fetch failed %s..%s: %s", start, end, e)
            break
        if not df.empty:
            frames.append(df)
        end = start - timedelta(days=1)
        days_left -= take

    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["timestamp"])
    combined = combined.sort_values("timestamp").reset_index(drop=True)
    return combined


def main():
    """CLI: print top N spikes from the last N days of Amber data."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--threshold", type=float, default=20.0)
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    prices = _fetch_in_chunks(args.days, chunk_days=7)
    if prices.empty:
        print("No Amber history available.")
        return

    spikes = find_spikes(prices, threshold_c_kwh=args.threshold)
    print(f"Found {len(spikes)} spikes in ~{args.days} days of data "
          f"({len(prices)} intervals covered).")
    if not spikes:
        print(f"No spikes above {args.threshold:.1f} c/kWh threshold.")
        return
    print(f"Top {args.top}:")
    for s in spikes[: args.top]:
        print(
            f"  {s.start_ts} {s.direction:4} {s.channel:7} "
            f"peak={s.peak_c_kwh:+7.1f} delta={s.delta_from_median_c_kwh:+6.1f} c/kWh"
        )


if __name__ == "__main__":
    main()
