"""Push service tickets to a Jira Cloud project through the REST API.

Dry run by default: prints the issue payloads without sending anything.
With --send, creates one issue per ticket and skips tickets already in Jira
(each issue carries its ticket_id as a label, so reruns do not duplicate).

Environment (only needed with --send):
    JIRA_BASE_URL     e.g. https://your-site.atlassian.net
    JIRA_EMAIL        Atlassian account email
    JIRA_API_TOKEN    API token from id.atlassian.com
    JIRA_PROJECT_KEY  e.g. SOLAR

Usage:
    python -m integrations.jira_push --limit 5                 # dry run
    python -m integrations.jira_push --limit 20 --send --only-sla-breaches
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd
import requests

PRIORITY_RANK = {"High": 0, "Medium": 1, "Low": 2}


def adf(text_lines: list[str]) -> dict:
    """Minimal Atlassian Document Format body."""
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]} for line in text_lines
    ]}


def build_payload(row: pd.Series, project_key: str) -> dict:
    opened = pd.Timestamp(row["opened_at"]).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"Ticket {row['ticket_id']} generated from telemetry by solar-fleet-compliance.",
        f"Asset: {row['asset_id']} (site {row['site_id']})",
        f"Opened: {opened}",
    ]
    if row["ticket_type"] == "outage":
        lines += [f"Duration: {row['duration_hours']:.1f} h against a {row['sla_hours']:.0f} h restore SLA"
                  + (" (breached)" if row["sla_breached"] else ""),
                  f"Estimated lost energy: {row['lost_kwh']:,.0f} kWh"]
    else:
        lines += [f"Performance index vs peers: {row['performance_index']:.2f}",
                  f"Estimated energy gap: {row['lost_kwh']:,.0f} kWh"]
    return {"fields": {
        "project": {"key": project_key},
        "issuetype": {"name": "Task"},
        "summary": f"{row['summary_prefix']}: {row['asset_id']} ({opened[:10]})",
        "description": adf(lines),
        "labels": ["solar-fleet", row["ticket_type"], f"site-{row['site_id']}", row["ticket_id"]],
    }}


def already_exists(session: requests.Session, base: str, project: str, ticket_id: str) -> bool:
    jql = f'project = "{project}" AND labels = "{ticket_id}"'
    r = session.get(f"{base}/rest/api/3/search/jql",
                    params={"jql": jql, "maxResults": 1, "fields": "key"}, timeout=30)
    r.raise_for_status()
    return bool(r.json().get("issues"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickets", default="reports/marts/fct_service_tickets.csv")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--only-sla-breaches", action="store_true")
    ap.add_argument("--send", action="store_true", help="create issues (default is a dry run)")
    args = ap.parse_args(argv)

    t = pd.read_csv(Path(args.tickets), parse_dates=["opened_at", "closed_at"])
    if args.only_sla_breaches:
        t = t[t["sla_breached"]]
    t = (t.assign(rank=t["priority"].map(PRIORITY_RANK))
          .sort_values(["rank", "lost_kwh"], ascending=[True, False]).head(args.limit))

    project = os.environ.get("JIRA_PROJECT_KEY", "SOLAR")
    payloads = [(r["ticket_id"], build_payload(r, project)) for _, r in t.iterrows()]
    if not args.send:
        for tid, p in payloads:
            print(json.dumps({"ticket_id": tid, **p}, indent=2, default=str))
        print(f"Dry run: {len(payloads)} issue(s) prepared. Add --send to create them.")
        return 0

    missing = [k for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN") if not os.environ.get(k)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 2
    base = os.environ["JIRA_BASE_URL"].rstrip("/")
    with requests.Session() as s:
        s.auth = (os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])
        s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        created = skipped = 0
        for tid, p in payloads:
            if already_exists(s, base, project, tid):
                skipped += 1
                continue
            r = s.post(f"{base}/rest/api/3/issue", data=json.dumps(p), timeout=30)
            if r.status_code >= 300:
                print(f"{tid}: {r.status_code} {r.text[:300]}", file=sys.stderr)
                continue
            created += 1
            print(f"{tid} -> {r.json()['key']}")
    print(f"Created {created}, skipped {skipped} already in Jira.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
