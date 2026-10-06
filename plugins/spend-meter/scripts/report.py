#!/usr/bin/env python3
"""spend-meter report: spend across all projects for the last N days (default 14).

Scans <projects-dir>/*/*.jsonl (default ~/.claude/projects, override CLAUDE_PROJECTS_DIR or
--projects-dir), reuses the incremental session cache, and prints Markdown:
per-day table, per-session table, top 5 sessions with first prompt, per-model split,
cache hit ratio.

    python3 report.py [--days 14] [--projects-dir DIR] [--json]
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usage  # noqa: E402


def collect(projects_dir, days):
    cutoff_date = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    cutoff_mtime = time.time() - (days + 1) * 86400
    sessions = []
    for path in sorted(glob.glob(os.path.join(projects_dir, "*", "*.jsonl"))):
        try:
            if os.path.getmtime(path) < cutoff_mtime:
                continue
        except OSError:
            continue
        sid = os.path.basename(path)[:-6]
        try:
            # Only messages dated inside the window count, for every table.
            s = usage.summarize(usage.update_session(path, sid), since=cutoff_date)
        except Exception:
            continue
        if not s["total"]["messages"]:
            continue
        s["project"] = os.path.basename(os.path.dirname(path))
        s["window_cost"] = s["total"]["cost"]
        s["window_dates"] = s["per_date"]
        sessions.append(s)
    return sessions, cutoff_date


def build(sessions, cutoff_date, days):
    per_day, per_model = {}, {}
    tot = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "cost": 0.0,
           "subagent_cost": 0.0}
    for s in sessions:
        for d, c in s["window_dates"].items():
            row = per_day.setdefault(d, {"cost": 0.0, "sessions": 0})
            row["cost"] += c
            row["sessions"] += 1
        for m, v in s["per_model"].items():
            pm = per_model.setdefault(m, {"cost": 0.0, "input": 0, "output": 0,
                                          "cache_write": 0, "cache_read": 0})
            for k in pm:
                pm[k] += v[k]
        for k in tot:
            tot[k] += s["total"][k]
    denom = tot["input"] + tot["cache_write"] + tot["cache_read"]
    tot["cache_hit_ratio"] = tot["cache_read"] / denom if denom else 0.0
    tot["window_cost"] = sum(s["window_cost"] for s in sessions)
    ranked = sorted(sessions, key=lambda s: -s["window_cost"])
    return {
        "days": days, "since": cutoff_date, "total": tot,
        "per_day": dict(sorted(per_day.items())),
        "per_model": dict(sorted(per_model.items(), key=lambda kv: -kv[1]["cost"])),
        "sessions": [{
            "session_id": s["session_id"], "project": s["project"],
            "cost": round(s["window_cost"], 4),
            "subagent_cost": round(s["total"]["subagent_cost"], 4),
            "tokens": s["total"]["tokens"], "cache_hit_ratio": round(s["total"]["cache_hit_ratio"], 4),
            "first_ts": s["meta"].get("first_ts"), "last_ts": s["meta"].get("last_ts"),
            "first_prompt": (s["meta"].get("first_prompt") or "")[:80],
            "models": sorted(s["per_model"]),
        } for s in ranked],
    }


def short_project(p):
    return p.replace("-Users-" + os.path.basename(os.path.expanduser("~")), "~") or p


def markdown(r):
    t = r["total"]
    out = ["# spend-meter report: last %d days (since %s)" % (r["days"], r["since"]), "",
           "**%s** across %d sessions · subagents %s · cache hit ratio %.0f%%" % (
               usage.fmt_usd(t["window_cost"]), len(r["sessions"]),
               usage.fmt_usd(t["subagent_cost"]), 100 * t["cache_hit_ratio"]),
           "", "Prices come from data/prices.json (editable, verify against current pricing).", "",
           "## Per day", "", "| Date | Cost | Sessions |", "|---|---:|---:|"]
    for d, row in r["per_day"].items():
        out.append("| %s | %s | %d |" % (d, usage.fmt_usd(row["cost"]), row["sessions"]))
    out += ["", "## Top 5 sessions", ""]
    for i, s in enumerate(r["sessions"][:5], 1):
        out.append("%d. **%s** %s `%s` (%s) - \"%s\"" % (
            i, usage.fmt_usd(s["cost"]), (s["last_ts"] or "")[:10], s["session_id"][:8],
            short_project(s["project"]), s["first_prompt"] or "(no prompt)"))
    out += ["", "## By model", "", "| Model | Cost | Input | Output | Cache write | Cache read |",
            "|---|---:|---:|---:|---:|---:|"]
    for m, v in r["per_model"].items():
        out.append("| %s | %s | %s | %s | %s | %s |" % (
            m, usage.fmt_usd(v["cost"]), usage.fmt_tokens(v["input"]), usage.fmt_tokens(v["output"]),
            usage.fmt_tokens(v["cache_write"]), usage.fmt_tokens(v["cache_read"])))
    out += ["", "## Sessions", "", "| Last active | Session | Project | Cost | Subagents | Tokens | Cache hit |",
            "|---|---|---|---:|---:|---:|---:|"]
    for s in r["sessions"]:
        out.append("| %s | `%s` | %s | %s | %s | %s | %.0f%% |" % (
            (s["last_ts"] or "")[:16].replace("T", " "), s["session_id"][:8],
            short_project(s["project"]), usage.fmt_usd(s["cost"]), usage.fmt_usd(s["subagent_cost"]),
            usage.fmt_tokens(s["tokens"]), 100 * s["cache_hit_ratio"]))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--projects-dir", default=os.environ.get("CLAUDE_PROJECTS_DIR") or
                    os.path.join(os.path.expanduser("~"), ".claude", "projects"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    sessions, since = collect(a.projects_dir, max(1, a.days))
    r = build(sessions, since, max(1, a.days))
    print(json.dumps(r, indent=2) if a.json else markdown(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
