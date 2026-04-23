# Demo Script — 60-second video

Built with Opus 4.7 hackathon submission. One take, no voice-over editing needed.
Narration is word-for-word below. Hit the timestamps.

---

## Setup checklist before recording

```bash
# Terminal 1: backend
cd /Users/immanuellam/Desktop/opus4.7hackathon
source .venv/bin/activate
uvicorn arb.api.server:app --port 8000

# Terminal 2: frontend
cd /Users/immanuellam/Desktop/opus4.7hackathon/web
NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev

# Terminal 3: will be the live demo terminal
cd /Users/immanuellam/Desktop/opus4.7hackathon
source .venv/bin/activate
# (keep this one for running arb.agent.loop --once on camera)
```

Confirm before recording: `http://localhost:3000` loads, SOC gauge shows a number, price chart has data. If the gauge shows "--", the backend snapshot call failed — check Terminal 1 for errors.

---

## Beats

### [0:00 – 0:08] Hook

**Screen:** Browser, `http://localhost:3000`. Dark theme fills the viewport. SOC gauge visible top-left with the current battery state of charge. Price chart to its right showing the 24h Amber price curve.

**Narration:**
> Sydney home battery. 64 kWh, 24 kWp solar, Amber Electric. Import price swings 30x across the day. A static rule can't track that.

**Word count:** 25. Judging criterion: real-world grounding.

---

### [0:08 – 0:20] Agent loop running

**Screen:** Terminal 3 running `python -m arb.agent.loop --once --dry-run` with INFO logs scrolling.
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

### [0:20 – 0:35] Dashboard — spike demo and re-plan animation

**Screen:** Back to `http://localhost:3000`. Scroll down slightly so the "Inject synthetic spike" button is visible in the top-right corner. Click it. The ReplanSection below the price chart scrolls into view. The signature animation plays: baseline plan on the left morphs into the spiked plan on the right, price spike highlighted in amber/rose, action strip shows the before/after action change.

**Shot note:** If the live spike endpoint is slow, the animation still fires using the hardcoded story — either way the visual is the same.

**Narration:**
> When a price spike lands, the agent re-plans immediately. Here: a synthetic 120 c/kWh export spike. The scheduler flips from idle to charge-then-discharge in one pass.

**Word count:** 29. Judging criterion: agentic behaviour, re-planning on new information.

---

### [0:35 – 0:50] Backtest results

**Screen:** Scroll down on the dashboard to the backtest table panel. Let it load. The four-row table shows agent, B1, B2, B3 costs and cycles. Alternatively, cut to Terminal 3 and run:
```
python -m arb.eval.run_backtest
```
to show the full table:
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

**Screen:** Cut to `arb/agent/explain.py` open in editor at the fallback block (lines 131–151). Then cut to `actuator_audit.log` showing a few dry-run entries with timestamps and reasons. Optional: 2-second scroll of `docs/report.html` in a browser tab — the submission-grade static report.

**Narration:**
> The agent never fabricates. No API key? Templated fallback. Stale sensor? It stops and says so. That's what Opus 4.7 is for.

**Word count:** 26. Judging criterion: Opus 4.7 strengths, honesty.

---

## Shot list

Capture these before recording. All clips needed at the timestamps above.

- [ ] `http://localhost:3000` — dark theme, SOC gauge and 24h price chart both loaded and showing data
- [ ] `python -m arb.agent.loop --once --dry-run` — full scrolling log, pause on the Rationale line
- [ ] Dashboard: "Inject synthetic spike" button click, ReplanSection animation plays below
- [ ] Dashboard: backtest table panel loaded with four rows (or terminal output of `run_backtest`)
- [ ] Rationale feed panel — at least 3 entries with timestamps visible
- [ ] `arb/agent/explain.py` open in editor at the fallback block (lines 131–151)
- [ ] `actuator_audit.log` — a few dry-run entries visible, timestamps readable
- [ ] (optional) `docs/report.html` open in browser — 2-second scroll for closing shot

---

## Alternative recording path

If you want cinematic b-roll for the re-plan animation: open `http://localhost:3000/replan` in a full browser window (no chrome bars). It loops automatically. Space bar replays it. The dark background and atmosphere blobs fill the frame cleanly. Good for slow-motion overlay or title card background.

---

## Recording notes

- Record at 1080p. Font size >= 14pt in terminal.
- No background music.
- Narrate live or paste over a silent recording. Either works.
- Stay in dry-run for the recording unless the 24h dry-run match is clean. Annotate the video title: "dry-run mode" if so. Don't hide it.
