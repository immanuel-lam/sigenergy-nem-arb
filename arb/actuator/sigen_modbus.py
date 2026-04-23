"""Sigenergy register map reference and direct Modbus client (fallback only).

The primary control path is through HA service calls (see ha_control.py),
since the Sigenergy-Local-Modbus HA integration already holds the Modbus TCP
connection. This module documents the register map and provides a direct
Modbus client for cases where HA is unavailable.

Register map from: https://github.com/TypQxQ/Sigenergy-Local-Modbus/
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

# --- Unit IDs ---
PLANT_UNIT_ID = 247
# Inverter unit IDs are typically 1, 2, etc.

# --- Read-only registers (30000 range) ---

# Plant level
REG_PLANT_EMS_WORK_MODE = 30003        # U16, gain 1
REG_PLANT_GRID_POWER = 30005           # S32 (2 regs), gain 1000 -> kW
REG_PLANT_SOC = 30014                  # U16, gain 10 -> %
REG_PLANT_ACTIVE_POWER = 30031         # S32 (2 regs), gain 1000 -> kW
REG_PLANT_ESS_POWER = 30037            # S32 (2 regs), gain 1000 -> kW, <0=discharge >0=charge
REG_PLANT_RUNNING_STATE = 30051        # U16: 0=standby, 1=running, 2=fault

# Inverter level
REG_INV_RATED_CHARGE_POWER = 30550     # U32 (2 regs), gain 1000 -> kW
REG_INV_RATED_DISCHARGE_POWER = 30552  # U32 (2 regs), gain 1000 -> kW
REG_INV_RUNNING_STATE = 30578          # U16
REG_INV_ACTIVE_POWER = 30587           # S32 (2 regs), gain 1000 -> kW
REG_INV_MAX_CHARGE_POWER = 30591       # U32 (2 regs), gain 1000 -> kW
REG_INV_MAX_DISCHARGE_POWER = 30593    # U32 (2 regs), gain 1000 -> kW
REG_INV_ESS_POWER = 30599             # S32 (2 regs), gain 1000 -> kW
REG_INV_SOC = 30601                    # U16, gain 10 -> %
REG_INV_SOH = 30602                    # U16, gain 10 -> %
REG_INV_CELL_TEMP = 30603             # S16, gain 10 -> °C

# --- Writable registers (40000 range) ---

REG_PLANT_START_STOP = 40000                  # U16: 0=stop, 1=start
REG_PLANT_ACTIVE_POWER_TARGET = 40001         # S32 (2 regs), gain 1000 -> kW
REG_PLANT_REMOTE_EMS_MODE = 40031             # U16: see EMS_MODE_*
REG_PLANT_ESS_MAX_CHARGE_LIMIT = 40032        # U32 (2 regs), gain 1000 -> kW
REG_PLANT_ESS_MAX_DISCHARGE_LIMIT = 40034     # U32 (2 regs), gain 1000 -> kW
REG_PLANT_BACKUP_SOC = 40046                  # U16, gain 10 -> %
REG_PLANT_CHARGE_CUTOFF_SOC = 40047           # U16, gain 10 -> %
REG_PLANT_DISCHARGE_CUTOFF_SOC = 40048        # U16, gain 10 -> %

# EMS work modes (for REG_PLANT_REMOTE_EMS_MODE)
EMS_MODE_PCS_REMOTE = 0
EMS_MODE_STANDBY = 1
EMS_MODE_MAX_SELF_CONSUMPTION = 2
EMS_MODE_CHARGE_GRID_FIRST = 3
EMS_MODE_CHARGE_PV_FIRST = 4
EMS_MODE_DISCHARGE_PV_FIRST = 5
EMS_MODE_DISCHARGE_ESS_FIRST = 6


@dataclass
class InverterState:
    """Read-only state from one inverter."""

    unit_id: int
    ip: str
    soc_pct: float | None = None
    running_mode: int | None = None
    active_power_kw: float | None = None
    ess_power_kw: float | None = None
    max_charge_kw: float | None = None
    max_discharge_kw: float | None = None
    read_ok: bool = False
    error: str | None = None


def _get_inverter_configs() -> list[tuple[str, int, int]]:
    """Read inverter configs from env. Returns list of (ip, port, unit_id)."""
    port = int(os.getenv("SIGEN_MODBUS_PORT", "502"))
    configs = []
    for i in (1, 2):
        ip = os.getenv(f"SIGEN_INVERTER_{i}_IP")
        uid = int(os.getenv(f"SIGEN_UNIT_ID_{i}", str(i)))
        if ip:
            configs.append((ip, port, uid))
    return configs


def _decode_s32(registers: list[int]) -> float:
    """Decode two 16-bit Modbus registers as a signed 32-bit int."""
    raw = (registers[0] << 16) | registers[1]
    if raw >= 0x80000000:
        raw -= 0x100000000
    return float(raw)


async def read_inverter(ip: str, port: int = 502, unit_id: int = 1) -> InverterState:
    """Read battery state from one Sigen inverter via direct Modbus TCP.

    NOTE: This will fail if the HA Sigenergy integration already holds
    the Modbus connection. Use ha_control.read_state() instead.
    """
    from pymodbus.client import AsyncModbusTcpClient

    state = InverterState(unit_id=unit_id, ip=ip)
    client = AsyncModbusTcpClient(ip, port=port, timeout=10)

    try:
        connected = await client.connect()
        if not connected:
            state.error = f"Connection refused at {ip}:{port} (HA integration may hold the connection)"
            return state

        # Read SOC (plant level, unit 247)
        result = await client.read_holding_registers(REG_PLANT_SOC, 1, slave=PLANT_UNIT_ID)
        if not result.isError():
            state.soc_pct = result.registers[0] / 10.0

        # Read ESS power (plant level)
        result = await client.read_holding_registers(REG_PLANT_ESS_POWER, 2, slave=PLANT_UNIT_ID)
        if not result.isError():
            state.ess_power_kw = _decode_s32(result.registers) / 1000.0

        # Read running state
        result = await client.read_holding_registers(REG_PLANT_RUNNING_STATE, 1, slave=PLANT_UNIT_ID)
        if not result.isError():
            state.running_mode = result.registers[0]

        state.read_ok = True
        log.info(
            "Direct Modbus %s: SOC=%.1f%%, ESS=%.2fkW, state=%s",
            ip, state.soc_pct or 0, state.ess_power_kw or 0, state.running_mode,
        )
    except Exception as e:
        state.error = str(e)
        log.error("Direct Modbus read failed for %s: %s", ip, e)
    finally:
        client.close()

    return state


async def read_all_inverters() -> list[InverterState]:
    """Read state from all configured inverters via direct Modbus."""
    configs = _get_inverter_configs()
    if not configs:
        log.warning("No inverter IPs configured")
        return []
    tasks = [read_inverter(ip, port, uid) for ip, port, uid in configs]
    return await asyncio.gather(*tasks)


def read_all_inverters_sync() -> list[InverterState]:
    """Synchronous wrapper."""
    return asyncio.run(read_all_inverters())
