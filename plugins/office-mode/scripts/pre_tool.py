#!/usr/bin/env python3
"""PreToolUse hook (Write|Edit|Bash): save a copy of each existing file before it changes.

Never blocks. Prints {"systemMessage": "Saved a copy of budget.xlsx (...)"} when a new copy is made,
and remembers which copy belongs to which file so the PostToolUse hook can describe the change.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import om_common as om  # noqa: E402
from bash_detect import bash_targets  # noqa: E402

FILE_TOOLS = {"Write", "Edit", "MultiEdit"}


def pending_path(cwd):
    return om.project_state_dir(cwd) / "pending.json"


def load_pending(cwd):
    try:
        with open(pending_path(cwd), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_pending(cwd, data):
    cutoff = time.time() - 86400  # forget anything older than a day
    for sid in list(data):
        data[sid] = {k: v for k, v in data[sid].items() if v.get("at", 0) > cutoff}
        if not data[sid]:
            del data[sid]
    p = pending_path(cwd)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, p)


def targets_for(tool, tool_input, cwd, cfg):
    if tool in FILE_TOOLS:
        fp = tool_input.get("file_path")
        if not fp:
            return []
        p = om.abspath(fp, cwd)
        if p.is_file() and om.is_office_file(p, cfg):  # documents only, never source code
            return [str(p)]
        return []
    if tool == "Bash":
        return bash_targets(tool_input.get("command", ""), cwd, set(cfg["extensions"]))
    return []


def handle(data):
    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()
    session = data.get("session_id") or ""
    cfg = om.load_config(cwd)
    targets = targets_for(tool, tool_input, cwd, cfg)
    if not targets:
        return None
    pending = load_pending(cwd)
    mine = pending.setdefault(session, {})
    saved = []
    for path in targets:
        r = om.snapshot(path, cfg, session_id=session, reason=tool)
        if not r:
            continue
        mine[path] = {"version_path": r["version_path"], "tool": tool, "at": time.time()}
        if r["saved"]:
            saved.append(os.path.basename(path))
    save_pending(cwd, pending)
    if not saved:
        return None
    if len(saved) == 1:
        msg = f"Saved a copy of {saved[0]} (restore with /office-mode:restore)"
    else:
        shown = ", ".join(saved[:5]) + (f" and {len(saved) - 5} more" if len(saved) > 5 else "")
        msg = f"Saved copies of {shown} (restore with /office-mode:restore)"
    return {"systemMessage": msg}


def main():
    try:
        data = json.load(sys.stdin)
        out = handle(data)
        if out:
            print(json.dumps(out))
    except Exception as exc:  # a hook must never break the session
        print(f"office-mode: could not save a copy ({exc})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
