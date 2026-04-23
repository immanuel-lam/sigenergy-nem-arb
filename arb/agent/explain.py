"""LLM rationale for the scheduler's current decision.

Produces a short, plain-English explanation of what the agent is doing and
why. Falls back to a templated string when the Anthropic API is unavailable,
so the agent loop never crashes on a missing key or network blip.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd

from arb.ingest.snapshot import Snapshot
from arb.scheduler.plan import Action, Plan

log = logging.getLogger(__name__)

# Claude Opus 4.7 — the model we're submitting to the hackathon against.
DEFAULT_MODEL = "claude-opus-4-7"

SYSTEM_PROMPT = (
    "You're helping Immanuel understand what his battery arbitrage agent just "
    "decided. Write exactly two sentences. First sentence: what the agent is "
    "doing right now and for how long. Second sentence: why — cite specific "
    "numbers (price c/kWh, SOC%, forecast conditions). "
    "No greetings. No sign-off. No 'the agent' framing — speak directly: "
    "\"Charging because...\". Australian tone, tired engineer explaining it "
    "for the third time, not a copywriter. "
    "If nothing material changed from the previous plan, say \"no material change.\""
)


def _action_phrase(action: Action | str) -> str:
    """Human-readable phrase for an Action."""
    a = action.value if isinstance(action, Action) else str(action)
    return {
        "CHARGE_GRID": "charging from grid",
        "DISCHARGE_GRID": "discharging to grid",
        "HOLD_SOLAR": "diverting solar into the battery",
        "IDLE": "idle (self-consume)",
    }.get(a, a.lower())


def summarize_plan_changes(plan: Plan, previous: Plan | None) -> dict:
    """Structured diff for the current interval plus a 6h outlook.

    The LLM prompt is built from this dict, and the fallback template reads
    the same fields. Keeping both paths honest.
    """
    idx = plan.current_interval_idx
    # If current time is outside the plan horizon, point at interval 0 so we
    # still have something to describe.
    if idx is None:
        idx = 0

    interval_h = plan.interval_h
    charge_kw = float(plan.charge_grid_kwh[idx]) / interval_h
    discharge_kw = float(plan.discharge_grid_kwh[idx]) / interval_h

    action = plan.actions[idx]
    action_str = action.value if isinstance(action, Action) else str(action)

    ts = plan.timestamps[idx]
    # timestamps is numpy datetime64; convert for display
    ts_str = pd.Timestamp(ts).isoformat()

    current = {
        "timestamp": ts_str,
        "action": action_str,
        "charge_kw": charge_kw,
        "discharge_kw": discharge_kw,
        "import_c": float(plan.import_c_kwh[idx]),
        "export_c": float(plan.export_c_kwh[idx]),
        "soc_before": float(plan.soc[idx]),
        "soc_after": float(plan.soc[idx + 1]),
    }

    changed = False
    previous_action: str | None = None
    if previous is not None:
        prev_idx = previous.current_interval_idx
        if prev_idx is None:
            prev_idx = 0
        if prev_idx < len(previous.actions):
            prev_act = previous.actions[prev_idx]
            previous_action = prev_act.value if isinstance(prev_act, Action) else str(prev_act)
            changed = previous_action != action_str

    # Next 6h = next 72 five-minute intervals
    horizon_end = min(idx + 72, plan.n)
    window_actions = plan.actions[idx:horizon_end]
    charge_intervals = sum(
        1 for a in window_actions
        if (a.value if isinstance(a, Action) else a) == "CHARGE_GRID"
    )
    discharge_intervals = sum(
        1 for a in window_actions
        if (a.value if isinstance(a, Action) else a) == "DISCHARGE_GRID"
    )
    hold_solar_intervals = sum(
        1 for a in window_actions
        if (a.value if isinstance(a, Action) else a) == "HOLD_SOLAR"
    )
    idle_intervals = sum(
        1 for a in window_actions
        if (a.value if isinstance(a, Action) else a) == "IDLE"
    )

    window_import = plan.import_c_kwh[idx:horizon_end]
    window_export = plan.export_c_kwh[idx:horizon_end]
    peak_import = float(window_import.max()) if len(window_import) else 0.0
    min_export = float(window_export.min()) if len(window_export) else 0.0

    return {
        "current_interval": current,
        "changed_from_previous": changed,
        "previous_action": previous_action,
        "next_6h_summary": {
            "charge_intervals": int(charge_intervals),
            "discharge_intervals": int(discharge_intervals),
            "hold_solar_intervals": int(hold_solar_intervals),
            "idle_intervals": int(idle_intervals),
            "peak_import_price_c": peak_import,
            "min_export_price_c": min_export,
        },
    }


def _fallback_rationale(diff: dict, snapshot: Snapshot) -> str:
    """Templated explanation when the LLM is unavailable."""
    cur = diff["current_interval"]
    action = cur["action"]
    soc_pct = cur["soc_after"] * 100
    imp = cur["import_c"]
    exp = cur["export_c"]

    if action == "CHARGE_GRID":
        verb = f"Charging at {cur['charge_kw']:.1f} kW"
    elif action == "DISCHARGE_GRID":
        verb = f"Discharging at {cur['discharge_kw']:.1f} kW"
    elif action == "HOLD_SOLAR":
        verb = "Holding solar into the battery"
    else:
        verb = "Idle (self-consume)"

    return (
        f"{verb}, targeting {soc_pct:.0f}% SOC. "
        f"Import {imp:.1f} c/kWh, export {exp:.1f} c/kWh."
    )


def _build_user_prompt(diff: dict, snapshot: Snapshot, first_look: bool) -> str:
    """Stitch the structured diff into prompt text for Claude."""
    cur = diff["current_interval"]
    nxt = diff["next_6h_summary"]

    action_phrase = _action_phrase(cur["action"])
    lines = [
        f"Current interval starts {cur['timestamp']}.",
        f"Decision: {action_phrase}.",
        f"Charge power this interval: {cur['charge_kw']:.2f} kW.",
        f"Discharge power this interval: {cur['discharge_kw']:.2f} kW.",
        f"Import price: {cur['import_c']:.2f} c/kWh. Export price: {cur['export_c']:.2f} c/kWh.",
        f"SOC: {cur['soc_before']*100:.1f}% -> {cur['soc_after']*100:.1f}%.",
        "",
        "Next 6 hours outlook:",
        f"  Charge intervals: {nxt['charge_intervals']}",
        f"  Discharge intervals: {nxt['discharge_intervals']}",
        f"  Hold-solar intervals: {nxt['hold_solar_intervals']}",
        f"  Idle intervals: {nxt['idle_intervals']}",
        f"  Peak import price ahead: {nxt['peak_import_price_c']:.2f} c/kWh",
        f"  Minimum export price ahead: {nxt['min_export_price_c']:.2f} c/kWh",
        "",
        f"Live sensor SOC: {snapshot.soc_pct}%"
        if snapshot.soc_pct is not None else "Live sensor SOC: unknown",
        f"Live load: {snapshot.load_kw} kW. Live solar: {snapshot.solar_kw} kW.",
    ]

    if snapshot.stale_sensors:
        lines.append(f"Stale sensors: {', '.join(snapshot.stale_sensors)}.")
    if snapshot.warnings:
        lines.append(f"Warnings: {'; '.join(snapshot.warnings)}.")

    if first_look:
        lines.append("")
        lines.append("No previous plan to compare against — this is a first look.")
    else:
        lines.append("")
        lines.append(
            f"Previous plan action for this interval: {diff['previous_action']}. "
            f"Changed: {diff['changed_from_previous']}."
        )

    lines.append("")
    lines.append("Write exactly two sentences explaining what and why.")
    return "\n".join(lines)


def explain_plan(
    plan: Plan,
    snapshot: Snapshot,
    previous_plan: Plan | None = None,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
) -> str:
    """Generate a 2-sentence rationale for the current interval's action.

    Returns the explanation string. Falls back to a templated description
    when ANTHROPIC_API_KEY is missing or the API call fails — never raises.
    """
    diff = summarize_plan_changes(plan, previous_plan)

    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        log.warning("ANTHROPIC_API_KEY not set, using templated fallback")
        return _fallback_rationale(diff, snapshot)

    # Determine whether we have a meaningful previous plan for this interval
    first_look = previous_plan is None or diff["previous_action"] is None
    if not first_look and not diff["changed_from_previous"]:
        # Same action as last cycle — still ask Claude for context, but flag it
        first_look = False

    user_prompt = _build_user_prompt(diff, snapshot, first_look=first_look)

    try:
        # Import here so a missing dep at module load still lets tests run
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model=model,
            max_tokens=200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text_blocks = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        text = "".join(text_blocks).strip()
        if not text:
            log.warning("Anthropic returned empty content, using fallback")
            return _fallback_rationale(diff, snapshot)
        return text
    except Exception as e:  # noqa: BLE001 — we never want explain to crash the loop
        log.error("Anthropic call failed (%s), using fallback", e)
        return _fallback_rationale(diff, snapshot)


__all__ = [
    "explain_plan",
    "summarize_plan_changes",
    "DEFAULT_MODEL",
    "SYSTEM_PROMPT",
]
