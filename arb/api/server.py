"""FastAPI server — read-only wrapper over the agent modules.

The web UI and the agent loop should see the same source of truth, so this
module only *reads* logs and the persisted plan pickle, and *invokes* the
same code paths the loop does (take_snapshot, build_forecast, schedule,
explain_plan). It never writes to the actuator.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from arb.agent import audit as audit_mod
from arb.agent import loop as loop_mod
from arb.agent.explain import explain_plan
from arb.agent.plan_diff import diff_plans, format_diff_for_llm
from arb.agent.spike_demo import run_spike_demo
from arb.forecast.builder import build_forecast
from arb.ingest import ha
from arb.ingest.snapshot import Snapshot, take_snapshot
from arb.scheduler.greedy import schedule
from arb.scheduler.plan import Action, Plan

log = logging.getLogger(__name__)

API_VERSION = "0.1.0"

app = FastAPI(title="Arbitrage Agent API", version=API_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _warmup_cache() -> None:
    """Prime the ingest cache in the background so /spike-demo is fast on first click."""

    async def _warm() -> None:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _prime_cache)
            log.info("startup cache warm complete")
        except Exception as e:  # noqa: BLE001
            log.warning("startup cache warm failed (non-fatal): %s", e)

    asyncio.create_task(_warm())


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _iso(ts: Any) -> str:
    """Normalise any timestamp-ish value to UTC ISO string."""
    t = pd.Timestamp(ts)
    if t.tz is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return t.isoformat()


def plan_to_dict(plan: Plan) -> dict:
    """Convert a Plan to a JSON-serialisable dict with native Python types."""
    return {
        "timestamps": [_iso(t) for t in plan.timestamps],
        "actions": [a.value if hasattr(a, "value") else str(a) for a in plan.actions],
        "soc": plan.soc.tolist(),
        "import_c_kwh": plan.import_c_kwh.tolist(),
        "export_c_kwh": plan.export_c_kwh.tolist(),
        "load_kw": plan.load_kw.tolist(),
        "solar_kw": plan.solar_kw.tolist(),
        "charge_grid_kwh": plan.charge_grid_kwh.tolist(),
        "discharge_grid_kwh": plan.discharge_grid_kwh.tolist(),
        "current_idx": plan.current_interval_idx,
        "summary": plan.summary(),
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
    }


def _price_forecast_summary(df: pd.DataFrame) -> dict:
    """Compact summary of the price forecast — full frame is too big."""
    if df is None or df.empty:
        return {"n": 0, "min_import_c": None, "max_import_c": None, "first_ts": None, "last_ts": None}
    col = "import_c_kwh" if "import_c_kwh" in df.columns else ("rrp_c_kwh" if "rrp_c_kwh" in df.columns else None)
    out: dict = {"n": int(len(df))}
    if col is not None:
        out["min_import_c"] = float(df[col].min())
        out["max_import_c"] = float(df[col].max())
        out["mean_import_c"] = float(df[col].mean())
        out["price_column"] = col
    if "timestamp" in df.columns and len(df):
        out["first_ts"] = _iso(df["timestamp"].iloc[0])
        out["last_ts"] = _iso(df["timestamp"].iloc[-1])
    return out


def snapshot_to_dict(snap: Snapshot) -> dict:
    return {
        "timestamp": snap.timestamp.astimezone(timezone.utc).isoformat(),
        "soc_pct": snap.soc_pct,
        "load_kw": snap.load_kw,
        "solar_kw": snap.solar_kw,
        "battery_power_kw": snap.battery_power_kw,
        "price_forecast": _price_forecast_summary(snap.price_forecast),
        "stale_sensors": list(snap.stale_sensors),
        "warnings": list(snap.warnings),
        "weather_n": int(len(snap.weather_forecast)) if snap.weather_forecast is not None else 0,
    }


def take_snapshot_safe() -> dict:
    """Wrapper that never raises — returns a minimal error payload on failure."""
    try:
        return snapshot_to_dict(take_snapshot())
    except Exception as e:  # noqa: BLE001
        log.error("take_snapshot failed: %s", e)
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error": str(e),
            "stale_sensors": ["all"],
            "warnings": [f"snapshot failed: {e}"],
        }


# ---------------------------------------------------------------------------
# Log readers
# ---------------------------------------------------------------------------


def _read_rationale(limit: int) -> list[dict]:
    path = loop_mod.RATIONALE_LOG
    if not Path(path).exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    out: list[dict] = []
    for line in lines[-limit:]:
        line = line.rstrip("\n")
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) == 3:
            out.append({"timestamp": parts[0], "action": parts[1], "rationale": parts[2]})
        else:
            out.append({"timestamp": None, "action": None, "rationale": line})
    return out


def _read_spike_events(limit: int) -> list[dict]:
    path = loop_mod.SPIKE_LOG
    if not Path(path).exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f.readlines() if ln.strip()]
    out: list[dict] = []
    for line in lines[-limit:]:
        # Spike log is formatted text from format_spike_for_log; expose as-is.
        out.append({"raw": line})
    return out


def _read_audit(limit: int) -> dict:
    entries = audit_mod.read_audit_log(limit)
    summary = audit_mod.summarize_recent_audits(limit)
    serialised: list[dict] = []
    for e in entries:
        d = asdict(e)
        if e.timestamp is not None:
            d["timestamp"] = e.timestamp.isoformat()
        if e.plan_created_at is not None:
            d["plan_created_at"] = e.plan_created_at.isoformat()
        serialised.append(d)
    return {"entries": serialised, "summary": summary}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"ok": True, "version": API_VERSION}


@app.get("/snapshot")
def get_snapshot() -> dict:
    return take_snapshot_safe()


@app.get("/plan/current")
def plan_current() -> dict:
    """Most recent persisted plan, or build one fresh if none exists."""
    plan = loop_mod._load_previous_plan()
    if plan is None:
        # No cached plan — build one now so the UI has something to show.
        try:
            snap = take_snapshot()
            try:
                history = ha.fetch_history(days=14)
            except Exception as e:  # noqa: BLE001
                log.warning("HA history fetch failed: %s", e)
                history = None
            forecast_df = build_forecast(snap, ha_history=history)
            soc_now = (snap.soc_pct or 50.0) / 100.0
            plan = schedule(forecast_df, soc_now)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"failed to build plan: {e}")
    return plan_to_dict(plan)


# TTL cache for the expensive ingest calls. /plan/refresh warms it; /spike-demo
# reuses it so the demo button doesn't pay a fresh AEMO + 14-day HA fetch.
_INGEST_CACHE: dict[str, Any] = {"snapshot": None, "history": None, "at": None}
_INGEST_TTL_SEC = 120


def _cache_fresh() -> bool:
    at = _INGEST_CACHE["at"]
    if at is None:
        return False
    return (datetime.now(timezone.utc) - at).total_seconds() < _INGEST_TTL_SEC


def _prime_cache() -> tuple[Snapshot, pd.DataFrame | None]:
    """Return (snapshot, history), reusing cached values if fresh."""
    if _cache_fresh():
        return _INGEST_CACHE["snapshot"], _INGEST_CACHE["history"]
    snap = take_snapshot()
    try:
        hist = ha.fetch_history(days=14)
    except Exception as e:  # noqa: BLE001
        log.warning("HA history fetch failed: %s", e)
        hist = None
    _INGEST_CACHE["snapshot"] = snap
    _INGEST_CACHE["history"] = hist
    _INGEST_CACHE["at"] = datetime.now(timezone.utc)
    return snap, hist


@app.post("/plan/refresh")
def plan_refresh() -> dict:
    """Run the read-only critical path: ingest, forecast, schedule, explain."""
    try:
        snap, history = _prime_cache()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"snapshot failed: {e}")

    previous = loop_mod._load_previous_plan()
    forecast_df = build_forecast(snap, ha_history=history)
    soc_now = (snap.soc_pct or 50.0) / 100.0
    plan = schedule(forecast_df, soc_now)

    # Diff and rationale — same surface the loop writes to the rationale log.
    plan_diff = diff_plans(plan, previous)
    rationale = explain_plan(plan, snap, previous_plan=previous)

    # Persist so subsequent /plan/current calls see the fresh plan, matching
    # the loop's behaviour.
    loop_mod._save_plan(plan)

    out = plan_to_dict(plan)
    out["rationale"] = rationale
    try:
        out["diff"] = {"summary": format_diff_for_llm(plan_diff)}
    except Exception:  # noqa: BLE001
        out["diff"] = {"summary": ""}
    return out


@app.get("/rationale")
def rationale(limit: int = Query(20, ge=1, le=500)) -> list[dict]:
    return _read_rationale(limit)


@app.get("/audit")
def audit_endpoint(limit: int = Query(20, ge=1, le=500)) -> dict:
    return _read_audit(limit)


@app.get("/spike-events")
def spike_events(limit: int = Query(20, ge=1, le=500)) -> list[dict]:
    return _read_spike_events(limit)


# --- Backtest (cached) -----------------------------------------------------


_BACKTEST_CACHE: dict = {"data": None, "computed_at": None}
_BACKTEST_TTL_SEC = 3600


def _run_backtest_7d() -> dict:
    """Run the 7-day backtest and return a JSON-shaped summary.

    Mirrors arb.eval.run_backtest.main but returns rather than prints.
    """
    from arb.eval.amber_replay import compute_amber_cost
    from arb.eval.backtest import run_backtest
    from arb.eval.baselines import self_consume_strategy, static_tou_strategy
    from arb.ingest import amber as amber_ingest

    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=7)

    history = ha.fetch_history(days=9, end=end)
    prices = amber_ingest.fetch_historical_prices(days=8)
    if prices.empty:
        prices = amber_ingest.fetch_prices()

    initial_soc = (
        float(history["soc_pct"].dropna().iloc[0]) / 100.0
        if not history.empty and "soc_pct" in history.columns and not history["soc_pct"].dropna().empty
        else 0.5
    )

    def _pack(r) -> dict:
        return {
            "cost_dollars": float(r.total_cost_dollars),
            "import_kwh": float(r.total_import_kwh),
            "export_kwh": float(r.total_export_kwh),
            "cycles": float(r.total_charge_cycles),
        }

    results: dict[str, dict] = {}
    for name, strat in (
        ("agent", schedule),
        ("b1_self_consume", self_consume_strategy),
        ("b2_static_tou", static_tou_strategy),
    ):
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
        results[name] = _pack(r)

    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    hist_window = history[(history["timestamp"] >= start_ts) & (history["timestamp"] < end_ts)]
    amber_cost = compute_amber_cost(hist_window, prices)
    results["b3_amber_actual"] = {
        "cost_dollars": float(amber_cost["total_cost_dollars"]),
        "import_kwh": float(amber_cost["total_import_kwh"]),
        "export_kwh": float(amber_cost["total_export_kwh"]),
        "cycles": float(amber_cost["total_cycles"]),
    }

    return {
        "agent": results["agent"],
        "b1_self_consume": results["b1_self_consume"],
        "b2_static_tou": results["b2_static_tou"],
        "b3_amber_actual": results["b3_amber_actual"],
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": 7,
        },
    }


@app.get("/backtest/latest")
def backtest_latest(refresh: bool = Query(False)) -> dict:
    now = datetime.now(timezone.utc)
    cached = _BACKTEST_CACHE["data"]
    computed_at = _BACKTEST_CACHE["computed_at"]
    stale = (
        cached is None
        or computed_at is None
        or (now - computed_at).total_seconds() > _BACKTEST_TTL_SEC
    )
    if refresh or stale:
        try:
            data = _run_backtest_7d()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"backtest failed: {e}")
        _BACKTEST_CACHE["data"] = data
        _BACKTEST_CACHE["computed_at"] = now
        return {**data, "computed_at": now.isoformat(), "cached": False}
    return {**cached, "computed_at": computed_at.isoformat(), "cached": True}


# --- Spike demo ------------------------------------------------------------


class SpikeDemoRequest(BaseModel):
    magnitude_c_kwh: float = Field(default=120.0)
    minutes_ahead: int = Field(default=10, ge=0)
    duration_min: int = Field(default=15, ge=1)
    channel: str = Field(default="export")
    use_llm: bool = Field(default=False)


@app.post("/spike-demo")
def spike_demo(req: SpikeDemoRequest) -> dict:
    if req.channel not in ("import", "export"):
        raise HTTPException(status_code=400, detail="channel must be 'import' or 'export'")

    # Reuse the cached snapshot/history if /plan/refresh primed it recently.
    cached_snap, cached_hist = (None, None)
    if _cache_fresh():
        cached_snap = _INGEST_CACHE["snapshot"]
        cached_hist = _INGEST_CACHE["history"]

    try:
        result = run_spike_demo(
            magnitude_c_kwh=req.magnitude_c_kwh,
            minutes_ahead=req.minutes_ahead,
            duration_min=req.duration_min,
            channel=req.channel,  # type: ignore[arg-type]
            skip_llm=not req.use_llm,
            snapshot=cached_snap,
            history=cached_hist,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"spike demo failed: {e}")

    return {
        "baseline_plan": plan_to_dict(result.baseline_plan),
        "spiked_plan": plan_to_dict(result.spiked_plan),
        "diff_summary": result.diff_summary,
        "baseline_rationale": result.baseline_rationale,
        "spiked_rationale": result.spiked_rationale,
        "action_changed": bool(result.action_changed),
        "spike_start": result.spike_start.isoformat(),
        "spike_end": result.spike_end.isoformat(),
        "spike_c_kwh": float(result.spike_c_kwh),
        "channel": req.channel,
    }


# --- WebSocket -------------------------------------------------------------


active_ws: set[WebSocket] = set()
WS_TICK_SEC = int(os.getenv("ARB_WS_TICK_SEC", "30"))


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    active_ws.add(ws)
    try:
        # Send one snapshot immediately on connect.
        await ws.send_json({"type": "tick", "snapshot": take_snapshot_safe()})
        while True:
            await asyncio.sleep(WS_TICK_SEC)
            try:
                snap = take_snapshot_safe()
                await ws.send_json({"type": "tick", "snapshot": snap})
            except WebSocketDisconnect:
                break
            except Exception as e:  # noqa: BLE001
                log.warning("ws send failed: %s", e)
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.error("ws loop crashed: %s", e)
    finally:
        active_ws.discard(ws)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
