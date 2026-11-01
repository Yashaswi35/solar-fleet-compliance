# Solar Fleet Contract Compliance: Readout

**Fleet:** 2 US commercial solar sites, 35 inverters (about 3.48 MW by observed peak AC), 2017-11 to 2025-12. Source: NREL PVDAQ 2023 Solar Data Prize data.

## Headline

- **152** of 155 site-months had enough data to assess. **17** met every contract term and **135** breached at least one.
- Breaches by term: time availability 90, energy availability 88, data availability 58, restore SLA 58.
- **9,453** outage tickets generated from telemetry (3,962 site-wide), median duration 1.3 h, **208** past the restore SLA. **328** underperformance tickets.
- Fleet produced 34,517.4 MWh and lost an estimated 1,334.42 MWh to downtime (energy-based availability 96.3%). Illustrative liquidated-damages exposure: $99,438.

## Hypotheses

**1. Downtime losses are concentrated in a few inverters.**
The top 4 inverters (10% of the fleet) account for 22.6% of lost energy: 2105-INV05 (88.6 MWh), 2107-INV07 (77.28 MWh), 2107-INV06 (70.6 MWh), 2107-INV02 (64.89 MWh). The 10 largest outages account for 7.7% of outage losses.

**2. Outages are seasonal.**
Highest outage rates per 100 inverter-months: Jul (610.4), Aug (466.0), Jun (440.4). Lowest: 162.9.

**3. Monitoring gaps and downtime happen together.**
Rank correlation between an inverter-month's data-gap share and its downtime share: -0.27. 37 of 135 breach months failed only the data availability term, which points to monitoring rather than hardware for those months.

**4. Some inverters drift below their peers over time.**
9 of 35 inverters with at least 12 assessable months lose 1% or more of peer-relative performance per year. Worst: 2105-INV04 (-8.1%/yr), 2105-INV11 (-8.1%/yr), 2107-INV15 (-4.2%/yr).

## What this does and does not show

| Finding | What the data supports | What a service team would check with internal data |
|---|---|---|
| Contract breaches | Telemetry-based availability against the illustrative terms below | The real contract language, exclusions (curtailment, grid events, force majeure) and measurement rules |
| Outage tickets | When an inverter stopped producing while its peers or its typical profile said it should | Work orders, fault codes and the actual dispatch and repair times |
| Lost energy | Peer- or profile-based estimate of energy not produced during downtime | Revenue-grade meter data and any irradiance-based expected energy model |
| Underperformance | Energy below peers while available, which can mean soiling, shading, string faults or clipping | Site inspection, IV curves, string-level data |
| Drift | A peer-relative trend, not absolute degradation | Irradiance-corrected performance ratio over time |

**Contract terms are illustrative** (seeds/contract_terms.csv): 98% time availability, 98.5% energy availability, 95% data availability, restore within 72 hours, $0.10/kWh liquidated damages. A site-wide outage cannot be told apart from heavy snow cover using power data alone.
