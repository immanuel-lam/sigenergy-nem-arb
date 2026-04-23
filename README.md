# Sigenergy NEM Arbitrage Agent

Autonomous agent that plans battery charge/discharge against live AEMO wholesale prices. Built for the Built with Opus 4.7 hackathon (Cerebral Valley, submission deadline 2026-04-26 8 PM EST). Runs on exactly one house: Immanuel's place in Sydney, with a 64 kWh Sigenergy LFP pack, 2× Sigen inverters, and 24 kWp of solar on an Amber Electric tariff.

Licensed MIT. Backend (Python), frontend (Next.js), data, and prompts are all in this repo.

Repo: [github.com/immanuel-lam/sigenergy-nem-arb](https://github.com/immanuel-lam/sigenergy-nem-arb)

## What it does

Every 30 minutes the agent runs a cycle:

1. **Ingest** — AEMO 5MPD prices (NSW1), Open-Meteo cloud and irradiance forecast, HA sensors for SOC, load, solar, battery power. Amber API if the key is set.
2. **Forecast** — 48h load from day-of-week rolling average over HA history; 48h solar from clear-sky × cloud derating.
3. **Schedule** — greedy rank-and-fill. Enumerates (charge, discharge) interval pairs, sorts by `(spread × RTE − cycle_cost)`, assigns energy under SOC bounds and rate caps.
4. **Diff + audit** — structured diff of new plan vs the last one; audits how far actual SOC drifted from what the previous plan expected.
5. **Explain** — Opus 4.7 writes two sentences of rationale quoting the specific prices and SOC.
6. **Actuate** — writes the current interval's setpoint through the Sigenergy HA integration (`select.plant_remote_ems_control_mode` + charge/discharge limits). Dry-run by default. Every attempt logs to `actuator_audit.log`.

Runs in **advisory mode** for the hackathon. Amber SmartShift keeps control of the battery. The agent builds its own plan on the same inputs, logs what it would have done, and the backtest compares strategies against Amber's actual dispatch reconstructed from HA history.

## Results

7-day backtest on Immanuel's real HA + Amber data (2026-04-15 to 2026-04-22). Perfect-foresight upper bound — real forecasting error would reduce the headline numbers somewhat.

| Strategy | Cost $ | Import kWh | Export kWh | Cycles |
|---|---:|---:|---:|---:|
| **Agent (greedy)** | **0.17** | 0.1 | 8.6 | 2.20 |
| B1 self-consume | 0.17 | 0.1 | 8.6 | 2.20 |
| B2 static TOU | 93.48 | 328.6 | 294.5 | 6.66 |
| B3 Amber SmartShift (actual) | 41.52 | 312.2 | 340.8 | 2.65 |

Agent beats static TOU by **$13.33/day** and Amber's actual dispatch by **$5.91/day**. The honest reading: Amber's feed-in on this house sat at or below zero all week, so pure grid arbitrage loses money. The agent correctly declines to trade and lands on self-consume. SmartShift's aggressive round-tripping (340 kWh exported) repeatedly hit the negative export price.

The Amber comparison is reconstructed from HA history (`arb/eval/amber_replay.py`) and should be read as indicative — Amber optimises for things we don't model, like network peak tariffs.

## Web UI

FastAPI backend on port 8000, Next.js dashboard on port 3000. Both read the same persisted plan and logs the agent loop writes — no separate data store.

### Starting it

Two terminals:

```bash
# Terminal 1 — backend
cd /path/to/sigenergy-nem-arb
source .venv/bin/activate
uvicorn arb.api.server:app --port 8000

# Terminal 2 — frontend
cd web
NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev
```

Open http://localhost:3000.

### What the dashboard shows (top to bottom)

1. **Header strip** — live Sydney clock, DRY_RUN badge, sensor health dot.
2. **SOC gauge** — current battery state of charge with floor (10%) and ceiling (95%) marked. Colored band tells you how close to either limit.
3. **24h price + action chart** — import price (amber line) and export price (rose line, filled when negative). Shaded bands show the agent's planned action per interval: cyan = charge from grid, green = discharge, violet = hold solar, dim slate = idle.
4. **Current interval status** — the action the agent is taking RIGHT NOW, current import/export price, countdown to the next scheduled re-plan.
5. **Rationale feed** — last 5 decisions, each with the timestamp, the action, and the 2-sentence Opus 4.7 explanation.
6. **Signature animation slot** — empty until you click the spike demo button. Then the 8-second re-plan animation plays here.
7. **Backtest table** — agent vs self-consume vs static TOU vs Amber SmartShift actual. Agent row has a cyan glow.
8. **Data quality** — a pill per source (HA, Amber, AEMO, BOM, Modbus). Hover for the specific warning if one turns amber/red.

### Interactive moves

- **"Inject synthetic spike"** button (top-right): POSTs `/spike-demo` to the backend. Runs the greedy scheduler twice — once normally, once with a synthetic +120 c/kWh export spike injected 10 min in the future. The response has both plans, and the `<ReplanSection>` below plays the signature animation showing the agent react. This is THE demo moment.
- **`/replan` page** (standalone): http://localhost:3000/replan. Full-viewport, dark, no header. Loops the re-plan animation every ~10 seconds. Press space to replay immediately. Use for b-roll in the video.
- **Static report**: open `docs/report.html` directly in a browser (no server needed). It's a ~270 KB single-page document with the backtest, architecture, and Opus 4.7-authored prose. This is a submission artifact — link it from your hackathon entry.
- **Impact analysis**: `docs/impact.md` — scale numbers for the submission form, with citations.

## Architecture

```
ingest/       forecast/     scheduler/    actuator/
  aemo.py       load.py       greedy.py     ha_control.py   (primary)
  amber.py      solar.py      plan.py       sigen_modbus.py (reference)
  bom.py        builder.py    constants.py
  ha.py
  snapshot.py

agent/          eval/           api/              web/
  loop.py         backtest.py     server.py         app/page.tsx
  explain.py      baselines.py    (FastAPI)         app/replan/page.tsx
  plan_diff.py    amber_replay.py                   components/
  audit.py        offline_dryrun.py                 hooks/useLiveData.ts
                  run_backtest.py
```

Layers don't skip. Ingest returns dataframes. Forecast reads dataframes, returns dataframes. Scheduler takes a forecast dataframe and a starting SOC, returns a `Plan`. Actuator reads a `Plan` and either writes setpoints or logs intent. Same code runs live and in backtest.

## Status

Shipped:
- Ingest: AEMO, Amber (historical + forecast), Open-Meteo, HA REST
- Forecast: load day-of-week, solar clear-sky × cloud
- Scheduler: greedy rank-and-fill with `Plan` SOC trajectory, rate caps, hard SOC bounds
- Actuator: HA service-call path via `select.plant_remote_ems_control_mode`, audit log, rate limiter (10 writes/hr), SOC hard refuse, `ARB_KILL` switch
- Agent loop: `--once` and `--continuous` modes, signal shutdown, previous-plan persistence
- Explain: Opus 4.7 via anthropic SDK, fallback template if API down
- Plan diff: structured old vs new plan comparison
- Execution audit: post-interval SOC drift check
- Backtest: no-look-ahead replay with perfect-foresight toggle, self-consume in the sim layer
- Baselines: B1 self-consume, B2 static TOU, B3 Amber actual reconstruction
- Offline 24h dry-run: replays last N hours through the live pipeline
- FastAPI backend (`arb/api/server.py`): 6 tests, read-only, WebSocket tick
- Next.js dashboard with custom dark theme, Recharts, Framer Motion, SWR
- Signature re-plan animation: standalone `/replan` page for demo recording
- Static HTML report: `docs/report.html` with backtest results and Opus 4.7 prose
- Scale / Impact analysis: `docs/impact.md` (~$12.5M AUD/year conservative aggregate across Amber-style spot-tariff households)
- 146 tests, all passing (39 new edge-case tests added in the final hardening pass)

Also shipped:
- Spike detection + mid-interval re-plan (`arb/agent/spike_detector.py`): continuous loop polls every 5 min, triggers full cycle on CAP/MAJOR/MINOR deviations from the plan's assumed prices. 10-min cooldown. Synthetic demo in `arb/agent/spike_demo.py` because NSW1 had no real spikes in the last 30 days.

Not shipped:
- HA heartbeat sensor. Kill switch works via env but no external liveness signal.
- Sensitivity analysis (capacity sweep, cycle cost calibration) — brainstormed, not built.
- Real NSW price cap captured live — none happened in the demo window.

## Hardware and assumptions

- Battery: Sigenergy 64 kWh LFP, 2× inverters (15 kW each)
- Solar: 24 kWp
- Region: NSW1
- Tariff: Amber Electric (5-min NEM pass-through)
- SOC floor 10%, ceiling 95%, round-trip efficiency 90%, cycle cost 2 c/kWh (conservative LFP)
- Agent loop 30 min, horizon 24 h, scheduler granularity 5 min

## Setup

```bash
git clone https://github.com/immanuel-lam/sigenergy-nem-arb
cd sigenergy-nem-arb
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Fill in HA_URL, HA_TOKEN, AMBER_API_KEY, lat/long, Sigen HA entity IDs.
python -m pytest tests/
python -m arb.agent.loop --once
```

Live usage:

```bash
python -m arb.ingest.snapshot               # prints current price, SOC, forecast coverage
python -m arb.agent.loop --once             # one cycle, dry-run by default
python -m arb.agent.loop --continuous       # keep going every 30 min
python -m arb.eval.run_backtest 7           # 7-day backtest, all strategies
python -m arb.eval.offline_dryrun 24        # replay last 24h at 30-min cadence

# Backend API
uvicorn arb.api.server:app --port 8000

# Frontend (separate terminal)
cd web && npm install && npm run dev
```

## Safety

- `DRY_RUN=true` default. Actuator refuses writes when set.
- SOC bounds enforced twice: scheduler clips, actuator hard-refuses breaches of 10% / 95%.
- Rate limiter: max 10 writes per hour through `ha_control.py`. Protects Sigen flash.
- `ARB_KILL=1` makes the loop no-op and log. Flip from anywhere.
- Every write attempt (real or dry) logs to `actuator_audit.log`: timestamp, entity, value, reason, dry-run flag.
- Every rationale logs to `agent_rationale.log` with the exact action and timestamp.
- Every cross-cycle comparison logs to `execution_audit.log` with SOC drift and status.
- No live writes this hackathon cycle. Advisory mode only.

## Hackathon context

Submission for Built with Opus 4.7 (Claude Code virtual hackathon, 2026-04-28 deadline). The agent leans on Opus 4.7's specific traits: it catches logical faults during planning, and it reports missing data instead of fabricating. Both matter here — a battery scheduler that hallucinates a price forecast when NEMWEB is down tries to arbitrage against a ghost. The loop explicitly surfaces stale sensors and refuses to run without `--force`, and the dashboard shows sensor health as coloured status pills.

See `docs/demo_script.md` for the 60-second video plan and `docs/postmortem_template.md` for the writeup skeleton.

Owner: Immanuel. Sole user, sole house.
