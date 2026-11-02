"""Solar fleet contract compliance dashboard.

Reads the CSV marts in reports/marts (override with FLEET_MARTS).
Run: streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
MARTS = Path(os.environ.get("FLEET_MARTS", ROOT / "reports" / "marts"))
STATUS_COLORS = {"compliant": "#2E7D5B", "breach": "#C0392B", "insufficient_data": "#9AA0A6"}
PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}

st.set_page_config(page_title="Solar Fleet Compliance", layout="wide")


@st.cache_data
def load() -> dict[str, pd.DataFrame]:
    read = lambda n, dates=(): pd.read_csv(MARTS / f"{n}.csv", parse_dates=list(dates))
    return {
        "assets": read("dim_assets", ["first_seen_ts", "last_seen_ts"]),
        "site": read("fct_site_monthly_compliance", ["month"]),
        "inv": read("fct_inverter_monthly", ["month"]),
        "tickets": read("fct_service_tickets", ["opened_at", "closed_at"]),
    }


if not (MARTS / "fct_site_monthly_compliance.csv").exists():
    st.error(f"No marts found in {MARTS}. Run `make all` first.")
    st.stop()

d = load()
site, inv, tickets, assets = d["site"], d["inv"], d["tickets"], d["assets"]

# ---------------------------------------------------------------- filters
st.sidebar.header("Filters")
site_ids = sorted(site["site_id"].unique())
chosen_sites = st.sidebar.multiselect("Sites", site_ids, default=site_ids,
                                      format_func=lambda s: f"Site {s}")
months = sorted(site["month"].dt.to_period("M").unique())
start, end = st.sidebar.select_slider("Months", options=months, value=(months[0], months[-1]),
                                      format_func=lambda p: p.strftime("%b %Y"))
lo, hi = start.to_timestamp(), end.to_timestamp()

site_f = site[site["site_id"].isin(chosen_sites) & site["month"].between(lo, hi)]
inv_f = inv[inv["site_id"].isin(chosen_sites) & inv["month"].between(lo, hi)]
tix_f = tickets[tickets["site_id"].isin(chosen_sites)
                & tickets["opened_at"].between(lo, hi + pd.offsets.MonthEnd(1))]

st.title("Solar Fleet Contract Compliance")
st.caption(f"{len(assets)} inverters across {len(site_ids)} US commercial solar sites "
           "(NREL PVDAQ public data). Contract terms are illustrative.")

tab_score, tab_tickets, tab_inv, tab_about = st.tabs(
    ["Scorecard", "Service tickets", "Inverters", "Method and limits"])

# ---------------------------------------------------------------- scorecard
with tab_score:
    assessable = site_f[site_f["is_assessable"]]
    actual, lost = site_f["actual_kwh"].sum(), site_f["lost_kwh"].sum()
    outages = tix_f[tix_f["ticket_type"] == "outage"]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Site-months compliant",
              f"{(assessable['compliance_status'] == 'compliant').mean():.0%}" if len(assessable) else "n/a",
              help="Share of assessable site-months meeting every contract term")
    c2.metric("Energy availability", f"{actual / (actual + lost):.2%}" if actual + lost else "n/a")
    c3.metric("Lost energy", f"{lost / 1000:,.0f} MWh")
    c4.metric("Outage tickets", f"{len(outages):,}")
    c5.metric("Restore SLA breaches", f"{int(outages['sla_breached'].sum()):,}")

    st.subheader("Monthly compliance by site")
    grid = site_f.assign(site=lambda x: "Site " + x["site_id"].astype(str))
    fig = px.scatter(grid, x="month", y="site", color="compliance_status",
                     color_discrete_map=STATUS_COLORS, symbol_sequence=["square"],
                     hover_data={"time_availability": ":.2%", "energy_availability": ":.2%",
                                 "data_availability": ":.2%", "outage_events": True,
                                 "restore_sla_breaches": True, "month": "|%b %Y"})
    fig.update_traces(marker={"size": 14})
    fig.update_layout(height=120 + 60 * grid["site"].nunique(), legend_title_text="",
                      yaxis_title=None, xaxis_title=None, margin={"t": 10})
    st.plotly_chart(fig, width="stretch")

    st.subheader("Time availability against the guarantee")
    st.caption("Assessable months only. Gaps are months without enough telemetry to score.")
    trend = grid.assign(time_availability=grid["time_availability"].where(grid["is_assessable"]))
    fig = px.line(trend, x="month", y="time_availability", color="site", markers=True)
    for g in site_f["min_time_availability"].unique():
        fig.add_hline(y=g, line_dash="dash", line_color="#C0392B",
                      annotation_text=f"Guarantee {g:.0%}", annotation_position="bottom right")
    fig.update_layout(yaxis_tickformat=".0%", yaxis_title=None, xaxis_title=None,
                      legend_title_text="", height=360, margin={"t": 10})
    st.plotly_chart(fig, width="stretch")

    st.subheader("Breaches by contract term")
    terms = {
        "Time availability": (~assessable["meets_time_availability"]).sum(),
        "Energy availability": (~assessable["meets_energy_availability"]).sum(),
        "Data availability": (~assessable["meets_data_availability"]).sum(),
        "Restore SLA": (~assessable["meets_restore_sla"]).sum(),
    }
    st.bar_chart(pd.Series(terms, name="site-months in breach"), horizontal=True)

# ---------------------------------------------------------------- tickets
with tab_tickets:
    f1, f2, f3 = st.columns(3)
    types = f1.multiselect("Type", ["outage", "underperformance"], default=["outage", "underperformance"])
    prios = f2.multiselect("Priority", ["High", "Medium", "Low"], default=["High", "Medium", "Low"])
    breached_only = f3.toggle("Only restore SLA breaches")
    view = tix_f[tix_f["ticket_type"].isin(types) & tix_f["priority"].isin(prios)]
    if breached_only:
        view = view[view["sla_breached"]]

    by_month = (view.assign(month=view["opened_at"].dt.to_period("M").dt.to_timestamp())
                .groupby(["month", "ticket_type"]).size().rename("tickets").reset_index())
    fig = px.bar(by_month, x="month", y="tickets", color="ticket_type",
                 color_discrete_map={"outage": "#C0392B", "underperformance": "#E0A030"})
    fig.update_layout(height=300, legend_title_text="", xaxis_title=None, margin={"t": 10})
    st.plotly_chart(fig, width="stretch")

    view = view.sort_values(["priority", "lost_kwh"], key=lambda s: s.map(PRIORITY_ORDER)
                            if s.name == "priority" else -s)
    st.dataframe(
        view[["ticket_id", "ticket_type", "priority", "asset_id", "opened_at", "closed_at",
              "duration_hours", "sla_hours", "sla_breached", "lost_kwh", "performance_index"]],
        hide_index=True, width="stretch",
        column_config={
            "duration_hours": st.column_config.NumberColumn("Duration (h)", format="%.1f"),
            "lost_kwh": st.column_config.NumberColumn("Lost kWh", format="%.0f"),
            "performance_index": st.column_config.NumberColumn("Perf. index", format="%.2f"),
            "sla_breached": st.column_config.CheckboxColumn("SLA breached"),
        })
    st.download_button("Download tickets (CSV)", view.to_csv(index=False), "service_tickets.csv")

# ---------------------------------------------------------------- inverters
with tab_inv:
    worst = (inv_f.groupby("asset_id")
             .agg(lost_kwh=("lost_kwh", "sum"), down_intervals=("down_intervals", "sum"),
                  median_pi=("performance_index", "median"))
             .sort_values("lost_kwh", ascending=False).reset_index())
    left, right = st.columns([2, 3])
    with left:
        st.subheader("Inverters by lost energy")
        st.dataframe(worst, hide_index=True, width="stretch", height=420,
                     column_config={"lost_kwh": st.column_config.NumberColumn("Lost kWh", format="%.0f"),
                                    "median_pi": st.column_config.NumberColumn("Median perf. index",
                                                                              format="%.3f")})
    with right:
        asset = st.selectbox("Inverter", worst["asset_id"] if len(worst) else assets["asset_id"])
        one = inv_f[inv_f["asset_id"] == asset].sort_values("month")
        long = one.melt(id_vars="month", value_vars=["time_availability", "performance_index"],
                        var_name="metric", value_name="value")
        fig = px.line(long, x="month", y="value", color="metric", markers=True)
        fig.add_hline(y=1.0, line_dash="dot", line_color="#9AA0A6")
        fig.update_layout(height=420, legend_title_text="", xaxis_title=None, yaxis_title=None,
                          margin={"t": 10})
        st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------- about
with tab_about:
    readout = ROOT / "reports" / "readout.md"
    if readout.exists():
        st.markdown(readout.read_text())
    else:
        st.info("Run `make readout` to generate the readout.")
