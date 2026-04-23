"""Run a backtest against Immanuel's actual HA history + Amber prices.

Compares four strategies:
  - Agent (greedy arbitrage)
  - B1 self-consume only
  - B2 static TOU (1-5am charge, 5-9pm discharge)
  - B3 Amber actual (what Amber SmartShift actually did)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from arb.eval.amber_replay import compute_amber_cost
from arb.eval.backtest import run_backtest
from arb.eval.baselines import self_consume_strategy, static_tou_strategy
from arb.ingest import amber, ha
from arb.scheduler.greedy import schedule

log = logging.getLogger(__name__)


def main(days: int = 7, perfect_foresight: bool = True) -> None:
    """Run the backtest and print results."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)

    log.info("=== Fetching %d days of data ===", days)
    log.info("HA history...")
    history = ha.fetch_history(days=days + 2, end=end)

    log.info("Amber historical prices...")
    prices = amber.fetch_historical_prices(days=days + 1)
    if prices.empty:
        log.warning("Amber historical empty, falling back to Amber current")
        prices = amber.fetch_prices()

    log.info("History: %d rows from %s to %s", len(history),
             history["timestamp"].min(), history["timestamp"].max())
    log.info("Prices: %d rows from %s to %s", len(prices),
             prices["timestamp"].min() if not prices.empty else "N/A",
             prices["timestamp"].max() if not prices.empty else "N/A")
    log.info("Forecast mode: %s", "PERFECT FORESIGHT (upper bound)" if perfect_foresight else "persistence (realistic lower bound)")

    # Initial SOC from earliest history row
    initial_soc = (history["soc_pct"].dropna().iloc[0] if not history.empty else 50.0) / 100.0

    results = {}

    for name, strat in [
        ("agent_greedy", schedule),
        ("B1_self_consume", self_consume_strategy),
        ("B2_static_tou", static_tou_strategy),
    ]:
        log.info("=== Running %s ===", name)
        result = run_backtest(
            history=history,
            prices=prices,
            start=start,
            end=end,
            strategy_fn=strat,
            initial_soc=initial_soc,
            strategy_name=name,
            perfect_foresight=perfect_foresight,
        )
        results[name] = result
        log.info(
            "%s: cost=$%.2f, import=%.1f kWh, export=%.1f kWh, cycles=%.2f",
            name, result.total_cost_dollars, result.total_import_kwh,
            result.total_export_kwh, result.total_charge_cycles,
        )

    # B3: what Amber actually did
    log.info("=== Running B3_amber_actual ===")
    # Filter history/prices to the backtest window for fair comparison
    start_ts = pd.Timestamp(start) if pd.Timestamp(start).tz else pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end) if pd.Timestamp(end).tz else pd.Timestamp(end, tz="UTC")
    hist_window = history[(history["timestamp"] >= start_ts) & (history["timestamp"] < end_ts)]
    amber_cost = compute_amber_cost(hist_window, prices)
    log.info(
        "B3_amber_actual: cost=$%.2f, import=%.1f kWh, export=%.1f kWh, cycles=%.2f",
        amber_cost["total_cost_dollars"], amber_cost["total_import_kwh"],
        amber_cost["total_export_kwh"], amber_cost["total_cycles"],
    )

    # Summary
    print("\n" + "=" * 72)
    print(f"{days}-day backtest ($ lower = better). Forecast: "
          f"{'perfect foresight' if perfect_foresight else 'persistence'}")
    print("=" * 72)
    print(f"{'Strategy':<22} {'Cost $':>10} {'Import kWh':>12} {'Export kWh':>12} {'Cycles':>8}")
    print("-" * 72)
    for name, r in results.items():
        print(f"{name:<22} {r.total_cost_dollars:>10.2f} {r.total_import_kwh:>12.1f} {r.total_export_kwh:>12.1f} {r.total_charge_cycles:>8.2f}")
    print(f"{'B3_amber_actual':<22} {amber_cost['total_cost_dollars']:>10.2f} {amber_cost['total_import_kwh']:>12.1f} {amber_cost['total_export_kwh']:>12.1f} {amber_cost['total_cycles']:>8.2f}")
    print("=" * 72)

    agent = results["agent_greedy"].total_cost_dollars
    b1 = results["B1_self_consume"].total_cost_dollars
    b2 = results["B2_static_tou"].total_cost_dollars
    amber_cost_val = amber_cost["total_cost_dollars"]

    print(f"\nAgent saves vs B1 (self-consume): ${b1 - agent:+.2f}  (${(b1-agent)/days:+.2f}/day)")
    print(f"Agent saves vs B2 (static TOU):   ${b2 - agent:+.2f}  (${(b2-agent)/days:+.2f}/day)")
    if pd.notna(amber_cost_val):
        print(f"Agent saves vs Amber actual:      ${amber_cost_val - agent:+.2f}  (${(amber_cost_val-agent)/days:+.2f}/day)")


if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    perfect = "--realistic" not in sys.argv
    main(days, perfect_foresight=perfect)
