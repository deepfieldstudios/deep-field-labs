#!/usr/bin/env python3
"""blast-radius PreToolUse hook (Bash, Write, Edit, MultiEdit, NotebookEdit).

low  (< low threshold)   -> permissionDecision "allow" (no prompt)
medium                   -> no decision (normal permission flow) + a short note
high (>= high threshold) -> permissionDecision "ask" with a plain-English preview,
                            after taking a recovery snapshot for local destructive commands
Every decision is appended to <state>/decisions.jsonl.
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import preview  # noqa: E402
import scoring  # noqa: E402
import snapshot  # noqa: E402


def _matches(patterns, text):
    for p in patterns or []:
        try:
            if re.search(p, text):
                return p
        except re.error:
            continue
    return None


def decide(data):
    """Return (output dict or None, log record)."""
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()
    cfg = config.load(cwd)
    low, high = cfg["thresholds"]["low"], cfg["thresholds"]["high"]

    if tool == "Bash":
        subject = ti.get("command", "") or ""
        rule = _matches(cfg.get("deny"), subject)
        if rule:
            return _out("deny", "blast-radius: denied by your config rule /%s/" % rule), \
                _rec(tool, subject, 100, "deny", "deny", ["config deny /%s/" % rule])
        rule = _matches(cfg.get("allow"), subject)
        if rule:
            return (_out("allow", "blast-radius: allowed by config rule /%s/" % rule)
                    if cfg.get("auto_allow", False) else None), \
                _rec(tool, subject, 0, "low", "allow", ["config allow /%s/" % rule])
        a = scoring.assess(subject, cwd)
        if _matches(cfg.get("ask"), subject):
            a.score = max(a.score, high)
            a.reasons.insert(0, "matches a config ask rule")
    elif tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        subject = ti.get("file_path") or ti.get("notebook_path") or ""
        a = scoring.assess_file_write(tool, subject, cwd)
        if a is None:
            return None, None  # inside the project: leave it to the normal flow, no log
    else:
        return None, None

    tier = "low" if a.score < low else "high" if a.score >= high else "medium"
    label = "blast-radius %d/100 (%s)" % (a.score, tier)
    snap = None
    if tier == "low":
        out = _out("allow", "%s: %s" % (label, "; ".join(a.reasons) or "read-only or easily undone"))\
            if cfg.get("auto_allow", False) else None
        decision = "allow" if out else "defer"
    elif tier == "medium":
        msg = "%s: %s" % (label, "; ".join(a.reasons) or "unrecognised command")
        out = {"systemMessage": msg} if cfg.get("show_medium", True) else None
        decision = "defer"
    else:
        lines = _explain(a, cfg)
        if cfg["snapshot"].get("enabled", True):
            snap = snapshot.take(a.facts, cwd, config.state_dir(cwd), subject,
                                 cfg["snapshot"].get("max_mb", 50),
                                 cfg.get("preview_max_files", 5000))
        reason = "%s. %s" % (label, ". ".join(lines) if lines else "high risk")
        if snap:
            if snap["type"] != "none":
                reason += ". Recovery point %s (%s) - see /blast-radius:restore" % (snap["id"], snap["note"])
            else:
                reason += ". %s" % (snap["note"] or "no snapshot")
        out = _out("ask", reason)
        decision = "ask"
        a.reasons = lines
    return out, _rec(tool, subject, a.score, tier, decision, a.reasons,
                     snap["id"] if snap and snap["type"] != "none" else None)


EXTRA = re.compile(r"production|remote host|root privileges|filesystem root|home directory|"
                   r"unset variable|system path|sensitive|branch|without a confirmation|"
                   r"piped|database host|rewriting")


def _cap(s):
    return s[:1].upper() + s[1:]


def _explain(a, cfg):
    """Preview sentences first, then the scoring reasons they do not already cover."""
    concrete = [f for f in a.facts if f.get("kind") not in ("prod", "deploy")]
    lines = preview.build(concrete, cfg.get("preview_max_files", 5000))
    if not lines:  # nothing to preview concretely: lead with the top scoring reasons
        lines = [_cap(r) for r in a.reasons[:2]]
    if any(f.get("kind") == "prod" for f in a.facts) and "production" not in " ".join(lines).lower():
        lines.append("Runs against production")
    have = " ".join(lines).lower()
    for r in a.reasons:
        if len(lines) >= 4:
            break
        key = EXTRA.search(r)
        if key and key.group(0) not in have:
            lines.append(_cap(r))
            have += " " + r.lower()
    return lines


def _out(decision, reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": decision,
                                   "permissionDecisionReason": reason}}


def _rec(tool, subject, score, tier, decision, reasons, snap=None):
    return {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), "tool": tool,
            "subject": subject[:500], "score": score, "tier": tier, "decision": decision,
            "reasons": reasons, "snapshot": snap}


def log(cwd, record):
    d = config.state_dir(cwd)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "decisions.jsonl"), "a") as fh:
        fh.write(json.dumps(record) + "\n")


def main():
    try:
        data = json.load(sys.stdin)
        out, record = decide(data)
        if record:
            try:
                log(data.get("cwd") or os.getcwd(), record)
            except OSError:
                pass
        if out:
            print(json.dumps(out))
    except Exception as e:  # never break the session
        print("blast-radius internal error: %s" % e, file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
