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

### Day 1 — Apr 23 (today): Foundation

- Repo scaffold, `CLAUDE.md`, `.env.example`, pre-commit hooks
- `ingest/aemo.py` pulling NSW1 RRP from NEMWEB, parsing, returning a dataframe
- `ingest/ha.py` pulling 30 days of history into parquet cache
- `ingest/bom.py` via open-meteo
- First plot: price history + load + solar on one chart. This is the "I can see the problem" moment.

**Success criteria:** `python -m arb.ingest.snapshot` prints current price, current SOC, next 6h forecast. No scheduler yet.

### Day 2 — Apr 24: Scheduler v1 + actuator dry-run

- `forecast/load.py` — simple approach: same day-of-week, same time-of-day, 4-week rolling average. Good enough.
- `forecast/solar.py` — clear-sky model × (1 - cloud_cover × 0.75).
- `scheduler/greedy.py` — the core algorithm above.
- `actuator/sigen_modbus.py` in **read-only mode** (connect, read SOC, don't write).
- End-to-end: `python -m arb.agent.loop --once --dry-run` prints a plan and what *would* be written.

**Success criteria:** one full agent cycle runs dry against live data and prints a reasonable plan.

### Day 3 — Apr 25: Backtest + baselines

- `eval/backtest.py` — the replay engine.
- `eval/baselines.py` — B1 and B2.
- First backtest report on 7 days. Debug the arbitrage numbers until they look realistic.
- `agent/explain.py` — LLM call for rationale.

**Success criteria:** backtest report shows agent > B1 > B2 (usually) on $/week, with realistic magnitudes.

### Day 4 — Apr 26: Live dry-run + dashboard

- Run the agent in dry-run mode on live data for 24 hours.
- Compare its intended actions against what the inverter actually did in self-consume mode.
- `demo/dashboard.py` — Streamlit with live price chart, SOC, planned vs actual, rationale log.
- Fix whatever the 24h dry-run reveals (it will reveal things).

**Success criteria:** the dashboard is screenshot-worthy. The 24h dry-run log shows the agent re-planning in response to real events.

### Day 5 — Apr 27: Polish + demo + submit

- Record the 60-second video. Keep it tight.
- Write the postmortem: pick one interesting moment from Day 4's dry-run where the plan changed unexpectedly, explain why.
- README with architecture diagram, setup, results.
- **Flip DRY_RUN=false for the actual demo recording only if the backtest-to-dry-run match was clean.** Otherwise stay in dry-run and annotate the video. Do not ship a "yolo it worked" to the hackathon and cook the battery.
- Submit.

**Success criteria:** submission URL returned.

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

## 13. Linear issues to create

Flat list. Parent: "Built with Opus 4.7 — Sigenergy NEM Arbitrage Agent" project. Create these on Linear or paste into the command line; the labels and priorities are worth keeping.

| # | Title | Day | Priority | Estimate |
|---|---|---|---|---|
| 1 | Repo scaffold + CLAUDE.md + Opus plan | 1 | Urgent | 2 |
| 2 | AEMO NEMWEB ingestion (NSW1 RRP 5MPD) | 1 | Urgent | 3 |
| 3 | BOM / open-meteo forecast ingestion | 1 | High | 2 |
| 4 | HA history pull (load, solar, SOC, 90 days) | 1 | Urgent | 3 |
| 5 | Load forecaster (day-of-week rolling average) | 2 | High | 2 |
| 6 | Solar forecaster (clear-sky × cloud derating) | 2 | High | 2 |
| 7 | Scheduler v1 — greedy rank-and-fill | 2 | Urgent | 5 |
| 8 | Battery/grid constants + Plan dataclass | 2 | Urgent | 1 |
| 9 | Sigen Modbus read-only client | 2 | High | 3 |
| 10 | Sigen Modbus write path with DRY_RUN flag + audit log | 3 | Urgent | 3 |
| 11 | Safety rails: SOC hard bounds, rate limiter, kill switch | 3 | Urgent | 2 |
| 12 | Agent loop (ingest → forecast → schedule → actuate) | 3 | Urgent | 3 |
| 13 | explain.py — LLM rationale for plan changes | 3 | High | 2 |
| 14 | Backtest harness (no look-ahead) | 3 | Urgent | 5 |
| 15 | Baseline B1 (self-consume only) | 3 | High | 1 |
| 16 | Baseline B2 (static TOU) | 3 | High | 1 |
| 17 | 24h live dry-run on actual house | 4 | Urgent | 2 |
| 18 | Streamlit dashboard (price, SOC, plan, rationale) | 4 | High | 3 |
| 19 | Scheduler v2 — LP / MILP with PuLP (stretch) | 4 | Low | 5 |
| 20 | 60-second demo video | 5 | Urgent | 3 |
| 21 | Postmortem writeup (one interesting day) | 5 | High | 2 |
| 22 | README + architecture diagram | 5 | High | 2 |
| 23 | Submit to hackathon | 5 | Urgent | 1 |

---

## 14. Open questions (answer before Day 1 ends)

- [ ] Amber Electric or fixed TOU? (Big impact on premise.)
- [ ] HA entity IDs for load, solar, SOC, battery_power?
- [ ] Sigen inverter IPs and Modbus unit IDs?
- [ ] Do you have the Sigen Modbus register map PDF handy?
- [ ] Location lat/long for open-meteo?
- [ ] Is there an existing HA automation for the battery that we need to disable before the agent takes over?

---

## 15. Appendix: Opus 4.7 specifics to exploit

- **"xhigh" effort level.** Use it for the scheduler design and backtest debugging. Don't use it for boilerplate.
- **Self-catches logical faults during planning.** When you ask it to implement something, ask it to first produce a plan and critique the plan before coding. The SWE-bench uplift is mostly from this.
- **3× image resolution.** If you feed it a screenshot of an AEMO chart or a BOM map, it'll actually read the axes now.
- **Long-horizon async work.** Let it run the backtest in a separate Claude Code session while you work on the dashboard in another. The model is built for this.
- **More opinionated than 4.6.** When it pushes back on a design choice, consider the pushback rather than overriding by default. Per Anthropic's release notes this is a deliberate characteristic change.

---

*Last updated: 2026-04-23. Keep this doc current or delete it — a stale plan is worse than no plan.*
