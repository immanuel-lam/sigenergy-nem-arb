"""Control Sigenergy battery through Home Assistant service calls.

This is the primary actuator. The Sigenergy-Local-Modbus HA integration
exposes select/number/switch entities that we call through the HA REST API.
This avoids fighting the HA integration for the Modbus TCP connection.

Key HA entities (entity_id prefix depends on plant name in HA config):
  select.*_remote_ems_control_mode  -> EMS mode (self-consume, charge, discharge)
  number.*_ess_max_charging_limit   -> max charge power kW
  number.*_ess_max_discharging_limit -> max discharge power kW
  number.*_charge_cut_off_soc       -> stop charging at SOC %
  number.*_discharge_cut_off_soc    -> stop discharging at SOC %
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from arb.scheduler.plan import Action

load_dotenv()
log = logging.getLogger(__name__)

DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes")
KILL_SWITCH = os.getenv("ARB_KILL", "0") == "1"

# HA entity IDs for Sigenergy control — set in .env
# These are the entities created by the Sigenergy-Local-Modbus integration
ENTITY_EMS_MODE = os.getenv("HA_ENTITY_EMS_MODE", "select.plant_remote_ems_control_mode")
ENTITY_MAX_CHARGE = os.getenv("HA_ENTITY_MAX_CHARGE", "number.plant_ess_max_charging_limit")
ENTITY_MAX_DISCHARGE = os.getenv("HA_ENTITY_MAX_DISCHARGE", "number.plant_ess_max_discharging_limit")
ENTITY_CHARGE_CUTOFF_SOC = os.getenv("HA_ENTITY_CHARGE_CUTOFF_SOC", "number.plant_charge_cut_off_soc")
ENTITY_DISCHARGE_CUTOFF_SOC = os.getenv("HA_ENTITY_DISCHARGE_CUTOFF_SOC", "number.plant_discharge_cut_off_soc")

# EMS mode values as strings (what HA select entity expects)
EMS_MAX_SELF_CONSUMPTION = "Maximum Self Consumption"
EMS_CHARGE_GRID_FIRST = "Command Charging - Grid First"
EMS_CHARGE_PV_FIRST = "Command Charging - PV First"
EMS_DISCHARGE_ESS_FIRST = "Command Discharging - ESS First"
EMS_STANDBY = "Standby"

# Rate limiting: max writes per hour to protect Sigen flash
MAX_WRITES_PER_HOUR = 10
AUDIT_LOG_PATH = Path(os.getenv("ARB_AUDIT_LOG", "actuator_audit.log"))

_write_timestamps: list[datetime] = []


def _ha_url() -> str:
    return os.environ["HA_URL"].rstrip("/")


def _ha_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['HA_TOKEN']}",
        "Content-Type": "application/json",
    }


def _rate_limited() -> bool:
    """Check if we've exceeded the write rate limit."""
    now = datetime.now(timezone.utc)
    # Purge timestamps older than 1 hour
    cutoff = now.replace(second=0, microsecond=0)
    recent = [t for t in _write_timestamps if (now - t).total_seconds() < 3600]
    _write_timestamps.clear()
    _write_timestamps.extend(recent)
    return len(recent) >= MAX_WRITES_PER_HOUR


def _audit_log(action: str, entity: str, value: str, reason: str, dry_run: bool) -> None:
    """Append to audit log file."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "entity": entity,
        "value": value,
        "reason": reason,
        "dry_run": dry_run,
    }
    try:
        with open(AUDIT_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        log.warning("Failed to write audit log: %s", e)


def _call_ha_service(domain: str, service: str, entity_id: str, data: dict) -> bool:
    """Call a HA service. Returns True on success."""
    url = f"{_ha_url()}/api/services/{domain}/{service}"
    payload = {"entity_id": entity_id, **data}

    try:
        resp = requests.post(url, headers=_ha_headers(), json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error("HA service call failed: %s/%s on %s: %s", domain, service, entity_id, e)
        return False


def set_ems_mode(mode: str, reason: str = "") -> bool:
    """Set the EMS work mode via HA select entity."""
    _audit_log("set_ems_mode", ENTITY_EMS_MODE, mode, reason, DRY_RUN)

    if DRY_RUN:
        log.info("[DRY-RUN] Would set EMS mode to: %s (%s)", mode, reason)
        return True

    if KILL_SWITCH:
        log.warning("[KILL] Kill switch active, refusing to set EMS mode")
        return False

    if _rate_limited():
        log.warning("Rate limited, skipping EMS mode change")
        return False

    ok = _call_ha_service("select", "select_option", ENTITY_EMS_MODE, {"option": mode})
    if ok:
        _write_timestamps.append(datetime.now(timezone.utc))
        log.info("Set EMS mode to: %s (%s)", mode, reason)
    return ok


def set_charge_limit(power_kw: float, reason: str = "") -> bool:
    """Set max charge power limit via HA number entity."""
    _audit_log("set_charge_limit", ENTITY_MAX_CHARGE, str(power_kw), reason, DRY_RUN)

    if DRY_RUN:
        log.info("[DRY-RUN] Would set charge limit to: %.1f kW (%s)", power_kw, reason)
        return True

    if KILL_SWITCH:
        log.warning("[KILL] Kill switch active, refusing to set charge limit")
        return False

    if _rate_limited():
        log.warning("Rate limited, skipping charge limit change")
        return False

    ok = _call_ha_service("number", "set_value", ENTITY_MAX_CHARGE, {"value": power_kw})
    if ok:
        _write_timestamps.append(datetime.now(timezone.utc))
        log.info("Set charge limit to: %.1f kW (%s)", power_kw, reason)
    return ok


def set_discharge_limit(power_kw: float, reason: str = "") -> bool:
    """Set max discharge power limit via HA number entity."""
    _audit_log("set_discharge_limit", ENTITY_MAX_DISCHARGE, str(power_kw), reason, DRY_RUN)

    if DRY_RUN:
        log.info("[DRY-RUN] Would set discharge limit to: %.1f kW (%s)", power_kw, reason)
        return True

    if KILL_SWITCH:
        log.warning("[KILL] Kill switch active, refusing to set discharge limit")
        return False

    if _rate_limited():
        log.warning("Rate limited, skipping discharge limit change")
        return False

    ok = _call_ha_service("number", "set_value", ENTITY_MAX_DISCHARGE, {"value": power_kw})
    if ok:
        _write_timestamps.append(datetime.now(timezone.utc))
        log.info("Set discharge limit to: %.1f kW (%s)", power_kw, reason)
    return ok


def apply_action(
    action: Action,
    charge_kw: float = 0.0,
    discharge_kw: float = 0.0,
    soc_pct: float | None = None,
    reason: str = "",
) -> bool:
    """Apply a scheduled action to the battery via HA.

    This is what the agent loop calls. It translates the Plan's action
    into the appropriate HA service calls.
    """
    from arb.scheduler.constants import BatteryConstants
    bc = BatteryConstants()

    # Hard SOC bounds check — actuator is the last line of defence
    if soc_pct is not None:
        if soc_pct <= bc.soc_floor * 100 + 1 and action == Action.DISCHARGE_GRID:
            log.warning("HARD REFUSE: SOC %.1f%% too low to discharge (floor %.0f%%)",
                        soc_pct, bc.soc_floor * 100)
            return False
        if soc_pct >= bc.soc_ceiling * 100 - 1 and action == Action.CHARGE_GRID:
            log.warning("HARD REFUSE: SOC %.1f%% too high to charge (ceiling %.0f%%)",
                        soc_pct, bc.soc_ceiling * 100)
            return False

    if action == Action.CHARGE_GRID:
        set_ems_mode(EMS_CHARGE_GRID_FIRST, reason)
        set_charge_limit(min(charge_kw, bc.max_charge_kw), reason)
        return True

    elif action == Action.DISCHARGE_GRID:
        set_ems_mode(EMS_DISCHARGE_ESS_FIRST, reason)
        set_discharge_limit(min(discharge_kw, bc.max_discharge_kw), reason)
        return True

    elif action == Action.HOLD_SOLAR:
        # Self-consume mode blocks export, solar goes to battery
        set_ems_mode(EMS_MAX_SELF_CONSUMPTION, reason)
        return True

    else:  # IDLE
        set_ems_mode(EMS_MAX_SELF_CONSUMPTION, reason)
        return True


def reset_to_self_consume(reason: str = "agent reset") -> bool:
    """Safe fallback: put the system back into self-consumption mode."""
    return set_ems_mode(EMS_MAX_SELF_CONSUMPTION, reason)
