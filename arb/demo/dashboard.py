"""Streamlit dashboard for the Sigenergy NEM arbitrage agent.

Run with: streamlit run arb/demo/dashboard.py

Reads live snapshot + plan, the rationale log, and the actuator audit log.
Can trigger a fresh agent cycle and a 7-day backtest on demand.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from arb.demo.data_loader import (
    SYDNEY_TZ,
    load_actuator_audit,
    load_rationale_log,
    load_snapshot,
    run_agent_cycle,
    run_backtest_cached,
    source_status,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

st.set_page_config(
    page_title="Sigenergy NEM Arbitrage Agent",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes")

# Action colors — consistent across charts
ACTION_COLORS = {
    "IDLE": "#808080",
    "CHARGE_GRID": "#2ca02c",
    "DISCHARGE_GRID": "#d62728",
    "HOLD_SOLAR": "#1f77b4",
}


def _fmt_local(ts: datetime | pd.Timestamp | None) -> str:
    """Format a UTC timestamp as Sydney local time."""
    if ts is None:
        return "n/a"
    t = pd.Timestamp(ts)
    if t.tz is None:
        t = t.tz_localize("UTC")
    return t.tz_convert(SYDNEY_TZ).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Session state for last cycle result
# ---------------------------------------------------------------------------

if "last_cycle" not in st.session_state:
    st.session_state.last_cycle = None


# ---------------------------------------------------------------------------
# Panel 1: Header
# ---------------------------------------------------------------------------

st.title("Sigenergy NEM Arbitrage Agent")

header_cols = st.columns([2, 2, 1, 1])

snap, snap_err = load_snapshot()

with header_cols[0]:
    now_local = pd.Timestamp.now(tz=SYDNEY_TZ).strftime("%Y-%m-%d %H:%M:%S")
    st.caption("Now (Sydney)")
    st.markdown(f"**{now_local}**")

with header_cols[1]:
    last_plan = st.session_state.last_cycle
    last_ts = last_plan.timestamp if last_plan else None
    st.caption("Last plan")
    st.markdown(f"**{_fmt_local(last_ts) if last_ts else 'not run yet'}**")

with header_cols[2]:
    st.caption("Mode")
    tag = "DRY RUN" if DRY_RUN else "LIVE"
    color = "#888" if DRY_RUN else "#d62728"
    st.markdown(f"<span style='color:{color};font-weight:600'>{tag}</span>", unsafe_allow_html=True)

with header_cols[3]:
    if st.button("Run agent now", type="primary", use_container_width=True):
        with st.spinner("Ingest, forecast, schedule, explain..."):
            cycle = run_agent_cycle()
        st.session_state.last_cycle = cycle
        if cycle.error:
            st.error(f"Agent cycle failed: {cycle.error}")
        else:
            st.success("Cycle complete.")
        st.rerun()

if snap_err:
    st.error(f"Snapshot failed: {snap_err}")

# ---------------------------------------------------------------------------
# Panel 6: Data quality (top — so warnings are visible immediately)
# ---------------------------------------------------------------------------

st.subheader("Data sources")

status_map = source_status(snap)
status_cols = st.columns(len(status_map))
status_dot = {"ok": "", "warn": "[warn]", "error": "[error]"}
status_color = {"ok": "#2ca02c", "warn": "#e8a100", "error": "#d62728"}

for i, (name, (state, msg)) in enumerate(status_map.items()):
    with status_cols[i]:
        color = status_color.get(state, "#888")
        st.markdown(
            f"<div style='padding:6px 10px;border-left:4px solid {color};"
            f"background:#111;border-radius:4px'>"
            f"<div style='font-weight:600'>{name}</div>"
            f"<div style='font-size:12px;color:#bbb'>{msg}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

warn_states = [name for name, (state, _) in status_map.items() if state != "ok"]
if warn_states:
    st.warning(f"Degraded: {', '.join(warn_states)}. Agent will continue but decisions may be weaker.")

# ---------------------------------------------------------------------------
# Panel 2: Current state
# ---------------------------------------------------------------------------

st.subheader("Current state")

state_cols = st.columns(3)

# Column 1: SOC gauge
with state_cols[0]:
    soc_value = snap.soc_pct if snap and snap.soc_pct is not None else None
    gauge = go.Figure(go.Indicator(
        mode="gauge+number",
        value=soc_value if soc_value is not None else 0,
        number={"suffix": "%", "font": {"size": 36}},
        title={"text": "Battery SOC"},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": "#1f77b4"},
            "steps": [
                {"range": [0, 10], "color": "#3a1515"},     # below floor
                {"range": [10, 95], "color": "#1a1a1a"},
                {"range": [95, 100], "color": "#3a3315"},   # above ceiling
            ],
            "threshold": {
                "line": {"color": "#d62728", "width": 3},
                "thickness": 0.75,
                "value": soc_value if soc_value is not None else 0,
            },
        },
    ))
    gauge.update_layout(height=240, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(gauge, use_container_width=True)
    st.caption("Floor 10%, ceiling 95%")

# Column 2: Live load/solar/price
with state_cols[1]:
    st.markdown("**Power now**")
    load_kw = snap.load_kw if snap else None
    solar_kw = snap.solar_kw if snap else None
    batt_kw = snap.battery_power_kw if snap else None

    st.metric("Load", f"{load_kw:.2f} kW" if load_kw is not None else "n/a")
    st.metric("Solar", f"{solar_kw:.2f} kW" if solar_kw is not None else "n/a")
    st.metric("Battery", f"{batt_kw:+.2f} kW" if batt_kw is not None else "n/a",
              help="positive = charging, negative = discharging")

    # Current import price from price_forecast (first row)
    price_now = None
    try:
        if snap and snap.price_forecast is not None and not snap.price_forecast.empty:
            pdf = snap.price_forecast.copy()
            if "import_c_kwh" in pdf.columns:
                price_now = float(pdf["import_c_kwh"].iloc[0])
            elif "rrp_c_kwh" in pdf.columns:
                price_now = float(pdf["rrp_c_kwh"].iloc[0])
    except Exception:
        price_now = None
    st.metric("Import price", f"{price_now:.2f} c/kWh" if price_now is not None else "n/a")

# Column 3: Current plan action + rationale
with state_cols[2]:
    st.markdown("**Current action**")
    cycle = st.session_state.last_cycle
    if cycle is None or cycle.plan is None:
        st.info("No plan yet. Click 'Run agent now' to generate one.")
    else:
        plan = cycle.plan
        idx = plan.current_interval_idx
        if idx is None:
            st.warning("Current time is outside the plan horizon.")
        else:
            action = plan.actions[idx]
            action_str = action.value if hasattr(action, "value") else str(action)
            color = ACTION_COLORS.get(action_str, "#888")
            st.markdown(
                f"<div style='font-size:22px;font-weight:700;color:{color}'>"
                f"{action_str.replace('_', ' ')}</div>",
                unsafe_allow_html=True,
            )
            charge = float(plan.charge_grid_kwh[idx]) / plan.interval_h
            discharge = float(plan.discharge_grid_kwh[idx]) / plan.interval_h
            if charge > 0.01:
                st.caption(f"Charging {charge:.1f} kW from grid")
            elif discharge > 0.01:
                st.caption(f"Discharging {discharge:.1f} kW to grid")
            else:
                st.caption("No grid-side command")
        st.markdown("**Rationale**")
        st.write(cycle.rationale or "(none)")

# ---------------------------------------------------------------------------
# Panel 3: Price + SOC chart
# ---------------------------------------------------------------------------

st.subheader("24h horizon")

cycle = st.session_state.last_cycle
if cycle is None or cycle.plan is None:
    st.info("Run the agent to populate this chart.")
else:
    plan = cycle.plan
    df = plan.to_dataframe()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["ts_local"] = df["timestamp"].dt.tz_convert(SYDNEY_TZ)

    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.35, 0.35, 0.3],
        vertical_spacing=0.04,
        subplot_titles=("Price (c/kWh)", "Planned SOC", "Load and solar (kW)"),
    )

    # --- Row 1: prices ---
    fig.add_trace(
        go.Scatter(x=df["ts_local"], y=df["import_c_kwh"],
                   name="Import", line=dict(color="#d62728", width=1.5)),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=df["ts_local"], y=df["export_c_kwh"],
                   name="Export", line=dict(color="#2ca02c", width=1.5)),
        row=1, col=1,
    )
    # Shade where export < 0
    neg_export = df["export_c_kwh"].clip(upper=0)
    fig.add_trace(
        go.Scatter(
            x=df["ts_local"], y=neg_export,
            fill="tozeroy", name="Negative export",
            line=dict(color="rgba(214, 39, 40, 0.0)"),
            fillcolor="rgba(214, 39, 40, 0.25)",
            showlegend=False, hoverinfo="skip",
        ),
        row=1, col=1,
    )

    # --- Row 2: SOC with colored action regions ---
    # Build action bands (contiguous runs of the same action)
    soc_after = df["soc_after"].values * 100
    actions = df["action"].values
    ts_local = df["ts_local"].values

    # Find contiguous runs
    run_start = 0
    shapes = []
    for i in range(1, len(actions) + 1):
        if i == len(actions) or actions[i] != actions[run_start]:
            act = actions[run_start]
            color = ACTION_COLORS.get(act, "#888")
            shapes.append(dict(
                type="rect",
                xref="x2", yref="y2",
                x0=ts_local[run_start],
                x1=ts_local[min(i, len(ts_local) - 1)],
                y0=0, y1=100,
                fillcolor=color,
                opacity=0.12,
                line=dict(width=0),
                layer="below",
            ))
            run_start = i

    fig.add_trace(
        go.Scatter(
            x=df["ts_local"], y=soc_after,
            name="SOC %", line=dict(color="#ffffff", width=2),
        ),
        row=2, col=1,
    )
    # Floor / ceiling lines
    fig.add_hline(y=10, line_dash="dash", line_color="#d62728", row=2, col=1)
    fig.add_hline(y=95, line_dash="dash", line_color="#e8a100", row=2, col=1)

    # --- Row 3: load + solar ---
    fig.add_trace(
        go.Scatter(
            x=df["ts_local"], y=df["solar_kw"],
            name="Solar", line=dict(color="#ffb20f", width=1.5),
            fill="tozeroy", fillcolor="rgba(255, 178, 15, 0.25)",
        ),
        row=3, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df["ts_local"], y=df["load_kw"],
            name="Load", line=dict(color="#6baed6", width=1.5),
        ),
        row=3, col=1,
    )

    # Apply action shapes
    fig.update_layout(shapes=shapes)

    fig.update_layout(
        height=650,
        margin=dict(l=40, r=20, t=40, b=30),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="left", x=0),
    )
    fig.update_yaxes(title_text="c/kWh", row=1, col=1)
    fig.update_yaxes(title_text="SOC %", range=[0, 100], row=2, col=1)
    fig.update_yaxes(title_text="kW", row=3, col=1)

    st.plotly_chart(fig, use_container_width=True)

    # Action legend
    legend_cols = st.columns(4)
    for i, (act, col) in enumerate(ACTION_COLORS.items()):
        with legend_cols[i]:
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:6px'>"
                f"<div style='width:14px;height:14px;background:{col};opacity:0.6'></div>"
                f"<span style='font-size:12px'>{act.replace('_',' ')}</span></div>",
                unsafe_allow_html=True,
            )

# ---------------------------------------------------------------------------
# Panel 4: Backtest
# ---------------------------------------------------------------------------

st.subheader("Backtest (7 days)")

bt_cols = st.columns([1, 4])
with bt_cols[0]:
    if st.button("Re-run backtest", help="Takes ~90s. Cached for 1h."):
        run_backtest_cached.clear()
        st.rerun()

with bt_cols[1]:
    st.caption("Perfect-foresight upper bound. Agent uses the greedy scheduler against actual Amber prices.")

with st.spinner("Replaying history..."):
    bt_df, bt_err = run_backtest_cached(days=7)

if bt_err:
    st.error(f"Backtest failed: {bt_err}")
elif bt_df is None or bt_df.empty:
    st.info("Click 'Re-run backtest' to generate results.")
else:
    # Highlight agent row
    def _style(row: pd.Series) -> list[str]:
        if "Agent" in row["Strategy"]:
            return ["background-color: #1a2b1a"] * len(row)
        return [""] * len(row)

    st.dataframe(
        bt_df.style.apply(_style, axis=1),
        use_container_width=True,
        hide_index=True,
    )

    # Quick uplift callouts
    try:
        agent_cost = bt_df[bt_df["Strategy"].str.contains("Agent")]["Cost $"].iloc[0]
        rows = []
        for label in ("B1 self-consume", "B2 static TOU", "B3 Amber actual"):
            m = bt_df[bt_df["Strategy"] == label]
            if not m.empty:
                diff = m["Cost $"].iloc[0] - agent_cost
                rows.append((label, diff))
        uplift_cols = st.columns(len(rows))
        for i, (label, diff) in enumerate(rows):
            with uplift_cols[i]:
                st.metric(f"vs {label}", f"${diff:+.2f}", f"${diff/7:+.2f}/day")
    except Exception as e:
        log.warning("uplift calc failed: %s", e)

# ---------------------------------------------------------------------------
# Panel 5: Rationale log
# ---------------------------------------------------------------------------

st.subheader("Recent rationales")

rationale_df = load_rationale_log(limit=10)
if rationale_df.empty:
    st.info("No plans yet — click 'Run agent now'.")
else:
    rationale_df = rationale_df.copy()
    rationale_df["time"] = rationale_df["timestamp"].dt.tz_convert(SYDNEY_TZ).dt.strftime("%m-%d %H:%M")

    for _, row in rationale_df.iterrows():
        act = row["action"]
        color = ACTION_COLORS.get(act, "#888")
        text = row["rationale"] or ""
        short = text[:200] + ("..." if len(text) > 200 else "")
        with st.container():
            cols = st.columns([1, 1, 6])
            with cols[0]:
                st.markdown(f"`{row['time']}`")
            with cols[1]:
                st.markdown(
                    f"<span style='color:{color};font-weight:600'>{act}</span>",
                    unsafe_allow_html=True,
                )
            with cols[2]:
                if len(text) > 200:
                    with st.expander(short):
                        st.write(text)
                else:
                    st.write(short)

# ---------------------------------------------------------------------------
# Panel 7: Actuator audit
# ---------------------------------------------------------------------------

st.subheader("Actuator writes")

audit_df = load_actuator_audit(limit=10)
if audit_df.empty:
    st.info("No writes logged yet.")
else:
    display = audit_df.copy()
    if "timestamp" in display.columns:
        display["time"] = display["timestamp"].dt.tz_convert(SYDNEY_TZ).dt.strftime("%m-%d %H:%M:%S")
    cols = [c for c in ["time", "action", "entity", "value", "reason", "dry_run"] if c in display.columns]
    st.dataframe(display[cols], use_container_width=True, hide_index=True)

st.caption(f"Agent loop period {30} min. Reading from {Path('agent_rationale.log').resolve()}.")
