#!/usr/bin/env python3
"""Stop: collect handoff notes without any tool call.

First Stop of a session that touched files: block once, asking Claude to end its reply with a
fenced ```handoff block (or `skip`). Follow-up Stop (stop_hook_active true): parse that block
from the last assistant message, save the notes, never block again.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handoff_lib as hl  # noqa: E402
import transcript  # noqa: E402

MAX_FILES = 15
MAX_FAILURES = 3
MAX_NOTES = 5
FENCE_RE = re.compile(r"```[ \t]*handoff[ \t]*\n(.*?)(?:\n```|\Z)", re.S | re.I)
KIND_ALIASES = {"decision": "decision", "dead_end": "dead_end", "dead end": "dead_end",
                "dead-end": "dead_end", "deadend": "dead_end", "open": "open", "todo": "open",
                "gotcha": "gotcha"}


def build_reason(files, failures):
    shown = files[:MAX_FILES]
    more = " (+%d more)" % (len(files) - len(shown)) if len(files) > len(shown) else ""
    lines = ["Handoff: end your reply with a brief fenced block of 1-5 notes for the next session "
             "(only what is hard to rediscover). No tool calls.",
             "```handoff",
             "decision: <what and why> | files: a.py, b.py",
             "dead_end: <what failed and why>",
             "open: <what is unfinished>",
             "gotcha: <surprising trap>",
             "```",
             "Use only the kinds that apply. If nothing is worth keeping, the block is the single line: skip",
             "Files touched: " + ", ".join(shown) + more]
    if failures:
        lines.append("Failed attempts: " + "; ".join(f[:110] for f in failures[:MAX_FAILURES]))
    return "\n".join(lines)


def parse_block(text):
    """Return (notes, skipped). notes = [(kind, text, files)] from the LAST ```handoff fence."""
    blocks = FENCE_RE.findall(text or "")
    if not blocks:
        return [], False
    body = blocks[-1]
    lines = [l.strip().lstrip("-*").strip() for l in body.splitlines() if l.strip()]
    if len(lines) == 1 and lines[0].lower().rstrip(".") == "skip":
        return [], True
    notes = []
    for line in lines:
        m = re.match(r"^([A-Za-z _-]+?)\s*:\s*(.+)$", line)
        if not m:
            continue
        kind = KIND_ALIASES.get(m.group(1).strip().lower())
        if not kind:
            continue
        rest, files = m.group(2), []
        fm = re.search(r"\|\s*files?\s*:\s*(.*)$", rest, re.I)
        if fm:
            files = [f.strip().strip("`") for f in re.split(r"[,\s]+", fm.group(1)) if f.strip().strip("`")]
            rest = rest[:fm.start()]
        rest = rest.strip()
        if rest and not rest.startswith("<"):  # ignore an echoed template line
            notes.append((kind, rest, files))
    return notes[:MAX_NOTES], False


def collect(data, home, sid, st, cwd):
    """Follow-up stop: save notes from the reply. Returns a systemMessage or None."""
    text = data.get("last_assistant_message")
    if not isinstance(text, str) or "```" not in text:
        text = transcript.last_assistant_text(data.get("transcript_path"))
    notes, skipped = parse_block(text)
    st["collected"] = True
    if skipped:
        st["skipped"] = True
        hl.save_state(home, sid, st)
        return None
    if not notes:
        hl.save_state(home, sid, st)
        return None
    store = hl.load_notes(home)
    for kind, body, files in notes:
        store.append(hl.make_note(kind, body, files, session_id=sid, cwd=cwd))
    hl.save_notes(home, store)
    st["noted"] = True
    hl.save_state(home, sid, st)
    return "handoff: saved %d note(s)" % len(notes)


def run(data):
    cwd = data.get("cwd") or os.getcwd()
    sid = data.get("session_id") or "unknown"
    home = hl.home_dir(cwd)
    st = hl.load_state(home, sid)
    if st.get("stop_blocked") and not st.get("collected"):
        msg = collect(data, home, sid, st, cwd)
        return {"systemMessage": msg} if msg else None
    if data.get("stop_hook_active"):
        return None
    if st.get("stop_blocked") or st.get("noted") or st.get("skipped"):
        return None
    if any(n.get("session_id") == sid for n in hl.load_notes(home)):
        return None
    parsed = transcript.parse(data.get("transcript_path"), cwd)
    if parsed["wrote_note"] or not parsed["files"]:
        return None
    st["stop_blocked"] = True
    hl.save_state(home, sid, st)
    return {"decision": "block", "reason": build_reason(parsed["files"], parsed["failures"])}


def main():
    try:
        out = run(hl.read_hook_input(sys.stdin))
        if out:
            print(json.dumps(out))
    except Exception as e:
        print("handoff stop: %s" % e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
