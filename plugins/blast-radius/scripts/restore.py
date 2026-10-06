#!/usr/bin/env python3
"""List blast-radius recovery points and print how to restore each one.

Usage: restore.py [SNAPSHOT_ID] [--cwd DIR] [--limit N]
This script only prints instructions; it never restores anything itself.
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402


def load_records(cwd):
    dirs = [config.state_dir(cwd), os.path.expanduser("~/.claude/blast-radius")]
    seen, out = set(), []
    for d in dirs:
        try:
            with open(os.path.join(d, "snapshots.jsonl")) as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("id") not in seen:
                        seen.add(r.get("id"))
                        out.append(r)
        except OSError:
            continue
    return out


def git_refs(cwd):
    """Snapshot refs present in the repo at cwd (covers refs whose log was lost)."""
    try:
        r = subprocess.run(["git", "for-each-ref", "--sort=-refname",
                            "--format=%(refname)\t%(contents:subject)", "refs/blast-radius/"],
                           cwd=cwd, capture_output=True, text=True, timeout=5)
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return [], None
    refs = [l.split("\t", 1) for l in r.stdout.splitlines() if l.strip()] if r.returncode == 0 else []
    return refs, top or None


def q(path):
    return "'" + path.replace("'", "'\\''") + "'"


def describe(r, cwd="."):
    lines = ["%s%s" % (r["id"], "  (%s)" % r["created"] if r.get("created") else ""),
             "  before: %s" % r.get("command", "")]
    for g in r.get("git", []):
        ref, repo = g["ref"], g["repo"]
        lines.append("  git snapshot in %s" % repo)
        here = os.path.realpath(cwd) == os.path.realpath(repo or "")
        git = "git" if here else "git -C %s" % q(repo)
        lines.append("    see what it holds:   %s show --stat %s" % (git, ref))
        lines.append("    compare with now:    %s diff %s --stat" % (git, ref))
        if g.get("paths"):
            rel = " ".join(q(os.path.relpath(p, repo)) for p in g["paths"])
            lines.append("    restore those paths: %s checkout %s -- %s" % (git, ref, rel))
        lines.append("    restore everything:  %s checkout %s -- ." % (git, ref))
        lines.append("    (checkout also stages the restored files; `git restore --staged .` unstages)")
        lines.append("    discard snapshot:    %s update-ref -d %s" % (git, ref))
    c = r.get("copy")
    if c:
        lines.append("  file copy at %s" % c["dir"])
        for root in c.get("roots", []):
            parent = os.path.dirname(root["original"])
            lines.append("    restore %s:" % root["original"])
            lines.append("      mkdir -p %s && cp -Rp %s %s/" % (q(parent), q(root["copy"]), q(parent)))
        lines.append("    discard snapshot:    rm -r %s" % q(c["dir"]))
    if r.get("note"):
        lines.append("  note: %s" % r["note"])
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("snapshot_id", nargs="?")
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args(argv)
    records = sorted(load_records(args.cwd), key=lambda r: r.get("id", ""), reverse=True)
    refs, top = git_refs(args.cwd)
    known = {g["ref"] for r in records for g in r.get("git", [])}
    for ref, subject in refs:
        if ref not in known:
            records.append({"id": ref.rsplit("/", 1)[-1], "command": subject,
                            "git": [{"repo": top, "ref": ref, "paths": []}]})
    if args.snapshot_id:
        records = [r for r in records if r.get("id", "").startswith(args.snapshot_id)]
        if not records:
            print("No snapshot matching %s." % args.snapshot_id)
            return 1
    if not records:
        print("No blast-radius snapshots for %s yet." % args.cwd)
        print("Snapshots are taken automatically before high-risk local deletes/overwrites.")
        return 0
    print("blast-radius recovery points (newest first). Nothing below has been run.\n")
    for r in records[: args.limit if not args.snapshot_id else None]:
        print(describe(r, args.cwd))
        print()
    if len(records) > args.limit and not args.snapshot_id:
        print("... %d older snapshots not shown (use --limit)." % (len(records) - args.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
