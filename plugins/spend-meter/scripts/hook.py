#!/usr/bin/env python3
"""spend-meter PostToolUse / PostToolUseFailure hook.

Every tool call:
1. Incrementally re-prices the session transcript (cheap: only new lines are read).
2. Budget check against session_budget_usd / daily_budget_usd:
   - crossing warn_at  -> one systemMessage per threshold
   - exceeding budget  -> one {"decision":"block"} per threshold, asking Claude to pause
3. Loop detection over recent tool results:
   - the same normalised Bash command failing 3 times, or
   - the same error signature 3 times in the last 10 tool results
   -> one additionalContext nudge per signature.

Never crashes the session: any internal error exits 0 with no output.
"""
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usage  # noqa: E402

WINDOW = 10        # tool results kept for signature matching
REPEAT = 3         # repeats that count as a loop
LOOP_MSG = ("You've hit the same error {n} times; stop and reconsider approach or ask the user. "
            "(spend-meter loop detector: {what})")


def hook_state_path(session_id):
    return os.path.join(usage.home_dir(), "sessions", usage.safe_id(session_id) + ".hook.json")


# ---------------------------------------------------------------- tool result analysis

def _text_of(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "\n".join(_text_of(x.get("text") if isinstance(x, dict) else x) for x in v)
    if isinstance(v, dict):
        return "\n".join(_text_of(v.get(k)) for k in ("stderr", "stdout", "error", "content",
                                                      "output", "message") if v.get(k))
    return str(v)


def is_failure(event):
    """Did this tool call fail? Handles PostToolUseFailure and the shapes PostToolUse can carry."""
    if event.get("hook_event_name") == "PostToolUseFailure" or event.get("error"):
        return True
    r = event.get("tool_response")
    if isinstance(r, str):
        return r.startswith("Error") or r.startswith("Exit code")
    if isinstance(r, dict):
        if r.get("is_error") or r.get("isError") or r.get("error"):
            return True
        for k in ("exit_code", "exitCode", "returncode", "return_code", "code"):
            if isinstance(r.get(k), int) and r.get(k) != 0:
                return True
        if r.get("interrupted"):
            return False
        out = (r.get("stdout") or "") if isinstance(r.get("stdout"), str) else ""
        if out.startswith("Exit code ") or out.startswith("Error:"):
            return True
    return False


def error_text(event):
    parts = []
    if event.get("error"):
        parts.append(_text_of(event["error"]))
    r = event.get("tool_response")
    if isinstance(r, dict) and r.get("stderr"):
        parts.append(_text_of(r["stderr"]))   # stderr wins: it is where the error lives
    else:
        parts.append(_text_of(r))
    return "\n".join(p for p in parts if p)


def signature(text):
    """Last non-empty line, digits stripped, whitespace collapsed."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return ""
    sig = re.sub(r"\d+", "", lines[-1])
    return re.sub(r"\s+", " ", sig).strip()[:200]


def normalise_command(cmd):
    cmd = re.sub(r"\s+", " ", str(cmd or "")).strip()
    cmd = re.sub(r"\s*2>&1\s*$", "", cmd)
    return cmd[:500]


def loop_check(event, st):
    """Update loop state; return an additionalContext string or None."""
    failed = is_failure(event)
    tool = event.get("tool_name") or "?"
    sig = signature(error_text(event)) if failed else ""
    recent = st.setdefault("recent", [])
    recent.append({"tool": tool, "sig": sig})
    del recent[:-WINDOW]
    notified = st.setdefault("notified", [])
    alerts = []

    if tool == "Bash":
        cmd = normalise_command((event.get("tool_input") or {}).get("command"))
        fails = st.setdefault("bash_fails", {})
        if failed and cmd:
            fails[cmd] = fails.get(cmd, 0) + 1
            if fails[cmd] >= REPEAT and ("cmd:" + cmd) not in notified:
                notified.append("cmd:" + cmd)
                alerts.append((fails[cmd], "command `%s` failed %d times" % (cmd[:120], fails[cmd])))
        elif cmd:
            fails.pop(cmd, None)            # success resets that command's streak
        if len(fails) > 50:                 # bound state size
            for k in list(fails)[:-50]:
                fails.pop(k)

    if sig:
        n = sum(1 for r in recent if r["sig"] == sig)
        if n >= REPEAT and ("sig:" + sig) not in notified:
            notified.append("sig:" + sig)
            alerts.append((n, "error \"%s\" seen %d times in the last %d tool results"
                           % (sig[:120], n, len(recent))))

    del notified[:-200]
    if alerts:
        # The statusline shows "loop?" until WINDOW further tool calls pass without a new alert.
        st["loop_alert"] = alerts[0][1]
        st["since_alert"] = 0
        n = max(a[0] for a in alerts)
        return LOOP_MSG.format(n=n, what="; ".join(a[1] for a in alerts))
    if "since_alert" in st:
        st["since_alert"] += 1
    return None


# ---------------------------------------------------------------- budgets

def budget_check(cost, today_cost, cfg, st):
    """Return (systemMessage or None, block reason or None). Each fires once per threshold."""
    warn_at, today = cfg.get("warn_at", 0.8), datetime.now().strftime("%Y-%m-%d")
    fired = st.setdefault("fired", {})
    msgs, block = [], None
    checks = [("session", cfg.get("session_budget_usd"), cost, "session"),
              ("daily", cfg.get("daily_budget_usd"), today_cost, "daily:" + today)]
    for label, budget, spent, scope in checks:
        try:
            budget = float(budget) if budget is not None else None
        except (TypeError, ValueError):
            budget = None
        if not budget or budget <= 0:
            continue
        if spent >= budget and not fired.get(scope + ":block"):
            fired[scope + ":block"] = True
            fired[scope + ":warn"] = True
            block = ("spend-meter: the %s budget of %s is exceeded (%s spent). Pause now: "
                     "summarise the progress so far, what is left to do, and ask the user whether "
                     "to continue before making further tool calls."
                     % (label, usage.fmt_usd(budget), usage.fmt_usd(spent)))
        elif spent >= warn_at * budget and not fired.get(scope + ":warn"):
            fired[scope + ":warn"] = True
            msgs.append("spend-meter: %s spend %s is %d%% of the %s budget."
                        % (label, usage.fmt_usd(spent), round(100 * spent / budget),
                           usage.fmt_usd(budget)))
    # Forget daily flags from earlier days.
    for k in [k for k in fired if k.startswith("daily:") and not k.startswith("daily:" + today)]:
        fired.pop(k)
    return (" ".join(msgs) or None), block


# ---------------------------------------------------------------- main

def run(event):
    session_id = event.get("session_id") or "unknown"
    transcript = event.get("transcript_path")
    cwd = event.get("cwd")
    st_path = hook_state_path(session_id)
    st = usage.read_json(st_path, {}) or {}

    cost = today_cost = 0.0
    if transcript and os.path.exists(transcript):
        s = usage.summarize(usage.update_session(transcript, session_id))
        cost = s["total"]["cost"]
        led = usage.update_ledger(session_id, s["per_date"], cwd)
        today_cost = usage.daily_total(led)
    st["last_cost"] = cost

    cfg = usage.load_config(cwd)
    sysmsg, block = budget_check(cost, today_cost, cfg, st)
    loop_ctx = loop_check(event, st)
    usage.write_json(st_path, st)

    out = {}
    if sysmsg:
        out["systemMessage"] = sysmsg
    if block:
        out["decision"] = "block"
        out["reason"] = block
    if loop_ctx:
        out["hookSpecificOutput"] = {
            "hookEventName": event.get("hook_event_name") or "PostToolUse",
            "additionalContext": loop_ctx}
    return out


def main():
    try:
        raw = sys.stdin.read()
        event = json.loads(raw, strict=False) if raw.strip() else {}
        out = run(event if isinstance(event, dict) else {})
        if out:
            sys.stdout.write(json.dumps(out))
    except Exception as e:  # never break the session
        if os.environ.get("SPEND_METER_DEBUG"):
            sys.stderr.write("spend-meter hook error: %r\n" % (e,))
    return 0


if __name__ == "__main__":
    sys.exit(main())
