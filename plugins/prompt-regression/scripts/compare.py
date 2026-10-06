#!/usr/bin/env python3
"""Compare two results files and write a Markdown report.

  compare.py <old-results.json> <new-results.json> [--out report.md]
  compare.py --latest [--project DIR]          # the two most recent runs in prompt-tests/.results

Shows pass-rate change, cases that regressed or were fixed, output changes (truncated),
and latency/token deltas.
"""
import argparse
import difflib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pr_common as pc  # noqa: E402

MAX_DIFF_LINES = 20
MAX_DIFF_CASES = 10


def load(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data, {(c["suite"], c["case"]): c for c in data.get("cases", [])}


def _rate(d):
    s = d.get("summary", {})
    total = s.get("total") or 0
    return s.get("passed", 0), total, (100.0 * s.get("passed", 0) / total if total else 0.0)


def _avg(cases, key):
    vals = [c.get(key) for c in cases if isinstance(c.get(key), (int, float))]
    return sum(vals) / len(vals) if vals else None


def _tokens(cases, key):
    vals = [(c.get("usage") or {}).get(key) for c in cases]
    vals = [v for v in vals if isinstance(v, (int, float))]
    return sum(vals) if vals else None


def _delta(a, b, unit="", pct=False):
    if a is None or b is None:
        return "n/a"
    d = b - a
    s = f"{d:+.0f}{unit}"
    if pct and a:
        s += f" ({100.0 * d / a:+.0f}%)"
    return s


def _fmt(v, unit=""):
    return "n/a" if v is None else f"{v:.0f}{unit}"


def _why(c):
    if c.get("status") == "error":
        return f"error: {c.get('error', '')[:150]}"
    bad = [f"{a['type']} ({a['detail'][:100]})" if a.get("detail") else a["type"]
           for a in c.get("assertions", []) if a.get("passed") is False]
    return "; ".join(bad) or "failed"


def report(old_path, new_path):
    od, oc = load(old_path)
    nd, nc = load(new_path)
    op, ot, orate = _rate(od)
    np_, nt, nrate = _rate(nd)
    common = [k for k in nc if k in oc]
    regressed = [k for k in common if oc[k].get("passed") and not nc[k].get("passed")]
    fixed = [k for k in common if not oc[k].get("passed") and nc[k].get("passed")]
    still = [k for k in common if not oc[k].get("passed") and not nc[k].get("passed")]
    added = [k for k in nc if k not in oc]
    removed = [k for k in oc if k not in nc]
    ocs, ncs = [oc[k] for k in common], [nc[k] for k in common]

    lines = ["# Prompt regression report", "",
             f"- Old: `{Path(old_path).name}` ({od.get('started_at', '?')})",
             f"- New: `{Path(new_path).name}` ({nd.get('started_at', '?')})", ""]
    verdict = ("**Regressions found.**" if regressed else
               "**No regressions.**" + (" Some cases were fixed." if fixed else ""))
    lines += [verdict, "",
              "| | Old | New | Change |", "|---|---|---|---|",
              f"| Pass rate | {op}/{ot} ({orate:.1f}%) | {np_}/{nt} ({nrate:.1f}%) | {nrate - orate:+.1f} pts |",
              f"| Avg latency (shared cases) | {_fmt(_avg(ocs, 'latency_ms'), ' ms')} | {_fmt(_avg(ncs, 'latency_ms'), ' ms')} "
              f"| {_delta(_avg(ocs, 'latency_ms'), _avg(ncs, 'latency_ms'), ' ms', True)} |",
              f"| Input tokens | {_fmt(_tokens(ocs, 'input_tokens'))} | {_fmt(_tokens(ncs, 'input_tokens'))} "
              f"| {_delta(_tokens(ocs, 'input_tokens'), _tokens(ncs, 'input_tokens'), '', True)} |",
              f"| Output tokens | {_fmt(_tokens(ocs, 'output_tokens'))} | {_fmt(_tokens(ncs, 'output_tokens'))} "
              f"| {_delta(_tokens(ocs, 'output_tokens'), _tokens(ncs, 'output_tokens'), '', True)} |", ""]

    def section(title, keys, fmt):
        if keys:
            lines.append(f"## {title} ({len(keys)})")
            lines.extend(f"- `{s}/{c}` — {fmt((s, c))}" for s, c in keys)
            lines.append("")
    section("Regressed", regressed, lambda k: _why(nc[k]))
    section("Fixed", fixed, lambda k: f"was: {_why(oc[k])}")
    section("Still failing", still, lambda k: _why(nc[k]))
    section("New cases", added, lambda k: nc[k].get("status", "?"))
    section("Removed cases", removed, lambda k: "no longer run")

    changed = [k for k in common if (oc[k].get("output") or "") != (nc[k].get("output") or "")]
    if changed:
        lines.append(f"## Output changes ({len(changed)})")
        # Regressions first, they matter most.
        changed.sort(key=lambda k: (k not in regressed, k not in fixed))
        for k in changed[:MAX_DIFF_CASES]:
            a = (oc[k].get("output") or "").splitlines()
            b = (nc[k].get("output") or "").splitlines()
            diff = list(difflib.unified_diff(a, b, "old", "new", lineterm="", n=1))[2:]
            shown = diff[:MAX_DIFF_LINES]
            lines += ["", f"### `{k[0]}/{k[1]}`", "```diff", *shown]
            if len(diff) > MAX_DIFF_LINES:
                lines.append(f"... {len(diff) - MAX_DIFF_LINES} more lines")
            lines.append("```")
        if len(changed) > MAX_DIFF_CASES:
            lines.append(f"\n…and {len(changed) - MAX_DIFF_CASES} more cases with changed output.")
        lines.append("")
    return "\n".join(lines), bool(regressed)


def latest_two(project):
    files = sorted(pc.results_dir(project).glob("*.json"), key=lambda p: (p.stat().st_mtime_ns, p.name))
    return files[-2:] if len(files) >= 2 else None


def main(argv=None, out=sys.stdout):
    ap = argparse.ArgumentParser(description="Compare two prompt-regression results files.")
    ap.add_argument("old", nargs="?")
    ap.add_argument("new", nargs="?")
    ap.add_argument("--latest", action="store_true", help="Compare the two most recent runs")
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--out", help="Write the Markdown report to this file too")
    ap.add_argument("--fail-on-regression", action="store_true", help="Exit 1 if any case regressed (for CI)")
    a = ap.parse_args(argv)
    if a.latest:
        pair = latest_two(a.project)
        if not pair:
            print("Need at least two runs in prompt-tests/.results to compare.", file=out)
            return 2
        a.old, a.new = str(pair[0]), str(pair[1])
    if not (a.old and a.new):
        ap.error("give two results files, or --latest")
    md, regressed = report(a.old, a.new)
    print(md, file=out)
    if a.out:
        Path(a.out).write_text(md + "\n", encoding="utf-8")
    return 1 if (regressed and a.fail_on_regression) else 0


if __name__ == "__main__":
    sys.exit(main())
