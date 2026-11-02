"""Write the one-page readout (reports/readout.md) and headline numbers
(reports/summary.json) from the exported marts.

Each hypothesis is tested with the data available and reported with its
limits; nothing here claims a cause that the telemetry cannot show.

Usage:
    python -m analysis.readout [--marts reports/marts] [--out reports]
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


def load(marts: Path) -> dict[str, pd.DataFrame]:
    read = lambda name, dates=(): pd.read_csv(marts / f"{name}.csv", parse_dates=list(dates))
    return {
        "assets": read("dim_assets", ["first_seen_ts", "last_seen_ts"]),
        "site": read("fct_site_monthly_compliance", ["month"]),
        "inv": read("fct_inverter_monthly", ["month"]),
        "tickets": read("fct_service_tickets", ["opened_at", "closed_at"]),
    }


def pct(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.1%}"


def headline(d: dict) -> dict:
    assets, site, inv, t = d["assets"], d["site"], d["inv"], d["tickets"]
    assessable = site[site["is_assessable"]]
    outages = t[t["ticket_type"] == "outage"]
    actual = site["actual_kwh"].sum()
    lost = site["lost_kwh"].sum()
    return {
        "sites": int(assets["site_id"].nunique()),
        "inverters": int(len(assets)),
        "capacity_proxy_kw": round(float(assets["capacity_kw"].sum()), 1),
        "first_month": site["month"].min().strftime("%Y-%m"),
        "last_month": site["month"].max().strftime("%Y-%m"),
        "inverter_months": int(len(inv)),
        "site_months": int(len(site)),
        "site_months_assessable": int(len(assessable)),
        "site_months_compliant": int((assessable["compliance_status"] == "compliant").sum()),
        "site_months_breach": int((assessable["compliance_status"] == "breach").sum()),
        "breaches_time_availability": int((~assessable["meets_time_availability"]).sum()),
        "breaches_energy_availability": int((~assessable["meets_energy_availability"]).sum()),
        "breaches_data_availability": int((~assessable["meets_data_availability"]).sum()),
        "breaches_restore_sla": int((~assessable["meets_restore_sla"]).sum()),
        "outage_tickets": int(len(outages)),
        "site_wide_outage_tickets": int((outages["summary_prefix"] == "Site-wide outage").sum()),
        "restore_sla_breaches": int(outages["sla_breached"].sum()),
        "median_outage_hours": round(float(outages["duration_hours"].median()), 1) if len(outages) else None,
        "underperformance_tickets": int((t["ticket_type"] == "underperformance").sum()),
        "recurring_trip_tickets": int((outages["summary_prefix"] == "Recurring inverter trips").sum()),
        "monitoring_fault_inverter_hours": round(float(inv.get("monitoring_fault_intervals",
                                                       pd.Series(dtype=float)).sum()) * 5 / 60, 1),
        "actual_mwh": round(actual / 1000, 1),
        "lost_mwh": round(lost / 1000, 2),
        "fleet_energy_availability": round(actual / (actual + lost), 4) if actual + lost else None,
        "illustrative_ld_usd": round(float(assessable["illustrative_ld_usd"].sum()), 0),
    }


def concentration(d: dict) -> dict:
    inv, t = d["inv"], d["tickets"]
    by_asset = inv.groupby("asset_id")["lost_kwh"].sum().sort_values(ascending=False)
    total = by_asset.sum()
    k = max(1, math.ceil(len(by_asset) * 0.10))
    outages = t[t["ticket_type"] == "outage"].sort_values("lost_kwh", ascending=False)
    top_events = outages.head(10)["lost_kwh"].sum()
    return {
        "top_decile_assets": k,
        "top_decile_share_of_lost_energy": float(by_asset.head(k).sum() / total) if total else None,
        "top_assets": [(a, round(v / 1000, 2)) for a, v in by_asset.head(k).items()],
        "top10_events_share_of_outage_energy": float(top_events / outages["lost_kwh"].sum())
        if len(outages) and outages["lost_kwh"].sum() else None,
    }


def seasonality(d: dict) -> pd.DataFrame:
    inv, t = d["inv"], d["tickets"]
    inv = inv.assign(cal_month=inv["month"].dt.month)
    exposure = inv.groupby("cal_month")["asset_id"].count().rename("inverter_months")
    lost = inv.groupby("cal_month")["lost_kwh"].sum().rename("lost_kwh")
    out = t[t["ticket_type"] == "outage"].assign(cal_month=lambda x: x["opened_at"].dt.month)
    events = out.groupby("cal_month")["ticket_id"].count().rename("outage_events")
    table = pd.concat([exposure, events, lost], axis=1).fillna(0)
    table["events_per_100_inverter_months"] = 100 * table["outage_events"] / table["inverter_months"]
    return table


def comms_vs_downtime(d: dict) -> dict:
    inv = d["inv"]
    inv = inv[inv["window_intervals"] > 0].assign(
        no_data_share=lambda x: x["no_data_intervals"] / x["window_intervals"],
        down_share=lambda x: x["down_intervals"] / x["window_intervals"],
    )
    rho = inv[["no_data_share", "down_share"]].rank().corr().iloc[0, 1] if len(inv) > 2 else float("nan")
    site = d["site"][d["site"]["is_assessable"]]
    breach = site[site["compliance_status"] == "breach"]
    data_only = breach[
        ~breach["meets_data_availability"]
        & breach["meets_time_availability"]
        & breach["meets_energy_availability"]
        & breach["meets_restore_sla"]
    ]
    return {
        "spearman_rho": float(rho) if not math.isnan(rho) else None,
        "breach_months": int(len(breach)),
        "data_only_breach_months": int(len(data_only)),
    }


def recurring_by_year(d: dict) -> pd.DataFrame:
    t = d["tickets"]
    r = t[t["summary_prefix"] == "Recurring inverter trips"]
    if r.empty:
        return pd.DataFrame(columns=["year", "incidents", "lost_mwh", "top_inverters"])
    r = r.assign(year=r["opened_at"].dt.year)
    rows = []
    for y, g in r.groupby("year"):
        top = g["asset_id"].value_counts().head(3)
        rows.append({"year": int(y), "incidents": len(g), "lost_mwh": round(g["lost_kwh"].sum() / 1000, 1),
                     "top_inverters": ", ".join(f"{a} ({n})" for a, n in top.items())})
    return pd.DataFrame(rows)


def drift(d: dict) -> pd.DataFrame:
    inv = d["inv"].dropna(subset=["performance_index"])
    inv = inv[inv["available_intervals"] >= 300]
    rows = []
    for asset, g in inv.groupby("asset_id"):
        if len(g) < 12:
            continue
        years = (g["month"] - g["month"].min()).dt.days / 365.25
        slope = np.polyfit(years, g["performance_index"], 1)[0]
        rows.append({"asset_id": asset, "months": len(g), "slope_per_year": slope,
                     "latest_pi": g.sort_values("month")["performance_index"].iloc[-1]})
    return pd.DataFrame(rows, columns=["asset_id", "months", "slope_per_year", "latest_pi"])


def site_labels(config: Path, site_ids) -> list[str]:
    try:
        sites = yaml.safe_load(config.read_text())["sites"]
    except (OSError, KeyError, TypeError):
        return [f"Site {s}" for s in site_ids]
    labels = {int(x["site_id"]): x.get("label", f"Site {x['site_id']}") for x in sites}
    return [labels.get(int(s), f"Site {s}") for s in site_ids]


def render(h: dict, c: dict, s: pd.DataFrame, cv: dict, dr: pd.DataFrame, labels: list[str],
           rec: pd.DataFrame | None = None) -> str:
    L = []
    L += ["# Solar Fleet Contract Compliance: Readout", ""]
    noun = "site" if h["sites"] == 1 else "sites"
    L += [f"**Fleet:** {h['sites']} US commercial solar {noun}, {h['inverters']} inverters "
          f"(about {h['capacity_proxy_kw'] / 1000:,.2f} MW by observed peak AC), "
          f"{h['first_month']} to {h['last_month']}. Source: NREL PVDAQ 2023 Solar Data Prize data.", ""]
    L += [f"- {label}" for label in labels] + [""]
    L += ["## Headline", ""]
    L += [f"- **{h['site_months_assessable']}** of {h['site_months']} site-months had enough data to assess. "
          f"**{h['site_months_compliant']}** met every contract term and **{h['site_months_breach']}** breached at least one."]
    L += [f"- Breaches by term: time availability {h['breaches_time_availability']}, energy availability "
          f"{h['breaches_energy_availability']}, data availability {h['breaches_data_availability']}, "
          f"restore SLA {h['breaches_restore_sla']}."]
    L += [f"- **{h['outage_tickets']:,}** outage tickets generated from telemetry "
          f"({h['site_wide_outage_tickets']:,} site-wide), median duration {h['median_outage_hours']} h, "
          f"**{h['restore_sla_breaches']:,}** past the restore SLA. "
          f"**{h['underperformance_tickets']:,}** underperformance tickets."]
    L += [f"- {h['recurring_trip_tickets']:,} of the outage tickets are recurring trips: an inverter dropping out "
          f"several times within a couple of hours, grouped into one ticket per incident."]
    if h.get("monitoring_fault_inverter_hours"):
        L += [f"- {h['monitoring_fault_inverter_hours']:,.0f} inverter-hours where every inverter read zero but the revenue "
              f"meter showed production were treated as monitoring faults, not downtime."]
    L += [f"- Fleet produced {h['actual_mwh']:,.0f} MWh and lost an estimated {h['lost_mwh']:,.0f} MWh to downtime "
          f"(energy-based availability {pct(h['fleet_energy_availability'])}). "
          f"Illustrative liquidated-damages exposure: ${h['illustrative_ld_usd']:,.0f}.", ""]

    L += ["## Hypotheses", ""]
    L += ["**1. Downtime losses are concentrated in a few inverters.**"]
    if c["top_decile_share_of_lost_energy"] is not None:
        names = ", ".join(f"{a} ({v} MWh)" for a, v in c["top_assets"])
        L += [f"The top {c['top_decile_assets']} inverter(s) (10% of the fleet) account for "
              f"{pct(c['top_decile_share_of_lost_energy'])} of lost energy: {names}. "
              f"The 10 largest outages account for {pct(c['top10_events_share_of_outage_energy'])} of outage losses."]
    L += [""]
    L += ["**2. Outages are seasonal.**"]
    if len(s):
        top = s.sort_values("events_per_100_inverter_months", ascending=False).head(3)
        months = ", ".join(f"{pd.Timestamp(2000, int(m), 1):%b} ({r.events_per_100_inverter_months:.1f})"
                           for m, r in top.iterrows())
        L += [f"Highest outage rates per 100 inverter-months: {months}. "
              f"Lowest: {s['events_per_100_inverter_months'].min():.1f}."]
    L += [""]
    L += ["**3. Monitoring gaps and downtime happen together.**"]
    rho = cv["spearman_rho"]
    L += [f"Rank correlation between an inverter-month's data-gap share and its downtime share: "
          f"{'n/a' if rho is None else f'{rho:.2f}'}. "
          f"{cv['data_only_breach_months']} of {cv['breach_months']} breach months failed only the data "
          f"availability term, which points to monitoring rather than hardware for those months.", ""]
    L += ["**4. Some inverters drift below their peers over time.**"]
    if len(dr):
        drifting = dr[dr["slope_per_year"] <= -0.01].sort_values("slope_per_year")
        L += [f"{len(drifting)} of {len(dr)} inverters with at least 12 assessable months lose 1% or more "
              f"of peer-relative performance per year."
              + (" Worst: " + ", ".join(f"{r.asset_id} ({r.slope_per_year:+.1%}/yr)"
                                        for r in drifting.head(3).itertuples()) + "." if len(drifting) else "")]
    else:
        L += ["Not enough history to estimate drift."]
    L += [""]

    L += ["**5. Recurring trips are concentrated in a few inverters and change over time.**"]
    if rec is not None and len(rec):
        L += ["", "| Year | Recurring-trip incidents | Lost MWh | Most affected inverters |", "|---|---|---|---|"]
        L += [f"| {r.year} | {r.incidents:,} | {r.lost_mwh:,} | {r.top_inverters} |" for r in rec.itertuples()]
        L += ["", "A sharp drop after a given year is consistent with a repair, replacement or firmware change on "
              "those inverters. Fault codes and work orders would confirm the cause."]
    L += [""]
    L += ["## What this does and does not show", ""]
    L += ["| Finding | What the data supports | What a service team would check with internal data |",
          "|---|---|---|",
          "| Contract breaches | Telemetry-based availability against the illustrative terms below | The real contract language, exclusions (curtailment, grid events, force majeure) and measurement rules |",
          "| Outage tickets | When an inverter stopped producing while irradiance (or its peers) said it should, grouped into incidents | Work orders, fault codes and the actual dispatch and repair times |",
          "| Lost energy | Peer-based estimate, or irradiance-based for site-wide outages, of energy not produced during downtime | A full irradiance and temperature performance model, and curtailment records |",
          "| Underperformance | Energy below peers while available, which can mean soiling, shading, string faults or clipping | Site inspection, IV curves, string-level data |",
          "| Drift | A peer-relative trend, not absolute degradation | Irradiance-corrected performance ratio over time |",
          ""]
    L += ["**Contract terms are illustrative** (seeds/contract_terms.csv): 98% time availability, 98.5% energy "
          "availability, 95% data availability, restore within 72 hours, $0.10/kWh liquidated damages. "
          "Production windows use plane-of-array irradiance of at least 200 W/m2 where a sensor exists, "
          "and peer comparison otherwise.", ""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--marts", default="reports/marts")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--config", default="config/sites.yml")
    args = ap.parse_args(argv)
    d = load(Path(args.marts))
    labels = site_labels(Path(args.config), sorted(d["assets"]["site_id"].unique()))
    h, c, s, cv, dr = headline(d), concentration(d), seasonality(d), comms_vs_downtime(d), drift(d)
    rec = recurring_by_year(d)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "readout.md").write_text(render(h, c, s, cv, dr, labels, rec))
    summary = {"headline": h, "concentration": c, "comms_vs_downtime": cv,
               "drifting_inverters": int((dr["slope_per_year"] <= -0.01).sum()) if len(dr) else 0}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(h, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
