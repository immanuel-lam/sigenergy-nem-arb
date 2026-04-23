# Sigenergy NEM Arbitrage Agent

Autonomous agent that plans battery charge/discharge against live AEMO wholesale prices and writes the schedule to a Sigenergy inverter. Built for the Built with Opus 4.7 hackathon (deadline 2026-04-28). Runs on exactly one house: Immanuel's place in Sydney, with a 64 kWh Sigenergy LFP pack, 2× Sigen inverters, and 24 kWp of solar on an Amber Electric tariff.

Repo: [github.com/immanuel-lam/sigenergy-nem-arb](https://github.com/immanuel-lam/sigenergy-nem-arb)

## What it does

Every 30 minutes the agent runs a cycle:

1. **Ingest** — AEMO 5MPD prices (NSW1), Open-Meteo cloud/irradiance forecast, HA sensors for SOC, load, solar, battery power. Amber API if the key is set, otherwise derive from NEMWEB.
2. **Forecast** — 48h load from day-of-week rolling average over HA history; 48h solar from clear-sky × cloud derating.
3. **Schedule** — greedy rank-and-fill. Enumerates (charge_interval, discharge_interval) pairs, sorts by `(spread × RTE − cycle_cost)`, assigns energy subject to SOC bounds and rate limits.
4. **Actuate** — writes the current interval's setpoint through the Sigenergy HA integration (`select.plant_remote_ems_control_mode` + charge/discharge limits). Dry-run by default. Audit log at `actuator_audit.log`.
5. **Log / explain** — plan summary, reasons, SOC trajectory.

Runs in **advisory mode** for now. Amber SmartShift keeps controlling the battery. The agent builds its own plan on the same inputs, logs what it would have done, and the backtest compares strategies against Amber's actual dispatch. Nothing writes to the inverter until the dry-run output matches live behaviour for a full day.

## Architecture

```
ingest/       forecast/     scheduler/    actuator/
  aemo.py       load.py       greedy.py     ha_control.py   (primary)
  amber.py      solar.py      plan.py       sigen_modbus.py (fallback)
  bom.py        builder.py    constants.py
  ha.py
  snapshot.py
                                  |
                                  v
                           agent/loop.py   (ingest -> forecast -> schedule -> actuate)
```

Layers don't skip. Ingest returns dataframes. Forecast reads dataframes, returns dataframes. Scheduler reads a forecast dataframe and a starting SOC, returns a `Plan`. Actuator reads a `Plan` and either writes setpoints or logs intent. Same code runs live and in backtest.

## Current status

Day 1 (done):
- Repo scaffold, `CLAUDE.md` plan doc, `.env.example`.
- `arb/ingest/aemo.py` pulling NSW1 RRP from NEMWEB.
- `arb/ingest/ha.py` history pull against HA REST API.
- `arb/ingest/bom.py` via open-meteo.
- `arb/ingest/amber.py` with timestamp alignment fixed.
- `arb/ingest/snapshot.py` merges the sources and flags stale sensors.

Day 2 (done):
- `arb/forecast/load.py` day-of-week rolling mean.
- `arb/forecast/solar.py` clear-sky × cloud derate.
- `arb/forecast/builder.py` glues price + load + solar into one frame.
- `arb/scheduler/greedy.py` + `plan.py` — greedy rank-and-fill, SOC trajectory, rate caps.
- `arb/scheduler/constants.py` — battery specs in one place.
- `arb/actuator/sigen_modbus.py` read-only client against the real Sigen register map.
- `arb/actuator/ha_control.py` write path via HA integration (dry-run default, audit log).
- `arb/agent/loop.py` — one-shot cycle runs end-to-end.

Pending (Day 3+):
- `arb/agent/explain.py` — LLM rationale between consecutive plans.
- `arb/eval/backtest.py` — no-look-ahead replay harness.
- `arb/eval/baselines.py` — self-consume only, static TOU.
- Continuous 30-min loop (currently `--once` only).
- `arb/demo/dashboard.py` — Streamlit.
- Rate limiter on writes (1/10min, 10/hr cap).
- Kill switch is wired via env var but no HA heartbeat yet.
- Tests. No `tests/` directory yet. This is a gap.

## Hardware / assumptions

- Battery: Sigenergy 64 kWh LFP, 2× inverters (15 kW each).
- Solar: 24 kWp.
- Region: NSW1.
- Tariff: Amber Electric (5-min NEM pass-through). If this turns out to be fixed TOU the arbitrage premise weakens and we pivot to self-consumption optimisation.
- SOC floor 10%, ceiling 95%, round-trip efficiency 90%, cycle cost 2 c/kWh (conservative LFP).
- Agent loop 30 min, horizon 24 h, scheduler granularity 5 min.

## Setup

```bash
git clone https://github.com/immanuel-lam/sigenergy-nem-arb
cd sigenergy-nem-arb
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Fill in HA_URL, HA_TOKEN, Sigen IPs/unit IDs, lat/long, AMBER_API_KEY if you have one.
python -m arb.agent.loop --once --dry-run
```

Running the loop against live data:

```bash
python -m arb.ingest.snapshot        # prints current price, SOC, next 6h forecast
python -m arb.agent.loop --once      # one full cycle, dry-run by default
python -m arb.agent.loop --once --force   # ignore stale-sensor check
```

## Safety

- `DRY_RUN=true` is the default in `.env.example` and `loop.py`. The actuator refuses to write when set.
- SOC bounds enforced in two places: the scheduler clips the plan, the actuator hard-refuses writes that would breach 10%/95%.
- `ARB_KILL=1` makes the loop no-op and log. Environment flip from anywhere.
- Every write attempt (real or dry) logs to `actuator_audit.log` with timestamp, entity, old/new, reason.
- Advisory mode for the whole hackathon demo window. No live writes until the backtest-to-dry-run match is clean.

Not done yet: rate limiter on writes, HA heartbeat sensor. Both on the Day 3 list.

## Results

Pending. Backtest harness is Day 3 work. Realistic expectation for NSW Amber with 64 kWh is $3–8/day arbitrage uplift over pure self-consume. If the first backtest prints much higher than that, something has look-ahead bias and the number gets thrown out until it's fixed.

## Hackathon context

Submission for Built with Opus 4.7 (Claude Code virtual hackathon, Apr 28 2026 deadline). The agent is built to lean on Opus 4.7's specific behaviour: it catches its own logical faults during planning, and it reports missing data instead of making it up. Both matter here — a battery scheduler that fabricates a price forecast when NEMWEB is down is a battery scheduler that tries to arbitrage against a hallucination. The loop explicitly surfaces stale sensors and refuses to run unless `--force` is passed.

Owner: Immanuel. Sole user, sole house.
