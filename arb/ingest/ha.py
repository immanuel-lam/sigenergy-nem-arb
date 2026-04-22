"""Pull historical and live data from Home Assistant REST API."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)


def _ha_url() -> str:
    return os.environ["HA_URL"].rstrip("/")


def _ha_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['HA_TOKEN']}",
        "Content-Type": "application/json",
    }


def _sensor_ids() -> dict[str, str]:
    """Return configured sensor entity IDs."""
    return {
        "load": os.getenv("HA_SENSOR_LOAD", "sensor.grid_power"),
        "solar": os.getenv("HA_SENSOR_SOLAR", "sensor.solar_power"),
        "soc": os.getenv("HA_SENSOR_SOC", "sensor.battery_soc"),
        "battery_power": os.getenv("HA_SENSOR_BATTERY_POWER", "sensor.battery_power"),
    }


def fetch_history(
    days: int = 30,
    end: datetime | None = None,
) -> pd.DataFrame:
    """Pull history for load, solar, SOC, battery_power from HA.

    Returns a dataframe resampled to 5-min intervals with columns:
    timestamp (UTC), load_kw, solar_kw, soc_pct, battery_power_kw.
    """
    sensors = _sensor_ids()
    entity_ids = ",".join(sensors.values())

    end = end or datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    url = f"{_ha_url()}/api/history/period/{start.isoformat()}"
    params = {
        "filter_entity_id": entity_ids,
        "end_time": end.isoformat(),
        "minimal_response": "",
        "no_attributes": "",
    }

    log.info("Fetching HA history: %d days, %d sensors", days, len(sensors))
    resp = requests.get(url, headers=_ha_headers(), params=params, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    if not data:
        log.warning("Empty HA history response")
        return pd.DataFrame()

    # HA returns a list of lists — one per entity
    entity_map = {v: k for k, v in sensors.items()}
    frames = {}

    for entity_history in data:
        if not entity_history:
            continue
        entity_id = entity_history[0].get("entity_id", "")
        col_name = entity_map.get(entity_id)
        if col_name is None:
            continue

        records = []
        for state in entity_history:
            try:
                val = float(state["state"])
                ts = pd.to_datetime(state["last_changed"], utc=True)
                records.append({"timestamp": ts, col_name: val})
            except (ValueError, KeyError):
                continue

        if records:
            frames[col_name] = pd.DataFrame(records).set_index("timestamp")

    if not frames:
        log.warning("No parseable HA history data")
        return pd.DataFrame()

    # Merge all sensors on timestamp, resample to 5-min
    combined = pd.DataFrame()
    for name, df in frames.items():
        df = df[~df.index.duplicated(keep="last")]
        df = df.resample("5min").mean()
        if combined.empty:
            combined = df
        else:
            combined = combined.join(df, how="outer")

    combined = combined.interpolate(method="time", limit=6)  # fill gaps up to 30 min

    # Rename to standard columns
    col_map = {
        "load": "load_kw",
        "solar": "solar_kw",
        "soc": "soc_pct",
        "battery_power": "battery_power_kw",
    }
    combined = combined.rename(columns=col_map)
    combined = combined.reset_index().rename(columns={"index": "timestamp"})

    log.info("HA history: %d rows, columns: %s", len(combined), list(combined.columns))
    return combined


def get_current_state() -> dict[str, float | None]:
    """Get live sensor values. Returns dict with load_kw, solar_kw, soc_pct, battery_power_kw."""
    sensors = _sensor_ids()
    result = {}

    for name, entity_id in sensors.items():
        url = f"{_ha_url()}/api/states/{entity_id}"
        try:
            resp = requests.get(url, headers=_ha_headers(), timeout=10)
            resp.raise_for_status()
            state = resp.json()
            result[f"{name}_kw" if name != "soc" else "soc_pct"] = float(state["state"])
        except (requests.RequestException, ValueError, KeyError) as e:
            log.warning("Failed to read %s (%s): %s", name, entity_id, e)
            result[f"{name}_kw" if name != "soc" else "soc_pct"] = None

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Current state:")
    state = get_current_state()
    for k, v in state.items():
        print(f"  {k}: {v}")
