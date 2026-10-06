#!/usr/bin/env python3
"""secret-shield PostToolUse hook (Read|Bash): warn Claude when tool output held a
credential, and log the exposure in the rotation register."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
import detectors  # noqa: E402
import register  # noqa: E402


def handle(data):
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}
    findings = common.active(detectors.scan_obj(data.get("tool_response")))
    if not findings:
        return None
    if tool == "Read":
        where = "Read %s" % (ti.get("file_path") or "?")
    else:
        where = "%s: %s" % (tool, detectors.redact(str(ti.get("command") or ti))[:160])
    register.record(findings, where, "exposed-in-context")
    names = common.summary(findings)
    return {
        "systemMessage": "secret-shield: %s output contained %s. It is now in Claude's context; "
                         "consider rotating it (/secret-shield:register)." % (tool, names),
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                "secret-shield: the %s output above contains live credential(s): %s. Do not repeat, "
                "echo, quote, log, commit, or send these values anywhere, and do not paste them into "
                "code. Refer to them only by environment variable name (e.g. %s). If you need to "
                "mention one, use the masked form shown here." % (tool, names, common.env_advice(findings))),
        },
    }


if __name__ == "__main__":
    common.run_safely(handle)
