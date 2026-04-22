"""Weather forecast via Open-Meteo (cloud cover, temperature, irradiance)."""
from __future__ import annotations

import logging
import os

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def fetch_weather_forecast(
    lat: float | None = None,
    lon: float | None = None,
    hours: int = 48,
) -> pd.DataFrame:
    """Fetch hourly weather forecast from Open-Meteo.

    Returns columns: timestamp (UTC), cloud_cover_pct, temperature_c,
    shortwave_radiation_wm2, is_day.
    """
    lat = lat or float(os.getenv("LATITUDE", "-33.8688"))
    lon = lon or float(os.getenv("LONGITUDE", "151.2093"))

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "cloud_cover,temperature_2m,shortwave_radiation,is_day",
        "timezone": "UTC",
        "forecast_days": max(2, hours // 24 + 1),
    }

    log.info("Fetching Open-Meteo forecast for (%.4f, %.4f)", lat, lon)
    resp = requests.get(OPEN_METEO_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    hourly = data.get("hourly", {})
    if not hourly.get("time"):
        log.error("No hourly data in Open-Meteo response")
        return pd.DataFrame()

    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(hourly["time"], utc=True),
            "cloud_cover_pct": hourly.get("cloud_cover", [None] * len(hourly["time"])),
            "temperature_c": hourly.get("temperature_2m", [None] * len(hourly["time"])),
            "shortwave_radiation_wm2": hourly.get(
                "shortwave_radiation", [None] * len(hourly["time"])
            ),
            "is_day": hourly.get("is_day", [None] * len(hourly["time"])),
        }
    )

    # Trim to requested horizon
    df = df.head(hours).reset_index(drop=True)
    log.info("Weather forecast: %d hours", len(df))
    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    df = fetch_weather_forecast()
    if df.empty:
        print("No weather data")
    else:
        print(df.to_string())
