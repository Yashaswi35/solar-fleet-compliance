"""Reshape raw PVDAQ CSVs into one tidy table of 5-minute inverter AC power.

Output: <processed_dir>/inverter_power/site_id=<id>/data.parquet with columns
    site_id (int), inverter_id (varchar), ts (timestamp, site local standard time),
    ac_kw (double, mean AC power over the 5-minute bucket)

Two raw layouts are supported (see config/sites.yml):
  * wide_power: one file, one AC power column per inverter.
  * per_inverter_energy: one file per inverter with AC energy in kWh. The loader
    detects whether energy is a cumulative meter reading or energy per reading
    and converts it to average power.

A schema profile is written to reports/schema_profile.md so that a change in
the source files is visible in the repository history.

Usage:
    python -m ingest.load [--config config/sites.yml] [--profile reports/schema_profile.md]
"""
from __future__ import annotations

import argparse
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from ingest.config import ROOT, load_config

UNIT_FACTOR = {"kW": 1.0, "W": 0.001}
BUCKET = "INTERVAL 5 MINUTE"


def q(identifier: str) -> str:
    """Quote a SQL identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def lit(value: str) -> str:
    """Quote a SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


@dataclass
class FileProfile:
    name: str
    n_columns: int
    matched: list[str] = field(default_factory=list)
    sample_columns: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class SiteProfile:
    site_id: int
    files: list[FileProfile] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    error: str = ""


def read_columns(con: duckdb.DuckDBPyConnection, path: Path) -> list[str]:
    rel = con.sql(f"select * from read_csv({lit(path)}, header = true, all_varchar = true) limit 0")
    return rel.columns


def inverter_id(match: re.Match) -> str:
    return f"INV{int(match.group(1)):02d}"


def load_wide_power(con, site: dict, files: list[Path], prof: SiteProfile) -> None:
    pattern = re.compile(site["value_regex"])
    factor = UNIT_FACTOR[site.get("power_units", "kW")]
    ts_col = site["timestamp_column"]
    for path in files:
        cols = read_columns(con, path)
        fp = FileProfile(path.name, len(cols), sample_columns=cols[:40])
        mapping = {c: inverter_id(m) for c in cols if (m := pattern.match(c))}
        fp.matched = sorted(mapping)
        prof.files.append(fp)
        if ts_col not in cols or not mapping:
            fp.note = "no timestamp column or no matching power columns"
            continue
        values = ", ".join(f"({lit(c)}, {lit(i)})" for c, i in mapping.items())
        select_cols = ", ".join(q(c) for c in mapping)
        con.execute(f"""
            insert into raw_power
            with src as (
                select {q(ts_col)} as measured_on, {select_cols}
                from read_csv({lit(path)}, header = true, all_varchar = true)
            ),
            long_values as (unpivot src on {select_cols} into name col value val),
            inv_map(col, inverter_id) as (values {values})
            select {site['site_id']} as site_id,
                   inv_map.inverter_id,
                   try_cast(long_values.measured_on as timestamp) as reading_ts,
                   try_cast(long_values.val as double) * {factor} as ac_kw
            from long_values join inv_map using (col)
        """)


def load_per_inverter_energy(con, site: dict, files: list[Path], prof: SiteProfile) -> None:
    pattern = re.compile(site["value_regex"])
    ts_col = site["timestamp_column"]
    for path in files:
        cols = read_columns(con, path)
        fp = FileProfile(path.name, len(cols), sample_columns=cols[:40])
        matches = [(c, m) for c in cols if (m := pattern.match(c))]
        fp.matched = [c for c, _ in matches]
        prof.files.append(fp)
        if ts_col not in cols or len(matches) != 1:
            fp.note = f"expected 1 energy column, found {len(matches)}"
            continue
        col, m = matches[0]
        inv = inverter_id(m)
        con.execute(f"""
            create or replace temp table e as
            select try_cast({q(ts_col)} as timestamp) as reading_ts,
                   try_cast({q(col)} as double) as kwh
            from read_csv({lit(path)}, header = true, all_varchar = true)
            where try_cast({q(ts_col)} as timestamp) is not null
              and try_cast({q(col)} as double) is not null;
            create or replace temp table d as
            select reading_ts, kwh,
                   kwh - lag(kwh) over w as dkwh,
                   epoch(reading_ts) - epoch(lag(reading_ts) over w) as dt_s
            from e window w as (order by reading_ts);
        """)
        neg_share, median_dt = con.execute("""
            select coalesce(avg(case when dkwh < 0 then 1.0 else 0.0 end)
                            filter (where dkwh <> 0), 0),
                   median(dt_s) filter (where dt_s > 0)
            from d
        """).fetchone()
        cumulative = neg_share < 0.05
        median_dt = median_dt or 300
        fp.note = (f"energy column treated as {'cumulative meter' if cumulative else 'energy per reading'} "
                   f"(negative step share {neg_share:.3f}, median step {median_dt:.0f}s)")
        if cumulative:
            power = "dkwh / (dt_s / 3600.0)"
            where = "dt_s between 60 and 1200 and dkwh >= 0"
        else:
            # Energy since the previous reading: divide by that reading's own
            # interval. The first reading after a night gap uses the median step.
            power = (f"kwh / (case when dt_s between 60 and 1200 then dt_s "
                     f"else {median_dt} end / 3600.0)")
            where = "true"
        con.execute(f"""
            insert into raw_power
            select {site['site_id']}, {lit(inv)}, reading_ts, {power}
            from d where {where}
        """)


LOADERS = {"wide_power": load_wide_power, "per_inverter_energy": load_per_inverter_energy}


def write_samples(site: dict, raw_dir: Path, samples_dir: Path, lines: int = 15) -> None:
    """Keep the first lines of each raw file and the site metadata, so units and
    formats can be checked from the repository without the full download."""
    sid = str(site["site_id"])
    out = samples_dir / sid
    out.mkdir(parents=True, exist_ok=True)
    for rel in site["files"]:
        src = raw_dir / sid / Path(rel).name
        if src.exists():
            with open(src, errors="replace") as f:
                head = [next(f, "") for _ in range(lines)]
            (out / f"{src.stem}.head.csv").write_text("".join(head))
    if site.get("metadata"):
        meta = raw_dir / sid / Path(site["metadata"]).name
        if meta.exists():
            shutil.copy(meta, out / meta.name)


def write_site(con, site_id: int, out_root: Path, prof: SiteProfile) -> None:
    out_dir = out_root / "inverter_power" / f"site_id={site_id}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    con.execute(f"""
        copy (
            select site_id, inverter_id,
                   time_bucket({BUCKET}, reading_ts) as ts,
                   avg(ac_kw) as ac_kw
            from raw_power
            where site_id = {site_id} and reading_ts is not null and ac_kw is not null
              and isfinite(ac_kw)
            group by all
            order by inverter_id, ts
        ) to {lit(out_dir / 'data.parquet')} (format parquet)
    """)
    row = con.execute(f"""
        select count(*), count(distinct inverter_id), min(ts), max(ts),
               quantile_cont(ac_kw, 0.5), quantile_cont(ac_kw, 0.99)
        from read_parquet({lit(out_dir / 'data.parquet')})
    """).fetchone()
    prof.stats = dict(zip(["rows", "inverters", "first_ts", "last_ts", "p50_kw", "p99_kw"], row))


def render_profile(profiles: list[SiteProfile]) -> str:
    lines = ["# Schema profile", "",
             "Generated by `ingest/load.py`. Shows what the loader found in each raw file.", ""]
    for p in profiles:
        lines.append(f"## Site {p.site_id}")
        if p.error:
            lines.append(f"**Error:** {p.error}")
        s = p.stats
        if s:
            lines.append(
                f"- 5-minute rows: {s['rows']:,} | inverters: {s['inverters']} | "
                f"range: {s['first_ts']} to {s['last_ts']} | "
                f"median AC kW: {s['p50_kw'] or 0:,.2f} | p99 AC kW: {s['p99_kw'] or 0:,.2f}")
        lines.append("")
        lines.append("| File | Columns | Matched | Note |")
        lines.append("|---|---|---|---|")
        for f in p.files:
            lines.append(f"| {f.name} | {f.n_columns} | {len(f.matched)} | {f.note} |")
        bad = [f for f in p.files if not f.matched]
        for f in bad:
            lines.append("")
            lines.append(f"Columns in `{f.name}` (first 40): `{', '.join(f.sample_columns)}`")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yml")
    ap.add_argument("--profile", default="reports/schema_profile.md")
    ap.add_argument("--samples-dir", default="reports/raw_samples")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    samples_dir = Path(args.samples_dir)
    if not samples_dir.is_absolute():
        samples_dir = ROOT / samples_dir

    con = duckdb.connect()
    con.execute("set preserve_insertion_order = false")
    profiles: list[SiteProfile] = []
    for site in cfg["sites"]:
        sid = int(site["site_id"])
        prof = SiteProfile(sid)
        profiles.append(prof)
        con.execute("""create or replace temp table raw_power
                       (site_id integer, inverter_id varchar, reading_ts timestamp, ac_kw double)""")
        files = [cfg["raw_dir"] / str(sid) / Path(f).name for f in site["files"]]
        missing = [f.name for f in files if not f.exists()]
        if missing:
            prof.error = f"missing raw files: {', '.join(missing)}"
        files = [f for f in files if f.exists()]
        write_samples(site, cfg["raw_dir"], samples_dir)
        try:
            LOADERS[site["layout"]](con, site, files, prof)
            write_site(con, sid, cfg["processed_dir"], prof)
        except duckdb.Error as exc:
            prof.error = f"{prof.error} {type(exc).__name__}: {exc}".strip()
        print(f"[{sid}] {prof.stats or prof.error}", flush=True)

    profile_path = Path(args.profile)
    if not profile_path.is_absolute():
        profile_path = ROOT / profile_path
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(render_profile(profiles))

    failed = [p.site_id for p in profiles if not p.stats or not p.stats.get("rows")]
    if failed:
        print(f"No usable data for site(s): {failed}. See {profile_path}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
