#!/usr/bin/env python3
"""PostToolUse hook (Write|Edit|Bash): describe what changed in plain English.

Looks up the copy the PreToolUse hook saved, compares it with the file now, and hands
Claude a summary with an instruction to explain the change to the user without jargon.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import om_common as om  # noqa: E402
import preview  # noqa: E402
from pre_tool import FILE_TOOLS, load_pending, save_pending  # noqa: E402

MAX_CONTEXT = 6000


def handle(data):
    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()
    session = data.get("session_id") or ""
    pending = load_pending(cwd)
    mine = pending.get(session, {})
    if not mine:
        return None
    if tool in FILE_TOOLS:
        fp = tool_input.get("file_path")
        if not fp:
            return None
        key = str(om.abspath(fp, cwd))
        keys = [key] if key in mine else []
    elif tool == "Bash":
        keys = [k for k, v in mine.items() if v.get("tool") == "Bash"]
    else:
        return None
    if not keys:
        return None

    summaries = []
    for key in keys:
        info = mine.pop(key)
        old, new = info.get("version_path"), key
        if not old or not os.path.exists(old):
            continue
        if os.path.isfile(new) and om.sha256_file(new) == om.sha256_file(old):
            continue  # nothing actually changed
        name = os.path.basename(new)
        summary = preview.summarize(old, new, name=name)
        summaries.append(f"File: {name}\nWhere: {os.path.dirname(new)}\n{summary}")
    save_pending(cwd, pending)
    if not summaries:
        return None
    ctx = ("office-mode: the following document(s) just changed. A copy of each previous version "
           "is saved and can be put back with /office-mode:restore.\n\n" + "\n\n".join(summaries))
    if len(ctx) > MAX_CONTEXT:
        ctx = ctx[:MAX_CONTEXT] + "\n…(summary shortened)"
    ctx += ("\n\nTell the user in plain, non-technical language what changed (name the file and "
            "say which folder it is in), mention the earlier version is saved, and that they can "
            "ask you to undo it. Do not use words like diff, repo, stdout or commit.")
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": ctx}}


def main():
    try:
        data = json.load(sys.stdin)
        out = handle(data)
        if out:
            print(json.dumps(out))
    except Exception as exc:
        print(f"office-mode: could not summarise the change ({exc})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
