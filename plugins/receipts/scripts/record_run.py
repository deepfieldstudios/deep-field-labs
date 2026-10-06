#!/usr/bin/env python3
"""PostToolUse(Bash) hook: append the run to the session ledger.

Silent on stdout; never blocks. Ledger: <cwd>/.claude/receipts/<session>.jsonl
"""
import json
import os
import sys

import receipts_lib as lib


def main():
    data = json.load(sys.stdin)
    if data.get("tool_name") != "Bash":
        return
    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command") or ""
    if not command.strip():
        return
    cwd = data.get("cwd") or os.getcwd()
    entry = lib.make_entry(command, data.get("tool_response"), data.get("tool_use_id"))
    path = lib.ledger_path(cwd, data.get("session_id"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # never break the session
        sys.stderr.write("receipts: record_run error: %s\n" % exc)
    sys.exit(0)
