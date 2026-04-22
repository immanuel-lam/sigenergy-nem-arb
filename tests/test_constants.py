"""Sanity checks on battery constants."""
from __future__ import annotations

from arb.scheduler.constants import BatteryConstants


def test_roundtrip_efficiency():
    bc = BatteryConstants()
    assert bc.roundtrip_efficiency == 0.95 * 0.95


def test_usable_kwh():
    bc = BatteryConstants()
    expected = 64.0 * (0.95 - 0.10)
    assert abs(bc.usable_kwh - expected) < 0.01


def test_soc_floor_below_ceiling():
    bc = BatteryConstants()
    assert bc.soc_floor < bc.soc_ceiling
