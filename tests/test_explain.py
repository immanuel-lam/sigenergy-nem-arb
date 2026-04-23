"""Tests for arb.agent.explain — structured diff, fallback, and API mocking."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from arb.agent.explain import (
    _fallback_rationale,
    explain_plan,
    summarize_plan_changes,
)
from arb.ingest.snapshot import Snapshot
from arb.scheduler.constants import INTERVAL_MIN
from arb.scheduler.plan import Action, Plan


def _make_timestamps(n: int) -> np.ndarray:
    # Anchor on "now" so current_interval_idx resolves to something
    start = pd.Timestamp.now(tz="UTC").floor(f"{INTERVAL_MIN}min").to_pydatetime()
    ts = pd.date_range(start=start, periods=n, freq=f"{INTERVAL_MIN}min", tz="UTC")
    return ts.values


def _make_plan(n: int = 24, soc: float = 0.5) -> Plan:
    ts = _make_timestamps(n)
    return Plan.from_self_consume(
        timestamps=ts,
        import_c_kwh=np.full(n, 20.0),
        export_c_kwh=np.full(n, 8.0),
        load_kw=np.full(n, 1.0),
        solar_kw=np.full(n, 0.0),
        soc_now=soc,
    )


def _make_snapshot() -> Snapshot:
    return Snapshot(
        timestamp=datetime.now(timezone.utc),
        soc_pct=50.0,
        load_kw=1.2,
        solar_kw=0.0,
        battery_power_kw=0.0,
        price_forecast=pd.DataFrame(),
        weather_forecast=pd.DataFrame(),
    )


def test_summarize_plan_changes_structure():
    """Returns all expected keys and nested keys."""
    plan = _make_plan()
    diff = summarize_plan_changes(plan, previous=None)

    assert set(diff.keys()) == {
        "current_interval",
        "changed_from_previous",
        "previous_action",
        "next_6h_summary",
    }

    cur = diff["current_interval"]
    assert set(cur.keys()) == {
        "timestamp",
        "action",
        "charge_kw",
        "discharge_kw",
        "import_c",
        "export_c",
        "soc_before",
        "soc_after",
    }

    nxt = diff["next_6h_summary"]
    assert set(nxt.keys()) == {
        "charge_intervals",
        "discharge_intervals",
        "hold_solar_intervals",
        "idle_intervals",
        "peak_import_price_c",
        "min_export_price_c",
    }


def test_summarize_plan_no_previous():
    """previous=None produces a valid diff with changed_from_previous=False."""
    plan = _make_plan()
    diff = summarize_plan_changes(plan, previous=None)

    assert diff["changed_from_previous"] is False
    assert diff["previous_action"] is None
    assert diff["current_interval"]["action"] in {a.value for a in Action}


def test_summarize_plan_with_previous():
    """changed_from_previous reflects differing actions at the current interval."""
    prev = _make_plan()
    curr = _make_plan()

    # Force a divergence at the current interval
    idx = curr.current_interval_idx or 0
    curr.charge(idx, 1.0)  # sets action CHARGE_GRID

    diff = summarize_plan_changes(curr, previous=prev)
    assert diff["current_interval"]["action"] == Action.CHARGE_GRID.value
    assert diff["previous_action"] == Action.IDLE.value
    assert diff["changed_from_previous"] is True


def test_explain_plan_fallback_without_api_key(monkeypatch):
    """With no ANTHROPIC_API_KEY, returns a templated string rather than crashing."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    plan = _make_plan()
    snap = _make_snapshot()

    result = explain_plan(plan, snap, previous_plan=None)

    assert isinstance(result, str)
    assert len(result) > 0
    # Fallback mentions price numbers
    assert "c/kWh" in result


def test_explain_plan_mocked_anthropic(monkeypatch):
    """Mock the Anthropic client and verify the call includes key facts."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy")
    plan = _make_plan()
    # Force a known action so we can check the prompt payload
    idx = plan.current_interval_idx or 0
    plan.charge(idx, 2.0)

    snap = _make_snapshot()

    mock_text_block = SimpleNamespace(type="text", text="Charging because prices are cheap. SOC is low.")
    mock_response = SimpleNamespace(content=[mock_text_block])

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("anthropic.Anthropic", return_value=mock_client) as mock_ctor:
        result = explain_plan(plan, snap, previous_plan=None)

    assert result == "Charging because prices are cheap. SOC is low."
    mock_ctor.assert_called_once()
    assert mock_client.messages.create.called

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-opus-4-7"
    assert call_kwargs["max_tokens"] == 200

    # Prompt should mention the current action and SOC
    user_msg = call_kwargs["messages"][0]["content"]
    assert Action.CHARGE_GRID.value.lower() in user_msg.lower() or "charging" in user_msg.lower()
    assert "SOC" in user_msg or "soc" in user_msg.lower()


def test_explain_plan_api_error_falls_back(monkeypatch):
    """If Anthropic raises, we get the fallback template, not an exception."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy")
    plan = _make_plan()
    snap = _make_snapshot()

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("network down")

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = explain_plan(plan, snap, previous_plan=None)

    assert isinstance(result, str)
    assert "c/kWh" in result  # fallback template signature


def test_fallback_rationale_renders_action():
    """Fallback template mentions the action verb and a SOC percentage."""
    plan = _make_plan()
    idx = plan.current_interval_idx or 0
    plan.discharge(idx, 1.0)
    diff = summarize_plan_changes(plan, previous=None)

    text = _fallback_rationale(diff, _make_snapshot())
    assert "Discharg" in text
    assert "%" in text
