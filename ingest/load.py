"""Reshape raw PVDAQ CSVs into tidy 5-minute tables.

Outputs (one parquet file per site and year):
    <processed_dir>/inverter_power/site_id=<id>/<year>.parquet
        site_id (int), inverter_id (varchar), ts (timestamp, site local time),
        ac_kw (double, mean AC power over the 5-minute bucket)
    <processed_dir>/site_irradiance/site_id=<id>/data.parquet
        site_id (int), ts (timestamp), poa_wm2 (double, plane-of-array irradiance)

A site whose files cannot be read is skipped and reported, so one bad source
does not stop the rest of the fleet.

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
    irradiance: dict = field(default_factory=dict)
    meter_rows: int = 0
    error: str = ""


def read_columns(con: duckdb.DuckDBPyConnection, path: Path) -> list[str]:
    rel = con.sql(f"select * from read_csv({lit(path)}, header = true, all_varchar = true) limit 0")
    return rel.columns


def inverter_id(match: re.Match) -> str:
    """INV07 for plain numbers, INV1.2 for dotted IDs such as block.inverter."""
    g = match.group(1)
    if g.isdigit():
        return f"INV{int(g):02d}"
    return "INV" + re.sub(r"[^0-9A-Za-z]+", ".", g).strip(".").upper()


UTC_COLUMN = "utc_measured_on"


def ts_expr(cols: list[str], site: dict, preferred: str) -> tuple[str | None, str]:
    """SQL expression for the reading timestamp in site local time.

    Most PVDAQ files carry local time in `measured_on`; some newer files carry
    UTC in `utc_measured_on`, which is converted using the site's time zone."""
    if preferred in cols:
        return f"try_cast({q(preferred)} as timestamp)", ""
    if UTC_COLUMN in cols and site.get("timezone"):
        tz = lit(site["timezone"])
        return (f"timezone({tz}, timezone('UTC', try_cast({q(UTC_COLUMN)} as timestamp)))",
                f"timestamps converted from UTC to {site['timezone']}")
    return None, "no usable timestamp column"


def load_wide_power(con, site: dict, files: list[Path], prof: SiteProfile) -> None:
    pattern = re.compile(site["value_regex"])
    meter_pattern = re.compile(site["meter_regex"]) if site.get("meter_regex") else None
    factor = UNIT_FACTOR[site.get("power_units", "kW")]
    for path in files:
        cols = read_columns(con, path)
        fp = FileProfile(path.name, len(cols), sample_columns=cols[:40])
        mapping = {c: inverter_id(m) for c in cols if (m := pattern.match(c))}
        meters = [c for c in cols if meter_pattern and meter_pattern.match(c)]
        fp.matched = sorted(mapping)
        prof.files.append(fp)
        ts, note = ts_expr(cols, site, site["timestamp_column"])
        fp.note = "; ".join(x for x in [note, f"meter: {meters[0]}" if meters else ""] if x)
        if ts is None or not mapping:
            fp.note = fp.note or "no matching power columns"
            continue
        values = ", ".join(f"({lit(c)}, {lit(i)})" for c, i in mapping.items())
        select_cols = ", ".join(q(c) for c in mapping)
        con.execute(f"""
            insert into raw_power
            with src as (
                select {ts} as reading_ts, {select_cols}
                from read_csv({lit(path)}, header = true, all_varchar = true)
            ),
            long_values as (unpivot src on {select_cols} into name col value val),
            inv_map(col, inverter_id) as (values {values})
            select {site['site_id']} as site_id,
                   inv_map.inverter_id,
                   long_values.reading_ts,
                   try_cast(long_values.val as double) * {factor} as ac_kw
            from long_values join inv_map using (col)
        """)
        if meters:
            con.execute(f"""
                insert into raw_meter
                select {ts}, try_cast({q(meters[0])} as double) * {factor}
                from read_csv({lit(path)}, header = true, all_varchar = true)
            """)


def load_per_inverter_energy(con, site: dict, files: list[Path], prof: SiteProfile) -> None:
    pattern = re.compile(site["value_regex"])
    for path in files:
        cols = read_columns(con, path)
        fp = FileProfile(path.name, len(cols), sample_columns=cols[:40])
        matches = [(c, m) for c in cols if (m := pattern.match(c))]
        fp.matched = [c for c, _ in matches]
        prof.files.append(fp)
        ts, _ = ts_expr(cols, site, site["timestamp_column"])
        if ts is None or len(matches) != 1:
            fp.note = f"expected 1 energy column and a timestamp, found {len(matches)} columns"
            continue
        col, m = matches[0]
        inv = inverter_id(m)
        con.execute(f"""
            create or replace temp table e as
            select * from (
                select {ts} as reading_ts, try_cast({q(col)} as double) as kwh
                from read_csv({lit(path)}, header = true, all_varchar = true)
            ) where reading_ts is not null and kwh is not null;
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


def series_files(site: dict, kind: str) -> list[str]:
    return list((site.get(kind) or {}).get("files", []))


def irradiance_files(site: dict) -> list[str]:
    return series_files(site, "irradiance")


def read_series(con, site: dict, kind: str, raw_dir: Path, prof: SiteProfile,
                table: str, combine: str) -> None:
    """Read a site-level series (irradiance or meter) from its own files into
    `table` (reading_ts, value). combine='mean' averages every matching column
    (several irradiance sensors); combine='first' takes the first match (meter total)."""
    sid = int(site["site_id"])
    spec = site.get(kind) or {}
    pattern = re.compile(spec.get("value_regex", "$^"), re.IGNORECASE)
    ts_col = spec.get("timestamp_column", site["timestamp_column"])
    for rel in series_files(site, kind):
        path = raw_dir / str(sid) / Path(rel).name
        if not path.exists():
            prof.files.append(FileProfile(path.name, 0, note=f"{kind} file missing"))
            continue
        cols = read_columns(con, path)
        matched = [c for c in cols if pattern.search(c)]
        if combine == "first":
            matched = matched[:1]
        ts, note = ts_expr(cols, site, ts_col)
        prof.files.append(FileProfile(path.name, len(cols), matched=matched, sample_columns=cols[:40],
                                      note="; ".join(x for x in [kind, note] if x)))
        if ts is None or not matched:
            continue
        total = " + ".join(f"coalesce(try_cast({q(c)} as double), 0)" for c in matched)
        n = " + ".join(f"(try_cast({q(c)} as double) is not null)::int" for c in matched)
        con.execute(f"""
            insert into {table}
            select {ts}, ({total}) / nullif({n}, 0)
            from read_csv({lit(path)}, header = true, all_varchar = true)
        """)


def load_irradiance(con, site: dict, raw_dir: Path, out_root: Path, prof: SiteProfile) -> None:
    """Average the matching plane-of-array sensors into one 5-minute series.
    Always writes a file (possibly empty) so downstream models can rely on it."""
    sid = int(site["site_id"])
    con.execute("create or replace temp table raw_irr (reading_ts timestamp, poa_wm2 double)")
    read_series(con, site, "irradiance", raw_dir, prof, "raw_irr", "mean")
    out_dir = out_root / "site_irradiance" / f"site_id={sid}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    con.execute(f"""
        copy (
            select {sid}::integer as site_id,
                   time_bucket({BUCKET}, reading_ts) as ts,
                   round(avg(poa_wm2), 1) as poa_wm2
            from raw_irr
            where reading_ts is not null and poa_wm2 between -50 and 2000
            group by all
            order by ts
        ) to {lit(out_dir / 'data.parquet')} (format parquet, compression zstd)
    """)
    row = con.execute(f"""select count(*), min(ts), max(ts), quantile_cont(poa_wm2, 0.99)
                          from read_parquet({lit(out_dir / 'data.parquet')})""").fetchone()
    prof.irradiance = dict(zip(["rows", "first_ts", "last_ts", "p99_wm2"], row))


def write_samples(site: dict, raw_dir: Path, samples_dir: Path, lines: int = 15) -> None:
    """Keep the first lines of each raw file and the site metadata, so units and
    formats can be checked from the repository without the full download."""
    sid = str(site["site_id"])
    out = samples_dir / sid
    out.mkdir(parents=True, exist_ok=True)
    for rel in list(site["files"]) + irradiance_files(site) + series_files(site, "meter"):
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
        create or replace temp table site_power as
        select site_id, inverter_id,
               time_bucket({BUCKET}, reading_ts) as ts,
               round(avg(ac_kw), 3) as ac_kw
        from raw_power
        where site_id = {site_id} and reading_ts is not null and ac_kw is not null
          and isfinite(ac_kw)
        group by all
    """)
    # One file per year keeps every file small enough for a git snapshot.
    years = [r[0] for r in con.execute("select distinct year(ts) from site_power order by 1").fetchall()]
    for y in years:
        con.execute(f"""
            copy (select * from site_power where year(ts) = {y} order by inverter_id, ts)
            to {lit(out_dir / f'{y}.parquet')} (format parquet, compression zstd)
        """)
    row = con.execute("""
        select count(*), count(distinct inverter_id), min(ts), max(ts),
               quantile_cont(ac_kw, 0.5), quantile_cont(ac_kw, 0.99)
        from site_power
    """).fetchone()
    # Site revenue meter (empty file when the site has none), used to tell a
    # real site-wide outage from a monitoring fault that records zeros.
    meter_dir = out_root / "site_meter" / f"site_id={site_id}"
    if meter_dir.exists():
        shutil.rmtree(meter_dir)
    meter_dir.mkdir(parents=True)
    con.execute(f"""
        copy (
            select {site_id}::integer as site_id,
                   time_bucket({BUCKET}, reading_ts) as ts,
                   round(avg(meter_kw), 3) as meter_kw
            from raw_meter
            where reading_ts is not null and meter_kw is not null and isfinite(meter_kw)
            group by all order by ts
        ) to {lit(meter_dir / 'data.parquet')} (format parquet, compression zstd)
    """)
    prof.meter_rows = con.execute(f"select count(*) from read_parquet({lit(meter_dir / 'data.parquet')})").fetchone()[0]
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
        lines.append(f"- revenue meter rows: {p.meter_rows:,}" if p.meter_rows else "- revenue meter: none")
        ir = p.irradiance
        if ir and ir.get("rows"):
            lines.append(f"- irradiance rows: {ir['rows']:,} | range: {ir['first_ts']} to {ir['last_ts']} | "
                         f"p99 POA: {ir['p99_wm2'] or 0:,.0f} W/m2")
        else:
            lines.append("- irradiance: none (peer comparison only)")
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
        con.execute("create or replace temp table raw_meter (reading_ts timestamp, meter_kw double)")
        files = [cfg["raw_dir"] / str(sid) / Path(f).name for f in site["files"]]
        missing = [f.name for f in files if not f.exists()]
        if missing:
            prof.error = f"missing raw files: {', '.join(missing)}"
        files = [f for f in files if f.exists()]
        write_samples(site, cfg["raw_dir"], samples_dir)
        try:
            LOADERS[site["layout"]](con, site, files, prof)
            read_series(con, site, "meter", cfg["raw_dir"], prof, "raw_meter", "first")
            write_site(con, sid, cfg["processed_dir"], prof)
            load_irradiance(con, site, cfg["raw_dir"], cfg["processed_dir"], prof)
        except duckdb.Error as exc:
            prof.error = f"{prof.error} {type(exc).__name__}: {exc}".strip()
        if not prof.stats or not prof.stats.get("rows"):
            # Skip the site entirely rather than leave partial outputs behind.
            for sub in ("inverter_power", "site_irradiance", "site_meter"):
                shutil.rmtree(cfg["processed_dir"] / sub / f"site_id={sid}", ignore_errors=True)
        print(f"[{sid}] {prof.stats or prof.error} {prof.irradiance or ''}", flush=True)

    profile_path = Path(args.profile)
    if not profile_path.is_absolute():
        profile_path = ROOT / profile_path
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(render_profile(profiles))

    usable = [p.site_id for p in profiles if p.stats and p.stats.get("rows")]
    skipped = [p.site_id for p in profiles if p.site_id not in usable]
    if skipped:
        print(f"Skipped site(s) with no usable data: {skipped}. See {profile_path}")
    return 0 if usable else 1


if __name__ == "__main__":
    raise SystemExit(main())
