#!/usr/bin/env python3
"""PostToolUse / PostToolUseFailure on Bash: capture gotcha candidates, auto-verify commands.

- A failing command followed later in the session by a successful variant -> gotcha.
- Output matching a known signature (missing env var, port in use, runtime version,
  service not running) -> gotcha, deduped by key.
- A successful run of a candidate command from scan.json -> verified ok.
"""
import difflib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import onboarder_lib as ol  # noqa: E402

MAX_FAILURES = 10
SERVICE_PORTS = {"5432": "PostgreSQL", "3306": "MySQL", "6379": "Redis", "27017": "MongoDB",
                 "9200": "Elasticsearch", "5672": "RabbitMQ", "11211": "Memcached"}

SIGNATURES = [
    # (kind, regex, function(match, text) -> (detail, message))
    ("env_missing", re.compile(r"(?:environment variable|env var(?:iable)?)\s+[`'\"]?([A-Z][A-Z0-9_]{2,})[`'\"]?\s+"
                               r"(?:is\s+)?(?:not set|missing|required|undefined|not defined)", re.I),
     lambda m, t: (m.group(1), "Needs env var `%s` set." % m.group(1))),
    ("env_missing", re.compile(r"(?:missing|required)\s+(?:required\s+)?(?:environment variable|env var)s?:?\s+[`'\"]?([A-Z][A-Z0-9_]{2,})", re.I),
     lambda m, t: (m.group(1), "Needs env var `%s` set." % m.group(1))),
    ("env_missing", re.compile(r"KeyError: ['\"]([A-Z][A-Z0-9_]{2,})['\"]"),
     lambda m, t: (m.group(1), "Needs env var `%s` set (KeyError on lookup)." % m.group(1))),
    ("port_in_use", re.compile(r"EADDRINUSE[^\n]*?:(\d{2,5})|address already in use[^\n]*?:(\d{2,5})|"
                               r"port (\d{2,5}) is (?:already )?in use|address already in use", re.I),
     lambda m, t: (next((g for g in m.groups() if g), "?"),
                   "Port %s is often already in use; stop the old process or pick another port."
                   % next((g for g in m.groups() if g), "(unknown)"))),
    ("runtime_version", re.compile(r"(?:requires?|expected|unsupported engine|incompatible)[^\n]{0,40}?"
                                   r"\b(node|python|ruby|go|java|npm|pnpm)\b[^\n]{0,20}?(\d+(?:\.\d+)*)", re.I),
     lambda m, t: ("%s %s" % (m.group(1).lower(), m.group(2)),
                   "Needs a specific %s version (%s mentioned in error)." % (m.group(1).lower(), m.group(2)))),
    ("runtime_version", re.compile(r"The engine \"(\w+)\" is incompatible[^\n]*?Expected version \"([^\"]+)\"", re.I),
     lambda m, t: ("%s %s" % (m.group(1), m.group(2)), "Needs %s %s." % (m.group(1), m.group(2)))),
    ("service_down", re.compile(r"(?:ECONNREFUSED|Connection refused|could not connect)[\s\S]{0,120}?(?::|port )(\d{4,5})\b", re.I),
     lambda m, t: (SERVICE_PORTS.get(m.group(1), "port " + m.group(1)),
                   "Needs %s running locally (connection refused on :%s)." % (SERVICE_PORTS.get(m.group(1), "a service"), m.group(1)))),
    ("service_down", re.compile(r"(Is the server running|redis[^\n]{0,40}connection|postgres[^\n]{0,40}(?:refused|not running)|"
                                r"Cannot connect to the Docker daemon)", re.I),
     lambda m, t: (_service_name(m.group(0)), "Needs %s running before this works." % _service_name(m.group(0)))),
]


def _service_name(s):
    low = s.lower()
    for name in ("docker", "redis", "postgres", "mysql", "mongo"):
        if name in low:
            return {"docker": "the Docker daemon", "postgres": "PostgreSQL", "mongo": "MongoDB"}.get(name, name.capitalize())
    return "a database server"


def outcome(data):
    """(failed: bool, text: str) from either event's payload."""
    if data.get("hook_event_name") == "PostToolUseFailure":
        return True, str(data.get("error") or data.get("tool_response") or "")
    r = data.get("tool_response")
    if isinstance(r, str):
        return False, r
    if not isinstance(r, dict):
        return False, ""
    text = "\n".join(str(r.get(k) or "") for k in ("stdout", "stderr", "output", "error") if r.get(k))
    code = next((r[k] for k in ("exit_code", "exitCode", "returnCode", "return_code", "code") if k in r), None)
    failed = bool(r.get("is_error") or r.get("interrupted")) or (isinstance(code, int) and code != 0)
    return failed, text


def program(cmd):
    c = re.sub(r"^(cd\s+\S+\s*&&\s*)", "", ol.norm_cmd(cmd))
    c = re.sub(r"^([A-Z_][A-Z0-9_]*=\S+\s+)+", "", c)
    return c.split(" ", 1)[0] if c else ""


def is_variant(failed_cmd, ok_cmd):
    a, b = ol.norm_cmd(failed_cmd), ol.norm_cmd(ok_cmd)
    if a == b:
        return False
    if a in b:
        return True
    return program(a) == program(b) and difflib.SequenceMatcher(None, a, b).ratio() >= 0.5


def first_error_line(text):
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    for l in lines:
        if re.search(r"error|fail|not found|refused|denied|missing|cannot|unable", l, re.I):
            return l[:140]
    return (lines[-1][:140] if lines else "no output")


def run(data):
    if data.get("tool_name") != "Bash":
        return []
    cmd = ol.norm_cmd((data.get("tool_input") or {}).get("command"))
    if not cmd:
        return []
    cwd = data.get("cwd") or os.getcwd()
    sid = data.get("session_id") or "unknown"
    home = ol.home_dir(cwd)
    failed, text = outcome(data)
    added = []

    for kind, rx, fn in SIGNATURES:
        m = rx.search(text or "")
        if m:
            detail, msg = fn(m, text)
            if ol.add_gotcha(home, "%s:%s" % (kind, detail), kind, "%s Seen running `%s`." % (msg, cmd[:100]), cmd, sid):
                added.append(msg)
            break  # one signature per output is enough

    st_path = ol.state_path(home, sid)
    st = ol.load_json(st_path, {})
    fails = st.get("failures", [])
    if failed:
        fails = [f for f in fails if f["cmd"] != cmd] + [{"cmd": cmd, "err": first_error_line(text), "at": ol.now_iso()}]
        st["failures"] = fails[-MAX_FAILURES:]
        ol.save_json(st_path, st)
        return added

    # success: does it fix an earlier failure?
    remaining = []
    for f in fails:
        if f["cmd"] == cmd:
            continue  # plain retry worked; not a gotcha
        if is_variant(f["cmd"], cmd):
            msg = "`%s` failed (%s); `%s` worked." % (f["cmd"][:100], f["err"], cmd[:140])
            if ol.add_gotcha(home, "fix:%s->%s" % (f["cmd"], cmd), "fix", msg, cmd, sid):
                added.append(msg)
        else:
            remaining.append(f)
    if remaining != fails:
        st["failures"] = remaining
        ol.save_json(st_path, st)

    cand = ol.match_candidate(cmd, ol.candidate_commands(home))
    if cand and ol.load_verified(home).get(ol.norm_cmd(cand), {}).get("status") != "ok":
        ol.mark(home, cand, "ok", "succeeded in a session", source="hook")
    return added


def main():
    try:
        run(ol.read_hook_input(sys.stdin))
    except Exception as e:  # never break the session
        print("onboarder post_bash: %s" % e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
