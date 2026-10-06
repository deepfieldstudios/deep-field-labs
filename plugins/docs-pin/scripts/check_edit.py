#!/usr/bin/env python3
"""PreToolUse / PostToolUse hook for Edit|Write|MultiEdit.

PreToolUse: a deny-severity hit returns permissionDecision "ask" (never a hard
  deny) so the user decides. With DOCS_PIN_WARN=allow, warn-only hits return
  "allow" + permissionDecisionReason, as an opt-in.
PostToolUse: every hit is fed back to Claude ({"decision":"block","reason"}),
  so Claude learns about the deprecated call and can fix it. The edit itself
  has already landed; PostToolUse cannot undo it.
"""
import json
import os
import sys

import rules as R


def edit_texts(tool_name, tool_input, on_disk):
    """(new_text, old_text) for the edit being made."""
    if tool_name == "Write":
        return tool_input.get("content") or "", on_disk
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        return ("\n".join(e.get("new_string") or "" for e in edits),
                "\n".join(e.get("old_string") or "" for e in edits))
    if tool_name == "NotebookEdit":
        return tool_input.get("new_source") or "", ""
    return tool_input.get("new_string") or "", tool_input.get("old_string") or ""


def read_file(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError, TypeError):
        return ""


def main():
    data = json.load(sys.stdin)
    event = data.get("hook_event_name") or "PreToolUse"
    tool_name = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not path or tool_name not in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return
    cwd = data.get("cwd") or os.getcwd()
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    on_disk = read_file(path)
    # After a Write the file on disk IS the new content, so there is no "old".
    new_text, old_text = edit_texts(tool_name, tool_input, on_disk if event == "PreToolUse" else "")
    hits = R.scan(cwd, path, new_text, old_text, file_text=on_disk)
    if not hits:
        return
    message = R.format_hits(hits, path)

    if event == "PostToolUse":
        print(json.dumps({"decision": "block", "reason": message + "\nRewrite these calls for the installed versions "
                          "(check node_modules / site-packages source if unsure)."}))
        return

    denies = [h for h in hits if h["severity"] == "deny"]
    if denies:
        decision = "ask"
    elif (os.environ.get("DOCS_PIN_WARN") or "").lower() == "allow":
        decision = "allow"
    else:
        return  # warn-only: reported to Claude by the PostToolUse pass
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                             "permissionDecisionReason": message}}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # never break the session
        sys.stderr.write("docs-pin: check_edit error: %s\n" % exc)
    sys.exit(0)
