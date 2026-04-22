"""Agent control loop — runs the full ingest/forecast/schedule/actuate cycle."""
from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone

from arb.forecast.builder import build_forecast
from arb.ingest.snapshot import take_snapshot
from arb.scheduler.constants import INTERVAL_MIN, BatteryConstants
from arb.scheduler.greedy import schedule
from arb.scheduler.plan import Action

log = logging.getLogger(__name__)

DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes")
KILL_SWITCH = os.getenv("ARB_KILL", "0") == "1"


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
        # Default SOC to 50% if unknown
        if snapshot.soc_pct is None:
            snapshot.soc_pct = 50.0
            log.warning("SOC unknown, defaulting to 50%%")

    # 2. Forecast
    log.info("=== Building forecast ===")
    ha_history = None
    try:
        from arb.ingest import ha
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

    # 4. Report what would be written
    idx = plan.current_interval_idx
    if idx is not None:
        action = plan.actions[idx]
        charge = plan.charge_grid_kwh[idx]
        discharge = plan.discharge_grid_kwh[idx]

        if action == Action.CHARGE_GRID:
            power_kw = charge / (INTERVAL_MIN / 60.0)
            log.info(">>> WOULD WRITE: Force charge at %.1f kW", power_kw)
        elif action == Action.DISCHARGE_GRID:
            power_kw = discharge / (INTERVAL_MIN / 60.0)
            log.info(">>> WOULD WRITE: Force discharge at %.1f kW", power_kw)
        elif action == Action.HOLD_SOLAR:
            log.info(">>> WOULD WRITE: Hold solar (block export)")
        else:
            log.info(">>> WOULD WRITE: Self-consume mode (IDLE)")

        log.info(
            ">>> Current SOC: %.1f%% -> %.1f%% | Price: import %.1f c/kWh, export %.1f c/kWh",
            plan.soc[idx] * 100, plan.soc[idx + 1] * 100,
            plan.import_c_kwh[idx], plan.export_c_kwh[idx],
        )
    else:
        log.warning("Current time is outside the plan horizon")

    if not dry_run:
        log.warning("DRY_RUN=false but write path not implemented yet (Day 3)")

    # 5. Modbus read (optional, don't fail if unreachable)
    log.info("=== Modbus read ===")
    try:
        from arb.actuator.sigen_modbus import read_all_inverters_sync
        states = read_all_inverters_sync()
        for s in states:
            if s.read_ok:
                log.info("Inverter %s: SOC=%.1f%%, mode=%s", s.ip, s.soc_pct, s.running_mode)
            else:
                log.warning("Inverter %s: %s", s.ip, s.error)
        if not states:
            log.info("No inverters configured, skipping Modbus read")
    except Exception as e:
        log.info("Modbus read skipped: %s", e)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sigenergy NEM arbitrage agent")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Don't write to inverter")
    parser.add_argument("--force", action="store_true", help="Run even with stale/missing sensors")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.once:
        run_once(dry_run=args.dry_run, force=args.force)
    else:
        log.info("Continuous loop not yet implemented (Day 3). Running once.")
        run_once(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
