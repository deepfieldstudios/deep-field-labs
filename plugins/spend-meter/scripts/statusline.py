#!/usr/bin/env python3
"""spend-meter status line.

Claude Code pipes JSON on stdin (transcript_path, session_id, model, cwd, ...) and shows
whatever this prints. Output, parts omitted when not applicable:

    $0.42 this session · 1.2M tok · budget 21% · ⚠ loop?

Fast on big transcripts because usage.update_session only reads lines appended since
the last call (byte offsets cached under SPEND_METER_HOME/sessions/).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usage  # noqa: E402

LOOP_WINDOW = 10


def render(event):
    transcript = event.get("transcript_path")
    session_id = event.get("session_id")
    cwd = event.get("cwd") or (event.get("workspace") or {}).get("current_dir")
    if not transcript or not os.path.exists(transcript):
        return "spend-meter: no transcript yet"
    session_id = session_id or os.path.basename(transcript).rsplit(".jsonl", 1)[0]
    s = usage.summarize(usage.update_session(transcript, session_id))
    cost, tokens = s["total"]["cost"], s["total"]["tokens"]
    parts = ["%s this session" % usage.fmt_usd(cost), "%s tok" % usage.fmt_tokens(tokens)]

    cfg = usage.load_config(cwd)
    pcts = []
    try:
        if cfg.get("session_budget_usd"):
            pcts.append(cost / float(cfg["session_budget_usd"]))
        if cfg.get("daily_budget_usd"):
            led = usage.update_ledger(session_id, s["per_date"], cwd)
            pcts.append(usage.daily_total(led) / float(cfg["daily_budget_usd"]))
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    if pcts:
        parts.append("budget %d%%" % round(100 * max(pcts)))

    hs = usage.read_json(os.path.join(usage.home_dir(), "sessions",
                                      usage.safe_id(session_id) + ".hook.json"), {}) or {}
    if hs.get("loop_alert") and hs.get("since_alert", LOOP_WINDOW) < LOOP_WINDOW:
        parts.append("⚠ loop?")
    return " · ".join(parts)


def main():
    try:
        raw = sys.stdin.read()
        event = json.loads(raw, strict=False) if raw.strip() else {}
        print(render(event if isinstance(event, dict) else {}))
    except Exception:
        print("spend-meter: unavailable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
