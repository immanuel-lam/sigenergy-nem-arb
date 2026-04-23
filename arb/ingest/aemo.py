"""Fetch NSW1 wholesale prices from AEMO NEMWEB 5MPD reports."""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import datetime, timezone

import pandas as pd
import requests

log = logging.getLogger(__name__)

NEMWEB_BASE = "https://nemweb.com.au"
NEMWEB_5MPD_URL = f"{NEMWEB_BASE}/Reports/Current/P5_Reports/"
NEMWEB_DISPATCH_URL = f"{NEMWEB_BASE}/Reports/Current/DispatchIS_Reports/"
REGION = "NSW1"


def _fetch_latest_zip_url(index_url: str) -> str | None:
    """Scrape the NEMWEB directory listing for the most recent zip file."""
    resp = requests.get(index_url, timeout=30)
    resp.raise_for_status()
    # NEMWEB uses uppercase HREF tags: <A HREF="/Reports/...zip">
    import re
    links: list[str] = []
    for match in re.finditer(r'HREF="([^"]+\.zip)"', resp.text, re.IGNORECASE):
        links.append(match.group(1))

    if not links:
        return None
    # Files are named with timestamps — last alphabetically is most recent
    links.sort()
    latest = links[-1]
    if latest.startswith("http"):
        return latest
    if latest.startswith("/"):
        return NEMWEB_BASE + latest
    return index_url.rstrip("/") + "/" + latest


def _extract_table(csv_text: str, table_name: str) -> pd.DataFrame | None:
    """Extract a named table from AEMO's multi-table CSV format.

    AEMO CSVs pack multiple tables into one file. Each table has:
    - I row: header (I,report,table,version,col1,col2,...)
    - D rows: data  (D,report,table,version,val1,val2,...)

    We match on the table name in column index 2.
    """
    header_cols: list[str] | None = None
    data_rows: list[list[str]] = []

    for line in csv_text.splitlines():
        parts = line.split(",")
        if len(parts) < 4:
            continue
        row_type, _report, tbl = parts[0], parts[1], parts[2]
        if tbl.strip().upper() != table_name.upper():
            continue
        if row_type == "I":
            # Columns start at index 4 (skip I, report, table, version)
            header_cols = [c.strip().strip('"') for c in parts[4:]]
        elif row_type == "D" and header_cols is not None:
            vals = [v.strip().strip('"') for v in parts[4:]]
            if len(vals) >= len(header_cols):
                data_rows.append(vals[: len(header_cols)])

    if header_cols is None or not data_rows:
        return None

    return pd.DataFrame(data_rows, columns=header_cols)


def _parse_5mpd_csv(csv_text: str) -> pd.DataFrame:
    """Parse raw AEMO 5MPD CSV into a clean dataframe.

    Returns columns: timestamp (UTC), rrp_mwh, rrp_c_kwh, region.
    """
    df = _extract_table(csv_text, "REGIONSOLUTION")
    if df is None or df.empty:
        log.warning("No REGIONSOLUTION table found in CSV")
        return pd.DataFrame()

    # Filter to NSW1 and non-intervention runs
    df = df[df["REGIONID"].str.strip() == REGION].copy()
    if "INTERVENTION" in df.columns:
        df = df[df["INTERVENTION"].str.strip() == "0"]

    if df.empty:
        return pd.DataFrame()

    # INTERVAL_DATETIME is the timestamp for each 5-min interval.
    # Use errors='coerce' so malformed timestamps become NaT (dropped by dropna below).
    df["timestamp"] = pd.to_datetime(df["INTERVAL_DATETIME"], errors="coerce")
    # AEMO timestamps are AEST (UTC+10)
    df["timestamp"] = df["timestamp"].dt.tz_localize("Australia/Sydney").dt.tz_convert("UTC")
    df["rrp_mwh"] = pd.to_numeric(df["RRP"], errors="coerce")
    df["rrp_c_kwh"] = df["rrp_mwh"] / 10.0  # $/MWh -> c/kWh
    df["region"] = REGION

    return df[["timestamp", "rrp_mwh", "rrp_c_kwh", "region"]].dropna().reset_index(drop=True)


def fetch_5mpd_forecast() -> pd.DataFrame:
    """Fetch the latest 5-minute predispatch price forecast for NSW1.

    Returns a dataframe with columns: timestamp (UTC), rrp_mwh, rrp_c_kwh, region.
    """
    url = _fetch_latest_zip_url(NEMWEB_5MPD_URL)
    if url is None:
        log.error("No 5MPD zip files found on NEMWEB")
        return pd.DataFrame()

    log.info("Fetching 5MPD: %s", url)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_names = [n for n in zf.namelist() if n.endswith(".CSV") or n.endswith(".csv")]
        if not csv_names:
            log.error("No CSV in zip: %s", url)
            return pd.DataFrame()

        all_dfs = []
        for name in csv_names:
            with zf.open(name) as f:
                text = f.read().decode("utf-8", errors="replace")
                df = _parse_5mpd_csv(text)
                if not df.empty:
                    all_dfs.append(df)

    if not all_dfs:
        return pd.DataFrame()

    result = pd.concat(all_dfs, ignore_index=True)
    result = result.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    log.info("5MPD forecast: %d intervals, %s to %s", len(result), result["timestamp"].iloc[0], result["timestamp"].iloc[-1])
    return result


def _parse_dispatch_csv(csv_text: str) -> pd.DataFrame:
    """Parse AEMO DispatchIS CSV (table PRICE)."""
    df = _extract_table(csv_text, "PRICE")
    if df is None or df.empty:
        return pd.DataFrame()

    df = df[df["REGIONID"].str.strip() == REGION].copy()
    if "INTERVENTION" in df.columns:
        df = df[df["INTERVENTION"].str.strip() == "0"]

    if df.empty:
        return pd.DataFrame()

    df["timestamp"] = pd.to_datetime(df["SETTLEMENTDATE"])
    df["timestamp"] = df["timestamp"].dt.tz_localize("Australia/Sydney").dt.tz_convert("UTC")
    df["rrp_mwh"] = pd.to_numeric(df["RRP"], errors="coerce")
    df["rrp_c_kwh"] = df["rrp_mwh"] / 10.0
    df["region"] = REGION
    return df[["timestamp", "rrp_mwh", "rrp_c_kwh", "region"]].dropna().reset_index(drop=True)


def fetch_dispatch_prices(lookback_hours: int = 24) -> pd.DataFrame:
    """Fetch recent actual dispatch prices (for backtest and comparison).

    Same schema as fetch_5mpd_forecast.
    """
    url = _fetch_latest_zip_url(NEMWEB_DISPATCH_URL)
    if url is None:
        log.error("No dispatch zip files found on NEMWEB")
        return pd.DataFrame()

    log.info("Fetching dispatch prices: %s", url)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_names = [n for n in zf.namelist() if n.endswith(".CSV") or n.endswith(".csv")]
        all_dfs = []
        for name in csv_names:
            with zf.open(name) as f:
                text = f.read().decode("utf-8", errors="replace")
                df = _parse_dispatch_csv(text)
                if not df.empty:
                    all_dfs.append(df)

    if not all_dfs:
        return pd.DataFrame()

    result = pd.concat(all_dfs, ignore_index=True)
    result = result.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    log.info("Dispatch prices: %d intervals", len(result))
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    df = fetch_5mpd_forecast()
    if df.empty:
        print("No 5MPD data retrieved")
    else:
        print(f"Got {len(df)} intervals")
        print(df.head(10).to_string())
        print(f"\nPrice range: {df['rrp_c_kwh'].min():.1f} to {df['rrp_c_kwh'].max():.1f} c/kWh")
