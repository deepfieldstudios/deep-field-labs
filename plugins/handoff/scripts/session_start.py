#!/usr/bin/env python3
"""SessionStart: inject the relevant, non-expired handoff notes (capped)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handoff_lib as hl  # noqa: E402


def run(data):
    cwd = data.get("cwd") or os.getcwd()
    sid = data.get("session_id") or "unknown"
    home = hl.home_dir(cwd)
    notes = hl.load_notes(home)
    if not notes:
        return None
    text, ids = hl.select_for_session(notes, hl.recent_repo_files(cwd))
    if not ids:
        return None
    st = hl.load_state(home, sid)
    st["injected"] = sorted(set(st.get("injected", [])) | set(ids))
    hl.save_state(home, sid, st)
    return text


def main():
    try:
        text = run(hl.read_hook_input(sys.stdin))
        if text:
            hl.emit_context("SessionStart", text)
    except Exception as e:  # never break the session
        print("handoff session_start: %s" % e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
