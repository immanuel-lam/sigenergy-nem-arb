# Impact

## Headline numbers

- **Per-house, this week:** The agent matched self-consume ($0.50 gross electricity cost over 7 days) while Amber SmartShift's actual dispatch cost $38.41 — a $5.49/day gap, ~$2,004/year at this rate, from a single correctly-timed refusal to trade into negative export prices.
- **Per-house, vs naive rules:** Against a static TOU strategy (charge 1–5am, discharge 5–9pm regardless of price), the agent saved $13.37/day, or ~$4,880/year.
- **Aggregate, conservative:** Australia had approximately 320,000 home batteries installed as of mid-2024, growing to roughly 500,000+ by end-2025 after the federal Cheaper Home Batteries Program launched [1, 2]. Even assuming only 5% of those are on spot-pass-through tariffs like Amber (a rough lower bound given Amber had ~30,000 customers in early 2024 [3]), that's ~25,000 households. If each household avoided just $500/year in avoidable dispatch losses — well below what this week's backtest shows — the aggregate saving is $12.5M AUD/year.
- **Grid-level:** AEMO's 2024 ISP projects well-coordinated home batteries can avoid ~AU$4.1B in grid-scale infrastructure costs over the transition [4]. Schedulers that decline to trade when the spread is negative reduce curtailment and stop VPPs from amplifying the same price swings they're supposed to smooth.

## Who benefits

- **Households with batteries on Amber-style spot tariffs.** The direct beneficiary. The agent replaces a naive scheduler with one that knows when not to trade.
- **Households considering a battery purchase.** A credible scheduler changes the economics. A battery on self-consume already pencils out in NSW [5]; add selective arbitrage and the payback period shortens.
- **The grid operator (AEMO/DNSPs).** Smarter dispatch timing reduces the coincident peak load from battery owners all charging at the same cheap interval. NSW's PDRS scheme installed 7,800+ batteries between November 2024 and April 2025 specifically to shave peak demand [6]; those batteries only help if they charge and discharge at the right times.
- **Other Amber SmartShift users.** The backtest shows SmartShift's aggressive round-tripping (340 kWh exported over 7 days vs the agent's 43 kWh) drove up costs when export prices were at or below zero. A conservative scheduler reduces this. Multiple SmartShift users making the same trade in the same interval is a coordination problem; the agent's "decline to trade" decision is individually correct and, at scale, less harmful to the grid.

## The per-house argument

7-day backtest, Immanuel's real HA + Amber data, 16–22 April 2026, NSW1 region. All figures are from actual sensor history, not synthetic data.

| Strategy | 7-day cost | vs Agent |
|---|---:|---:|
| Agent (greedy) | $0.50 | — |
| B1 — self-consume | $0.50 | $0 |
| B2 — static TOU | $96.04 | +$95.54 |
| B3 — Amber SmartShift (actual) | $38.41 | +$37.91 |

The honest reading: Amber feed-in prices sat at or below zero most of this week. Grid arbitrage — buy cheap, sell dear — is unprofitable when "sell dear" means selling at a negative price. The agent's greedy scheduler correctly computed that every (charge, discharge) pair had negative net value after the 2c/kWh cycle cost, and landed on self-consume. SmartShift's actual dispatch round-tripped 340 kWh through the battery anyway.

The caveat is a real one: this is one week, and it was an unusually bad week for export prices in NSW1. In periods with price spikes — the WattClarity case study above shows 52% of one user's gross export earnings came from just 47 spike days in 2025 [7] — an agent that can detect and trade on those spikes earns material money. The backtest is a perfect-foresight upper bound; real forecasting error would reduce the headline vs-SmartShift gap. The structural finding is that the decision to *not* trade was correct this week, and a conservative scheduler captures that without look-ahead.

## The aggregate argument

Australia had ~320,000 cumulative home battery installations as of early 2025 [1], accelerating sharply after the federal rebate launched in July 2025. The average system size was ~11.75 kWh in 2024 [2], rising to ~23 kWh by late 2025 as larger systems became rebate-eligible.

Amber had roughly 30,000 customers in February 2024 [3] and has been growing. The total number of Australian households on spot-pass-through tariffs with battery storage is not publicly disclosed. A conservative working assumption: 25,000–50,000 households nationally.

Scenario: 25,000 households with batteries on spot tariffs, each avoiding an average of $500/year in dispatch losses by running a scheduler that declines to trade when the spread is negative. Aggregate: **$12.5M AUD/year**. If the average household instead achieves the $2,004/year gap this backtest showed against Amber SmartShift's actual dispatch, the figure is **$50M AUD/year** for that cohort. Both numbers are speculative — one week of data on one house is not a population estimate — but the direction is clear and the mechanism is traceable.

The Amber comparison deserves a specific caveat: the backtest reconstructs Amber's dispatch from HA sensor history (`amber_replay.py`) rather than Amber's own internal logs. Amber optimises for things this agent doesn't model, including network peak tariffs and demand charges. The $38.41 vs $0.50 comparison should be read as indicative, not a controlled experiment.

## Grid-level impact

AEMO's 2024 ISP projects 27 GW of flexible demand response from consumer-owned batteries and VPPs by 2050 [4]. Whether that flexibility helps or hurts depends on dispatch decisions. A batch of batteries all charging at the same cheap interval creates a new coincident peak; all discharging at the same expensive interval drives prices down and reduces the arbitrage value for everyone. Schedulers that are conservative — that model the spread correctly and decline to trade into negative feed-in — reduce the synchronisation problem. NSW's PDRS installed 7,800 batteries specifically targeted at peak demand reduction [6]; those installations are worth their subsidy only if they dispatch at the right times. A scheduler that runs the same decision logic as this agent costs nothing marginal to run on each additional home.

## Honest limits

- **One week, one house, unusual market conditions.** NSW1 had no price cap events in the 7-day window and persistently low or negative export prices. Results in a week with a cap event would look materially different. The agent has a spike-detection path that handles this, but it wasn't exercised on live data.
- **Perfect-foresight backtest.** The scheduler in the backtest sees actual prices, not forecasts. Real scheduling uses AEMO 5MPD forecasts with imperfect price prediction. The true out-of-sample edge is smaller than the headline numbers.
- **Amber SmartShift comparison is reconstructed, not controlled.** The $37.91/week gap vs SmartShift's actual dispatch is based on HA sensor readings, not Amber's internal dispatch records. Amber may have been optimising for costs this agent doesn't see.
- **Scale numbers are estimates.** The 25,000–50,000 spot-tariff battery household estimate is derived from Amber's 30,000-customer figure (early 2024) plus growth assumptions. No authoritative breakdown of spot-tariff vs flat-tariff battery owners exists in public data.

## Citations

1. "Australia's residential battery installations rise 30% in 2024." PV Magazine, February 2025. <https://www.pv-magazine.com/2025/02/13/australias-residential-battery-installations-rise-30-in-2024/>
2. "Home Battery Boom Smashes 2025 Forecasts: See The Numbers." SolarQuotes, 2025. <https://www.solarquotes.com.au/blog/battery-installations-2025-mb3349/>
3. "Amber eyes international market after $29 million raise." PV Magazine Australia, February 2024. <https://www.pv-magazine-australia.com/2024/02/02/amber-eyes-international-market-after-29-million-raise/>
4. "AEMO 2024 Integrated System Plan." AEMO, 2024. <https://www.aemo.com.au/energy-systems/major-publications/integrated-system-plan-isp/2024-integrated-system-plan-isp>
5. "Turning point for incentives to invest in residential batteries." AEMC, accessed April 2026. <https://www.aemc.gov.au/turning-point-incentives-invest-residential-batteries>
6. "NSW peak demand backed up by 7,800 home battery installations in 5 months." PV Magazine Australia, April 2025. <https://www.pv-magazine-australia.com/2025/04/07/nsw-peak-demand-backed-up-by-7800-home-battery-installations-in-5-months/>
7. "An update on my home energy trading adventures in 10 charts." WattClarity, February 2026. <https://wattclarity.com.au/articles/2026/02/an-update-on-my-home-energy-trading-adventures-in-10-charts/>

---

## For submission form (120–180 words)

We ran a 7-day backtest on one real Sydney house: 64 kWh Sigenergy battery, 24 kWp solar, Amber Electric spot tariff. The agent's greedy scheduler cost $0.50 in gross electricity over the week. Amber SmartShift's actual dispatch cost $38.41 — because it kept round-tripping energy through a battery when export prices were at or below zero. The agent correctly declined to trade and stayed on self-consume. Against a static TOU rule, the gap was $95.54 over seven days (~$4,880/year).

The structural case for this agent isn't that it's smarter than Amber. It's that it knows when *not* to act — and it explains why in plain English every 30 minutes. Amber had roughly 30,000 customers in early 2024; Australia had 320,000+ home batteries by mid-2025, growing fast. Even if 5% of those households avoided $500/year in avoidable dispatch losses by running a conservative scheduler, that's $12.5M AUD/year. AEMO projects coordinated home batteries can avoid $4.1B in grid infrastructure costs over the energy transition. Schedulers that actually decline bad trades are a prerequisite for that to happen.
