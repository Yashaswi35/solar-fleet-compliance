"""Load the fleet configuration."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_config(path: str | Path = "config/sites.yml") -> dict:
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    with open(path) as f:
        cfg = yaml.safe_load(f)
    for key in ("raw_dir", "processed_dir"):
        p = Path(cfg[key])
        cfg[key] = p if p.is_absolute() else ROOT / p
    return cfg
