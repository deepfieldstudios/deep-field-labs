#!/usr/bin/env python3
"""Stop hook: check 'done' claims in the final reply against the run ledger.

RECEIPTS_MODE=block (default): unbacked claims block the stop, telling Claude
  which claims lack evidence. Never blocks twice in a row (stop_hook_active).
RECEIPTS_MODE=warn: show a systemMessage to the user instead.
RECEIPTS_MODE=off: only write the receipt file.
Always writes <state>/receipt-<session>.md.
"""
import json
import os
import sys

import receipts_lib as lib


def build_message(problems):
    lines = ["receipts: your final reply makes claims without passing evidence:"]
    for p in problems:
        lines.append('- "%s" (%s): %s - %s.' % (p["phrase"], p["kind"], p["verdict"], p["detail"]))
    lines.append("For each one, either run the command now and report its real result, "
                 "or correct the claim in your reply (say it was not run, or that it failed).")
    return "\n".join(lines)


def main():
    data = json.load(sys.stdin)
    mode = (os.environ.get("RECEIPTS_MODE") or "block").strip().lower()
    if mode not in ("block", "warn", "off"):
        mode = "block"
    session = data.get("session_id") or "unknown"
    cwd = data.get("cwd") or os.getcwd()

    lines = lib.load_transcript(data.get("transcript_path"))
    t_runs, edits, final_text = lib.build_timeline(lines)
    if not final_text:
        # Some Claude Code versions pass the reply directly.
        final_text = data.get("last_assistant_message") or ""
    runs = lib.merge_runs(t_runs, lib.read_ledger(cwd, session), lines)
    results = lib.evaluate(lib.extract_claims(final_text), runs, edits)

    path = lib.receipt_path(cwd, session)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(lib.render_receipt(session, runs, results, mode))

    problems = [r for r in results if r["verdict"] != "VERIFIED"]
    if not problems or mode == "off":
        return
    message = build_message(problems)
    if mode == "block" and not data.get("stop_hook_active"):
        print(json.dumps({"decision": "block", "reason": message}))
    else:
        # warn mode, or we already blocked once this stop: tell the user, don't loop.
        print(json.dumps({"systemMessage": message}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # never break the session
        sys.stderr.write("receipts: check_claims error: %s\n" % exc)
    sys.exit(0)
