"""Shared helpers for the secret-shield hooks."""
import json
import os
import re
import subprocess
import sys

import detectors
import register


def read_input():
    return json.load(sys.stdin)


def emit(obj):
    if obj:
        print(json.dumps(obj))


def run_safely(handler):
    """Run a hook handler; any internal error exits 0 so the session never breaks."""
    try:
        emit(handler(read_input()))
    except Exception as e:
        print("secret-shield internal error: %s" % e, file=sys.stderr)
    sys.exit(0)


def active(findings):
    """Drop findings the user marked as false positives."""
    if not findings:
        return []
    try:
        ignored = register.ignored_fingerprints()
    except Exception:
        ignored = set()
    return [f for f in findings if f.fingerprint not in ignored]


def blocking(findings):
    return [f for f in findings if f.severity != "low"]


def summary(findings, limit=4):
    parts = [f.describe() for f in findings[:limit]]
    if len(findings) > limit:
        parts.append("%d more" % (len(findings) - limit))
    return ", ".join(parts)


def env_advice(findings):
    names = []
    for f in findings:
        n = detectors.env_hint(f.kind)
        if n not in names:
            names.append(n)
    return " / ".join(names[:3])


def ignore_hint(findings):
    return "If this is a false positive: /secret-shield:register --ignore %s" % (
        " ".join(f.fingerprint[:12] for f in findings[:3]))


ENV_FILE_RE = re.compile(r"^\.env(\..+)?$|^\.envrc$|\.env$")
TEMPLATE_RE = re.compile(r"(?i)\.(example|sample|template|dist|defaults?)$")


def git_ignored(path):
    """True if git says `path` is ignored (works for files that do not exist yet)."""
    d = os.path.dirname(path) or "."
    while d and not os.path.isdir(d):
        d = os.path.dirname(d)
    try:
        r = subprocess.run(["git", "check-ignore", "-q", "--", path], cwd=d or "/",
                           capture_output=True, timeout=3)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def secret_file_allowed(path, cwd):
    """Literal secrets may go into .env* files (not templates) or gitignored files."""
    if not path:
        return False
    full = os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))
    name = os.path.basename(full)
    if ENV_FILE_RE.search(name) and not TEMPLATE_RE.search(name):
        return True
    return git_ignored(full)


def pre_deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}
