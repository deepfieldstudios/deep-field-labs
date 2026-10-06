#!/usr/bin/env python3
"""PostToolUse hook (Edit|Write|MultiEdit): nudge Claude to run the prompt tests after a prompt change.

Triggers when the edited file
  1. is a prompt_file referenced by a suite in prompt-tests/*.json, or
  2. matches a configured glob (default: prompts/**, *.prompt.md, *system_prompt*), or
  3. had a Claude model id ("claude-...") added, removed or swapped in code.
Nudges at most once per file per session (state in .claude/prompt-regression/nudged.json).
"""
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pr_common as pc  # noqa: E402

MODEL_RE = re.compile(r"\bclaude-[a-z0-9.\-]*\d[a-z0-9.\-]*", re.I)
DOC_EXT = {".md", ".markdown", ".txt", ".rst", ".lock", ".log", ".csv"}
MAX_SESSIONS = 20


def model_ids(text):
    return {m.rstrip(".-").lower() for m in MODEL_RE.findall(text or "")}


def glob_match(rel, patterns):
    rel = rel.replace(os.sep, "/")
    name = rel.rsplit("/", 1)[-1]
    for pat in patterns:
        if pat.endswith("/**"):
            prefix = pat[:-3].strip("/")
            if rel == prefix or rel.startswith(prefix + "/") or f"/{prefix}/" in f"/{rel}":
                return pat
        elif "/" in pat:
            if fnmatch.fnmatch(rel, pat):
                return pat
        elif fnmatch.fnmatch(name, pat):
            return pat
    return None


def suites_using(path, cwd):
    """Suite names whose prompt_file is this file (read loosely; a broken suite shouldn't break the hook)."""
    hits = []
    for f in pc.suite_files(cwd):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pf = data.get("prompt_file") if isinstance(data, dict) else None
        if pf and (Path(cwd) / pf).resolve() == Path(path).resolve():
            hits.append(data.get("name") or f.stem)
    return hits


def model_change(tool, tool_input, tool_response, path):
    if Path(path).suffix.lower() in DOC_EXT:
        return None
    if tool == "Edit":
        before, after = model_ids(tool_input.get("old_string")), model_ids(tool_input.get("new_string"))
    elif tool == "MultiEdit":
        edits = tool_input.get("edits") or []
        before = set().union(*[model_ids(e.get("old_string")) for e in edits]) if edits else set()
        after = set().union(*[model_ids(e.get("new_string")) for e in edits]) if edits else set()
    elif tool == "Write":
        after = model_ids(tool_input.get("content"))
        patch = (tool_response or {}).get("structuredPatch") if isinstance(tool_response, dict) else None
        if patch:
            plus = "\n".join(l[1:] for h in patch for l in h.get("lines", []) if l.startswith("+"))
            minus = "\n".join(l[1:] for h in patch for l in h.get("lines", []) if l.startswith("-"))
            before, after = model_ids(minus), model_ids(plus)
        elif isinstance(tool_response, dict) and tool_response.get("originalFile") is not None:
            before = model_ids(tool_response.get("originalFile"))
        else:
            before = set()  # unknown history: treat ids in the new content as new
    else:
        return None
    if before == after:
        return None
    gone, new = sorted(before - after), sorted(after - before)
    if gone and new:
        return f"model changed {', '.join(gone)} → {', '.join(new)}"
    if new:
        return f"model id {', '.join(new)} added"
    return f"model id {', '.join(gone)} removed"


def load_state(cwd):
    try:
        data = json.loads((pc.state_dir(cwd) / "nudged.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(cwd, state):
    if len(state) > MAX_SESSIONS:  # keep the most recent sessions only
        for sid in sorted(state, key=lambda s: state[s].get("_at", 0))[:-MAX_SESSIONS]:
            del state[sid]
    d = pc.state_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "nudged.json.tmp"
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, d / "nudged.json")


def handle(data):
    import time
    tool = data.get("tool_name", "")
    if tool not in ("Edit", "Write", "MultiEdit"):
        return None
    ti = data.get("tool_input") or {}
    fp = ti.get("file_path")
    if not fp:
        return None
    cwd = data.get("cwd") or os.getcwd()
    path = Path(fp) if os.path.isabs(fp) else Path(cwd) / fp
    path = Path(os.path.normpath(str(path)))
    try:
        rel = os.path.relpath(path, cwd)
    except ValueError:
        rel = str(path)
    if rel.replace(os.sep, "/").startswith(pc.TESTS_DIR + "/"):
        return None  # editing the tests themselves

    reasons = []
    suites = suites_using(path, cwd)
    if suites:
        reasons.append(f"it is the prompt for suite(s) {', '.join(suites)}")
    pat = glob_match(rel, load_config_globs(cwd))
    if pat and not suites:
        reasons.append(f"it matches the prompt pattern '{pat}'")
    mc = model_change(tool, ti, data.get("tool_response"), path)
    if mc:
        reasons.append(mc)
    if not reasons:
        return None

    session = data.get("session_id") or "no-session"
    state = load_state(cwd)
    seen = state.setdefault(session, {"files": []})
    key = str(path)
    if key in seen["files"]:
        return None
    seen["files"].append(key)
    seen["_at"] = time.time()
    save_state(cwd, state)

    run = f'python3 "{os.path.join(HERE, "run.py")}" --project "{cwd}"'
    if pc.suite_files(cwd):
        ctx = (f"prompt-regression: you changed {rel} ({'; '.join(reasons)}). This can silently change how "
               f"the AI feature behaves. Before saying the work is done, run /prompt-regression:run "
               f"(or `{run}`), report the pass rate, and if anything regressed compare with the previous run "
               f"(`python3 \"{os.path.join(HERE, 'compare.py')}\" --latest --project \"{cwd}\"`) and explain it. "
               "If the change was intentional and outputs are now expected to differ, offer to re-record "
               "golden outputs with /prompt-regression:record rather than editing assertions to pass.")
    else:
        ctx = (f"prompt-regression: you changed {rel} ({'; '.join(reasons)}), which looks like part of an AI "
               "feature, but this project has no prompt tests yet (no prompt-tests/*.json). Mention this to the "
               "user and offer /prompt-regression:init to scaffold a suite from real examples.")
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": ctx}}


def load_config_globs(cwd):
    globs = pc.load_config(cwd).get("globs") or []
    return [g for g in globs if isinstance(g, str)]


def main():
    try:
        out = handle(json.load(sys.stdin))
        if out:
            print(json.dumps(out))
    except Exception as exc:  # never break the session
        print(f"prompt-regression hook error: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
