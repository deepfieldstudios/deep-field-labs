#!/usr/bin/env python3
"""secret-shield PreToolUse hook (Write|Edit|MultiEdit|NotebookEdit|Bash|WebFetch|WebSearch|mcp__.*).

- Write/Edit: deny writing a literal secret into a file, unless the file is a
  .env* file (not a template like .env.example) or is gitignored.
- Bash: deny `git commit` / `git push` when the staged diff / outgoing commits add
  a secret; deny commands that carry a literal secret to the network (curl, ssh,
  ...) or into a non-ignored file or commit message.
- WebFetch / WebSearch / MCP tools: deny if any input string holds a secret.
Test-mode keys (low severity) are warned about, never denied.
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
import detectors  # noqa: E402
import register  # noqa: E402

NETWORK_RE = re.compile(r"(?<![\w./-])(curl|wget|http|https|xh|nc|ncat|netcat|telnet|ssh|scp|sftp|"
                        r"rsync|ftp|gh|aws|gcloud|az|mail|sendmail|openssl\s+s_client)(?![\w-])")
GIT_RE = re.compile(r"(?<![\w-])git((?:\s+(?:-C\s+\S+|-c\s+\S+|--[\w-]+(?:=\S+)?))*)\s+(commit|push)\b([^;&|]*)")
REDIRECT_RE = re.compile(r"(?:\d?>>?|&>>?|\btee\s+(?:-a\s+)?)\s*[\"']?([^\s\"';|&<>]+)")
MAX_DIFF = 5 * 1024 * 1024


def _git(args, cwd, timeout=10):
    try:
        r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout[:MAX_DIFF].decode("utf-8", "replace")


def added_by_file(diff):
    """{path: added text} from a unified diff (only '+' lines)."""
    out, cur = {}, None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip()
            cur = p[2:] if p.startswith("b/") else p
        elif line.startswith("+") and cur and cur != "/dev/null":
            out.setdefault(cur, []).append(line[1:])
    return {k: "\n".join(v) for k, v in out.items()}


def outgoing_diff(cwd, kind, tail):
    if kind == "commit":
        diff = _git(["diff", "--cached", "--no-color", "--no-ext-diff", "-U0"], cwd) or ""
        if re.search(r"(?:^|\s)(-[a-zA-Z]*a[a-zA-Z]*|--all)\b", tail):
            diff += _git(["diff", "--no-color", "--no-ext-diff", "-U0"], cwd) or ""
        return diff
    upstream = _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd)
    rng = ["%s..HEAD" % upstream.strip()] if upstream else ["HEAD", "--not", "--remotes"]
    return _git(["log", "-p", "--no-color", "--no-ext-diff", "-U0", "-n", "200"] + rng, cwd, timeout=15) or ""


def check_git(cmd, cwd):
    """Return a deny reason if a git commit/push would publish a secret."""
    for m in GIT_RE.finditer(cmd):
        opts, kind, tail = m.group(1), m.group(2), m.group(3)
        gcwd = cwd
        c = re.search(r"-C\s+(\S+)", opts or "")
        if c:
            gcwd = os.path.normpath(os.path.join(cwd, os.path.expanduser(c.group(1).strip("'\""))))
        hits = []
        for path, text in added_by_file(outgoing_diff(gcwd, kind, tail)).items():
            for f in common.blocking(common.active(detectors.find_secrets(text))):
                hits.append((path, f))
        if hits:
            register.record([f for _, f in hits], "git %s: %s" % (kind, ", ".join(sorted({p for p, _ in hits}))[:200]), "blocked")
            files = sorted({p for p, _ in hits})
            what = "the staged changes" if kind == "commit" else "the commits being pushed"
            fix = ("Unstage it (git restore --staged %s), move the value to an env var or a gitignored "
                   ".env file, then commit again." % " ".join(files[:3])) if kind == "commit" else (
                   "Rewrite the commit to remove it (e.g. git commit --amend or an interactive rebase), "
                   "move the value to an env var, and rotate the key: it is in local history.")
            return ("secret-shield blocked git %s: %s add %s in %s. %s %s"
                    % (kind, what, common.summary([f for _, f in hits]), ", ".join(files[:5]), fix,
                       common.ignore_hint([f for _, f in hits])))
    return None


def handle_bash(cmd, cwd):
    reason = check_git(cmd, cwd) if "git" in cmd else None
    if reason:
        return common.pre_deny(reason)
    findings = common.active(detectors.find_secrets(cmd))
    if not findings:
        return None
    stop = common.blocking(findings)
    net = NETWORK_RE.search(cmd)
    targets = REDIRECT_RE.findall(cmd)
    bad_targets = [t for t in targets if not t.startswith("/dev/") and not common.secret_file_allowed(t, cwd)]
    where = "Bash: " + detectors.redact(cmd)[:160]
    if stop and net:
        register.record(stop, where, "blocked")
        return common.pre_deny(
            "secret-shield blocked this command: it would send %s over the network via %s. Export it as an "
            "environment variable (%s) in your own shell and reference it as $NAME in the command, so the "
            "literal never appears in the transcript. %s"
            % (common.summary(stop), net.group(1), common.env_advice(stop), common.ignore_hint(stop)))
    if stop and GIT_RE.search(cmd):
        register.record(stop, where, "blocked")
        return common.pre_deny("secret-shield blocked this git command: it contains %s (e.g. in the commit "
                               "message), which would be published with the repo. %s"
                               % (common.summary(stop), common.ignore_hint(stop)))
    if stop and bad_targets:
        register.record(stop, where, "blocked")
        return common.pre_deny(
            "secret-shield blocked this command: it writes %s into %s, which is not a .env file and not "
            "gitignored. Write it to a gitignored .env file instead and read it as an env var (%s). %s"
            % (common.summary(stop), ", ".join(bad_targets[:3]), common.env_advice(stop), common.ignore_hint(stop)))
    if stop and targets:
        return None  # writing into .env / ignored files is the recommended pattern
    register.record(findings, where, "warned")
    return {"systemMessage": "secret-shield: this command contains %s as a literal. It is now in the "
                             "transcript; prefer an env var reference." % common.summary(findings)}


def handle_write(tool, ti, cwd):
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    payload = [ti.get("content"), ti.get("new_string"), ti.get("new_source"),
               [e.get("new_string") for e in ti.get("edits", []) if isinstance(e, dict)]]
    findings = common.active(detectors.scan_obj(payload))
    if not findings or common.secret_file_allowed(path, cwd):
        return None
    stop = common.blocking(findings)
    rel = os.path.relpath(os.path.join(cwd, path), cwd) if path else "a file"
    if not stop:
        register.record(findings, "%s %s" % (tool, rel), "warned")
        return {"systemMessage": "secret-shield: %s contains %s. Test keys are low risk, but an env var "
                                 "is still better." % (rel, common.summary(findings))}
    register.record(stop, "%s %s" % (tool, rel), "blocked")
    env = common.env_advice(stop)
    return common.pre_deny(
        "secret-shield blocked %s: it would write %s into %s as a literal. Read it from an environment "
        "variable instead (e.g. %s via os.environ / process.env), with the value kept in a gitignored "
        ".env file. %s" % (tool, common.summary(stop), rel, env, common.ignore_hint(stop)))


def handle_external(tool, ti):
    findings = common.active(detectors.scan_obj(ti))
    if not findings:
        return None
    stop = common.blocking(findings)
    target = ti.get("url") or ti.get("query") or tool
    where = "%s %s" % (tool, detectors.redact(str(target))[:160])
    if not stop:
        register.record(findings, where, "warned")
        return {"systemMessage": "secret-shield: %s input contains %s (test key)." % (tool, common.summary(findings))}
    register.record(stop, where, "blocked")
    return common.pre_deny(
        "secret-shield blocked %s: its input contains %s, which would leave this machine. Remove the "
        "credential from the request; if the service needs auth, configure it in the tool/server's own "
        "settings via an env var. %s" % (tool, common.summary(stop), common.ignore_hint(stop)))


def handle(data):
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()
    if tool == "Bash":
        return handle_bash(ti.get("command") or "", cwd)
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return handle_write(tool, ti, cwd)
    if tool in ("WebFetch", "WebSearch") or tool.startswith("mcp__"):
        return handle_external(tool, ti)
    return None


if __name__ == "__main__":
    common.run_safely(handle)
