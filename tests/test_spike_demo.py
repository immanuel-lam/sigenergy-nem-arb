"""Tests for spike_demo.inject_spike and historical_spikes.find_spikes.

No LLM calls, no network. Pure synthetic DataFrames.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from arb.agent.spike_demo import inject_spike
from arb.eval.historical_spikes import HistoricalSpike, find_spikes


def _price_frame(start: datetime, n: int = 24, freq_min: int = 5,
                 import_c: float = 10.0, export_c: float = 5.0) -> pd.DataFrame:
    ts = pd.date_range(start=start, periods=n, freq=f"{freq_min}min", tz="UTC")
    return pd.DataFrame({
        "timestamp": ts,
        "import_c_kwh": np.full(n, import_c, dtype=float),
        "export_c_kwh": np.full(n, export_c, dtype=float),
    })


# ---------------------------------------------------------------------------
# inject_spike
# ---------------------------------------------------------------------------

def test_inject_spike_raises_import_prices():
    now = datetime(2026, 4, 23, 14, 0, tzinfo=timezone.utc)
    df = _price_frame(now, n=24)

    out = inject_spike(
        df,
        start_offset_min=10,
        duration_min=15,
        magnitude_c_kwh=120.0,
        channel="import",
        now=now,
    )

    start = pd.Timestamp(now) + pd.Timedelta(minutes=10)
    end = start + pd.Timedelta(minutes=15)
    mask = (out["timestamp"] >= start) & (out["timestamp"] < end)
    assert mask.any()
    assert (out.loc[mask, "import_c_kwh"] == 130.0).all()


def test_inject_spike_respects_window():
    now = datetime(2026, 4, 23, 14, 0, tzinfo=timezone.utc)
    df = _price_frame(now, n=24)

    out = inject_spike(
        df,
        start_offset_min=10,
        duration_min=15,
        magnitude_c_kwh=50.0,
        channel="import",
        now=now,
    )

    start = pd.Timestamp(now) + pd.Timedelta(minutes=10)
    end = start + pd.Timedelta(minutes=15)
    in_window = out[(out["timestamp"] >= start) & (out["timestamp"] < end)]
    # 15-min duration / 5-min intervals = 3 rows
    assert len(in_window) == 3


def test_inject_spike_leaves_other_rows_unchanged():
    now = datetime(2026, 4, 23, 14, 0, tzinfo=timezone.utc)
    df = _price_frame(now, n=24)

    out = inject_spike(
        df,
        start_offset_min=10,
        duration_min=15,
        magnitude_c_kwh=50.0,
        channel="import",
        now=now,
    )

    start = pd.Timestamp(now) + pd.Timedelta(minutes=10)
    end = start + pd.Timedelta(minutes=15)
    outside = out[~((out["timestamp"] >= start) & (out["timestamp"] < end))]
    original_outside = df[~((df["timestamp"] >= start) & (df["timestamp"] < end))]

    pd.testing.assert_frame_equal(
        outside.reset_index(drop=True),
        original_outside.reset_index(drop=True),
        check_like=True,
    )


# ---------------------------------------------------------------------------
# find_spikes
# ---------------------------------------------------------------------------

def test_find_spikes_detects_synthetic():
    # 3 hours of flat 10 c/kWh with a single 60 c/kWh spike at 1h30m in.
    start = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    ts = pd.date_range(start=start, periods=36, freq="5min", tz="UTC")  # 3h
    import_c = np.full(36, 10.0)
    import_c[18] = 60.0  # 50c delta, well above 20c threshold
    df = pd.DataFrame({"timestamp": ts, "import_c_kwh": import_c})

    spikes = find_spikes(df, threshold_c_kwh=20.0, median_window_min=60)

    assert len(spikes) >= 1
    top = spikes[0]
    assert top.direction == "up"
    assert top.channel == "import"
    assert top.peak_c_kwh == 60.0
    assert top.delta_from_median_c_kwh >= 20.0


def test_find_spikes_respects_threshold():
    # 5c bump — below 20c threshold. Should not register.
    start = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    ts = pd.date_range(start=start, periods=36, freq="5min", tz="UTC")
    import_c = np.full(36, 10.0)
    import_c[18] = 15.0
    df = pd.DataFrame({"timestamp": ts, "import_c_kwh": import_c})

    spikes = find_spikes(df, threshold_c_kwh=20.0, median_window_min=60)

    assert spikes == []


def test_find_spikes_sorted_by_magnitude_desc():
    # Two spikes of different magnitudes.
    start = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    ts = pd.date_range(start=start, periods=72, freq="5min", tz="UTC")  # 6h
    import_c = np.full(72, 10.0)
    import_c[18] = 40.0  # +30c
    import_c[54] = 80.0  # +70c — bigger
    df = pd.DataFrame({"timestamp": ts, "import_c_kwh": import_c})

    spikes = find_spikes(df, threshold_c_kwh=15.0, median_window_min=60)

    assert len(spikes) >= 2
    # Descending by absolute delta.
    deltas = [abs(s.delta_from_median_c_kwh) for s in spikes]
    assert deltas == sorted(deltas, reverse=True)
    assert spikes[0].peak_c_kwh == 80.0
