"""Aggregate all ingest sources into a single state snapshot."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from arb.ingest import aemo, amber, bom, ha

log = logging.getLogger(__name__)


@dataclass
class Snapshot:
    """Current state of the world at a point in time."""

    timestamp: datetime
    soc_pct: float | None
    load_kw: float | None
    solar_kw: float | None
    battery_power_kw: float | None

    # Price forecasts
    price_forecast: pd.DataFrame  # timestamp, rrp_c_kwh (or import/export from Amber)
    weather_forecast: pd.DataFrame  # timestamp, cloud_cover_pct, shortwave_radiation_wm2

    # Data quality flags
    stale_sensors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def is_stale(self, threshold_minutes: int = 10) -> bool:
        return bool(self.stale_sensors)

    def summary(self) -> str:
        lines = [
            f"Snapshot at {self.timestamp.isoformat()}",
            f"  SOC: {self.soc_pct}%",
            f"  Load: {self.load_kw} kW",
            f"  Solar: {self.solar_kw} kW",
            f"  Battery: {self.battery_power_kw} kW",
            f"  Price intervals: {len(self.price_forecast)}",
            f"  Weather hours: {len(self.weather_forecast)}",
        ]
        if self.stale_sensors:
            lines.append(f"  STALE: {', '.join(self.stale_sensors)}")
        if self.warnings:
            for w in self.warnings:
                lines.append(f"  WARN: {w}")
        return "\n".join(lines)


def take_snapshot() -> Snapshot:
    """Pull live data from all sources and return a Snapshot."""
    now = datetime.now(timezone.utc)
    warnings: list[str] = []
    stale: list[str] = []

    # HA live state
    try:
        ha_state = ha.get_current_state()
    except Exception as e:
        log.error("HA state fetch failed: %s", e)
        ha_state = {}
        stale.extend(["load", "solar", "soc", "battery_power"])
        warnings.append(f"HA unreachable: {e}")

    # AEMO prices — try Amber first, fall back to NEMWEB
    price_forecast = pd.DataFrame()
    try:
        price_forecast = amber.fetch_prices()
    except Exception as e:
        warnings.append(f"Amber failed: {e}")

    if price_forecast.empty:
        try:
            price_forecast = aemo.fetch_5mpd_forecast()
        except Exception as e:
            log.error("AEMO 5MPD fetch failed: %s", e)
            warnings.append(f"AEMO 5MPD failed: {e}")
            stale.append("prices")

    # Weather
    weather_forecast = pd.DataFrame()
    try:
        weather_forecast = bom.fetch_weather_forecast()
    except Exception as e:
        log.error("Weather fetch failed: %s", e)
        warnings.append(f"Weather failed: {e}")

    for sensor_name in ["load_kw", "solar_kw", "soc_pct", "battery_power_kw"]:
        if ha_state.get(sensor_name) is None and sensor_name.replace("_kw", "").replace("_pct", "") not in stale:
            stale.append(sensor_name)

    return Snapshot(
        timestamp=now,
        soc_pct=ha_state.get("soc_pct"),
        load_kw=ha_state.get("load_kw"),
        solar_kw=ha_state.get("solar_kw"),
        battery_power_kw=ha_state.get("battery_power_kw"),
        price_forecast=price_forecast,
        weather_forecast=weather_forecast,
        stale_sensors=stale,
        warnings=warnings,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    snap = take_snapshot()
    print(snap.summary())
