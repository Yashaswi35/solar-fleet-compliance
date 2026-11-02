# Solar Fleet Contract Compliance: Readout

**Fleet:** 2 US commercial solar sites, 32 inverters (about 4.57 MW by observed peak AC), 2017-08 to 2025-12. Source: NREL PVDAQ 2023 Solar Data Prize data.

- Farm Solar Array, Arbuckle CA (893 kW DC, 24 x ABB TRIO-27.6, fixed tilt)
- Site 9068 (single-axis tracker, 2 central inverters reported as 8 inverter modules)

## Headline

- **177** of 188 site-months had enough data to assess. **68** met every contract term and **109** breached at least one.
- Breaches by term: time availability 97, energy availability 98, data availability 23, restore SLA 74.
- **3,401** outage tickets generated from telemetry (51 site-wide), median duration 5.7 h, **195** past the restore SLA. **233** underperformance tickets.
- 2,395 of the outage tickets are recurring trips: an inverter dropping out several times within a couple of hours, grouped into one ticket per incident.
- 14,392 inverter-hours where every inverter read zero but the revenue meter showed production were treated as monitoring faults, not downtime.
- Fleet produced 60,904 MWh and lost an estimated 1,627 MWh to downtime (energy-based availability 97.4%). Illustrative liquidated-damages exposure: $97,015.

## Hypotheses

**1. Downtime losses are concentrated in a few inverters.**
The top 4 inverter(s) (10% of the fleet) account for 38.2% of lost energy: 9068-INV1.4 (189.53 MWh), 9068-INV2.3 (157.38 MWh), 9068-INV2.2 (138.82 MWh), 9068-INV2.1 (135.64 MWh). The 10 largest outages account for 28.3% of outage losses.

**2. Outages are seasonal.**
Highest outage rates per 100 inverter-months: Jul (181.4), Jun (159.1), May (156.6). Lowest: 58.5.

**3. Monitoring gaps and downtime happen together.**
Rank correlation between an inverter-month's data-gap share and its downtime share: 0.34. 2 of 109 breach months failed only the data availability term, which points to monitoring rather than hardware for those months.

**4. Some inverters drift below their peers over time.**
5 of 32 inverters with at least 12 assessable months lose 1% or more of peer-relative performance per year. Worst: 2107-INV15 (-4.5%/yr), 2107-INV21 (-1.6%/yr), 2107-INV10 (-1.4%/yr).

**5. Recurring trips are concentrated in a few inverters and change over time.**

| Year | Recurring-trip incidents | Lost MWh | Most affected inverters |
|---|---|---|---|
| 2017 | 31 | 18.2 | 9068-INV2.1 (4), 2107-INV01 (4), 9068-INV1.3 (3) |
| 2018 | 486 | 73.3 | 2107-INV07 (67), 2107-INV04 (65), 2107-INV06 (60) |
| 2019 | 541 | 96.1 | 2107-INV03 (134), 2107-INV09 (69), 2107-INV02 (62) |
| 2020 | 226 | 30.1 | 2107-INV21 (93), 2107-INV02 (31), 2107-INV14 (27) |
| 2021 | 311 | 33.1 | 2107-INV07 (46), 2107-INV19 (41), 2107-INV06 (40) |
| 2022 | 628 | 91.2 | 2107-INV03 (71), 2107-INV05 (68), 2107-INV01 (67) |
| 2023 | 127 | 49.2 | 2107-INV07 (35), 2107-INV06 (33), 2107-INV04 (13) |
| 2024 | 36 | 17.7 | 9068-INV2.1 (4), 9068-INV2.3 (4), 2107-INV24 (3) |
| 2025 | 9 | 12.6 | 9068-INV1.1 (1), 9068-INV1.2 (1), 9068-INV1.3 (1) |

A sharp drop after a given year is consistent with a repair, replacement or firmware change on those inverters. Fault codes and work orders would confirm the cause.

## What this does and does not show

| Finding | What the data supports | What a service team would check with internal data |
|---|---|---|
| Contract breaches | Telemetry-based availability against the illustrative terms below | The real contract language, exclusions (curtailment, grid events, force majeure) and measurement rules |
| Outage tickets | When an inverter stopped producing while irradiance (or its peers) said it should, grouped into incidents | Work orders, fault codes and the actual dispatch and repair times |
| Lost energy | Peer-based estimate, or irradiance-based for site-wide outages, of energy not produced during downtime | A full irradiance and temperature performance model, and curtailment records |
| Underperformance | Energy below peers while available, which can mean soiling, shading, string faults or clipping | Site inspection, IV curves, string-level data |
| Drift | A peer-relative trend, not absolute degradation | Irradiance-corrected performance ratio over time |

**Contract terms are illustrative** (seeds/contract_terms.csv): 98% time availability, 98.5% energy availability, 95% data availability, restore within 72 hours, $0.10/kWh liquidated damages. Production windows use plane-of-array irradiance of at least 200 W/m2 where a sensor exists, and peer comparison otherwise.
