"""Sigenergy inverter Modbus TCP client — read-only on Day 2."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes")

# Register addresses — PLACEHOLDERS, verify against Sigen Modbus protocol PDF.
# Immanuel will provide the real register map.
REG_BATTERY_SOC = 0x0100
REG_RUNNING_MODE = 0x0200
REG_ACTIVE_POWER = 0x0300
REG_MAX_CHARGE_POWER = 0x0400
REG_MAX_DISCHARGE_POWER = 0x0500


@dataclass
class InverterState:
    """Read-only state from one inverter."""

    unit_id: int
    ip: str
    soc_pct: float | None = None
    running_mode: int | None = None
    active_power_w: float | None = None
    max_charge_w: float | None = None
    max_discharge_w: float | None = None
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


def _decode_signed_32(registers: list[int]) -> float:
    """Decode two 16-bit Modbus registers as a signed 32-bit int."""
    raw = (registers[0] << 16) | registers[1]
    if raw >= 0x80000000:
        raw -= 0x100000000
    return float(raw)


async def read_inverter(ip: str, port: int = 502, unit_id: int = 1) -> InverterState:
    """Read battery state from one Sigen inverter via Modbus TCP."""
    from pymodbus.client import AsyncModbusTcpClient

    state = InverterState(unit_id=unit_id, ip=ip)
    client = AsyncModbusTcpClient(ip, port=port, timeout=10)

    try:
        connected = await client.connect()
        if not connected:
            state.error = f"Connection failed to {ip}:{port}"
            return state

        # Read SOC
        result = await client.read_holding_registers(REG_BATTERY_SOC, 1, slave=unit_id)
        if not result.isError():
            state.soc_pct = result.registers[0] / 10.0

        # Read running mode
        result = await client.read_holding_registers(REG_RUNNING_MODE, 1, slave=unit_id)
        if not result.isError():
            state.running_mode = result.registers[0]

        # Read active power (signed 32-bit across 2 registers)
        result = await client.read_holding_registers(REG_ACTIVE_POWER, 2, slave=unit_id)
        if not result.isError():
            state.active_power_w = _decode_signed_32(result.registers)

        state.read_ok = True
        log.info(
            "Inverter %s (unit %d): SOC=%.1f%%, mode=%s, power=%.0fW",
            ip, unit_id, state.soc_pct or 0, state.running_mode, state.active_power_w or 0,
        )
    except Exception as e:
        state.error = str(e)
        log.error("Modbus read failed for %s: %s", ip, e)
    finally:
        client.close()

    return state


async def read_all_inverters() -> list[InverterState]:
    """Read state from all configured inverters."""
    configs = _get_inverter_configs()
    if not configs:
        log.warning("No inverter IPs configured in env")
        return []
    tasks = [read_inverter(ip, port, uid) for ip, port, uid in configs]
    return await asyncio.gather(*tasks)


def read_all_inverters_sync() -> list[InverterState]:
    """Synchronous wrapper for the agent loop."""
    return asyncio.run(read_all_inverters())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    states = read_all_inverters_sync()
    for s in states:
        if s.read_ok:
            print(f"Inverter {s.ip}: SOC={s.soc_pct}%, mode={s.running_mode}")
        else:
            print(f"Inverter {s.ip}: FAILED — {s.error}")
    if not states:
        print("No inverters configured. Set SIGEN_INVERTER_1_IP in .env")
