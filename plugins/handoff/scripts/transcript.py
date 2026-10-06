"""Parse a Claude Code transcript (JSONL) for touched files and failed attempts."""
import json
import os
import re
import shlex

FILE_TOOLS = {"Edit", "Write", "Read", "MultiEdit", "NotebookEdit"}
# Output that looks like a failure even when the tool did not flag is_error.
ERROR_RE = re.compile(
    r"(Traceback \(most recent call last\)|\berror\b[:\[]|\bERR!|command not found|"
    r"No such file or directory|Permission denied|exit code [1-9]|FAILED|fatal:)",
    re.IGNORECASE)
# A Bash token that plausibly names a file.
PATHISH_RE = re.compile(r"^[\w@~./+-]*[\w-]\.[A-Za-z0-9]{1,10}$|^[\w@~.+-]*/[\w@./+-]+$")
NOTE_CMD_RE = re.compile(r"note\.py[\"']?\s+(add|skip)\b")


def _iter_lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except (OSError, TypeError):
        return


def _blocks(obj):
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _result_text(block):
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


def paths_in_command(cmd):
    """Path-like tokens from a shell command (names only; no existence check)."""
    try:
        tokens = shlex.split(cmd, comments=False, posix=True)
    except ValueError:
        tokens = cmd.split()
    out = []
    for t in tokens:
        for part in re.split(r"[;|&<>()=,]+", t):
            part = part.strip()
            if not part or part.startswith("-") or "://" in part or "$" in part or "*" in part:
                continue
            if part in (".", "..", "/", "~") or part.startswith("/dev/"):
                continue
            if PATHISH_RE.match(part):
                out.append(part)
    return out


def _short(s, n=140):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 3] + "..."


def parse(transcript_path, cwd=None):
    """Return {"files": [...], "failures": [...], "wrote_note": bool}."""
    from handoff_lib import norm_path  # local import keeps this module standalone-ish

    tool_uses = {}       # id -> (name, input)
    order = []           # tool_use ids in order
    results = {}         # id -> (is_error, text)
    files = []
    wrote_note = False

    for obj in _iter_lines(transcript_path):
        for b in _blocks(obj):
            if b.get("type") == "tool_use":
                tid = b.get("id") or str(len(order))
                name = b.get("name") or ""
                inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                tool_uses[tid] = (name, inp)
                order.append(tid)
                if name in FILE_TOOLS:
                    p = inp.get("file_path") or inp.get("notebook_path")
                    if p:
                        files.append(p)
                elif name == "Bash":
                    cmd = inp.get("command") or ""
                    if NOTE_CMD_RE.search(cmd):
                        wrote_note = True
                        continue  # the handoff command itself is not "work"
                    files.extend(paths_in_command(cmd))
            elif b.get("type") == "tool_result":
                results[b.get("tool_use_id")] = (bool(b.get("is_error")), _result_text(b))

    # Failed attempts: flagged errors, or Bash error output followed by a different command.
    failures = []
    bash_ids = [t for t in order if tool_uses[t][0] == "Bash"]
    for tid in order:
        name, inp = tool_uses[tid]
        is_err, text = results.get(tid, (False, ""))
        what = inp.get("command") if name == "Bash" else inp.get("file_path") or ""
        if name == "Bash" and NOTE_CMD_RE.search(what or ""):
            continue
        if not is_err and name == "Bash" and ERROR_RE.search(text or ""):
            later = bash_ids[bash_ids.index(tid) + 1:]
            is_err = any((tool_uses[x][1].get("command") or "") != what for x in later)
        if is_err:
            first = next((l for l in (text or "").splitlines() if l.strip()), "")
            failures.append("%s `%s` -> %s" % (name, _short(what, 90), _short(first, 120)))

    seen, uniq = set(), []
    for p in files:
        np_ = norm_path(p, cwd)
        if np_ and np_ not in seen:
            seen.add(np_)
            uniq.append(np_)
    return {"files": uniq, "failures": failures, "wrote_note": wrote_note}


def last_assistant_text(transcript_path):
    """Text of the final assistant reply: assistant text blocks after the last real user prompt.

    A reply can span several assistant lines (one per content block), so join them.
    Lines holding only tool_result blocks do not count as a user prompt.
    """
    chunks = []
    for obj in _iter_lines(transcript_path):
        kind = obj.get("type")
        if kind == "user":
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            content = msg.get("content")
            only_results = isinstance(content, list) and content and all(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
            if not only_results:
                chunks = []
        elif kind == "assistant":
            for b in _blocks(obj):
                if b.get("type") == "text" and b.get("text"):
                    chunks.append(b["text"])
    return "\n".join(chunks)
