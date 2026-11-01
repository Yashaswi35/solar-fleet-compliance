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


def render(h: dict, c: dict, s: pd.DataFrame, cv: dict, dr: pd.DataFrame) -> str:
    L = []
    L += ["# Solar Fleet Contract Compliance: Readout", ""]
    L += [f"**Fleet:** {h['sites']} US commercial solar sites, {h['inverters']} inverters "
          f"(about {h['capacity_proxy_kw'] / 1000:,.2f} MW by observed peak AC), "
          f"{h['first_month']} to {h['last_month']}. Source: NREL PVDAQ 2023 Solar Data Prize data.", ""]
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
    L += [f"- Fleet produced {h['actual_mwh']:,} MWh and lost an estimated {h['lost_mwh']:,} MWh to downtime "
          f"(energy-based availability {pct(h['fleet_energy_availability'])}). "
          f"Illustrative liquidated-damages exposure: ${h['illustrative_ld_usd']:,.0f}.", ""]

    L += ["## Hypotheses", ""]
    L += ["**1. Downtime losses are concentrated in a few inverters.**"]
    if c["top_decile_share_of_lost_energy"] is not None:
        names = ", ".join(f"{a} ({v} MWh)" for a, v in c["top_assets"])
        L += [f"The top {c['top_decile_assets']} inverters (10% of the fleet) account for "
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

    L += ["## What this does and does not show", ""]
    L += ["| Finding | What the data supports | What a service team would check with internal data |",
          "|---|---|---|",
          "| Contract breaches | Telemetry-based availability against the illustrative terms below | The real contract language, exclusions (curtailment, grid events, force majeure) and measurement rules |",
          "| Outage tickets | When an inverter stopped producing while its peers or its typical profile said it should | Work orders, fault codes and the actual dispatch and repair times |",
          "| Lost energy | Peer- or profile-based estimate of energy not produced during downtime | Revenue-grade meter data and any irradiance-based expected energy model |",
          "| Underperformance | Energy below peers while available, which can mean soiling, shading, string faults or clipping | Site inspection, IV curves, string-level data |",
          "| Drift | A peer-relative trend, not absolute degradation | Irradiance-corrected performance ratio over time |",
          ""]
    L += ["**Contract terms are illustrative** (seeds/contract_terms.csv): 98% time availability, 98.5% energy "
          "availability, 95% data availability, restore within 72 hours, $0.10/kWh liquidated damages. "
          "A site-wide outage cannot be told apart from heavy snow cover using power data alone.", ""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--marts", default="reports/marts")
    ap.add_argument("--out", default="reports")
    args = ap.parse_args(argv)
    d = load(Path(args.marts))
    h, c, s, cv, dr = headline(d), concentration(d), seasonality(d), comms_vs_downtime(d), drift(d)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "readout.md").write_text(render(h, c, s, cv, dr))
    summary = {"headline": h, "concentration": c, "comms_vs_downtime": cv,
               "drifting_inverters": int((dr["slope_per_year"] <= -0.01).sum()) if len(dr) else 0}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(h, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
