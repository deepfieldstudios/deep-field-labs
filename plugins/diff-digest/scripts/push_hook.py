#!/usr/bin/env python3
"""diff-digest PostToolUse hook (matcher Bash).

When a `git push` or `gh pr create` command succeeds, measure the branch with
`git diff --shortstat` against its base and, if more than THRESHOLD lines changed,
add context suggesting /diff-digest:review. Base = merge-base with the default branch
(origin/HEAD, origin/main, origin/master, main, master), falling back to @{upstream}.
Every failure is silent: no output, exit 0.
"""
import json
import os
import re
import subprocess
import sys

THRESHOLD = int(os.environ.get("DIFF_DIGEST_HOOK_LINES", "300"))
TRIGGER_RE = re.compile(r"(^|[;&|(]\s*|\s)(git\s+(-C\s+\S+\s+)?push|gh\s+pr\s+create)(\s|$)")


def git(cwd, *args):
    p = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, timeout=3)
    if p.returncode != 0:
        raise RuntimeError(p.stderr)
    return p.stdout.strip()


def failed(event):
    if event.get("hook_event_name") == "PostToolUseFailure" or event.get("error"):
        return True
    r = event.get("tool_response")
    if isinstance(r, str):
        return r.startswith(("Error", "Exit code"))
    if isinstance(r, dict):
        if r.get("is_error") or r.get("interrupted"):
            return True
        for k in ("exit_code", "exitCode", "returncode", "code"):
            if isinstance(r.get(k), int) and r[k] != 0:
                return True
        out = r.get("stdout") if isinstance(r.get("stdout"), str) else ""
        return out.startswith(("Exit code ", "Error:"))
    return False


def changed_lines(cwd):
    """(lines changed, base label) for HEAD against its base, or None."""
    for ref in ("origin/HEAD", "origin/main", "origin/master", "main", "master"):
        try:
            base = git(cwd, "merge-base", "HEAD", ref)
            if base == git(cwd, "rev-parse", "HEAD"):
                continue                       # on the default branch itself: try the next base
            stat = git(cwd, "diff", "--shortstat", base, "HEAD")
            return count(stat), ref
        except (RuntimeError, subprocess.TimeoutExpired, OSError):
            continue
    try:
        return count(git(cwd, "diff", "--shortstat", "@{upstream}", "HEAD")), "@{upstream}"
    except (RuntimeError, subprocess.TimeoutExpired, OSError):
        return None


def count(shortstat):
    nums = re.findall(r"(\d+) (insertion|deletion)", shortstat)
    return sum(int(n) for n, _ in nums)


def run(event):
    if event.get("tool_name") != "Bash":
        return {}
    cmd = str((event.get("tool_input") or {}).get("command") or "")
    if not TRIGGER_RE.search(cmd) or failed(event):
        return {}
    cwd = event.get("cwd") or os.getcwd()
    res = changed_lines(cwd)
    if not res or res[0] <= THRESHOLD:
        return {}
    n, ref = res
    return {"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": ("diff-digest: this branch changes %d lines against %s. Suggest the user "
                              "runs /diff-digest:review before asking anyone to approve it, so review starts "
                              "with the riskiest hunks instead of a skim." % (n, ref))}}


def main():
    try:
        raw = sys.stdin.read()
        event = json.loads(raw, strict=False) if raw.strip() else {}
        out = run(event if isinstance(event, dict) else {})
        if out:
            sys.stdout.write(json.dumps(out))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
