"""Greedy rank-and-fill battery arbitrage scheduler."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from arb.scheduler.constants import INTERVAL_MIN, BatteryConstants
from arb.scheduler.plan import Action, Plan

log = logging.getLogger(__name__)

# Minimum energy to bother scheduling (avoid float noise)
MIN_ENERGY_KWH = 0.01


def schedule(
    forecast: pd.DataFrame,
    soc_now: float,
    battery: BatteryConstants | None = None,
) -> Plan:
    """Run greedy scheduler on a forecast DataFrame.

    Args:
        forecast: Must have columns: timestamp, import_c_kwh, export_c_kwh, load_kw, solar_kw.
        soc_now: Current battery SOC as fraction (0.0-1.0).
        battery: Battery constants. Defaults to BatteryConstants().

    Returns:
        A Plan with optimized charge/discharge schedule.
    """
    battery = battery or BatteryConstants()
    n = len(forecast)
    interval_h = INTERVAL_MIN / 60.0
    max_energy = battery.max_charge_kw * interval_h  # grid-side kWh per interval

    timestamps = forecast["timestamp"].values
    import_c = forecast["import_c_kwh"].values.astype(float)
    export_c = forecast["export_c_kwh"].values.astype(float)
    load_kw = forecast["load_kw"].values.astype(float)
    solar_kw = forecast["solar_kw"].values.astype(float)

    # Step 1: Build baseline self-consume plan
    plan = Plan.from_self_consume(
        timestamps=timestamps,
        import_c_kwh=import_c,
        export_c_kwh=export_c,
        load_kw=load_kw,
        solar_kw=solar_kw,
        soc_now=soc_now,
        battery=battery,
    )

    # Step 2: Rank (charge, discharge) pairs by arbitrage value
    rte = battery.roundtrip_efficiency
    cycle_cost = battery.cycle_cost_c_per_kwh

    # Grid-export arbitrage: only trade when export price at d beats import at c
    # enough to cover roundtrip losses and cycle cost. This is conservative by
    # design — on tariffs like Amber with negative feed-in, the agent will find
    # no profitable pairs and default to self-consume, which is near-optimal
    # on a solar-rich house.
    pairs: list[tuple[int, int, float]] = []
    for c in range(n):
        for d in range(c + 1, n):
            spread = export_c[d] - import_c[c]
            net_value = spread * rte - cycle_cost
            if net_value > 0:
                pairs.append((c, d, net_value))

    pairs.sort(key=lambda x: -x[2])
    log.info("Greedy: %d profitable pairs from %d total possible", len(pairs), n * (n - 1) // 2)

    # Step 3: Greedily assign energy to pairs
    assigned = 0
    total_value = 0.0

    for c, d, net_value in pairs:
        # Available charge room at interval c, considering SOC ceiling from c+1 to d
        # (between charge and discharge, SOC is elevated)
        charge_headroom_soc = np.min(battery.soc_ceiling - plan.soc[c + 1: d + 1])
        if charge_headroom_soc <= 0:
            continue
        charge_headroom_grid = charge_headroom_soc * battery.capacity_kwh / battery.charge_efficiency

        # Available discharge room at interval d, considering SOC floor from d+1 onward
        # After charge at c raises SOC, we need to check floor constraints.
        # The discharge at d lowers SOC from d+1 onward.
        discharge_headroom_soc = np.min(plan.soc[d + 1:] - battery.soc_floor)
        if discharge_headroom_soc <= 0:
            continue
        discharge_headroom_grid = discharge_headroom_soc * battery.capacity_kwh * battery.discharge_efficiency

        # Rate limits (remaining capacity at each interval)
        rate_c = max_energy - plan.charge_grid_kwh[c]
        rate_d = max_energy - plan.discharge_grid_kwh[d]

        energy = min(charge_headroom_grid, discharge_headroom_grid, rate_c, rate_d)
        if energy < MIN_ENERGY_KWH:
            continue

        plan.charge(c, energy)
        plan.discharge(d, energy)
        assigned += 1
        total_value += net_value * energy

    log.info("Greedy: assigned %d pairs, estimated value %.1f c", assigned, total_value)

    # Step 4: Handle negative export prices — hold solar instead of exporting at a loss
    held = 0
    for i in range(n):
        if (
            export_c[i] < 0
            and plan.actions[i] == Action.IDLE
            and solar_kw[i] > load_kw[i]
            and plan.soc[i + 1] < battery.soc_ceiling
        ):
            plan.hold_solar(i)
            held += 1

    if held:
        log.info("Greedy: marked %d intervals as HOLD_SOLAR (negative export)", held)

    log.info("Greedy done. %s", plan.summary())
    return plan
