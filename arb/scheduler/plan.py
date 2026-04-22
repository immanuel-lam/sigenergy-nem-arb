"""Battery schedule plan — the core data structure connecting scheduler, actuator, and backtest."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

import numpy as np
import pandas as pd

from arb.scheduler.constants import INTERVAL_MIN, BatteryConstants


class Action(str, Enum):
    IDLE = "IDLE"
    CHARGE_GRID = "CHARGE_GRID"
    DISCHARGE_GRID = "DISCHARGE_GRID"
    HOLD_SOLAR = "HOLD_SOLAR"


@dataclass
class Plan:
    """Full schedule across the planning horizon.

    All energy values are GRID-SIDE (what crosses the meter).
    Battery-side energy = grid-side * charge_efficiency (charging)
                        = grid-side / discharge_efficiency (discharging)

    SOC array has n+1 entries: soc[0] is the initial SOC, soc[i] is SOC
    after interval i-1 has been executed.
    """

    timestamps: np.ndarray        # datetime64[ns, UTC], length n
    import_c_kwh: np.ndarray      # c/kWh import price per interval, length n
    export_c_kwh: np.ndarray      # c/kWh export price per interval, length n
    load_kw: np.ndarray           # household load per interval, length n
    solar_kw: np.ndarray          # solar generation per interval, length n
    charge_grid_kwh: np.ndarray   # grid-side energy charged per interval, length n (>=0)
    discharge_grid_kwh: np.ndarray  # grid-side energy discharged per interval, length n (>=0)
    soc: np.ndarray               # SOC fraction (0-1), length n+1
    actions: np.ndarray           # Action enum values, length n (object array)
    battery: BatteryConstants = field(default_factory=BatteryConstants)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def n(self) -> int:
        return len(self.timestamps)

    @property
    def interval_h(self) -> float:
        return INTERVAL_MIN / 60.0

    @classmethod
    def from_self_consume(
        cls,
        timestamps: np.ndarray,
        import_c_kwh: np.ndarray,
        export_c_kwh: np.ndarray,
        load_kw: np.ndarray,
        solar_kw: np.ndarray,
        soc_now: float,
        battery: BatteryConstants | None = None,
    ) -> Plan:
        """Build a baseline plan assuming pure self-consumption (no grid arbitrage).

        Solar surplus charges battery, load deficit discharges battery.
        Everything else flows to/from grid. All intervals are IDLE.
        """
        battery = battery or BatteryConstants()
        n = len(timestamps)
        interval_h = INTERVAL_MIN / 60.0

        soc = np.zeros(n + 1)
        soc[0] = soc_now

        for i in range(n):
            surplus_kw = solar_kw[i] - load_kw[i]  # positive = excess solar

            if surplus_kw > 0:
                # Solar surplus charges battery
                charge_kw = min(surplus_kw, battery.max_charge_kw)
                # battery-side kWh entering the cells
                battery_kwh = charge_kw * battery.charge_efficiency * interval_h
                delta_soc = battery_kwh / battery.capacity_kwh
                soc[i + 1] = min(soc[i] + delta_soc, battery.soc_ceiling)
            elif surplus_kw < 0:
                # Load deficit discharges battery
                deficit_kw = -surplus_kw
                discharge_kw = min(deficit_kw, battery.max_discharge_kw)
                # battery-side kWh leaving the cells
                battery_kwh = discharge_kw / battery.discharge_efficiency * interval_h
                delta_soc = battery_kwh / battery.capacity_kwh
                soc[i + 1] = max(soc[i] - delta_soc, battery.soc_floor)
            else:
                soc[i + 1] = soc[i]

        actions = np.array([Action.IDLE] * n, dtype=object)

        return cls(
            timestamps=timestamps,
            import_c_kwh=import_c_kwh,
            export_c_kwh=export_c_kwh,
            load_kw=load_kw,
            solar_kw=solar_kw,
            charge_grid_kwh=np.zeros(n),
            discharge_grid_kwh=np.zeros(n),
            soc=soc,
            actions=actions,
            battery=battery,
        )

    def can_charge_kwh(self, idx: int) -> float:
        """Max grid-side kWh that can be charged at interval idx.

        Limited by: rate limit, SOC ceiling for all intervals from idx+1 onward
        (since charging here raises SOC for all subsequent intervals).
        """
        rate_limit = self.battery.max_charge_kw * self.interval_h
        remaining_rate = rate_limit - self.charge_grid_kwh[idx]
        if remaining_rate <= 0:
            return 0.0

        # Min headroom to ceiling from idx+1 to end
        headroom_soc = np.min(self.battery.soc_ceiling - self.soc[idx + 1:])
        if headroom_soc <= 0:
            return 0.0

        # Convert SOC headroom to grid-side kWh
        # battery_kwh = headroom_soc * capacity
        # grid_kwh = battery_kwh / charge_efficiency
        headroom_grid = headroom_soc * self.battery.capacity_kwh / self.battery.charge_efficiency

        return max(0.0, min(remaining_rate, headroom_grid))

    def can_discharge_kwh(self, idx: int) -> float:
        """Max grid-side kWh that can be discharged at interval idx.

        Limited by: rate limit, SOC floor for all intervals from idx+1 onward.
        """
        rate_limit = self.battery.max_discharge_kw * self.interval_h
        remaining_rate = rate_limit - self.discharge_grid_kwh[idx]
        if remaining_rate <= 0:
            return 0.0

        # Min headroom above floor from idx+1 to end
        headroom_soc = np.min(self.soc[idx + 1:] - self.battery.soc_floor)
        if headroom_soc <= 0:
            return 0.0

        # Convert SOC headroom to grid-side kWh
        # battery_kwh = headroom_soc * capacity
        # grid_kwh = battery_kwh * discharge_efficiency
        headroom_grid = headroom_soc * self.battery.capacity_kwh * self.battery.discharge_efficiency

        return max(0.0, min(remaining_rate, headroom_grid))

    def charge(self, idx: int, grid_kwh: float) -> None:
        """Add grid charging at interval idx.

        grid_kwh: energy pulled from grid (grid-side).
        Battery receives grid_kwh * charge_efficiency.
        SOC rises from idx+1 onward.
        """
        if grid_kwh <= 0:
            return
        battery_kwh = grid_kwh * self.battery.charge_efficiency
        delta_soc = battery_kwh / self.battery.capacity_kwh

        self.charge_grid_kwh[idx] += grid_kwh
        self.soc[idx + 1:] += delta_soc
        self.actions[idx] = Action.CHARGE_GRID

    def discharge(self, idx: int, grid_kwh: float) -> None:
        """Add grid discharging at interval idx.

        grid_kwh: energy delivered to grid (grid-side).
        Battery loses grid_kwh / discharge_efficiency.
        SOC drops from idx+1 onward.
        """
        if grid_kwh <= 0:
            return
        battery_kwh = grid_kwh / self.battery.discharge_efficiency
        delta_soc = battery_kwh / self.battery.capacity_kwh

        self.discharge_grid_kwh[idx] += grid_kwh
        self.soc[idx + 1:] -= delta_soc
        self.actions[idx] = Action.DISCHARGE_GRID

    def hold_solar(self, idx: int) -> None:
        """Mark interval as HOLD_SOLAR (divert solar to battery instead of exporting)."""
        self.actions[idx] = Action.HOLD_SOLAR

    @property
    def current_interval_idx(self) -> int | None:
        """Index of the interval containing the current time."""
        now = pd.Timestamp.now(tz="UTC")
        ts = pd.DatetimeIndex(self.timestamps)
        if ts.tz is None:
            ts = ts.tz_localize("UTC")
        interval_delta = pd.Timedelta(minutes=INTERVAL_MIN)

        for i in range(self.n):
            if ts[i] <= now < ts[i] + interval_delta:
                return i
        return None

    @property
    def current_action(self) -> Action | None:
        """Action for the current interval."""
        idx = self.current_interval_idx
        if idx is None:
            return None
        return self.actions[idx]

    def to_dataframe(self) -> pd.DataFrame:
        """Convert plan to a DataFrame for logging and display."""
        return pd.DataFrame({
            "timestamp": self.timestamps,
            "action": [a.value for a in self.actions],
            "import_c_kwh": self.import_c_kwh,
            "export_c_kwh": self.export_c_kwh,
            "load_kw": self.load_kw,
            "solar_kw": self.solar_kw,
            "charge_grid_kwh": self.charge_grid_kwh,
            "discharge_grid_kwh": self.discharge_grid_kwh,
            "soc_before": self.soc[:-1],
            "soc_after": self.soc[1:],
        })

    def summary(self) -> str:
        """One-paragraph summary of the plan."""
        df = self.to_dataframe()
        counts = df["action"].value_counts()
        total_charge = self.charge_grid_kwh.sum()
        total_discharge = self.discharge_grid_kwh.sum()
        soc_range = f"{self.soc.min():.1%}-{self.soc.max():.1%}"

        lines = [
            f"Plan: {self.n} intervals ({self.n * INTERVAL_MIN / 60:.0f}h), SOC range {soc_range}",
            f"  Grid charge: {total_charge:.1f} kWh, Grid discharge: {total_discharge:.1f} kWh",
        ]
        for action, count in counts.items():
            lines.append(f"  {action}: {count} intervals")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """JSON-serializable representation."""
        return {
            "created_at": self.created_at.isoformat(),
            "n_intervals": self.n,
            "soc_initial": float(self.soc[0]),
            "soc_final": float(self.soc[-1]),
            "total_charge_kwh": float(self.charge_grid_kwh.sum()),
            "total_discharge_kwh": float(self.discharge_grid_kwh.sum()),
            "actions": [a.value for a in self.actions],
        }
