"""Shared helpers for codebase-onboarder: paths, atomic io, verification + gotcha stores."""
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone

KEEP_START = "<!-- onboarder:keep -->"
KEEP_END = "<!-- /onboarder:keep -->"


def home_dir(cwd=None):
    """State dir: $ONBOARDER_HOME or <cwd>/.claude/onboarder."""
    env = os.environ.get("ONBOARDER_HOME")
    if env:
        return env
    return os.path.join(cwd or os.getcwd(), ".claude", "onboarder")


def scan_path(home):
    return os.path.join(home, "scan.json")


def verified_path(home):
    return os.path.join(home, "verified.json")


def gotchas_path(home):
    return os.path.join(home, "gotchas.jsonl")


def state_path(home, session_id):
    sid = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")[:120]
    return os.path.join(home, "state", sid + ".json")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
            return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    atomic_write(path, json.dumps(obj, indent=1, ensure_ascii=False))


def norm_cmd(cmd):
    return " ".join((cmd or "").split())


# ---------- verification ----------

def load_verified(home):
    return load_json(verified_path(home), {})


def mark(home, cmd, status, note="", source="manual"):
    if status not in ("ok", "fail"):
        raise ValueError("status must be ok or fail")
    v = load_verified(home)
    v[norm_cmd(cmd)] = {"status": status, "note": note, "at": now_iso(), "source": source}
    save_json(verified_path(home), v)
    return v


def candidate_commands(home):
    scan = load_json(scan_path(home), {})
    return [c.get("cmd") for c in scan.get("commands", []) if c.get("cmd")]


def match_candidate(cmd, candidates):
    """The candidate a run command corresponds to: exact, or candidate followed by extra args."""
    c = norm_cmd(cmd)
    # strip leading env assignments and `cd x &&`
    c = re.sub(r"^(cd\s+\S+\s*&&\s*)", "", c)
    c = re.sub(r"^([A-Z_][A-Z0-9_]*=\S+\s+)+", "", c)
    for cand in sorted(candidates, key=len, reverse=True):
        n = norm_cmd(cand)
        if c == n or c.startswith(n + " "):
            return cand
    return None


# ---------- gotchas ----------

def load_gotchas(home):
    out = []
    try:
        with open(gotchas_path(home), encoding="utf-8") as f:
            for line in f:
                try:
                    g = json.loads(line)
                except ValueError:
                    continue
                if isinstance(g, dict) and g.get("text"):
                    out.append(g)
    except OSError:
        pass
    return out


def add_gotcha(home, key, kind, text, cmd="", session_id=""):
    """Append a gotcha candidate unless one with the same key exists. Returns True if added."""
    gs = load_gotchas(home)
    if any(g.get("key") == key for g in gs):
        return False
    gs.append({"id": hashlib.sha1(key.encode()).hexdigest()[:8], "key": key, "kind": kind,
               "text": text, "cmd": cmd, "created": now_iso(), "session_id": session_id,
               "status": "candidate"})
    atomic_write(gotchas_path(home), "".join(json.dumps(g, ensure_ascii=False) + "\n" for g in gs))
    return True


def read_hook_input(stdin):
    try:
        d = json.load(stdin)
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}
