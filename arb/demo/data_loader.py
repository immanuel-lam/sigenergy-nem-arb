"""Data loaders for the Streamlit dashboard.

Thin wrappers around the ingest/forecast/scheduler modules with caching and
error handling so the UI never crashes on a bad API response or missing log.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
RATIONALE_LOG_PATH = REPO_ROOT / "agent_rationale.log"
ACTUATOR_AUDIT_PATH = REPO_ROOT / "actuator_audit.log"
SYDNEY_TZ = "Australia/Sydney"


@dataclass
class AgentCycle:
    """Result of one ingest/forecast/schedule/explain pass."""

    timestamp: datetime
    snapshot: Any
    forecast_df: pd.DataFrame
    plan: Any
    rationale: str
    error: str | None = None


# ---------------------------------------------------------------------------
# Live state
# ---------------------------------------------------------------------------


@st.cache_data(ttl=30, show_spinner=False)
def load_snapshot() -> tuple[Any, str | None]:
    """Pull a fresh snapshot. Returns (snapshot, error_message)."""
    try:
        from arb.ingest.snapshot import take_snapshot
        snap = take_snapshot()
        return snap, None
    except Exception as e:
        log.exception("snapshot failed")
        return None, str(e)


@st.cache_data(ttl=60, show_spinner=False)
def load_ha_history(days: int = 30) -> tuple[pd.DataFrame | None, str | None]:
    """Pull HA history for forecasting."""
    try:
        from arb.ingest import ha
        df = ha.fetch_history(days=days)
        return df, None
    except Exception as e:
        log.warning("HA history unavailable: %s", e)
        return None, str(e)


def run_agent_cycle() -> AgentCycle:
    """Run one full ingest -> forecast -> schedule -> explain cycle.

    Does NOT actuate. Safe to call from the UI.
    """
    try:
        from arb.agent.explain import explain_plan
        from arb.forecast.builder import build_forecast
        from arb.ingest.snapshot import take_snapshot
        from arb.scheduler.greedy import schedule

        snap = take_snapshot()
        history, _ = load_ha_history(30)
        forecast_df = build_forecast(snap, ha_history=history)
        soc_now = (snap.soc_pct or 50.0) / 100.0
        plan = schedule(forecast_df, soc_now)
        try:
            rationale = explain_plan(plan, snap, previous_plan=None)
        except Exception as e:
            log.warning("explain failed: %s", e)
            rationale = f"(rationale unavailable: {e})"
        return AgentCycle(
            timestamp=datetime.now(timezone.utc),
            snapshot=snap,
            forecast_df=forecast_df,
            plan=plan,
            rationale=rationale,
        )
    except Exception as e:
        log.exception("agent cycle failed")
        return AgentCycle(
            timestamp=datetime.now(timezone.utc),
            snapshot=None,
            forecast_df=pd.DataFrame(),
            plan=None,
            rationale="",
            error=str(e),
        )


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------


def load_rationale_log(limit: int = 10) -> pd.DataFrame:
    """Read last `limit` entries from agent_rationale.log."""
    if not RATIONALE_LOG_PATH.exists():
        return pd.DataFrame(columns=["timestamp", "action", "rationale"])

    rows: list[dict] = []
    try:
        with RATIONALE_LOG_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line:
                    continue
                parts = line.split("\t", 2)
                if len(parts) != 3:
                    continue
                rows.append({
                    "timestamp": parts[0],
                    "action": parts[1],
                    "rationale": parts[2],
                })
    except Exception as e:
        log.warning("Failed to read rationale log: %s", e)
        return pd.DataFrame(columns=["timestamp", "action", "rationale"])

    if not rows:
        return pd.DataFrame(columns=["timestamp", "action", "rationale"])

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp", ascending=False)
    return df.head(limit).reset_index(drop=True)


def load_actuator_audit(limit: int = 10) -> pd.DataFrame:
    """Read last `limit` JSON-line entries from actuator_audit.log."""
    if not ACTUATOR_AUDIT_PATH.exists():
        return pd.DataFrame()

    rows: list[dict] = []
    try:
        with ACTUATOR_AUDIT_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log.warning("Failed to read audit log: %s", e)
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.sort_values("timestamp", ascending=False)
    return df.head(limit).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------


def source_status(snapshot: Any) -> dict[str, tuple[str, str]]:
    """Classify each source as ok/warn/error with a short message.

    Returns {source: (status, message)} where status is one of ok, warn, error.
    """
    if snapshot is None:
        return {
            "HA": ("error", "no snapshot"),
            "Amber": ("error", "no snapshot"),
            "AEMO": ("error", "no snapshot"),
            "BOM": ("error", "no snapshot"),
            "Modbus": ("error", "no snapshot"),
        }

    status: dict[str, tuple[str, str]] = {}

    # HA — healthy when core sensors present
    ha_bad = [s for s in ("load_kw", "solar_kw", "soc_pct") if s in snapshot.stale_sensors]
    if ha_bad:
        status["HA"] = ("error", f"stale: {', '.join(ha_bad)}")
    elif snapshot.soc_pct is not None and snapshot.load_kw is not None:
        status["HA"] = ("ok", f"SOC {snapshot.soc_pct:.1f}%, load {snapshot.load_kw:.2f} kW")
    else:
        status["HA"] = ("warn", "partial data")

    # Prices — look at source of the DataFrame
    price_df = snapshot.price_forecast
    amber_warn = [w for w in snapshot.warnings if "Amber" in w]
    if price_df is not None and not price_df.empty and "import_c_kwh" in price_df.columns:
        status["Amber"] = ("ok", f"{len(price_df)} intervals")
        status["AEMO"] = ("ok", "amber in use")
    elif price_df is not None and not price_df.empty and "rrp_c_kwh" in price_df.columns:
        status["Amber"] = ("warn", "; ".join(amber_warn) or "fell back to AEMO")
        status["AEMO"] = ("ok", f"{len(price_df)} intervals")
    else:
        status["Amber"] = ("error", "; ".join(amber_warn) or "empty")
        status["AEMO"] = ("error", "no price data")

    # Weather
    w_df = snapshot.weather_forecast
    w_warn = [w for w in snapshot.warnings if "Weather" in w or "BOM" in w]
    if w_df is not None and not w_df.empty:
        status["BOM"] = ("ok", f"{len(w_df)} hours")
    else:
        status["BOM"] = ("error", "; ".join(w_warn) or "no weather data")

    # Modbus — we treat this as unknown unless actuator logged recently
    audit = load_actuator_audit(limit=1)
    if audit.empty:
        status["Modbus"] = ("warn", "no recent writes")
    else:
        last_ts = audit["timestamp"].iloc[0] if "timestamp" in audit.columns else None
        dry = bool(audit["dry_run"].iloc[0]) if "dry_run" in audit.columns else True
        label = "dry-run" if dry else "live"
        if last_ts is not None:
            delta = pd.Timestamp.now(tz="UTC") - pd.Timestamp(last_ts)
            mins = int(delta.total_seconds() / 60)
            status["Modbus"] = ("ok", f"{label}, {mins} min ago")
        else:
            status["Modbus"] = ("warn", "audit present but unreadable")

    return status


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------


@st.cache_data(ttl=3600, show_spinner=False)
def run_backtest_cached(days: int = 7) -> tuple[pd.DataFrame | None, str | None]:
    """Run the 4-strategy backtest. Returns (results_df, error_message).

    Cached for 1 hour because backtest is ~90s.
    """
    try:
        from datetime import timedelta

        from arb.eval.amber_replay import compute_amber_cost
        from arb.eval.backtest import run_backtest
        from arb.eval.baselines import self_consume_strategy, static_tou_strategy
        from arb.ingest import amber, ha
        from arb.scheduler.greedy import schedule

        end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(days=days)

        history = ha.fetch_history(days=days + 2, end=end)
        prices = amber.fetch_historical_prices(days=days + 1)
        if prices is None or prices.empty:
            prices = amber.fetch_prices()

        if history is None or history.empty or prices is None or prices.empty:
            return None, "insufficient history or price data"

        initial_soc = (
            history["soc_pct"].dropna().iloc[0] / 100.0
            if not history.empty and history["soc_pct"].dropna().size
            else 0.5
        )

        rows: list[dict] = []
        for name, strat in [
            ("Agent (greedy)", schedule),
            ("B1 self-consume", self_consume_strategy),
            ("B2 static TOU", static_tou_strategy),
        ]:
            r = run_backtest(
                history=history,
                prices=prices,
                start=start,
                end=end,
                strategy_fn=strat,
                initial_soc=initial_soc,
                strategy_name=name,
                perfect_foresight=True,
            )
            rows.append({
                "Strategy": name,
                "Cost $": round(r.total_cost_dollars, 2),
                "Import kWh": round(r.total_import_kwh, 1),
                "Export kWh": round(r.total_export_kwh, 1),
                "Cycles": round(r.total_charge_cycles, 2),
            })

        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC")
        hist_window = history[
            (history["timestamp"] >= start_ts) & (history["timestamp"] < end_ts)
        ]
        amber_cost = compute_amber_cost(hist_window, prices)
        rows.append({
            "Strategy": "B3 Amber actual",
            "Cost $": round(amber_cost["total_cost_dollars"], 2),
            "Import kWh": round(amber_cost["total_import_kwh"], 1),
            "Export kWh": round(amber_cost["total_export_kwh"], 1),
            "Cycles": round(amber_cost["total_cycles"], 2),
        })

        return pd.DataFrame(rows), None
    except Exception as e:
        log.exception("backtest failed")
        return None, str(e)


__all__ = [
    "AgentCycle",
    "SYDNEY_TZ",
    "load_snapshot",
    "load_ha_history",
    "run_agent_cycle",
    "load_rationale_log",
    "load_actuator_audit",
    "source_status",
    "run_backtest_cached",
]
