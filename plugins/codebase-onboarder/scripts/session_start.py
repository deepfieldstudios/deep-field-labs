#!/usr/bin/env python3
"""SessionStart: inject ONBOARDING.md Commands + Gotchas (capped), or suggest /onboarder:init."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import onboarder_lib as ol  # noqa: E402

CAP = 1200
SUGGEST_MIN_FILES = 20
SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target", ".claude"}


def section(text, title):
    m = re.search(r"^## %s\s*$(.*?)(?=^## |^---\s*$|\Z)" % re.escape(title), text, re.M | re.S)
    return m.group(1).strip() if m else ""


def count_files(root, stop_after):
    n = 0
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP]
        n += len(files)
        if n > stop_after:
            break
    return n


def build(cwd):
    path = os.path.join(cwd, "ONBOARDING.md")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = None
    if text is None:
        if count_files(cwd, SUGGEST_MIN_FILES) > SUGGEST_MIN_FILES:
            return "No ONBOARDING.md here yet. Suggest running /onboarder:init to map the repo's commands, environment and gotchas."
        return None
    header = "From ONBOARDING.md (codebase-onboarder; `unverified` commands have not been run yet):"
    body = ""
    for title in ("Commands", "Gotchas"):
        s = section(text, title)
        if s:
            body += "\n## %s\n%s\n" % (title, s)
    if not body:
        return None
    out = header + body
    if len(out) > CAP:
        out = out[:CAP - 40].rsplit("\n", 1)[0] + "\n... (truncated; see ONBOARDING.md)"
    return out


def main():
    try:
        data = ol.read_hook_input(sys.stdin)
        text = build(data.get("cwd") or os.getcwd())
        if text:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    except Exception as e:
        print("onboarder session_start: %s" % e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
