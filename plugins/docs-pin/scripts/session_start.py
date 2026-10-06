#!/usr/bin/env python3
"""SessionStart hook: inject the project's pinned dependency versions."""
import json
import os
import sys

import versions as V

CAP = 40


def build_context(cwd, cap=CAP):
    info = V.detect(cwd)
    table = V.render_table(info, cap)
    if not table:
        return None
    return "\n".join([
        "## Pinned versions in this project (docs-pin)",
        "Detected from: %s" % ", ".join(dict.fromkeys(info["files"])),
        "",
        table,
        "",
        "Write code for these exact versions, not for whatever version you remember best. "
        "Avoid APIs that are deprecated or removed in them, and do not use APIs newer than them. "
        "When unsure how an API looks at this version, read the installed source or type definitions "
        "(node_modules/<pkg>/package.json and its .d.ts files, or the package under site-packages) "
        "before writing the call.",
    ])


def main():
    data = json.load(sys.stdin)
    cwd = data.get("cwd") or os.getcwd()
    context = build_context(cwd)
    if context:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # never break the session
        sys.stderr.write("docs-pin: session_start error: %s\n" % exc)
    sys.exit(0)
