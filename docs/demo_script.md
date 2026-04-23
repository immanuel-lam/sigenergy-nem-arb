# Demo Script — 3-minute video

Built with Opus 4.7 hackathon submission (Cerebral Valley). Deadline Sun Apr 26 8 PM EST.

3 minutes total. ~150 words/min conversational pace = ~450 words narration.

Target the four judging criteria deliberately:
- **Impact (30%)** — quantify the dollar saving on a real house, scale story
- **Demo (25%)** — must work live, no editing magic
- **Opus 4.7 Use (25%)** — show rationale generation + report prose + missing-data handling
- **Depth & Execution (20%)** — show the system isn't a single LLM call wrapped in a UI

---

## Setup checklist before recording

```bash
# Terminal 1 — backend
cd /Users/immanuellam/Desktop/opus4.7hackathon
source .venv/bin/activate
uvicorn arb.api.server:app --port 8000

# Terminal 2 — frontend
cd /Users/immanuellam/Desktop/opus4.7hackathon/web
NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev

# Terminal 3 — live demo terminal
cd /Users/immanuellam/Desktop/opus4.7hackathon
source .venv/bin/activate
```

Pre-flight in browser before recording:
- http://localhost:3000 — SOC gauge shows a real number, price chart populated, dark theme renders
- http://localhost:3000/replan — animation loops cleanly
- file:///path/to/docs/report.html — opens, looks typeset

---

## Beats

### [0:00 – 0:15] The problem (Impact framing)

**Screen:** Browser at `http://localhost:3000`. Dashboard fully loaded — SOC gauge, price chart with the negative export region visible in rose, the action band showing HOLD_SOLAR badges.

**Narration:**
> NSW Amber Electric. Wholesale prices update every 5 minutes. Spreads of 30 cents per kWh between off-peak and peak. Negative feed-in tariffs most days. A static rule loses money on this tariff. A human can't watch it.

**Words:** 42. Hits Impact (real-world specifics) and Demo (live data on screen).

---

### [0:15 – 0:35] What the agent does (Depth)

**Screen:** Stay on dashboard. Optionally cut briefly to a code overview — `tree arb/` in Terminal 3 to show the layered structure: ingest, forecast, scheduler, actuator, agent, eval.

**Narration:**
> Every 30 minutes the agent ingests live AEMO prices, weather, and Home Assistant sensors, builds a 24-hour forecast, runs a greedy arbitrage scheduler, diffs the new plan against the last one, audits how much the actual battery drifted from the previous plan, and explains the decision.

**Words:** 48. Hits Depth (full pipeline named, not just "an LLM call").

---

### [0:35 – 1:00] Live cycle with Opus 4.7 rationale

**Screen:** Terminal 3, run live:
```bash
python -m arb.agent.loop --once --dry-run
```
Logs scroll. Pause on the rationale block:
```
INFO __main__: === Explaining decision ===
INFO __main__: Rationale: Holding all 2.2 kW of solar into the battery...
```

**Narration:**
> Here's a live cycle. Opus 4.7 reads the plan, the prices, the SOC, and the next six hours of forecast, and writes two sentences. Read it: holding solar in the battery because export is negative right now and there's a 29-cent peak coming. Specific numbers, no fluff.

**Words:** 51. Hits Opus 4.7 Use (creative — using it for in-loop reasoning, not just a chat).

---

### [1:00 – 1:35] Dashboard tour (Demo)

**Screen:** Back to browser. Walk through panels:
1. SOC gauge (cyan→amber→rose gradient at the floor and ceiling).
2. 24h price chart — point at the rose-filled region where export went negative.
3. Action band — show the HOLD_SOLAR clusters, explain why.
4. Rationale feed — three or four real entries, scroll past them.
5. Data quality pills — all five sources green.

**Narration:**
> The dashboard reads the same logs the agent writes — no separate store. State of charge, the 24-hour price forecast with planned actions shaded in, the live rationale feed, and a data quality strip per source. If Home Assistant goes stale, this turns amber and the agent stops planning.

**Words:** 53. Hits Demo (working visual) and Opus 4.7 Use (the rationale feed is all model output).

---

### [1:35 – 2:10] Spike demo — mid-interval re-plan (Opus 4.7 Use + Demo)

**Screen:** Click "Inject synthetic spike" button top-right. Animation plays in the middle of the page. Or cut to `http://localhost:3000/replan` for the full-screen version. The 8-second choreographed sequence runs: baseline plan, spike flash at +120 c/kWh, old plan ghosts, new plan draws in cyan/violet, action strip recolours, rationale typewriter-streams.

**Narration:**
> When new data lands mid-interval, the agent re-plans. Watch: a synthetic 120-cent export spike injected ten minutes out. Agent flips current action from hold-solar to charge-grid, schedules the discharge into the spike window, and Opus 4.7 narrates the change. This is the agentic part. A cron job can't do it.

**Words:** 53. Direct hit on Opus 4.7 Use ("creative, beyond basic integration") and Demo (visual proof).

---

### [2:10 – 2:40] Backtest — the dollar number (Impact + honesty)

**Screen:** Terminal 3:
```bash
python -m arb.eval.run_backtest 7
```
Or scroll to the dashboard's backtest table. Show the four-row comparison:
```
agent_greedy        $0.61 cost
B1_self_consume     $0.61 cost
B2_static_tou      $96.15 cost
B3_amber_actual    $38.66 cost
```

**Narration:**
> Seven-day backtest on this house's actual data. Static time-of-use loses 96 dollars. Amber's own SmartShift loses 38 dollars because it round-trips 340 kilowatt-hours into a feed-in tariff that prints negative. The agent matches pure self-consume — and that's the correct answer this week. The honest read: arbitrage doesn't pay on this tariff today, and the agent knows it.

**Words:** 67. Direct hit on Impact (real dollars on a real house) and the honesty axis.

---

### [2:40 – 2:55] Opus 4.7 catches a stale sensor

**Screen:** Terminal 3, cat the rationale log:
```bash
grep "no_data\|stale\|missing" agent_rationale.log execution_audit.log offline_dryrun_rationale.log
```
Or point at `docs/best_moments.md`'s Candidate 1 — the 08:30 UTC moment.

**Narration:**
> One more thing. When the solar sensor went stale at 8:30, the agent dropped hold-solar and went idle, instead of fabricating a plan against missing data. That's what we wanted Opus 4.7 for.

**Words:** 38. Direct hit on Opus 4.7 Use (creative — the report-missing-data behaviour judges specifically called out).

---

### [2:55 – 3:00] Close

**Screen:** Brief cut to `docs/report.html` open in browser, or just the GitHub repo URL on screen.

**Narration:**
> MIT licensed, repo's public. Built in three days during the hackathon. Thanks for watching.

**Words:** 18.

---

## Total

~370 narrated words across 3:00. Comfortable pace. Leaves natural pauses.

---

## Shot list (capture these clips before assembling)

- [ ] Dashboard at `http://localhost:3000` — full viewport, SOC and chart loaded with real numbers
- [ ] Terminal: `tree arb/` (or `ls arb/`) — quick architecture flash
- [ ] Terminal: `python -m arb.agent.loop --once --dry-run` — full scroll, pause on Rationale
- [ ] Dashboard panel-by-panel scroll: SOC gauge, price chart, action band, rationale feed, data quality pills
- [ ] Dashboard "Inject synthetic spike" button click + ReplanSection animation playing inline
- [ ] OR `http://localhost:3000/replan` cinematic loop (alternative for the spike beat)
- [ ] Terminal: `python -m arb.eval.run_backtest 7` — full output table
- [ ] Terminal: grep through rationale logs for the stale-sensor moment
- [ ] `docs/report.html` brief scroll for the close

---

## Recording notes

- **Resolution:** 1080p minimum. Font size ≥ 14pt in terminals.
- **Tool:** QuickTime on Mac (Cmd+Shift+5 → "Record Selected Portion"). Or Loom.
- **No background music.** Voice-over only, recorded live or dubbed in post.
- **Stay in dry-run mode.** Don't flip DRY_RUN=false for the recording. Advisory mode is the actual claim — fighting Amber SmartShift for control is not what the demo shows.
- **One take is fine.** If you fluff a beat, restart that clip — don't try to splice mid-narration.
- **Upload as Unlisted YouTube** or Loom. Submission needs the link, not the file.

---

## Submission checklist (after video is recorded)

1. Upload video → copy link
2. Open https://cerebralvalley.ai/built-with-4-7-hackathon-submissions
3. Submission fields:
   - **Repo:** https://github.com/immanuel-lam/sigenergy-nem-arb
   - **Video:** the link from step 1
   - **Description:** see template below

### Description template (paste into the form)

> Sigenergy NEM Arbitrage Agent — autonomous battery scheduler for an Australian home on the Amber Electric tariff. Every 30 minutes the agent ingests live AEMO wholesale prices, weather, and Home Assistant sensors; builds a 24-hour forecast; runs a greedy arbitrage scheduler with hard SOC bounds; and explains every decision in two sentences via Claude Opus 4.7. Polls every 5 minutes between cycles for price spikes and re-plans mid-interval when one lands.
>
> Backtested on seven days of the owner's actual house data: matches a self-consume baseline ($0.61) and beats a static TOU rule by $96 over the week. The honest read — Amber's negative feed-in this week meant the correct decision was to not arbitrage, which the agent figured out. Static rules lost money repeatedly.
>
> 99 tests, MIT licensed, runs advisory-mode against the owner's actual battery. Built in three days during the hackathon.
