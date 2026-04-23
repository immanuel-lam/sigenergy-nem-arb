"""Detect when newest price data diverges hard from what the last plan assumed.

The arbitrage agent re-plans every 30 min. Between cycles, Amber/AEMO can
revise their forecast — sometimes by a lot (cap events, demand shocks,
negative exports when rooftop solar overwhelms the grid). This module
spots those revisions early so the agent can react before the interval
actually hits.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

import numpy as np
import pandas as pd

from arb.scheduler.constants import INTERVAL_MIN
from arb.scheduler.plan import Plan


class SpikeDirection(str, Enum):
    UP = "up"
    DOWN = "down"


class SpikeSeverity(str, Enum):
    MINOR = "minor"
    MAJOR = "major"
    CAP = "cap"


@dataclass
class SpikeEvent:
    """A single price revision that crosses the significance bar."""

    detected_at: datetime
    interval_ts: datetime
    planned_price_c_kwh: float
    actual_price_c_kwh: float
    direction: SpikeDirection
    severity: SpikeSeverity
    magnitude_c_kwh: float
    price_type: str  # "import" or "export"
    reason: str


# Severity ordering for tiebreaks (CAP > MAJOR > MINOR).
_SEVERITY_RANK = {
    SpikeSeverity.MINOR: 0,
    SpikeSeverity.MAJOR: 1,
    SpikeSeverity.CAP: 2,
}


def _normalize_prices(price_df: pd.DataFrame) -> pd.DataFrame:
    """Normalise a price DataFrame to (timestamp, import_c_kwh, export_c_kwh).

    Handles both Amber (separate import/export) and AEMO (rrp_c_kwh used
    for both) shapes. Rounds timestamps to the 5-min grid so Amber's
    ~1-second offset doesn't break alignment.
    """
    if price_df is None or price_df.empty:
        return pd.DataFrame(columns=["timestamp", "import_c_kwh", "export_c_kwh"])

    df = price_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["timestamp"] = df["timestamp"].dt.round(f"{INTERVAL_MIN}min")

    if "import_c_kwh" in df.columns:
        out = df[["timestamp"]].copy()
        out["import_c_kwh"] = df["import_c_kwh"].astype(float)
        if "export_c_kwh" in df.columns:
            out["export_c_kwh"] = df["export_c_kwh"].astype(float)
        else:
            out["export_c_kwh"] = df["import_c_kwh"].astype(float)
        out = out.dropna(subset=["import_c_kwh"])
        # Keep last value if duplicate rounded timestamps collide.
        out = out.drop_duplicates(subset=["timestamp"], keep="last")
        return out.reset_index(drop=True)

    if "rrp_c_kwh" in df.columns:
        out = df[["timestamp"]].copy()
        out["import_c_kwh"] = df["rrp_c_kwh"].astype(float)
        out["export_c_kwh"] = df["rrp_c_kwh"].astype(float)
        out = out.dropna(subset=["import_c_kwh"])
        out = out.drop_duplicates(subset=["timestamp"], keep="last")
        return out.reset_index(drop=True)

    return pd.DataFrame(columns=["timestamp", "import_c_kwh", "export_c_kwh"])


def _classify(
    actual: float,
    planned: float,
    deviation_threshold: float,
    min_absolute_c_kwh: float,
    cap_threshold_c_kwh: float,
) -> tuple[SpikeSeverity, SpikeDirection, float] | None:
    """Return (severity, direction, magnitude) or None if not a spike."""
    delta = actual - planned
    magnitude = abs(delta)
    rel = magnitude / max(abs(planned), 1.0)
    direction = SpikeDirection.UP if delta > 0 else SpikeDirection.DOWN

    # CAP takes precedence — absolute price is extreme regardless of deviation.
    if abs(actual) > cap_threshold_c_kwh:
        return SpikeSeverity.CAP, direction, magnitude

    if magnitude < min_absolute_c_kwh:
        return None
    if rel <= deviation_threshold:
        return None

    if rel > 3 * deviation_threshold:
        return SpikeSeverity.MAJOR, direction, magnitude
    return SpikeSeverity.MINOR, direction, magnitude


def _better(a: SpikeEvent, b: SpikeEvent) -> SpikeEvent:
    """Pick the more severe / larger-magnitude of two events."""
    if a.magnitude_c_kwh != b.magnitude_c_kwh:
        return a if a.magnitude_c_kwh > b.magnitude_c_kwh else b
    # Tie on magnitude — use severity.
    return a if _SEVERITY_RANK[a.severity] >= _SEVERITY_RANK[b.severity] else b


def detect_spike(
    current_snapshot,
    previous_plan: Plan | None,
    deviation_threshold: float = 0.3,
    min_absolute_c_kwh: float = 5.0,
    lookahead_minutes: int = 120,
    cap_threshold_c_kwh: float = 100.0,
) -> SpikeEvent | None:
    """Find the most severe price revision in the next lookahead_minutes.

    Compares the newest snapshot prices against the import/export values
    baked into the previous plan. Returns None if there's no previous
    plan, no price data, or nothing crosses the threshold.
    """
    if previous_plan is None:
        return None
    if current_snapshot is None:
        return None

    prices = _normalize_prices(current_snapshot.price_forecast)
    if prices.empty:
        return None

    now = pd.Timestamp(current_snapshot.timestamp)
    if now.tz is None:
        now = now.tz_localize("UTC")
    else:
        now = now.tz_convert("UTC")
    window_end = now + pd.Timedelta(minutes=lookahead_minutes)

    prices = prices.sort_values("timestamp").reset_index(drop=True)
    tolerance = pd.Timedelta(minutes=INTERVAL_MIN)

    best: SpikeEvent | None = None
    detected_at = (
        current_snapshot.timestamp
        if isinstance(current_snapshot.timestamp, datetime)
        else now.to_pydatetime()
    )
    if detected_at.tzinfo is None:
        detected_at = detected_at.replace(tzinfo=timezone.utc)

    plan_ts = pd.DatetimeIndex(previous_plan.timestamps)
    if plan_ts.tz is None:
        plan_ts = plan_ts.tz_localize("UTC")

    price_ts_index = pd.DatetimeIndex(prices["timestamp"])

    for i in range(previous_plan.n):
        t = plan_ts[i]
        if t < now or t >= window_end:
            continue

        # Find nearest actual price within 5 min.
        pos = price_ts_index.get_indexer([t], method="nearest")[0]
        if pos < 0:
            continue
        nearest_ts = price_ts_index[pos]
        if abs(nearest_ts - t) > tolerance:
            continue

        actual_import = float(prices.iloc[pos]["import_c_kwh"])
        actual_export = float(prices.iloc[pos]["export_c_kwh"])
        planned_import = float(previous_plan.import_c_kwh[i])
        planned_export = float(previous_plan.export_c_kwh[i])

        for price_type, planned, actual in (
            ("import", planned_import, actual_import),
            ("export", planned_export, actual_export),
        ):
            result = _classify(
                actual,
                planned,
                deviation_threshold,
                min_absolute_c_kwh,
                cap_threshold_c_kwh,
            )
            if result is None:
                continue
            severity, direction, magnitude = result
            event = SpikeEvent(
                detected_at=detected_at,
                interval_ts=t.to_pydatetime(),
                planned_price_c_kwh=planned,
                actual_price_c_kwh=actual,
                direction=direction,
                severity=severity,
                magnitude_c_kwh=magnitude,
                price_type=price_type,
                reason="",
            )
            event.reason = spike_reason(event)
            best = event if best is None else _better(best, event)

    return best


def format_spike_for_log(event: SpikeEvent) -> str:
    """Single-line log format with all the facts that matter."""
    ts = event.interval_ts
    if ts.tzinfo is not None:
        ts_str = ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    else:
        ts_str = ts.strftime("%Y-%m-%d %H:%M")
    return (
        f"[SPIKE {event.severity.value} {event.direction.value} {event.price_type}] "
        f"{ts_str}: planned {event.planned_price_c_kwh:.1f} c/kWh, "
        f"now {event.actual_price_c_kwh:.1f} c/kWh "
        f"(delta {event.magnitude_c_kwh:.1f})"
    )


def spike_reason(event: SpikeEvent) -> str:
    """Short human-readable line for rationale and explain prompts."""
    ts = event.interval_ts
    hhmm = ts.astimezone(timezone.utc).strftime("%H:%M") if ts.tzinfo else ts.strftime("%H:%M")
    delta = event.magnitude_c_kwh
    actual = event.actual_price_c_kwh

    if event.severity == SpikeSeverity.CAP:
        return (
            f"AEMO cap event detected: {actual:.0f} c/kWh at {hhmm} "
            f"({event.price_type} price)"
        )

    direction_word = "up" if event.direction == SpikeDirection.UP else "down"

    if event.price_type == "import":
        if event.direction == SpikeDirection.UP:
            return (
                f"Amber revised import price up {delta:.0f}c for {hhmm} "
                f"— peak event incoming"
            )
        return (
            f"Import price dropped {delta:.0f}c below plan for {hhmm} "
            f"— cheaper charging window"
        )

    # export
    if event.direction == SpikeDirection.DOWN:
        return (
            f"Export price dropped {delta:.0f}c below plan for {hhmm} "
            f"— avoid exporting"
        )
    return (
        f"Export price revised up {delta:.0f}c for {hhmm} "
        f"— better discharge window"
    )


__all__ = [
    "SpikeDirection",
    "SpikeSeverity",
    "SpikeEvent",
    "detect_spike",
    "format_spike_for_log",
    "spike_reason",
]
