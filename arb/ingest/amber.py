"""Amber Electric API client — fallback/alternative to raw AEMO NEMWEB.

Amber prices include network fees so they're closer to what the battery
actually pays/earns. Use this if Immanuel has an Amber API key.
"""
from __future__ import annotations

import logging
import os

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

AMBER_API_BASE = "https://api.amber.com.au/v1"


def _api_key() -> str | None:
    return os.getenv("AMBER_API_KEY") or None


def _headers() -> dict[str, str]:
    key = _api_key()
    if not key:
        raise ValueError("AMBER_API_KEY not set")
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}


def get_site_id() -> str | None:
    """Get the first site ID from Amber account."""
    try:
        resp = requests.get(f"{AMBER_API_BASE}/sites", headers=_headers(), timeout=15)
        resp.raise_for_status()
        sites = resp.json()
        if sites:
            return sites[0]["id"]
    except (requests.RequestException, KeyError, ValueError) as e:
        log.warning("Failed to get Amber site ID: %s", e)
    return None


def fetch_prices(site_id: str | None = None) -> pd.DataFrame:
    """Fetch current + forecast prices from Amber.

    Returns columns: timestamp (UTC), import_c_kwh, export_c_kwh, price_type.
    price_type is one of: ActualPrice, ForecastPrice, CurrentPrice.
    """
    if _api_key() is None:
        log.info("No Amber API key, skipping")
        return pd.DataFrame()

    site_id = site_id or get_site_id()
    if site_id is None:
        return pd.DataFrame()

    url = f"{AMBER_API_BASE}/sites/{site_id}/prices"
    params = {"resolution": 5}  # 5-min intervals

    log.info("Fetching Amber prices for site %s", site_id)
    resp = requests.get(url, headers=_headers(), params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not data:
        return pd.DataFrame()

    rows = []
    for entry in data:
        ts = pd.to_datetime(entry.get("startTime"), utc=True)
        channel = entry.get("channelType", "")
        price = entry.get("perKwh", 0.0)  # already in c/kWh
        price_type = entry.get("type", "unknown")

        if channel == "general":
            rows.append(
                {
                    "timestamp": ts,
                    "import_c_kwh": price,
                    "price_type": price_type,
                }
            )
        elif channel == "feedIn":
            rows.append(
                {
                    "timestamp": ts,
                    "export_c_kwh": price,
                    "price_type": price_type,
                }
            )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # Pivot so each timestamp has both import and export
    imports = df[df["import_c_kwh"].notna()][["timestamp", "import_c_kwh", "price_type"]]
    exports = df[df["export_c_kwh"].notna()][["timestamp", "export_c_kwh"]]

    if not imports.empty and not exports.empty:
        result = imports.merge(exports, on="timestamp", how="outer")
    elif not imports.empty:
        result = imports
        result["export_c_kwh"] = None
    else:
        return pd.DataFrame()

    result = result.sort_values("timestamp").reset_index(drop=True)
    log.info("Amber prices: %d intervals", len(result))
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    df = fetch_prices()
    if df.empty:
        print("No Amber data (key missing or API error)")
    else:
        print(df.head(20).to_string())
