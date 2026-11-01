"""Export the dbt marts to small CSV files in reports/marts/.

The dashboard reads these files, so it runs without the raw data or the
DuckDB database (for example on Streamlit Community Cloud).

Usage:
    python -m analysis.export [--db data/fleet.duckdb] [--out reports/marts]
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import duckdb

MARTS = {
    "dim_assets": "order by asset_id",
    "fct_site_monthly_compliance": "order by site_id, month",
    "fct_inverter_monthly": "order by asset_id, month",
    "fct_service_tickets": "order by opened_at, ticket_id",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("FLEET_DB", "data/fleet.duckdb"))
    ap.add_argument("--out", default="reports/marts")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(args.db, read_only=True)
    for table, order in MARTS.items():
        path = out / f"{table}.csv"
        con.execute(f"copy (select * from {table} {order}) to '{path}' (header, delimiter ',')")
        n = con.execute(f"select count(*) from {table}").fetchone()[0]
        print(f"{table}: {n:,} rows -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
