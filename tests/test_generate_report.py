"""Tests for the static HTML report generator."""
from __future__ import annotations

from unittest.mock import patch

import pytest


def _mock_bundle():
    from arb.eval.generate_report import BacktestBundle, StrategyRow
    return BacktestBundle(
        days=7,
        rows=[
            StrategyRow("agent_greedy", "Agent", 0.17, 0.1, 8.6, 2.2),
            StrategyRow("B1_self_consume", "Self-consume only", 0.17, 0.1, 8.6, 2.2),
            StrategyRow("B2_static_tou", "Static TOU", 93.48, 328.6, 294.5, 6.66),
            StrategyRow("B3_amber_actual", "Amber SmartShift", 41.52, 312.2, 340.8, 2.65),
        ],
        agent_cost=0.17,
        b1_cost=0.17,
        b2_cost=93.48,
        amber_cost=41.52,
    )


def test_generate_report_writes_file(tmp_path):
    """With llm=False, generator writes a non-empty HTML file."""
    from arb.eval.generate_report import generate_report

    out = tmp_path / "report.html"
    with patch("arb.eval.generate_report._run_backtests", return_value=_mock_bundle()):
        generate_report(output_path=str(out), llm=False)

    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "Week in Review" in content
    assert "Sigenergy" in content


def test_report_includes_backtest_numbers(tmp_path):
    """Backtest cost numbers appear in the rendered HTML."""
    from arb.eval.generate_report import generate_report

    out = tmp_path / "report.html"
    with patch("arb.eval.generate_report._run_backtests", return_value=_mock_bundle()):
        generate_report(output_path=str(out), llm=False)

    content = out.read_text()
    assert "93.48" in content or "93.5" in content
    assert "41.52" in content or "41.5" in content


def test_fallback_prose_when_llm_fails(tmp_path):
    """If LLM returns None, generator falls back to templated prose."""
    from arb.eval.generate_report import generate_report

    out = tmp_path / "report.html"
    with patch("arb.eval.generate_report._run_backtests", return_value=_mock_bundle()), \
         patch("arb.eval.generate_report._llm_prose", return_value=None):
        generate_report(output_path=str(out), llm=True)

    assert out.exists()
    assert out.stat().st_size > 5000


def test_report_handles_missing_logs(tmp_path, monkeypatch):
    """Generator works even when rationale/audit/spike logs don't exist."""
    from arb.eval import generate_report as gr

    monkeypatch.setattr(gr, "_RATIONALE_LOG", tmp_path / "nope_rationale.log")
    monkeypatch.setattr(gr, "_OFFLINE_RATIONALE_LOG", tmp_path / "nope_offline.log")
    monkeypatch.setattr(gr, "_EXECUTION_LOG", tmp_path / "nope_execution.log")
    monkeypatch.setattr(gr, "_SPIKE_LOG", tmp_path / "nope_spike.log")

    out = tmp_path / "report.html"
    with patch("arb.eval.generate_report._run_backtests", return_value=_mock_bundle()):
        gr.generate_report(output_path=str(out), llm=False)

    assert out.exists()
    assert "Week in Review" in out.read_text()
