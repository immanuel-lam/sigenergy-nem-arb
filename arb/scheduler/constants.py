"""Battery and grid constants. Single source of truth for all physical limits."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BatteryConstants:
    """Sigenergy 64kWh LFP, 2x inverters."""

    capacity_kwh: float = 64.0
    soc_floor: float = 0.10
    soc_ceiling: float = 0.95
    max_charge_kw: float = 30.0  # 2x 15kW inverters
    max_discharge_kw: float = 30.0
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.95
    cycle_cost_c_per_kwh: float = 2.0

    @property
    def roundtrip_efficiency(self) -> float:
        return self.charge_efficiency * self.discharge_efficiency

    @property
    def usable_kwh(self) -> float:
        return self.capacity_kwh * (self.soc_ceiling - self.soc_floor)


@dataclass(frozen=True)
class GridConstants:
    region: str = "NSW1"
    import_fees_c_per_kwh: float = 0.0
    export_fees_c_per_kwh: float = 0.0


INTERVAL_MIN = 5
LOOP_PERIOD_MIN = 30
HORIZON_H = 24
