# Best Moments — Log Triage

Scanned 48 offline dry-run rationale entries, 48 plan JSONL entries, 3 live agent rationale entries, 4 actuator audit entries, and 1 execution audit entry across 5 files on 2026-04-23T02:30:00+00:00.

---

## Top 3 candidates for postmortem

### Candidate 1: Solar sensor goes stale mid-day, agent drops to IDLE rather than guessing

**Timestamp:** 2026-04-22T08:30:00+00:00  
**Action change:** HOLD_SOLAR -> IDLE  
**Rationale (verbatim):**
> Sitting idle for the next 5 minutes, neither charging nor discharging with battery at 87.1%. Export price is negative at -8.21 c/kWh so there's no point pushing anything out, and with no discharge or charge windows in the next 6 hours we're just riding on self-consumption until something actually moves.

**Why this one:** This is the Opus 4.7 "reports missing data instead of fabricating" behaviour the CLAUDE.md calls out as the model's defining feature. The snapshot confirms `stale_sensors=['solar_kw']` — the solar feed had gone dark, and the agent stopped doing HOLD_SOLAR rather than continuing to pretend there was solar to divert. It pivoted cleanly to IDLE. Two intervals later (09:30) it re-engaged HOLD_SOLAR when battery power readings suggested generation had resumed — same sensor gap, opposite conclusion. That pair tells a real story: the agent didn't just safe-mode and stay there, it re-evaluated when conditions shifted. That's the postmortem in three sentences.

**Context from surrounding entries:**
- T-30min (08:00): HOLD_SOLAR — holding at 88.3% SOC with export negative at -8.96 c/kWh, solar still live
- T (08:30): IDLE — solar_kw sensor gone stale; agent drops HOLD_SOLAR rather than assume PV still present
- T+30min (09:00): IDLE — still stale, holds idle; no discharge windows within 6h, nothing to act on

---

### Candidate 2: SOC ceiling triggers mid-morning transition, HOLD_SOLAR abandoned

**Timestamp:** 2026-04-22T03:00:00+00:00  
**Action change:** HOLD_SOLAR -> IDLE  
**Rationale (verbatim):**
> Sitting idle this half-hour, letting the 9.8 kW load pull from the grid at 7.48 c/kWh rather than cycling the battery. SOC's already at 95% so there's nothing worth topping up, export is negative (-0.23 c/kWh) so dumping is pointless, and we're holding the charge for the 30.21 c/kWh peak later.

**Why this one:** SOC hit 95% ceiling (the configured ceiling from constants.py) and the agent stopped absorbing despite a still-live solar feed. It cites the specific charge cost (7.48 c/kWh), the worthless export price, and the downstream target (30.21 c/kWh peak) in one sentence. The numbers are grounded — you can cross-check against the rationale log. However, compared to Candidate 1, this is a simpler decision (battery full, stop). It's a cleaner explanation but less interesting because there's no data ambiguity.

**Context from surrounding entries:**
- T-30min (02:30): IDLE -> HOLD_SOLAR — SOC at 90.5%, solar at 6.3 kW, export went negative, started banking PV
- T (03:00): HOLD_SOLAR -> IDLE — SOC hits 95.15%, ceiling reached, shuts off despite active solar
- T+30min (03:30): IDLE — "battery's full at 99.6% so nothing to do"; holds through the ceiling for next two hours

---

### Candidate 3: Price feed staleness triggers passive IDLE across afternoon

**Timestamp:** 2026-04-22T13:00:00+00:00  
**Action change:** HOLD_SOLAR -> IDLE  
**Rationale (verbatim):**
> Sitting idle for this 5-minute interval, self-consuming at 77.6% SOC. No material change — export is negative (-8.22 c/kWh) so dumping to grid is pointless, peak ahead is only 24.83 c/kWh which isn't worth cycling the battery for, and solar_kw is stale so I'm not chasing phantom PV.

**Why this one:** The agent explicitly names the stale sensor ("solar_kw is stale so I'm not chasing phantom PV") and cites the 24.83 c/kWh peak as an insufficient spread to cycle for. It's transparent about uncertainty and conservative for the right reason. The weakness as a postmortem pick: this is followed by 35 consecutive IDLE entries with flat 10 c/kWh price feed — the price data itself goes stale and the rest of the afternoon is dead from a decision standpoint. Less narrative arc than Candidate 1.

**Context from surrounding entries:**
- T-30min (12:30): HOLD_SOLAR — still soaking at 78.8% SOC, export -7.86 c/kWh
- T (13:00): HOLD_SOLAR -> IDLE — solar sensor stale, explicitly rejected as reason for action
- T+30min (13:30): IDLE — price feed flattens to 10 c/kWh both ways; now two separate data gaps compound

---

## Top 3 candidates for video subtitles (ranked)

### For the opening hook (0:00-0:10)

> Parking the solar into the battery rather than exporting, holding through this interval at 71.4% SOC with no grid charge or discharge. Export is negative at -0.85 c/kWh (you'd pay to push it out) and it gets worse ahead at -10.47, so we're banking the 1.88 kW of PV for the 30.21 c/kWh peak later.

**Timestamp:** 2026-04-22T01:30:00+00:00  
**Why:** Opens the 24h replay at the first interesting decision. Concrete numbers from the first sentence. Explains the whole strategy (avoid negative export, bank for peak) without jargon. Trim to: *"Export is negative at -0.85 c/kWh — you'd pay to push it out. Banking 1.88 kW for the 30.21 c/kWh peak later."* — 22 words.

---

### For the re-plan moment (0:20-0:35)

> Soaking solar into the battery this half-hour, bumping SOC from 91.4% to 93.8% rather than exporting. Export price is negative (-9.17 c/kWh) so shoving PV to the grid actually costs us, and with 24 hold-solar intervals ahead and peak import at 30.21 c/kWh later, every free electron in the battery is worth more than dumping it.

**Timestamp:** 2026-04-22T07:00:00+00:00  
**Why:** This is the re-engagement after the 06:30 IDLE dip — IDLE -> HOLD_SOLAR flip at 91.4% SOC. The phrase "every free electron in the battery is worth more than dumping it" is punchy and quotable. Trim to: *"Export price is -9.17 c/kWh — shoving PV to the grid costs us. Every free electron is worth more in the battery."* — 24 words.

---

### For the close (0:50-0:60)

> Sitting idle for this 5-minute interval, self-consuming at 77.6% SOC. No material change — export is negative (-8.22 c/kWh) so dumping to grid is pointless, peak ahead is only 24.83 c/kWh which isn't worth cycling the battery for, and solar_kw is stale so I'm not chasing phantom PV.

**Timestamp:** 2026-04-22T13:00:00+00:00  
**Why:** "I'm not chasing phantom PV" is the best line in the log. Shows the agent being explicit about sensor uncertainty rather than silently failing. Good closer for the video because it demonstrates the honesty angle judges care about. Trim to: *"Solar sensor stale. Not chasing phantom PV — peak at 24.83 c/kWh isn't worth cycling the battery for."* — 18 words.

---

## Honesty flags (surface these for README/postmortem)

- **Stale solar sensor, 08:30-13:00**: `solar_kw` goes `None` at 08:30 and stays that way for the rest of the dry-run. The agent calls it out explicitly in the 13:00 rationale but the 09:30 entry re-engages HOLD_SOLAR with a stale sensor — worth explaining in the postmortem how it can flip back to HOLD_SOLAR without solar data (battery power readings infer generation).
- **Price feed flattens post-13:00**: From 2026-04-22T14:00 onward, both import and export pin at 10 c/kWh flat for the remaining 11 hours. Rationale entries after 14:00 all say "no price data past now." The 30.21 c/kWh discharge window the agent was banking for never materialised in the forecast window. SOC drifted from 77% down to 57% on self-consumption only.
- **Execution audit drift (minor)**: Single entry at 2026-04-23T01:47:52 shows planned SOC delta of -0.65% vs actual delta of +0.00%, drift +0.65%. Status is `ok` but the battery wasn't doing what the plan said — actual battery power was -0.55 kW (charging slightly) vs planned zero. Not alarming but worth noting.
- **Actuator writes all dry_run=true**: All 4 actuator entries write `Maximum Self Consumption` mode in dry-run. No real writes occurred. This is correct behaviour but means the demo has no live hardware evidence yet.

---

## Ranked action-change moments (all 11, for reference)

1. 2026-04-22T08:30:00 — HOLD_SOLAR -> IDLE — solar sensor stale, agent drops action (**top postmortem pick**)
2. 2026-04-22T03:00:00 — HOLD_SOLAR -> IDLE — SOC hits 95% ceiling, stops absorbing solar
3. 2026-04-22T13:00:00 — HOLD_SOLAR -> IDLE — stale solar + spread too thin, explicit sensor callout
4. 2026-04-22T02:30:00 — IDLE -> HOLD_SOLAR — export goes negative, pivots to banking PV at 90.5% SOC
5. 2026-04-22T07:00:00 — IDLE -> HOLD_SOLAR — re-engages after 06:30 dip; export still -9.17 c/kWh
6. 2026-04-22T06:00:00 — IDLE -> HOLD_SOLAR — low-solar interval (0.56 kW), export -7.43, still banks it
7. 2026-04-22T09:30:00 — IDLE -> HOLD_SOLAR — re-engages with stale solar sensor (battery power inference)
8. 2026-04-22T12:30:00 — IDLE -> HOLD_SOLAR — brief re-engage; export -7.86 c/kWh, stale solar
9. 2026-04-22T02:00:00 — HOLD_SOLAR -> IDLE — 79.4% SOC, high load (17.4 kW), single IDLE between absorb intervals
10. 2026-04-22T06:30:00 — HOLD_SOLAR -> IDLE — solar drops to 0.18 kW, barely worth absorbing, brief pause
11. 2026-04-22T12:00:00 — HOLD_SOLAR -> IDLE — brief IDLE between 11:30 and 12:30 HOLD_SOLAR; rationale says "bugger-all load"

---

## Coverage gaps

- **Plans JSONL missing price context in snapshots**: The snapshot dict has no `import_c_kwh`, `export_c_kwh`, or `forecast_peak_import_c_kwh` fields — only `soc_pct`, `load_kw`, `solar_kw`, `battery_power_kw`, `stale_sensors`, `warnings`. Price numbers in the rationale log can't be cross-validated against the plan data programmatically. Minor, but limits automated quality checks.
- **Live agent log is thin**: `agent_rationale.log` has 3 entries, all IDLE, all from a 45-minute window at 01:00-01:47 UTC. No action changes. Not useful for postmortem or video; the offline dry-run is the right source.
- **No CHARGE_GRID or DISCHARGE_GRID actions**: The 24h dry-run contains only HOLD_SOLAR and IDLE. The 30.21 c/kWh peak the agent was banking for never appeared inside an actionable price window — or the price feed went stale before it arrived. The demo shows the agent being conservative and patient, not aggressive arbitrage. That's honest but the video needs to set expectations.
- **execution_audit.log has one entry**: Not enough to draw conclusions about plan vs reality accuracy over the full 24h period.

---

*Generated 2026-04-23 by Sonnet log triage.*
