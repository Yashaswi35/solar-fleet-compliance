"""Generate small synthetic raw files in the exact PVDAQ Solar Data Prize schemas.

These files exist only to test the pipeline in CI. They contain known, injected
problems so the tests can check that each one is detected:

Site 2107 (wide AC power, 6 inverters, 30 kW each)
  * INV02 outage from day 10 10:00 to day 11 14:00 (crosses a night)  -> 1 outage ticket
  * INV01 15-minute dip on day 3 (below the 30-minute ticket threshold) -> no ticket
  * INV05 communications gap for all of day 20 (no readings)          -> data gap, not downtime
  * INV04 runs at 80% of peers from day 30 (July)                      -> 1 underperformance ticket
  * every inverter trips on day 25 from 10:00 to 13:00                 -> 6 site-wide outage tickets
  * INV06 wakes up at 07:30 every day, while irradiance is under 200 W/m2 -> no tickets
  Site 2107 also gets a plane-of-array irradiance file; site 2105 has none, so it
  exercises the peer-comparison fallback.
Site 2105 (cumulative kWh per inverter file, 4 inverters, 40 kW each)
  * INV03 outage from day 5 08:00 to day 9 12:00 (> 72 h)            -> 1 outage ticket, SLA breach

Usage:
    python -m tests.make_fixtures [--out tests/fixtures/raw]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

START = pd.Timestamp("2023-06-01")
DAYS = 45
RNG = np.random.default_rng(7)


def solar_shape(ts: pd.DatetimeIndex) -> np.ndarray:
    hour = np.asarray(ts.hour + ts.minute / 60, dtype=float)
    x = np.clip((hour - 6) / 14, 0, 1)
    return np.sin(np.pi * x) ** 1.5


def day_cloud(ts: pd.DatetimeIndex) -> np.ndarray:
    factors = RNG.uniform(0.45, 1.0, DAYS + 1)
    return factors[(ts.normalize() - START).days]


def window(ts, d0, h0, d1, h1):
    a = START + pd.Timedelta(days=d0, hours=h0)
    b = START + pd.Timedelta(days=d1, hours=h1)
    return (ts >= a) & (ts < b)


def site_2107(out: Path) -> None:
    ts = pd.date_range(START, periods=DAYS * 288, freq="5min")
    cloud = day_cloud(ts)
    base = 30 * solar_shape(ts) * cloud
    df = pd.DataFrame({"measured_on": ts.strftime("%Y-%m-%d %H:%M:%S")})
    sensor = 149583
    for n in range(1, 7):
        p = base * RNG.normal(1.0, 0.015, len(ts))
        if n == 2:
            p[window(ts, 10, 10, 11, 14)] = 0.0
        if n == 1:
            p[window(ts, 3, 12, 3, 12.25)] = 0.0
        if n == 4:
            p[ts >= START + pd.Timedelta(days=30)] *= 0.80
        if n == 6:
            hour = np.asarray(ts.hour + ts.minute / 60)
            p[(hour >= 6) & (hour < 7.5)] = 0.0  # wakes up late every morning, below 200 W/m2
        p[window(ts, 25, 10, 25, 13)] = 0.0  # site-wide trip, all inverters
        p = np.round(np.maximum(p, 0), 3).astype(object)
        if n == 5:
            p[window(ts, 20, 0, 21, 0)] = None
        df[f"inv_{n:02d}_dc_current_inv_{sensor}"] = 1.0
        df[f"inv_{n:02d}_ac_power_inv_{sensor + 4}"] = p
        sensor += 5
    df = df[[c for c in df.columns if c != "measured_on"] + ["measured_on"]]
    (out / "2107").mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "2107" / "2107_electrical_data.csv", index=False)
    irr = pd.DataFrame({
        "measured_on": ts.strftime("%Y-%m-%d %H:%M:%S"),
        "ambient_temp_o_149575": 25.0,
        "poa_irradiance_o_149574": np.round(1000 * solar_shape(ts) * cloud, 1),
    })
    irr.to_csv(out / "2107" / "2107_irradiance_data.csv", index=False)


def site_2105(out: Path) -> None:
    ts = pd.date_range(START, periods=DAYS * 288, freq="5min")
    ts = ts[(ts.hour >= 6) & (ts.hour < 20)]
    jitter = pd.to_timedelta(RNG.integers(40, 55, len(ts)), unit="s")
    base_kw = 40 * solar_shape(ts) * day_cloud(ts)
    (out / "2105").mkdir(parents=True, exist_ok=True)
    sensor = 150200
    for n in range(1, 5):
        kw = np.maximum(base_kw * RNG.normal(1.0, 0.015, len(ts)), 0)
        if n == 3:
            kw[window(ts, 5, 8, 9, 12)] = 0.0
        kwh = 12000 + np.cumsum(kw * 5 / 60)
        df = pd.DataFrame({
            "measured_on": (ts + jitter).strftime("%Y-%m-%d %H:%M:%S"),
            f"inv_string{n:02d}_ac_output_(kwh)_inv_{sensor + 2}": np.round(kwh, 3),
            f"inv_string{n:02d}_ac_voltage_(v)_inv_{sensor + 1}": 480.0,
        })
        df.to_csv(out / "2105" / f"2105_inv{n:02d}_data.csv", index=False)
        sensor += 5


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tests/fixtures/raw")
    out = Path(ap.parse_args(argv).out)
    site_2107(out)
    site_2105(out)
    print(f"fixtures written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
