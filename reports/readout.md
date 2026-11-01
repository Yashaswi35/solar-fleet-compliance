# Solar Fleet Contract Compliance: Readout

**Fleet:** 2 US commercial solar sites, 26 inverters (about 4.51 MW by observed peak AC), 2017-08 to 2025-12. Source: NREL PVDAQ 2023 Solar Data Prize data.

- Farm Solar Array, Arbuckle CA (893 kW DC, 24 x ABB TRIO-27.6, fixed tilt)
- Site 9068 (single-axis tracker)

## Headline

- **183** of 188 site-months had enough data to assess. **78** met every contract term and **105** breached at least one.
- Breaches by term: time availability 87, energy availability -296, data availability 14, restore SLA 61.
- **6,714** outage tickets generated from telemetry (2,613 site-wide), median duration 1.5 h, **165** past the restore SLA. **225** underperformance tickets.
- Fleet produced 60,235.4 MWh and lost an estimated 817.91 MWh to downtime (energy-based availability 98.7%). Illustrative liquidated-damages exposure: $67,766.

## Hypotheses

**1. Downtime losses are concentrated in a few inverters.**
The top 3 inverter(s) (10% of the fleet) account for 22.4% of lost energy: 2107-INV07 (67.27 MWh), 2107-INV06 (61.08 MWh), 2107-INV02 (55.27 MWh). The 10 largest outages account for 11.8% of outage losses.

**2. Outages are seasonal.**
Highest outage rates per 100 inverter-months: Jul (625.0), Jun (475.5), Aug (470.0). Lowest: 77.3.

**3. Monitoring gaps and downtime happen together.**
Rank correlation between an inverter-month's data-gap share and its downtime share: -0.08. 8 of 105 breach months failed only the data availability term, which points to monitoring rather than hardware for those months.

**4. Some inverters drift below their peers over time.**
5 of 24 inverters with at least 12 assessable months lose 1% or more of peer-relative performance per year. Worst: 2107-INV15 (-3.8%/yr), 2107-INV21 (-1.6%/yr), 2107-INV10 (-1.3%/yr).

## What this does and does not show

| Finding | What the data supports | What a service team would check with internal data |
|---|---|---|
| Contract breaches | Telemetry-based availability against the illustrative terms below | The real contract language, exclusions (curtailment, grid events, force majeure) and measurement rules |
| Outage tickets | When an inverter stopped producing while its peers or its typical profile said it should | Work orders, fault codes and the actual dispatch and repair times |
| Lost energy | Peer- or profile-based estimate of energy not produced during downtime | Revenue-grade meter data and any irradiance-based expected energy model |
| Underperformance | Energy below peers while available, which can mean soiling, shading, string faults or clipping | Site inspection, IV curves, string-level data |
| Drift | A peer-relative trend, not absolute degradation | Irradiance-corrected performance ratio over time |

**Contract terms are illustrative** (seeds/contract_terms.csv): 98% time availability, 98.5% energy availability, 95% data availability, restore within 72 hours, $0.10/kWh liquidated damages. Production windows use plane-of-array irradiance of at least 200 W/m2 where a sensor exists, and peer comparison otherwise.
