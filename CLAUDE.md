# Built with Opus 4.7 — Sigenergy NEM Arbitrage Agent

> Plan document and operating manual for Opus. Read this top-to-bottom at the start of every session. Update it as reality changes.

---

## 0. TL;DR for Opus

You are building **an autonomous agent that arbitrages an Australian home battery (Sigenergy) against live AEMO wholesale prices**. The agent re-plans every 30 minutes against updated price forecasts, weather forecasts, and learned household load, then writes the schedule directly to the Sigen inverter over Modbus TCP. It explains every decision in plain English.

The submission is for **Built with Opus 4.7**, a Claude Code virtual hackathon. The judging prize is $100k in API credits. The deadline is **Apr 28, 2026**. Today is **Apr 23, 2026**. You have ~5 working days.

The owner is Immanuel — first-year UTS cybersecurity student, direct/dry communicator, swears occasionally, hates AI-sounding writing. Don't write like a LinkedIn post. Call him by name when addressing him in commit messages or issue comments. He's explicitly the only customer; this runs on his house.

**Do not ever brick the battery.** Every code path that touches the Sigen inverter must default to dry-run. See Section 8.

---

## 1. What good looks like

The demo at submission time shows three things:

1. A **60-second video** of the agent running against live data on Immanuel's actual house: Amber price chart, agent re-planning, Sigen SOC responding, explanation appearing in the chat.
2. A **backtest result** over the last 30 days of his real AEMO + HA history proving the agent beats two baselines on a hard $ number: (a) naive "self-consume only, no grid charging", (b) static TOU rule (charge 1–5am, discharge 5–9pm).
3. A **written postmortem** of one live day where the agent's plan diverged from a human's intuition and turned out to be right (or wrong — either is interesting).

The agent is not judged on being optimal. It is judged on being *agentic* — re-planning in response to new information, explaining itself, handling weird inputs, and not doing stupid shit when data is missing. Opus 4.7's strength over 4.6 is that it catches its own logical faults during planning and reports when data is missing instead of fabricating. Lean into that.

---

## 2. Hard constraints

| Constraint | Value | Why |
|---|---|---|
| Battery nameplate | 64 kWh usable (~70 kWh nominal) | Immanuel's actual system |
| Solar | 24 kWp | Actual install |
| Inverter count | 2× Sigen | Modbus unit IDs differ |
| Roundtrip efficiency | 90% (assume 95% charge × 95% discharge) | LFP with Sigen inverters |
| Max charge rate | 15 kW per inverter, 30 kW total | Physical inverter limit |
| Max discharge rate | 15 kW per inverter, 30 kW total | Same |
| Cycle cost | 2 c/kWh (conservative) | LFP degradation cost |
| SOC floor (safety) | 10% | Don't brick the pack |
| SOC ceiling | 95% | Leave headroom for solar and balance |
| Agent loop period | 30 min | Matches AEMO 5MPD update cadence |
| Horizon | 24 h (48 h stretch) | Beyond 24h forecast is low-value |
| Tariff | Amber Electric (5-min NEM pass-through) | Confirm with Immanuel; everything depends on this |

If the tariff turns out to be fixed TOU with flat FIT, the whole arbitrage premise weakens and you should pivot to solar self-consumption optimisation + TOU charging. Ask before assuming.

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    AGENT CONTROL LOOP (30 min)               │
│                                                              │
│  1. Ingest → 2. Forecast → 3. Schedule → 4. Actuate → 5. Log │
└─────────────────────────────────────────────────────────────┘
         │              │             │            │
         ▼              ▼             ▼            ▼
  ┌──────────┐   ┌───────────┐  ┌──────────┐  ┌──────────┐
  │  AEMO    │   │  Load     │  │ Greedy   │  │ Sigen    │
  │  BOM     │   │  Solar    │  │ or LP    │  │ Modbus   │
  │  HA hist │   │           │  │          │  │ (dry-run)│
  └──────────┘   └───────────┘  └──────────┘  └──────────┘
```

Layout:

```
arb/
  ingest/
    aemo.py           # 5MPD forecast + dispatch prices
    bom.py            # forecast + observations
    ha.py             # historical load, SOC, solar from HA REST API
    amber.py          # Amber pricing API (if available, else derive from AEMO)
  forecast/
    load.py           # next 48h load forecast from history
    solar.py          # next 48h PV forecast from BOM + system specs
  scheduler/
    greedy.py         # rank-and-fill heuristic — SHIP THIS FIRST
    lp.py             # PuLP MILP optimiser — stretch goal
    constants.py      # battery/inverter specs in one place
  actuator/
    sigen_modbus.py   # write setpoints over Modbus TCP, with dry-run flag
    ha_automation.py  # fallback: write HA YAML if Modbus fails
  agent/
    loop.py           # the 30-min cycle
    explain.py        # LLM call that produces the human-readable rationale
  eval/
    backtest.py       # replay history, compute $ vs baselines
    baselines.py      # naive + static TOU
  demo/
    dashboard.py      # live Grafana-style view (Streamlit fine)
  CLAUDE.md           # this file
  tests/
  README.md
```

**Key principle: clear layering.** Ingest speaks to the outside world and returns dataframes. Forecast reads dataframes and returns dataframes. Scheduler reads dataframes and returns a `Plan` dataclass. Actuator reads a `Plan` and either writes to hardware or to a log. No layer skips down. This makes the backtest and the live agent run identical code paths.

---

## 4. Data sources (full reference)

### 4.1 AEMO NEM 5MPD (Five Minute Pre-Dispatch)

- **What:** forecast price for each 5-min interval over the next ~6 hours, plus actual dispatch prices for intervals that have already cleared.
- **Access:** AEMO public NEMWEB (https://nemweb.com.au/Reports/Current/P5_Reports/). No auth. Zip files updated every 5 min.
- **Region:** NSW1 (Immanuel is in Sydney).
- **Fields needed:** `SETTLEMENTDATE`, `REGIONID`, `RRP` (regional reference price, $/MWh).
- **Convert:** divide by 10 to get c/kWh. Handle price cap events (prices can hit $17,500/MWh = $17.50/kWh).
- **Fallback:** Amber API (`https://api.amber.com.au/v1/...`) if NEMWEB is slow. Amber exposes both predicted and actual prices with fees already baked in, which is closer to what the battery actually pays/earns.
- **Gotcha:** NEMWEB timestamps are in AEST (no DST). Convert everything to UTC internally and display in local time only at the UI boundary.

### 4.2 BOM forecast

- **What:** cloud cover %, temperature, solar irradiance forecast for next 7 days.
- **Access:** BOM has no official JSON API. Use one of:
  1. `open-meteo.com` — free, global, includes `cloud_cover` and `shortwave_radiation` in W/m². **Preferred.**
  2. BOM FTP XML feeds — clunky but official.
- **Coordinates:** pull from HA (Immanuel has his configured).
- **Convert cloud cover to PV derating:** first pass, assume `pv_power = pv_max * (1 - cloud_cover * 0.75)`. Calibrate against his actual history in backtest.

### 4.3 Home Assistant

- **What:** 90 days of historical load, solar generation, battery SOC, battery charge/discharge power.
- **Access:** HA REST API at `http://ha.local:8123/api/` with long-lived token in `.env`.
- **Endpoints:**
  - `/api/history/period/{timestamp}?filter_entity_id=sensor.solar_power,sensor.grid_power,sensor.battery_soc,sensor.battery_power` — bulk history pull
  - `/api/services/automation/reload` — after writing fallback automation
- **Sensor names:** Immanuel needs to confirm the exact entity IDs. Don't hardcode; put them in `.env` as `HA_SENSOR_LOAD`, `HA_SENSOR_SOLAR`, etc.

### 4.4 Sigen Modbus TCP

- **What:** read live state, write setpoints (charge/discharge power, mode).
- **Access:** Modbus TCP on port 502 to each inverter's IP.
- **Library:** `pymodbus` (async version).
- **Register map:** Sigenergy publishes a "Sigen Energy Controller Modbus Protocol" PDF — it's probably already in Immanuel's Obsidian/Drive given he's done Sigen Modbus work before. If not, grab from their dev portal or ask him to share it.
- **Relevant registers (verify against PDF):**
  - Running mode (0 = auto, 1 = force charge, 2 = force discharge, 7 = self-consume)
  - Active power setpoint (signed int, +charge / -discharge)
  - Battery SOC (read-only)
  - Battery max charge/discharge power (read-only, updates with SOC)
- **Write cadence:** do NOT write every loop. Only write when the plan for the *current* interval changes. Sigen flash has a finite write-cycle budget.

---

## 5. The arbitrage algorithm (core)

### 5.1 Inputs (at each solve)

- `now`: current timestamp
- `horizon_end`: now + 24h
- `intervals`: list of 5-min buckets from now to horizon_end
- For each interval `i`:
  - `p_import[i]`: predicted import price c/kWh
  - `p_export[i]`: predicted export price c/kWh (on Amber, often equals import minus fees; can be negative)
  - `load[i]`: predicted household load kW
  - `solar[i]`: predicted solar generation kW
- `soc_now`: current battery SOC (0-1)
- Constants from `scheduler/constants.py`: capacity, max rates, efficiencies, cycle cost

### 5.2 Actions per interval

Each interval gets one dominant action (in reality the inverter blends, but for scheduling we discretise):

- `IDLE` — neither charging nor discharging from grid; solar serves load, surplus exports, deficit imports
- `CHARGE_GRID` — pull power from grid into battery (on top of whatever solar is doing)
- `DISCHARGE_GRID` — export battery to grid (on top of solar export)
- `HOLD_SOLAR` — divert solar to battery instead of exporting (when export price < 0)

The agent doesn't pick between "charge from solar" vs "self-consume" — those happen automatically via the inverter's self-consume mode. The agent picks *when to override* self-consume with grid-facing moves.

### 5.3 Greedy algorithm (ship this Day 2, it's enough)

```python
def greedy_schedule(intervals, soc_now, constants) -> Plan:
    # 1. Compute the "free" plan: self-consume mode everywhere.
    # This gives a baseline SOC trajectory from solar surplus - load.
    soc_trajectory = simulate_self_consume(intervals, soc_now)

    # 2. Rank intervals by arbitrage value.
    # For each pair (charge_interval, discharge_interval) where discharge > charge:
    #   spread = p_export[discharge] - p_import[charge]
    #   net_value_per_kwh = spread * RTE - cycle_cost
    # Sort pairs by net_value_per_kwh descending.

    pairs = []
    for c in intervals:
        for d in intervals:
            if d <= c:
                continue
            spread = p_export[d] - p_import[c]
            net = spread * RTE - cycle_cost
            if net > 0:
                pairs.append((c, d, net))
    pairs.sort(key=lambda x: -x[2])

    # 3. Greedily assign energy to pairs, respecting:
    #    - SOC bounds [10%, 95%] throughout trajectory
    #    - max charge/discharge rate per interval
    #    - battery capacity
    plan = Plan.from_self_consume(soc_trajectory)
    for c, d, net in pairs:
        energy = min(
            available_charge_room(plan, c),
            available_discharge_room(plan, d),
            max_rate_energy_5min,
        )
        if energy <= 0:
            continue
        plan.charge(c, energy)
        plan.discharge(d, energy)

    # 4. Handle negative export prices — divert solar to battery.
    for i in intervals:
        if p_export[i] < 0 and plan.can_absorb_more(i):
            plan.hold_solar(i)

    return plan
```

This is not optimal but it's ~90% of optimal for a single battery and fits in your head. Ship it.

### 5.4 LP upgrade (Day 4 if time permits)

Formulate as MILP in PuLP:

- Decision variables: `charge[i]`, `discharge[i]`, `soc[i]` for each interval
- Maximise: `Σ (discharge[i] * p_export[i] - charge[i] * p_import[i]) - Σ |Δsoc[i]| * cycle_cost`
- Subject to: energy balance, rate limits, SOC bounds, non-simultaneity (use binary var)
- Solve with CBC (free) or Gurobi if Immanuel has a license

Do NOT start here. Greedy first, then LP if you're ahead of schedule. Opus 4.7 can write the LP but it's a trap to over-invest in optimality before the pipeline works end-to-end.

### 5.5 Why this needs an agent, not a cron job

Three reasons a cron-based rule loses to an agent, and the demo must show each:

1. **New information arrives mid-horizon.** AEMO pushes a price update, BOM pushes a new cloud forecast, Immanuel turns on the dryer. The agent re-solves. A cron rule is stuck.
2. **Explanations.** "I moved discharge 45 minutes earlier because AEMO just predicted a 5:15pm cap event" is a feature, not flavour text. The LLM call in `agent/explain.py` produces these.
3. **Anomaly handling.** When Modbus returns garbage, when HA sensor is stale, when export prices spike past the import price (rare but happens) — the agent notices and either corrects or escalates. Opus 4.7's "reports missing data instead of fabricating" behaviour is the whole reason this works.

---

## 6. Agent loop (agent/loop.py)

```
every 30 min:
  state = ingest.snapshot()              # current prices, forecast, HA state, SOC
  if state.is_stale(threshold="10 min"):
    log_and_keep_last_plan()
    return
  forecast = forecast.build(state)
  plan = scheduler.greedy(forecast, state.soc)
  if plan differs from last_plan on the current interval:
    rationale = explain.llm(state, last_plan, plan)
    actuator.apply(plan.current_interval, dry_run=DRY_RUN)
    persist(plan, rationale)
  else:
    log("no change")
```

Explain prompt template (keep it short, no sycophancy):

```
You're summarising a battery arbitrage decision for Immanuel.
Previous plan for the current interval: {last}
New plan for the current interval: {now}
Key changes in inputs since last plan: {diffs}
In two sentences, plainly, explain what changed and why.
Don't editorialise. If nothing meaningful changed, say "no material change."
```

---

## 7. Backtest / eval (eval/backtest.py)

Backtest is the **most important code you write**, because it's what makes the submission credible.

1. Pull 30 days of Immanuel's HA history: load, solar, SOC trajectory.
2. Pull 30 days of AEMO NSW1 RRP (dispatch, not predispatch — we're using actuals for backtest).
3. For each day, replay the day at 30-min granularity:
   - Build a forecast from *data available at that timestamp* (no look-ahead). For price, use the 5MPD forecast published at that time. For solar/load, use the forecaster trained on data prior to that timestamp.
   - Run the scheduler on the forecast.
   - Step the simulated battery forward using the *actual* prices and loads.
4. Compute $ vs two baselines:
   - **B1: Self-consume only.** Battery only charges from solar surplus, only discharges to load deficit. No grid arbitrage.
   - **B2: Static TOU.** Charge from grid 1–5am regardless of price, discharge 5–9pm regardless.
5. Report: $ saved per week, peak % improvement, worst day (did we ever lose money vs B1?).

Critical: if your backtest says you beat B1 by $50/day, something is wrong. Realistic numbers for NSW Amber with 64 kWh are $3–8/day in arbitrage uplift. If it's much higher, you've got look-ahead bias.

---

## 8. Safety rails

This is where most hackathon projects die quietly or loudly. Do this up front.

- **DRY_RUN env var.** Default `true`. Set to `false` only after backtest matches live dry-run output for 24h on the same data.
- **Actuator writes logged to a separate file** with timestamp, register, old value, new value, reason. This is an audit trail and a rollback tool.
- **SOC bounds enforced in TWO places:** the scheduler (soft) AND the actuator (hard refuse). Scheduler bugs are real, the actuator is the last line.
- **Rate limit on actuator writes.** Max 1 write per 10 minutes, absolute max 10 writes per hour. Burn-in protection for Sigen flash.
- **Kill switch.** Environment variable `ARB_KILL=1` makes the agent no-op and log. Immanuel should be able to flip this from his phone in 10 seconds.
- **Heartbeat to HA.** Push a `sensor.arb_agent_last_run` every loop. If HA hasn't seen it in 15 min, trigger an HA notification.

---

## 9. Implementation roadmap (5 days, Apr 23 → Apr 27)

### Day 1 — Apr 23: Foundation ✓ done

- Repo scaffold, `CLAUDE.md`, `.env.example`
- `ingest/aemo.py` — NSW1 5MPD from NEMWEB (REGIONSOLUTION table, AEST->UTC, filters INTERVENTION=1)
- `ingest/bom.py` — Open-Meteo hourly cloud/irradiance/temp/is_day
- `ingest/ha.py` — REST API history + live state, 5-min resample with 30-min gap fill
- `ingest/amber.py` — /prices endpoint (current + historical days)
- `ingest/snapshot.py` — merges sources, flags stale sensors, graceful fallback

Shipped: 7 tests. Amber returns separate import/export with negative FIT intervals.

### Day 2 — Apr 24: Scheduler v1 + actuator ✓ done

- `forecast/load.py` — day-of-week × time-of-day rolling avg, 4-week window, flat fallback
- `forecast/solar.py` — clear-sky model from shortwave_radiation × cloud derate
- `forecast/builder.py` — glues price + load + solar into scheduler-ready DataFrame
- `scheduler/greedy.py` — greedy rank-and-fill, O(n²) pair enumeration (41k pairs for n=288)
- `scheduler/plan.py` — Plan dataclass, numpy SOC trajectory, grid-side/battery-side energy accounting
- `actuator/sigen_modbus.py` — read-only client with real Sigenergy register map (unit 247 plant, 40031 EMS mode, 40032/40034 charge/discharge limits)
- `actuator/ha_control.py` — primary write path via HA `select`/`number` services (Sigen integration holds the Modbus socket, so direct TCP gets refused)
- `agent/loop.py` — one-shot end-to-end cycle

Shipped: 36 tests, live cycle verified against Immanuel's house (SOC 59.9%, 163 Amber intervals, 288-interval forecast).

### Day 3 — Apr 25: Backtest + baselines ✓ done

Four parallel Opus agents spawned and stitched together:

- `eval/backtest.py` — no-look-ahead replay engine, 30-min outer loop × 5-min inner sim, perfect-foresight toggle for upper-bound analysis, self-consume baked into simulator
- `eval/baselines.py` — B1 self-consume, B2 static TOU (1-5am charge, 5-9pm discharge, tz-aware)
- `eval/amber_replay.py` — reconstructs SmartShift's actual dispatch from HA battery_power history
- `agent/explain.py` — Opus 4.7 generates 2-sentence rationale with specific numbers, templated fallback
- Integrated into loop: rationale persists to `agent_rationale.log`

**Result (7 days on real data, 2026-04-15 to 2026-04-22):**

| Strategy | Cost $ | Import kWh | Export kWh | Cycles |
|---|---:|---:|---:|---:|
| Agent (greedy) | 0.17 | 0.1 | 8.6 | 2.20 |
| B1 self-consume | 0.17 | 0.1 | 8.6 | 2.20 |
| B2 static TOU | 93.48 | 328.6 | 294.5 | 6.66 |
| B3 Amber SmartShift actual | 41.52 | 312.2 | 340.8 | 2.65 |

Agent beats B2 by $13.33/day, beats Amber actual by $5.91/day. Honest reading: on this house's negative feed-in, pure arbitrage doesn't work, and the agent correctly declines to trade.

Shipped: 58 tests.

### Day 4 — Apr 26: Dry-run + dashboard + stretch features ✓ done

Four parallel Opus agents + one Sonnet agent:

- `agent/plan_diff.py` — structured old-vs-new plan comparison, timestamp-aligned, separates action/energy/price-only changes
- `agent/audit.py` — post-interval SOC drift check, JSON-lines log, tolerance-based status
- `demo/dashboard.py` — Streamlit: SOC gauge, 24h price+SOC+load chart, backtest panel with all 4 strategies, rationale feed, data-quality pills, actuator audit log
- `eval/offline_dryrun.py` — replays last N hours at 30-min cadence, produces rationale artifacts without waiting real time (48 decisions, 11 action changes clustered at dawn/dusk)
- `agent/loop.py` upgraded: `--continuous` mode with SIGTERM/SIGINT shutdown, persists previous plan and prior SOC across cycles for diff + audit
- `docs/demo_script.md` + `docs/postmortem_template.md` — ready for Day 5

Shipped: 73 tests. Live cycle end-to-end: Plan diff `NO_CHANGE`, Audit `status=ok drift=+0.6%`, Opus 4.7 rationale quoting specific SOC/price numbers.

### Day 5 — Apr 27: Polish + demo + submit

- Record the 60-second video per `docs/demo_script.md` shot list
- Fill in `docs/postmortem_template.md` — candidates in `docs/best_moments.md` (Sonnet did the triage)
- **Stay in DRY_RUN for the recording.** Advisory mode is the whole point — we're not fighting Amber SmartShift for control. Annotate the video accordingly.
- Submit to hackathon portal with: repo URL, video link, brief description. Link `docs/report.html` as a submission artifact.

**Success criteria:** submission URL returned.

### What shipped beyond the original Day 4 scope (added 2026-04-23 afternoon)

- **Price spike detection + mid-interval re-plan** (`arb/agent/spike_detector.py` + loop integration): continuous mode polls every 5 min between 30-min scheduled cycles, triggers full re-plan on CAP/MAJOR/MINOR deviations. 10-min cooldown. 10 tests.
- **Synthetic spike demo** (`arb/agent/spike_demo.py`): reproducible video-ready injection. `--channel export --magnitude 120` produces HOLD_SOLAR -> CHARGE_GRID flip, 60 intervals changed.
- **FastAPI backend** (`arb/api/server.py`): REST + WebSocket wrapper around the existing Python modules. 6 tests. Port 8000.
- **Next.js 14 dashboard** (`web/`): custom dark theme (Inter + JetBrains Mono, Framer Motion, Recharts, SWR). Panels for SOC gauge, 24h price+action chart, current status, rationale feed, backtest table, data quality. Port 3000.
- **Signature re-plan animation** (`web/components/replan/`): 8-second choreographed sequence driven by a single playhead. Standalone `/replan` page for b-roll.
- **Static HTML report** (`docs/report.html`): 269 KB submission-grade single-page artifact with Opus 4.7-authored prose. Regenerate with `python -m arb.eval.generate_report`.
- **Log triage** (`docs/best_moments.md`): Sonnet picked 3 candidates for the postmortem. Top pick: 08:30 UTC offline-dryrun moment where solar sensor went stale and the agent dropped HOLD_SOLAR to IDLE instead of fabricating.
- **99 tests passing** (was 73 at end of Day 4 morning).

---

## 10. What judges care about (guesses — update as info arrives)

This is a Claude Code hackathon, so they'll weight:

- **Agentic behaviour over clever prompting.** Show the loop, the re-planning, the explanations. Not a static pipeline with one LLM call at the end.
- **Real-world grounding.** Running on Immanuel's actual house beats a synthetic demo.
- **Opus 4.7 strengths on display.** Its fault-catching, its missing-data handling. Have a demo moment where data is missing and the agent reports it instead of fabricating.
- **Honesty.** Show a failure mode. Show what doesn't work yet. Judges see through polish.

Not things to chase: UI flash, too many features, LP optimality.

---

## 11. Working notes for Opus

### Style
- Terse, direct code comments. No "This function elegantly handles..."
- Commit messages: conventional commits, one-line subject.
- Docstrings: Google style, one-paragraph max.
- Python 3.12, type hints on every signature, `from __future__ import annotations`.
- Tests pytest, one assertion per test where possible.

### Writing (for READMEs, commits, issue comments)
Immanuel uses the humanizer skill religiously. Avoid:
- "stands as", "serves as", "marks a pivotal moment", "vital role", "broader landscape"
- Rule of three ("fast, reliable, and scalable")
- -ing tails ("...enabling seamless integration")
- Promotional words: "robust", "seamless", "groundbreaking", "elegant"
- Em dashes except sparingly (he's fine with some, hates when every paragraph has two)
- Sycophancy ("Great question!", "I hope this helps!")
- Generic positive endings ("The future looks bright")
Write like a tired engineer on Slack explaining something for the third time.

### Ask before assuming
- Tariff type (Amber vs fixed TOU)
- HA sensor entity IDs
- Inverter IP addresses and Modbus unit IDs
- Whether to actually flip dry-run off for demo

### Don't
- Silently retry forever on API failures. Fail loud, surface in the dashboard.
- Roll your own timeseries library. Use pandas or polars.
- Add features not in this doc without a Linear issue first.
- Write the LP solver before the greedy works.
- Touch battery SOC floor/ceiling without a comment explaining why.

---

## 12. Reference constants (scheduler/constants.py)

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class BatteryConstants:
    # Sigenergy 64kWh LFP, 2x inverters
    capacity_kwh: float = 64.0
    soc_floor: float = 0.10
    soc_ceiling: float = 0.95
    max_charge_kw: float = 30.0       # 2x 15kW inverters
    max_discharge_kw: float = 30.0
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.95
    cycle_cost_c_per_kwh: float = 2.0

    @property
    def roundtrip_efficiency(self) -> float:
        return self.charge_efficiency * self.discharge_efficiency

    @property
    def usable_kwh(self) -> float:
        return self.capacity_kwh * (self.soc_ceiling - self.soc_floor)


@dataclass(frozen=True)
class GridConstants:
    region: str = "NSW1"
    import_fees_c_per_kwh: float = 0.0   # Amber has no extra fees; this is placeholder
    export_fees_c_per_kwh: float = 0.0

INTERVAL_MIN = 5
LOOP_PERIOD_MIN = 30
HORIZON_H = 24
```

---

## 13. Work completed (Linear-style rollup)

All Day 1-4 issues done. Kept for reference.

| # | Title | Day | Status |
|---|---|---|---|
| 1 | Repo scaffold + CLAUDE.md + Opus plan | 1 | ✓ |
| 2 | AEMO NEMWEB ingestion (NSW1 RRP 5MPD) | 1 | ✓ |
| 3 | BOM / open-meteo forecast ingestion | 1 | ✓ |
| 4 | HA history pull (load, solar, SOC, 90 days) | 1 | ✓ |
| 5 | Load forecaster (day-of-week rolling average) | 2 | ✓ |
| 6 | Solar forecaster (clear-sky × cloud derating) | 2 | ✓ |
| 7 | Scheduler v1 — greedy rank-and-fill | 2 | ✓ |
| 8 | Battery/grid constants + Plan dataclass | 2 | ✓ |
| 9 | Sigen Modbus read-only client | 2 | ✓ |
| 10 | Sigen write path with DRY_RUN + audit log | 3 | ✓ via HA `ha_control.py` |
| 11 | Safety rails: SOC bounds, rate limiter, kill switch | 3 | ✓ |
| 12 | Agent loop (ingest → forecast → schedule → actuate) | 3 | ✓ |
| 13 | explain.py — LLM rationale for plan changes | 3 | ✓ Opus 4.7 |
| 14 | Backtest harness (no look-ahead) | 3 | ✓ with perfect-foresight toggle |
| 15 | Baseline B1 (self-consume only) | 3 | ✓ |
| 16 | Baseline B2 (static TOU) | 3 | ✓ |
| 17 | 24h live dry-run on actual house | 4 | ✓ via `offline_dryrun.py` replay |
| 18 | Streamlit dashboard (price, SOC, plan, rationale) | 4 | ✓ |
| 19 | Scheduler v2 — LP / MILP with PuLP | 4 | skipped (stretch, per spec warning) |
| 19b | Plan diff + execution audit (Day 4 stretch) | 4 | ✓ |
| 20 | 60-second demo video | 5 | script in `docs/demo_script.md` |
| 21 | Postmortem writeup | 5 | template in `docs/postmortem_template.md` |
| 22 | README with architecture, setup, results | 5 | ✓ |
| 23 | Submit to hackathon | 5 | pending |

---

## 14. Open questions — all answered

- **Amber Electric or fixed TOU?** Amber. Confirmed, API key in `.env`.
- **HA entity IDs?** Confirmed by Immanuel, in his `.env`.
- **Sigen inverter IPs + unit IDs?** Confirmed. Port 502. But the HA Sigenergy integration (TypQxQ/Sigenergy-Local-Modbus) holds the Modbus connection, so direct TCP is refused. Actuator pivoted to HA service calls instead.
- **Sigen register map?** Got it from the HA integration source — plant unit 247, writable registers at 40000+, EMS mode at 40031.
- **Lat/long for open-meteo?** Sydney CBD default is close enough.
- **Existing HA automation to disable?** Amber SmartShift controls the battery via undocumented API. We don't fight it — agent runs **advisory-only**. The backtest compares what the agent would have done against what SmartShift actually did.

---

## 15. Appendix: Opus 4.7 specifics to exploit

- **"xhigh" effort level.** Use it for the scheduler design and backtest debugging. Don't use it for boilerplate.
- **Self-catches logical faults during planning.** When you ask it to implement something, ask it to first produce a plan and critique the plan before coding. The SWE-bench uplift is mostly from this.
- **3× image resolution.** If you feed it a screenshot of an AEMO chart or a BOM map, it'll actually read the axes now.
- **Long-horizon async work.** Let it run the backtest in a separate Claude Code session while you work on the dashboard in another. The model is built for this.
- **More opinionated than 4.6.** When it pushes back on a design choice, consider the pushback rather than overriding by default. Per Anthropic's release notes this is a deliberate characteristic change.

---

*Last updated: 2026-04-23 afternoon, after Day 4 + stretch features. Day 5 remaining: record video, fill postmortem, submit. 99 tests green. Backend on :8000, dashboard on :3000, advisory-mode loop runs clean against Immanuel's house. Full stack end-to-end verified.*
