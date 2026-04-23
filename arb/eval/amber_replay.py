"""Reconstruct what Amber SmartShift actually did from HA history.

Amber's SmartShift is the incumbent controller — we don't drive the battery,
we observe what it already did. This module classifies each 5-min interval
from HA history into an action (charge/discharge/idle) and tallies the cost
against matching Amber or AEMO price data.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from arb.scheduler.constants import INTERVAL_MIN, BatteryConstants

log = logging.getLogger(__name__)

# Classification thresholds (kW).
_CHARGE_THRESHOLD_KW = 1.0
_DISCHARGE_THRESHOLD_KW = -1.0
_IDLE_THRESHOLD_KW = 0.5
# Ratio used to decide whether a charge is "from solar" vs "from grid".
_SOLAR_DOMINANCE = 1.5


def _classify_action(battery_power_kw: float, load_kw: float, solar_kw: float) -> str:
    """Infer the action Amber took based on measured power flows."""
    if abs(battery_power_kw) < _IDLE_THRESHOLD_KW:
        return "IDLE"

    if battery_power_kw > _CHARGE_THRESHOLD_KW:
        # Charging. Was it driven by solar surplus or grid import?
        if solar_kw > load_kw * _SOLAR_DOMINANCE:
            return "CHARGE_FROM_SOLAR"
        if load_kw > solar_kw:
            return "CHARGE_FROM_GRID"
        return "CHARGE_FROM_SOLAR"  # mixed but solar-leaning

    if battery_power_kw < _DISCHARGE_THRESHOLD_KW:
        if load_kw > solar_kw * _SOLAR_DOMINANCE:
            return "DISCHARGE_TO_LOAD"
        if solar_kw > load_kw:
            return "DISCHARGE_TO_GRID"
        return "DISCHARGE_TO_LOAD"  # default to serving load

    return "IDLE"


def _prepare_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Normalise prices into a DataFrame with timestamp, import_c_kwh, export_c_kwh."""
    if prices is None or prices.empty:
        return pd.DataFrame(columns=["timestamp", "import_c_kwh", "export_c_kwh"])
    df = prices.copy()
    if "timestamp" not in df.columns:
        raise ValueError("prices must have a 'timestamp' column")

    # Round to 5-min boundary so merge_asof tolerance is tight.
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["timestamp"] = df["timestamp"].dt.round(f"{INTERVAL_MIN}min")

    if "import_c_kwh" not in df.columns:
        if "rrp_c_kwh" in df.columns:
            df["import_c_kwh"] = df["rrp_c_kwh"]
        elif "rrp" in df.columns:
            # AEMO RRP is $/MWh -> divide by 10 for c/kWh
            df["import_c_kwh"] = df["rrp"] / 10.0
        else:
            raise ValueError(
                "prices must have import_c_kwh, rrp_c_kwh, or rrp column"
            )

    if "export_c_kwh" not in df.columns:
        # Default: export price == import price (reasonable for pure NEM pass-through)
        df["export_c_kwh"] = df["import_c_kwh"]

    return df[["timestamp", "import_c_kwh", "export_c_kwh"]].sort_values("timestamp")


def reconstruct_amber_actions(
    history: pd.DataFrame,
    prices: pd.DataFrame,
    battery: BatteryConstants | None = None,
) -> pd.DataFrame:
    """Reconstruct Amber's actual battery actions from HA history.

    Args:
        history: HA history with columns timestamp (UTC), load_kw, solar_kw,
            soc_pct, battery_power_kw.
        prices: Price data with timestamp and import_c_kwh / export_c_kwh
            (or rrp_c_kwh / rrp as fallback).
        battery: Optional battery constants (unused here but kept for
            signature consistency).

    Returns:
        DataFrame with columns timestamp, load_kw, solar_kw, soc_pct,
        battery_power_kw, action, grid_kw, cost_c, import_c_kwh, export_c_kwh.

        - grid_kw: positive = import, negative = export.
        - cost_c: signed cost in cents (positive = paid).
    """
    if history.empty:
        return pd.DataFrame(
            columns=[
                "timestamp",
                "load_kw",
                "solar_kw",
                "soc_pct",
                "battery_power_kw",
                "action",
                "grid_kw",
                "cost_c",
                "import_c_kwh",
                "export_c_kwh",
            ]
        )

    # Normalise history timestamps to 5-min boundaries so merge_asof matches cleanly.
    hist = history.copy()
    hist["timestamp"] = pd.to_datetime(hist["timestamp"], utc=True)
    hist = hist.sort_values("timestamp").reset_index(drop=True)

    price_df = _prepare_prices(prices)

    # merge_asof with 5-min tolerance — prices may be offset by ~1s.
    merged = pd.merge_asof(
        hist,
        price_df,
        on="timestamp",
        direction="nearest",
        tolerance=pd.Timedelta(minutes=INTERVAL_MIN),
    )

    # Fill missing prices with 0 so cost accounting doesn't blow up, but log loudly.
    n_missing = merged["import_c_kwh"].isna().sum()
    if n_missing:
        log.warning(
            "Amber replay: %d/%d intervals missing price data (filled with 0)",
            n_missing,
            len(merged),
        )
    merged["import_c_kwh"] = merged["import_c_kwh"].fillna(0.0)
    merged["export_c_kwh"] = merged["export_c_kwh"].fillna(0.0)
    # HA solar sensor returns NaN at night — treat as 0 production.
    merged["solar_kw"] = merged["solar_kw"].fillna(0.0)
    merged["load_kw"] = merged["load_kw"].fillna(0.0)
    merged["battery_power_kw"] = merged["battery_power_kw"].fillna(0.0)
    merged["soc_pct"] = merged["soc_pct"].ffill().fillna(50.0)

    # Classify actions.
    merged["action"] = [
        _classify_action(
            float(row.battery_power_kw),
            float(row.load_kw),
            float(row.solar_kw),
        )
        for row in merged.itertuples(index=False)
    ]

    # Grid flow: load + battery (charging pulls from grid/solar) - solar
    # Positive = importing, negative = exporting.
    merged["grid_kw"] = (
        merged["load_kw"] + merged["battery_power_kw"] - merged["solar_kw"]
    )

    # Cost: import at import price, export credits at export price.
    interval_h = INTERVAL_MIN / 60.0
    grid_kwh = merged["grid_kw"] * interval_h
    price = np.where(
        merged["grid_kw"] > 0,
        merged["import_c_kwh"],
        merged["export_c_kwh"],
    )
    merged["cost_c"] = grid_kwh * price

    return merged[
        [
            "timestamp",
            "load_kw",
            "solar_kw",
            "soc_pct",
            "battery_power_kw",
            "action",
            "grid_kw",
            "cost_c",
            "import_c_kwh",
            "export_c_kwh",
        ]
    ].reset_index(drop=True)


def compute_amber_cost(
    history: pd.DataFrame,
    prices: pd.DataFrame,
    use_import_export: bool = True,
) -> dict:
    """Compute what Amber's actual dispatch cost over the history period.

    Args:
        history: HA history (see `reconstruct_amber_actions`).
        prices: Price data.
        use_import_export: If True, apply separate import/export prices
            (sign of grid_kw picks). If False, use import price for everything.

    Returns:
        dict with keys:
            total_cost_dollars: net cost across the period (negative = revenue).
            total_import_kwh: sum of grid import energy.
            total_export_kwh: sum of grid export energy (positive number).
            total_cycles: estimated full-equivalent battery cycles.
            daily: DataFrame with date, cost, import, export columns.
    """
    actions = reconstruct_amber_actions(history, prices)
    if actions.empty:
        return {
            "total_cost_dollars": 0.0,
            "total_import_kwh": 0.0,
            "total_export_kwh": 0.0,
            "total_cycles": 0.0,
            "daily": pd.DataFrame(columns=["date", "cost", "import", "export"]),
        }

    interval_h = INTERVAL_MIN / 60.0
    grid_kwh = actions["grid_kw"] * interval_h

    if use_import_export:
        cost_c = actions["cost_c"].to_numpy()
    else:
        # Single-price mode: use import price regardless of sign.
        cost_c = (grid_kwh * actions["import_c_kwh"]).to_numpy()

    import_mask = actions["grid_kw"] > 0
    export_mask = actions["grid_kw"] < 0

    total_import_kwh = float(grid_kwh[import_mask].sum())
    total_export_kwh = float(-grid_kwh[export_mask].sum())  # report as positive

    # Cycles: sum of |delta SOC| / 2. SOC is in percent here.
    soc_frac = actions["soc_pct"].to_numpy() / 100.0
    delta_soc = np.abs(np.diff(soc_frac))
    total_cycles = float(delta_soc.sum() / 2.0)

    # Daily roll-up.
    df = actions.copy()
    df["cost_c"] = cost_c
    df["date"] = df["timestamp"].dt.tz_convert("UTC").dt.date
    df["import_kwh"] = np.where(df["grid_kw"] > 0, grid_kwh, 0.0)
    df["export_kwh"] = np.where(df["grid_kw"] < 0, -grid_kwh, 0.0)

    daily = (
        df.groupby("date")
        .agg(
            cost=("cost_c", lambda s: s.sum() / 100.0),
            **{"import": ("import_kwh", "sum")},
            export=("export_kwh", "sum"),
        )
        .reset_index()
    )

    return {
        "total_cost_dollars": float(cost_c.sum() / 100.0),
        "total_import_kwh": total_import_kwh,
        "total_export_kwh": total_export_kwh,
        "total_cycles": total_cycles,
        "daily": daily,
    }
