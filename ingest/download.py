"""Download the configured PVDAQ files from the public OEDI data lake.

Files are fetched over anonymous HTTPS and skipped when a local copy of the
same size already exists, so the step is safe to rerun.

Usage:
    python -m ingest.download [--config config/sites.yml]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

from ingest.config import load_config

CHUNK = 8 * 1024 * 1024


def remote_size(session: requests.Session, url: str) -> int | None:
    r = session.head(url, timeout=60)
    if r.status_code != 200:
        return None
    return int(r.headers.get("Content-Length", 0))


def fetch(session: requests.Session, url: str, dest: Path) -> str:
    size = remote_size(session, url)
    if size is None:
        return "missing"
    if dest.exists() and dest.stat().st_size == size:
        return "cached"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with session.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(CHUNK):
                f.write(chunk)
    tmp.rename(dest)
    return f"downloaded {size / 1e6:,.1f} MB"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sites.yml")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)

    failures = 0
    with requests.Session() as session:
        for site in cfg["sites"]:
            keys = (list(site["files"])
                    + list((site.get("irradiance") or {}).get("files", []))
                    + ([site["metadata"]] if site.get("metadata") else []))
            for rel in keys:
                url = f"{cfg['base_url']}/{site['prefix']}/{rel}"
                dest = cfg["raw_dir"] / str(site["site_id"]) / Path(rel).name
                try:
                    status = fetch(session, url, dest)
                except requests.RequestException as exc:
                    status = f"error: {exc}"
                if status == "missing" or status.startswith("error"):
                    failures += 1
                print(f"[{site['site_id']}] {Path(rel).name}: {status}", flush=True)
    if failures:
        print(f"{failures} file(s) could not be downloaded", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
