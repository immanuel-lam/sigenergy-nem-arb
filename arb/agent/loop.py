"""Agent control loop — runs the full ingest/forecast/schedule/actuate cycle."""
from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

from arb.agent.audit import audit_current_interval
from arb.agent.explain import explain_plan
from arb.agent.plan_diff import diff_plans, format_diff_short
from arb.agent.spike_detector import detect_spike, format_spike_for_log
from arb.forecast.builder import build_forecast
from arb.ingest import ha
from arb.ingest.snapshot import take_snapshot
from arb.scheduler.constants import INTERVAL_MIN, LOOP_PERIOD_MIN, BatteryConstants
from arb.scheduler.greedy import schedule
from arb.scheduler.plan import Action

log = logging.getLogger(__name__)

DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes")
KILL_SWITCH = os.getenv("ARB_KILL", "0") == "1"
RATIONALE_LOG = Path(os.getenv("ARB_RATIONALE_LOG", "agent_rationale.log"))
PREVIOUS_PLAN_PATH = Path(os.getenv("ARB_PREVIOUS_PLAN", ".previous_plan.pkl"))
PREVIOUS_SOC_PATH = Path(os.getenv("ARB_PREVIOUS_SOC", ".previous_soc.txt"))
SPIKE_LOG = Path(os.getenv("ARB_SPIKE_LOG", "spike_events.log"))

# Min seconds between spike-triggered re-plans (rate limit)
SPIKE_COOLDOWN_SEC = 600  # 10 min

_shutdown = False


def _handle_shutdown(signum, frame) -> None:
    global _shutdown
    log.info("Received signal %d, shutting down after current cycle", signum)
    _shutdown = True


def _load_previous_plan():
    """Load the last plan from disk, if any."""
    if not PREVIOUS_PLAN_PATH.exists():
        return None
    try:
        with PREVIOUS_PLAN_PATH.open("rb") as fh:
            return pickle.load(fh)
    except Exception as e:
        log.warning("Failed to load previous plan: %s", e)
        return None


def _save_plan(plan) -> None:
    try:
        with PREVIOUS_PLAN_PATH.open("wb") as fh:
            pickle.dump(plan, fh)
    except Exception as e:
        log.error("Failed to save plan: %s", e)


def _load_previous_soc() -> float | None:
    if not PREVIOUS_SOC_PATH.exists():
        return None
    try:
        return float(PREVIOUS_SOC_PATH.read_text().strip())
    except Exception:
        return None


def _save_soc(soc_pct: float) -> None:
    try:
        PREVIOUS_SOC_PATH.write_text(f"{soc_pct:.4f}\n")
    except Exception as e:
        log.error("Failed to save SOC: %s", e)


def run_once(dry_run: bool = True, force: bool = False) -> None:
    """Execute one full agent cycle."""
    if KILL_SWITCH:
        log.warning("Kill switch active (ARB_KILL=1), doing nothing")
        return

    # 1. Ingest
    log.info("=== Taking snapshot ===")
    snapshot = take_snapshot()
    log.info(snapshot.summary())

    if snapshot.is_stale() and not force:
        log.warning("Stale sensors: %s — skipping this cycle (use --force to override)", snapshot.stale_sensors)
        return
    elif snapshot.is_stale():
        log.warning("Stale sensors: %s — continuing anyway (--force)", snapshot.stale_sensors)
        if snapshot.soc_pct is None:
            snapshot.soc_pct = 50.0
            log.warning("SOC unknown, defaulting to 50%%")

    # 2. Forecast
    log.info("=== Building forecast ===")
    ha_history = None
    try:
        ha_history = ha.fetch_history(days=30)
    except Exception as e:
        log.warning("HA history unavailable, using flat load profile: %s", e)

    forecast_df = build_forecast(snapshot, ha_history=ha_history)
    log.info(
        "Forecast: %d intervals (%s to %s)",
        len(forecast_df), forecast_df["timestamp"].iloc[0], forecast_df["timestamp"].iloc[-1],
    )

    # 3. Schedule
    log.info("=== Running scheduler ===")
    soc_now = (snapshot.soc_pct or 50.0) / 100.0
    plan = schedule(forecast_df, soc_now)
    log.info(plan.summary())

    # 4. Diff against previous plan
    previous_plan = _load_previous_plan()
    plan_diff = diff_plans(plan, previous_plan)
    log.info("Plan diff: %s", format_diff_short(plan_diff))

    # 5. Audit what actually happened since last cycle
    prior_soc = _load_previous_soc()
    if previous_plan is not None:
        ha_state = {
            "soc_pct": snapshot.soc_pct,
            "load_kw": snapshot.load_kw,
            "solar_kw": snapshot.solar_kw,
            "battery_power_kw": snapshot.battery_power_kw,
        }
        audit_entry = audit_current_interval(
            plan=previous_plan,
            current_ha_state=ha_state,
            prior_soc_pct=prior_soc,
        )
        log.info("Audit: status=%s drift=%s", audit_entry.status,
                 f"{audit_entry.soc_delta_pct:+.1f}%" if audit_entry.soc_delta_pct is not None else "n/a")

    # 6. Actuate (advisory mode — writes go through ha_control with DRY_RUN)
    idx = plan.current_interval_idx
    if idx is not None:
        action = plan.actions[idx]
        charge = plan.charge_grid_kwh[idx]
        discharge = plan.discharge_grid_kwh[idx]
        charge_kw = charge / (INTERVAL_MIN / 60.0)
        discharge_kw = discharge / (INTERVAL_MIN / 60.0)

        log.info("=== Actuating ===")
        log.info(
            "Action: %s | SOC: %.1f%% -> %.1f%% | Price: import %.1f, export %.1f c/kWh",
            action.value, plan.soc[idx] * 100, plan.soc[idx + 1] * 100,
            plan.import_c_kwh[idx], plan.export_c_kwh[idx],
        )

        # Generate rationale with diff context
        log.info("=== Explaining decision ===")
        rationale = explain_plan(plan, snapshot, previous_plan=previous_plan)
        log.info("Rationale: %s", rationale)
        _persist_rationale(snapshot.timestamp, action, rationale)

        from arb.actuator.ha_control import apply_action
        reason = f"import={plan.import_c_kwh[idx]:.1f} export={plan.export_c_kwh[idx]:.1f} c/kWh"
        apply_action(
            action=action,
            charge_kw=charge_kw,
            discharge_kw=discharge_kw,
            soc_pct=snapshot.soc_pct,
            reason=reason,
        )
    else:
        log.warning("Current time is outside the plan horizon")

    # 7. Persist state for next cycle
    _save_plan(plan)
    if snapshot.soc_pct is not None:
        _save_soc(snapshot.soc_pct)


def _persist_rationale(ts: datetime, action: Action, rationale: str) -> None:
    """Append a single-line rationale entry to the audit log."""
    try:
        line = f"{ts.isoformat()}\t{action.value}\t{rationale}\n"
        with RATIONALE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as e:
        log.error("Failed to persist rationale: %s", e)


def _poll_for_spike(poll_minutes: int = 5) -> object | None:
    """Cheap snapshot + spike check. Returns SpikeEvent or None."""
    try:
        snap = take_snapshot()
    except Exception as e:
        log.warning("Spike poll snapshot failed: %s", e)
        return None
    prev_plan = _load_previous_plan()
    if prev_plan is None:
        return None
    return detect_spike(snap, prev_plan)


def _log_spike(event: object) -> None:
    """Append a spike event to the spike log."""
    try:
        line = format_spike_for_log(event) + "\n"
        with SPIKE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as e:
        log.error("Failed to log spike: %s", e)


def run_continuous(dry_run: bool = True, force: bool = False,
                   period_min: int = LOOP_PERIOD_MIN,
                   spike_poll_min: int = 5) -> None:
    """Run the agent loop every `period_min` minutes until shutdown signal.

    Also polls for price spikes every `spike_poll_min` minutes between full
    cycles. If a spike is detected and the cooldown has elapsed, triggers an
    immediate full cycle instead of waiting for the next scheduled one.
    """
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    log.info("Continuous loop started (period %d min, spike poll %d min, dry_run=%s)",
             period_min, spike_poll_min, dry_run)

    last_full_cycle = 0.0
    while not _shutdown:
        now = time.monotonic()
        since_full = now - last_full_cycle
        scheduled_due = since_full >= period_min * 60

        spike_event = None
        if not scheduled_due and since_full >= SPIKE_COOLDOWN_SEC:
            spike_event = _poll_for_spike(poll_minutes=spike_poll_min)

        trigger = None
        if scheduled_due:
            trigger = "scheduled"
        elif spike_event is not None:
            trigger = f"spike ({format_spike_for_log(spike_event)})"
            _log_spike(spike_event)
            log.warning("Spike-triggered re-plan: %s", format_spike_for_log(spike_event))

        if trigger is not None:
            log.info("=== Cycle trigger: %s ===", trigger)
            try:
                run_once(dry_run=dry_run, force=force)
            except Exception as e:
                log.exception("Cycle failed: %s", e)
            last_full_cycle = time.monotonic()

        # Sleep until next poll, checking shutdown every 5s
        sleep_total = spike_poll_min * 60
        slept = 0.0
        while slept < sleep_total and not _shutdown:
            time.sleep(min(5, sleep_total - slept))
            slept += 5

    log.info("Continuous loop exited cleanly")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sigenergy NEM arbitrage agent")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--continuous", action="store_true", help="Run every 30 min until killed")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Don't write to inverter")
    parser.add_argument("--force", action="store_true", help="Run even with stale/missing sensors")
    parser.add_argument("--period-min", type=int, default=LOOP_PERIOD_MIN,
                        help="Loop period in minutes (continuous mode)")
    parser.add_argument("--spike-poll-min", type=int, default=5,
                        help="Price spike poll interval in minutes (continuous mode)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.continuous:
        run_continuous(dry_run=args.dry_run, force=args.force,
                       period_min=args.period_min,
                       spike_poll_min=args.spike_poll_min)
    else:
        run_once(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
