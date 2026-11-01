"""End-to-end test: synthetic raw files -> loader -> dbt -> marts.

The fixtures (tests/make_fixtures.py) contain known, injected problems. These
tests check that the pipeline finds each one and nothing else.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "ci.duckdb"
PROCESSED = ROOT / "data" / "ci_processed"


def run(*cmd: str, env: dict | None = None) -> None:
    result = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, f"{' '.join(cmd)} failed:\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}"


@pytest.fixture(scope="session")
def con():
    env = {**os.environ, "FLEET_PROCESSED": str(PROCESSED), "FLEET_DB": str(DB)}
    if DB.exists():
        DB.unlink()
    run(sys.executable, "-m", "tests.make_fixtures", "--out", "tests/fixtures/raw")
    run(sys.executable, "-m", "ingest.load", "--config", "config/sites.ci.yml",
        "--profile", "data/ci_schema_profile.md",
        "--samples-dir", "data/ci_samples")
    run("dbt", "build", "--project-dir", "dbt", "--profiles-dir", "dbt", env=env)
    connection = duckdb.connect(str(DB), read_only=True)
    yield connection
    connection.close()


def tickets(con, where: str = "true") -> list[tuple]:
    return con.execute(f"""
        select ticket_id, ticket_type, sla_breached, summary_prefix
        from fct_service_tickets where {where} order by ticket_id
    """).fetchall()


def test_loader_found_every_inverter(con):
    rows = dict(con.execute("select site_id, count(*) from dim_assets group by 1").fetchall())
    assert rows == {2107: 6, 2105: 4}


def test_overnight_outage_is_one_ticket_within_sla(con):
    found = tickets(con, "asset_id = '2107-INV02' and summary_prefix = 'Inverter outage'")
    assert len(found) == 1
    assert found[0][2] is False


def test_long_outage_breaches_restore_sla(con):
    found = tickets(con, "asset_id = '2105-INV03' and ticket_type = 'outage'")
    assert len(found) == 1
    assert found[0][2] is True
    hours = con.execute("""select duration_hours from fct_service_tickets
                           where asset_id = '2105-INV03'""").fetchone()[0]
    assert 95 <= hours <= 105


def test_short_dip_does_not_open_a_ticket(con):
    assert tickets(con, "asset_id = '2107-INV01' and summary_prefix = 'Inverter outage'") == []


def test_communications_gap_is_not_downtime(con):
    no_data, down = con.execute("""
        select sum(no_data_intervals), sum(down_intervals)
        from fct_inverter_monthly where asset_id = '2107-INV05'
    """).fetchone()
    assert no_data > 100
    assert down >= 30  # only the site-wide trip
    assert tickets(con, "asset_id = '2107-INV05' and summary_prefix = 'Inverter outage'") == []


def test_underperforming_inverter_is_flagged(con):
    found = tickets(con, "ticket_type = 'underperformance'")
    assert [t[0] for t in found] == ["UND-2107-INV04-202307"]
    pi = con.execute("""select performance_index from fct_inverter_monthly
                        where asset_id = '2107-INV04' and month = '2023-07-01'""").fetchone()[0]
    assert 0.78 <= pi <= 0.83


def test_site_wide_trip_is_detected_on_every_inverter(con):
    found = tickets(con, "summary_prefix = 'Site-wide outage'")
    assert len(found) == 6
    assert {t[0].split("-")[2] for t in found} == {f"INV0{i}" for i in range(1, 7)}


def test_total_outage_ticket_count(con):
    assert len(tickets(con, "ticket_type = 'outage'")) == 8


def test_breach_month_is_flagged(con):
    status = dict(con.execute("""
        select site_id || '-' || strftime(month, '%Y%m'), compliance_status
        from fct_site_monthly_compliance
    """).fetchall())
    assert status["2105-202306"] == "breach"
    assert status["2105-202307"] == "compliant"
