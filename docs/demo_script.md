# Demo Script — 60-second video

Built with Opus 4.7 hackathon submission. One take, no voice-over editing needed.
Narration is word-for-word below. Hit the timestamps.

---

## Beats

### [0:00 – 0:08] Hook

**Screen:** Static terminal showing current Amber price and SOC.
Example output from `python -m arb.ingest.snapshot`:
```
price: 32.4 c/kWh import | 1.2 c/kWh export  |  SOC: 74%
```

**Narration:**
> Sydney home battery. 64 kWh, 24 kWp solar, Amber Electric. Import price swings 30x across the day. A static rule can't track that.

**Word count:** 25. Judging criterion: real-world grounding.

---

### [0:08 – 0:20] Agent loop running

**Screen:** Terminal running `python -m arb.agent.loop --once --dry-run` with INFO logs scrolling.
Show these three lines clearly:
```
10:32:01 INFO arb.agent.loop: === Taking snapshot ===
10:32:04 INFO arb.agent.loop: === Building forecast ===
10:32:06 INFO arb.agent.loop: === Running scheduler ===
```
Then cut to the rationale line:
```
10:32:09 INFO arb.agent.loop: Rationale: Idle — export is 1.2 c/kWh, below cycle cost.
Waiting for afternoon peak forecast at 28c.
```

**Narration:**
> Every 30 minutes: ingest live prices and HA sensors, build a 24-hour forecast, run the greedy scheduler. Opus 4.7 writes the two-sentence rationale.

**Word count:** 29. Judging criterion: agentic behaviour, Opus 4.7 strengths.

---

### [0:20 – 0:35] Dashboard

**Screen:** Streamlit dashboard (`arb/demo/dashboard.py`) with three panels visible simultaneously:
- Top: Amber price chart (5-min resolution, past 6h + 6h forecast)
- Middle: SOC trajectory — planned vs actual
- Bottom: Data quality panel showing sensor freshness

Zoom in briefly on the rationale log panel showing the last 3 entries with timestamps.

**Narration:**
> Live dashboard. Price history, planned SOC trajectory, and a log of every rationale. If a sensor goes stale the agent flags it here and holds the last plan.

**Word count:** 29. Judging criterion: agentic behaviour, Opus 4.7 strengths (missing data handling).

---

### [0:35 – 0:50] Backtest results

**Screen:** Terminal output from `python -m arb.eval.run_backtest`. Show the full table:
```
Strategy               Cost $   Import kWh   Export kWh   Cycles
----------------------------------------------------------------------
agent_greedy             0.17          0.1          8.6     2.20
B1_self_consume          0.17          0.1          8.6     2.20
B2_static_tou           93.48        328.6        294.5     6.66
B3_amber_actual         41.52        312.2        340.8     2.65

Agent saves vs B2 (static TOU):  $+13.33/day
Agent saves vs Amber actual:     $+5.91/day
```

**Narration:**
> 7-day backtest on actual HA history. Static TOU loses $93. Amber SmartShift loses $41 — it exports aggressively into near-zero feed-in. The agent correctly declines to trade.

**Word count:** 30. Judging criterion: real-world grounding, honesty.

---

### [0:50 – 0:60] Close

**Screen:** `arb/agent/explain.py` open, showing the fallback path at lines 131–151.
Then cut to `actuator_audit.log` showing a few dry-run entries with timestamps and reasons.

**Narration:**
> The agent never fabricates. No API key? Templated fallback. Stale sensor? It stops and says so. That's what Opus 4.7 is for.

**Word count:** 26. Judging criterion: Opus 4.7 strengths, honesty.

---

## Shot list

Capture these before recording. All clips needed at the timestamps above.

- [ ] `python -m arb.ingest.snapshot` — clean terminal output, current price + SOC visible
- [ ] `python -m arb.agent.loop --once --dry-run` — full scrolling log, pause on the Rationale line
- [ ] Streamlit dashboard running with all three panels in frame — price chart, SOC trajectory, data quality
- [ ] Rationale log panel zoomed in, at least 3 entries with timestamps
- [ ] `python -m arb.eval.run_backtest` — full table output including the savings lines
- [ ] `arb/agent/explain.py` open in editor at the fallback block (lines 131–151)
- [ ] `actuator_audit.log` — a few dry-run entries visible, timestamps readable

---

## Recording notes

- Record at 1080p. Font size ≥ 14pt in terminal.
- No background music.
- Narrate live or paste over a silent recording. Either works.
- If the Streamlit dashboard isn't ready for Day 4, replace beat [0:20-0:35] with the rationale log scrolling in the terminal.
- Stay in dry-run for the recording unless the 24h dry-run match is clean. Annotate the video title: "dry-run mode" if so. Don't hide it.
