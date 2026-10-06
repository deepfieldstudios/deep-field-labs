#!/usr/bin/env python3
"""See and restore saved copies of files.

  versions.py list [file]                      saved copies of one file, or every file with copies
  versions.py restore <file> <when>            put back an earlier copy (undoable)
  versions.py changes <file> <when>            what is different between that copy and the file now

<when> can be: a number from the list (1 = newest), "latest", "oldest", "undo",
"today", "yesterday", a weekday ("tuesday", "last friday", "tuesday 14:02"),
"2 hours ago" / "30 minutes ago" / "3 days ago" / "an hour ago", a date ("2026-10-05 14:00"),
or the saved copy's name / code (e.g. "3fa9c1d2").
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import om_common as om  # noqa: E402

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
UNITS = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600,
         "day": 86400, "week": 604800}
WORD_NUMS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15,
             "twenty": 20, "thirty": 30, "forty-five": 45, "half an": 0.5, "half a": 0.5}


def entries_for(path, cfg):
    return om.read_index(om.version_dir_for(path, cfg))


def _t(e):
    return om.parse_time(e["time"])


def _parse_hm(s):
    m = re.search(r"\b(\d{1,2})[:.](\d{2})\s*(am|pm)?\b|\b(\d{1,2})\s*(am|pm)\b", s)
    if not m:
        return None
    if m.group(1):
        h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    else:
        h, mi, ap = int(m.group(4)), 0, m.group(5)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    return h, mi


def _pick_on_day(entries, day, hm):
    on_day = [e for e in entries if _t(e).date() == day]
    if not on_day:
        return None
    if hm is None:
        return on_day[-1]  # the last copy saved that day
    target = dt.datetime.combine(day, dt.time(*hm)).astimezone()
    return min(on_day, key=lambda e: abs((_t(e) - target).total_seconds()))


def select_version(entries, selector, now=None, vdir=None):
    """Pick one saved copy from plain-language `selector`. Entries are oldest first."""
    if not entries:
        return None
    now = (now or om.now_local()).astimezone()
    s = " ".join(str(selector).lower().replace(",", " ").split())
    s = re.sub(r"^(the\s+)?(version|copy)\s+(from\s+)?", "", s).strip()
    s = re.sub(r"^from\s+", "", s)

    if s in ("latest", "last", "newest", "most recent", "previous", "last saved"):
        return entries[-1]
    if s in ("oldest", "first", "original", "earliest"):
        return entries[0]
    if s in ("undo", "undo restore", "before restore", "before the restore"):
        info = {}
        if vdir:
            try:
                info = json.loads((Path(vdir) / "last_restore.json").read_text())
            except (OSError, ValueError):
                info = {}
        if info.get("before_sha"):
            for e in reversed(entries):
                if e.get("sha") == info["before_sha"]:
                    return e
        for e in reversed(entries):
            if e.get("reason") == "restore":
                return e
        return None

    # Number from the list: 1 = newest.
    m = re.fullmatch(r"#?(\d{1,3})", s)
    if m:
        n = int(m.group(1))
        return entries[-n] if 1 <= n <= len(entries) else None

    # Saved copy name or hash code.
    for e in entries:
        if s == e["version"].lower() or (len(s) >= 4 and re.fullmatch(r"[0-9a-f]+", s)
                                         and e.get("sha", "").startswith(s)):
            return e

    # "2 hours ago", "an hour ago", "half an hour ago", "3 days ago"
    m = re.fullmatch(r"(\d+(?:\.\d+)?|half an|half a|[a-z\-]+)\s*(second|sec|minute|min|hour|hr|day|week)s?\s+ago", s)
    if m:
        qty = m.group(1)
        n = float(qty) if re.match(r"\d", qty) else WORD_NUMS.get(qty)
        if n is None:
            return None
        target = now - dt.timedelta(seconds=n * UNITS[m.group(2)])
        before = [e for e in entries if _t(e) <= target]
        return before[-1] if before else None

    hm = _parse_hm(s)
    # Today / yesterday / weekday, optionally "at 14:02" / "3pm".
    if s.startswith("today"):
        return _pick_on_day(entries, now.date(), hm)
    if s.startswith("yesterday"):
        return _pick_on_day(entries, now.date() - dt.timedelta(days=1), hm)
    m = re.match(r"(last\s+|on\s+)?(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b", s)
    if m:
        wd = [w[:3] for w in WEEKDAYS].index(m.group(2))
        back = (now.weekday() - wd) % 7
        if back == 0 and m.group(1) and m.group(1).strip() == "last":
            back = 7
        return _pick_on_day(entries, now.date() - dt.timedelta(days=back), hm)

    # ISO date, optionally with a time.
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        day = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return _pick_on_day(entries, day, hm)
    return None


# ---------- commands ----------

def cmd_list(path, cfg, cwd, now=None, out=sys.stdout):
    if path:
        p = om.abspath(path, cwd)
        entries = entries_for(p, cfg)
        if not entries:
            print(f"No saved copies of {p.name} yet (in {p.parent}).", file=out)
            return 1
        print(f"Saved copies of {p.name} (in {p.parent}), newest first:", file=out)
        for i, e in enumerate(reversed(entries), 1):
            print(f"  {i:>2}. {om.human_time(_t(e), now):<18} {om.human_size(e.get('size')):>9}   "
                  f"{om.human_reason(e.get('reason'))}   [{e['sha'][:8]}]", file=out)
        print(f"\nTo put one back: restore {p.name} <number or when, e.g. 'yesterday'>", file=out)
        return 0
    found = list(_all_version_dirs(cwd, cfg))
    if not found:
        print(f"No saved copies yet for files in {cwd}.", file=out)
        return 1
    print(f"Files with saved copies (in {cwd}):", file=out)
    for orig, entries in sorted(found):
        try:
            shown = os.path.relpath(orig, cwd)
        except ValueError:
            shown = orig
        print(f"  {shown:<40} {len(entries):>3} cop{'y' if len(entries) == 1 else 'ies'}, "
              f"latest {om.human_time(_t(entries[-1]), now)}", file=out)
    return 0


def _all_version_dirs(cwd, cfg):
    """Yield (original path, entries) for every file with copies under cwd."""
    cwd = os.path.abspath(cwd)
    seen = set()
    roots = []
    for dirpath, dirnames, _ in os.walk(cwd):
        if len(Path(dirpath).relative_to(cwd).parts) > 8:
            dirnames[:] = []
            continue
        if ".versions" in dirnames:
            roots.append(Path(dirpath) / ".versions")
        dirnames[:] = [d for d in dirnames if d not in om.SKIP_PARTS and not d.startswith(".")]
    central = om.home() / "versions"
    if central.is_dir():
        roots.append(central)
    for root in roots:
        for vdir in sorted(root.iterdir()) if root.is_dir() else []:
            entries = om.read_index(vdir)
            if not entries:
                continue
            orig = entries[-1].get("original", "")
            if root == central and not orig.startswith(str(Path(cwd).resolve())) \
                    and not orig.startswith(str(cwd)):
                continue
            if orig in seen:
                continue
            seen.add(orig)
            yield orig, entries


def cmd_restore(path, selector, cfg, cwd, session_id="", now=None, out=sys.stdout):
    p = om.abspath(path, cwd)
    vdir = om.version_dir_for(p, cfg)
    entries = om.read_index(vdir)
    if not entries:
        print(f"There are no saved copies of {p.name} to restore.", file=out)
        return 1
    chosen = select_version(entries, selector, now=now, vdir=vdir)
    if not chosen:
        print(f"I couldn't find a saved copy of {p.name} matching \"{selector}\". "
              f"Run 'list {p.name}' to see what's available.", file=out)
        return 1
    current_sha = om.sha256_file(p) if p.is_file() else None
    if current_sha == chosen["sha"]:
        print(f"{p.name} already matches the copy from {om.human_time(_t(chosen), now)}. Nothing to do.", file=out)
        return 0
    # Save what is there now first, so the restore itself can be undone.
    before = om.snapshot(p, cfg, session_id=session_id, reason="restore", now=now) if p.is_file() else None
    p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(chosen["_path"], p)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "last_restore.json").write_text(json.dumps({
        "before_sha": before["entry"]["sha"] if before else None,
        "restored_sha": chosen["sha"], "time": (now or om.now_local()).isoformat(timespec="seconds")}))
    print(f"Restored {p.name} (in {p.parent}) to the copy from {om.human_time(_t(chosen), now)}.", file=out)
    if before:
        print(f"The version you had a moment ago was saved too. To undo: restore {p.name} undo", file=out)
    return 0


def cmd_changes(path, selector, cfg, cwd, now=None, out=sys.stdout):
    import preview
    p = om.abspath(path, cwd)
    vdir = om.version_dir_for(p, cfg)
    chosen = select_version(om.read_index(vdir), selector, now=now, vdir=vdir)
    if not chosen:
        print(f"I couldn't find a saved copy of {p.name} matching \"{selector}\".", file=out)
        return 1
    print(f"Comparing the copy from {om.human_time(_t(chosen), now)} with {p.name} as it is now.", file=out)
    print(preview.summarize(chosen["_path"], p, name=p.name), file=out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="See and restore saved copies of files.")
    ap.add_argument("--cwd", default=os.getcwd(), help="Folder to resolve file names against")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list")
    pl.add_argument("file", nargs="?")
    pr = sub.add_parser("restore")
    pr.add_argument("file")
    pr.add_argument("when", nargs="+")
    pc = sub.add_parser("changes")
    pc.add_argument("file")
    pc.add_argument("when", nargs="+")
    a = ap.parse_args(argv)
    cfg = om.load_config(a.cwd)
    if a.cmd == "list":
        return cmd_list(a.file, cfg, a.cwd)
    if a.cmd == "restore":
        return cmd_restore(a.file, " ".join(a.when), cfg, a.cwd,
                           session_id=os.environ.get("CLAUDE_SESSION_ID", ""))
    return cmd_changes(a.file, " ".join(a.when), cfg, a.cwd)


if __name__ == "__main__":
    sys.exit(main())
