"""Summarize the last dbt test run (dbt/target/run_results.json) into
reports/test_results.md so data test outcomes are visible in the repository.

Usage:
    python -m analysis.test_report
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="dbt/target/run_results.json")
    ap.add_argument("--out", default="reports/test_results.md")
    args = ap.parse_args(argv)

    path = Path(args.results)
    if not path.exists():
        print(f"{path} not found")
        return 0
    results = [r for r in json.loads(path.read_text())["results"]
               if r["unique_id"].startswith("test.")]
    counts = Counter(r["status"] for r in results)
    lines = ["# dbt test results", "",
             f"{len(results)} tests: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())), ""]
    flagged = [r for r in results if r["status"] in ("fail", "warn", "error")]
    if flagged:
        lines += ["| Test | Status | Rows flagged |", "|---|---|---|"]
        for r in sorted(flagged, key=lambda r: r["status"]):
            name = r["unique_id"].split(".")[2]
            lines.append(f"| {name} | {r['status']} | {r.get('failures') or ''} |")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
