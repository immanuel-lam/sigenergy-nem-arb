# Postmortem: One Day of the Agent

## How to use this template

Open `agent_rationale.log`. Find an entry where the action changed between two consecutive cycles — the `changed_from_previous` flag in the structured diff will be `True`. Cross-reference that timestamp in `actuator_audit.log` and the `B3_amber_actual` column from the backtest. Fill in each section below. Budget 30 minutes.

---

**Date observed:** [YYYY-MM-DD]
**Time window:** [HH:MM – HH:MM AEST]
**Observer:** Immanuel

---

## The moment

[PLACEHOLDER: 1–2 sentences describing the specific event. The agent changed its action at a specific time — what did it switch from, what did it switch to? Example: "At 14:30 AEST the agent switched from IDLE to CHARGE_GRID when import dropped to 4.1 c/kWh, 6 hours before a forecast peak of 28c at 20:00."]

---

## What the agent saw

[PLACEHOLDER: Copy the relevant block from `agent_rationale.log` for this timestamp. Include:]

- Import price at decision time: [X] c/kWh
- Export price at decision time: [X] c/kWh
- SOC at decision time: [X]%
- Forecast peak import price (next 6h): [X] c/kWh
- Forecast minimum export price (next 6h): [X] c/kWh
- Household load: [X] kW
- Solar generation: [X] kW
- Stale sensors (if any): [list or "none"]
- Warnings from snapshot (if any): [list or "none"]

---

## What the agent did

**Action:** [CHARGE_GRID / DISCHARGE_GRID / HOLD_SOLAR / IDLE]

**LLM rationale (verbatim from `agent_rationale.log`):**
> [PLACEHOLDER: paste the exact two-sentence rationale from the log. Do not paraphrase.]

---

## What Amber SmartShift actually did

[PLACEHOLDER: Pull from the `B3_amber_actual` reconstruction in the backtest or from HA history for this timestamp.]

- Action (approximate): [CHARGE_GRID / DISCHARGE_GRID / IDLE]
- Battery power reading from HA: [X] kW (charge) / [X] kW (discharge)
- Estimated cost/revenue for this interval: $[X]

---

## Who was right (and by how much)

[PLACEHOLDER: Track the 2–4 hours following the decision. What prices actually materialised? Did the agent's SOC trajectory match its plan?]

| Time (AEST) | Actual import price | Actual export price | Agent SOC | Amber SmartShift SOC |
|---|---|---|---|---|
| [HH:MM] | [X] c/kWh | [X] c/kWh | [X]% | [X]% |
| [HH:MM] | | | | |
| [HH:MM] | | | | |

**Net outcome (agent vs Amber, this window):**
- Agent cost/revenue: $[X]
- Amber actual cost/revenue: $[X]
- Delta: $[+X / -X] in agent's favour

---

## What this tells us

[PLACEHOLDER: 2–3 sentences. Be direct. Either "The agent correctly caught X because Y" or "The agent missed Y because Z — the greedy algorithm can't account for W." If Opus 4.7's behaviour was notable — it flagged a stale sensor, it refused to fabricate a forecast, it reported a discrepancy — say so specifically.]

---

## What would change the outcome

[PLACEHOLDER: If this was a loss for the agent — what specific improvement would have prevented it? If it was a win — is the condition that triggered it common enough to be repeatable, or was it a one-off spike? One paragraph.]
