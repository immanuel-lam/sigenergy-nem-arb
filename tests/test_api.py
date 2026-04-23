"""Tests for arb.api.server — FastAPI wrapper."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from arb.api import server as srv
from arb.ingest.snapshot import Snapshot
from arb.scheduler.constants import INTERVAL_MIN
from arb.scheduler.plan import Plan


@pytest.fixture
def client() -> TestClient:
    return TestClient(srv.app)


def _snapshot(ts: datetime | None = None) -> Snapshot:
    return Snapshot(
        timestamp=ts or datetime.now(timezone.utc),
        soc_pct=55.0,
        load_kw=1.2,
        solar_kw=0.3,
        battery_power_kw=0.0,
        price_forecast=pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-04-23", periods=4, freq="5min", tz="UTC"),
                "import_c_kwh": [10.0, 12.0, 11.0, 9.0],
                "export_c_kwh": [5.0, 6.0, 5.5, 4.5],
            }
        ),
        weather_forecast=pd.DataFrame(),
        stale_sensors=[],
        warnings=[],
    )


def _plan(n: int = 6) -> Plan:
    start = pd.Timestamp.now(tz="UTC").floor(f"{INTERVAL_MIN}min").to_pydatetime()
    ts = pd.date_range(start=start, periods=n, freq=f"{INTERVAL_MIN}min", tz="UTC").values
    return Plan.from_self_consume(
        timestamps=ts,
        import_c_kwh=np.full(n, 15.0),
        export_c_kwh=np.full(n, 7.0),
        load_kw=np.full(n, 1.0),
        solar_kw=np.full(n, 0.0),
        soc_now=0.5,
    )


def test_health_endpoint(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "version" in body


def test_snapshot_endpoint_structure(client: TestClient) -> None:
    with patch.object(srv, "take_snapshot", return_value=_snapshot()):
        r = client.get("/snapshot")
    assert r.status_code == 200
    body = r.json()
    for key in ("timestamp", "soc_pct", "load_kw", "solar_kw", "battery_power_kw",
                "price_forecast", "stale_sensors", "warnings"):
        assert key in body, f"missing {key}"
    assert body["soc_pct"] == 55.0
    assert body["price_forecast"]["n"] == 4


def test_rationale_log_reading(client: TestClient, tmp_path: Path) -> None:
    log_path = tmp_path / "rationale.log"
    lines = [
        "2026-04-23T00:00:00+00:00\tIDLE\tFirst entry",
        "2026-04-23T00:30:00+00:00\tCHARGE_GRID\tSecond one",
        "2026-04-23T01:00:00+00:00\tDISCHARGE_GRID\tThird",
    ]
    log_path.write_text("\n".join(lines) + "\n")

    with patch.object(srv.loop_mod, "RATIONALE_LOG", log_path):
        r = client.get("/rationale?limit=3")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    assert body[0]["action"] == "IDLE"
    assert body[1]["rationale"] == "Second one"
    assert body[2]["action"] == "DISCHARGE_GRID"


def test_backtest_endpoint_returns_cached(client: TestClient) -> None:
    canned = {
        "agent": {"cost_dollars": -1.23, "import_kwh": 10.0, "export_kwh": 5.0, "cycles": 0.5},
        "b1_self_consume": {"cost_dollars": 0.45, "import_kwh": 12.0, "export_kwh": 4.0, "cycles": 0.4},
        "b2_static_tou": {"cost_dollars": 1.10, "import_kwh": 15.0, "export_kwh": 3.0, "cycles": 0.6},
        "b3_amber_actual": {"cost_dollars": 0.20, "import_kwh": 11.0, "export_kwh": 4.5, "cycles": 0.5},
        "period": {"start": "2026-04-16T00:00:00+00:00", "end": "2026-04-23T00:00:00+00:00", "days": 7},
    }
    # Clear cache so we go through the run path.
    srv._BACKTEST_CACHE["data"] = None
    srv._BACKTEST_CACHE["computed_at"] = None

    with patch.object(srv, "_run_backtest_7d", return_value=canned) as mock_run:
        r1 = client.get("/backtest/latest")
        r2 = client.get("/backtest/latest")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["agent"]["cost_dollars"] == -1.23
    assert r1.json()["cached"] is False
    assert r2.json()["cached"] is True
    # Second call should have hit the cache, not run the backtest again.
    assert mock_run.call_count == 1

    for key in ("agent", "b1_self_consume", "b2_static_tou", "b3_amber_actual", "period"):
        assert key in r1.json()


def test_spike_demo_endpoint(client: TestClient) -> None:
    fake_result = SimpleNamespace(
        baseline_plan=_plan(),
        spiked_plan=_plan(),
        diff_summary="no material change",
        baseline_rationale="Idle because prices flat.",
        spiked_rationale="Charging because spike incoming.",
        action_changed=True,
        spike_start=datetime(2026, 4, 23, 3, 0, tzinfo=timezone.utc),
        spike_end=datetime(2026, 4, 23, 3, 15, tzinfo=timezone.utc),
        spike_c_kwh=120.0,
    )
    with patch.object(srv, "run_spike_demo", return_value=fake_result):
        r = client.post(
            "/spike-demo",
            json={"magnitude_c_kwh": 120, "minutes_ahead": 10, "duration_min": 15, "channel": "export"},
        )
    assert r.status_code == 200
    body = r.json()
    for key in ("baseline_plan", "spiked_plan", "diff_summary", "action_changed",
                "spike_start", "spike_end", "spike_c_kwh"):
        assert key in body
    assert body["action_changed"] is True
    assert body["spike_c_kwh"] == 120.0
    # plan_to_dict shape sanity
    assert "timestamps" in body["baseline_plan"]
    assert "actions" in body["spiked_plan"]


def test_cors_headers(client: TestClient) -> None:
    r = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code in (200, 204)
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
