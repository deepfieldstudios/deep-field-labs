#!/usr/bin/env python3
"""UserPromptSubmit: when the prompt names a file that has notes not yet shown, inject them."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handoff_lib as hl  # noqa: E402

CAP = 800


def run(data):
    cwd = data.get("cwd") or os.getcwd()
    sid = data.get("session_id") or "unknown"
    prompt = data.get("prompt") or ""
    home = hl.home_dir(cwd)
    notes = hl.load_notes(home)
    if not notes or not prompt.strip():
        return None
    st = hl.load_state(home, sid)
    already = set(st.get("injected", []))
    hits = hl.mentioned_notes(prompt, notes, already)
    if not hits:
        return None
    hits.sort(key=lambda n: n.get("created", ""), reverse=True)
    text, ids = hl.render_block("Handoff notes for files you mentioned", hits, CAP)
    if not ids:
        return None
    st["injected"] = sorted(already | set(ids))
    hl.save_state(home, sid, st)
    return text


def main():
    try:
        text = run(hl.read_hook_input(sys.stdin))
        if text:
            hl.emit_context("UserPromptSubmit", text)
    except Exception as e:
        print("handoff prompt_submit: %s" % e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
