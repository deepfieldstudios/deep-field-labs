"""Shared helpers for the handoff plugin: note store, per-session state, ranking.

Standard library only. Every function tolerates missing/corrupt files.
"""
import json
import os
import re
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

KINDS = ("decision", "dead_end", "open", "gotcha")
DEFAULT_TTL_DAYS = {"decision": 90, "dead_end": 60, "gotcha": 180, "open": 21}
KIND_LABEL = {"decision": "DECISION", "dead_end": "DEAD END", "open": "OPEN", "gotcha": "GOTCHA"}
SESSION_CAP_CHARS = 1500
RECENT_DECISIONS = 5


# ---------- paths ----------

def home_dir(cwd=None):
    """State dir: $HANDOFF_HOME or <cwd>/.claude/handoff."""
    env = os.environ.get("HANDOFF_HOME")
    if env:
        return env
    return os.path.join(cwd or os.getcwd(), ".claude", "handoff")


def notes_path(home):
    return os.path.join(home, "notes.jsonl")


def _safe_id(session_id):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")[:120]


def state_path(home, session_id):
    return os.path.join(home, "state", _safe_id(session_id) + ".json")


# ---------- time ----------

def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# ---------- atomic io ----------

def atomic_write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".tmp-")
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


def load_notes(home):
    out = []
    try:
        with open(notes_path(home), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    n = json.loads(line)
                except ValueError:
                    continue
                if isinstance(n, dict) and n.get("id"):
                    out.append(n)
    except OSError:
        pass
    return out


def save_notes(home, notes):
    atomic_write(notes_path(home), "".join(json.dumps(n, ensure_ascii=False) + "\n" for n in notes))


def load_state(home, session_id):
    try:
        with open(state_path(home, session_id), encoding="utf-8") as f:
            s = json.load(f)
            if isinstance(s, dict):
                return s
    except (OSError, ValueError):
        pass
    return {}


def save_state(home, session_id, state):
    atomic_write(state_path(home, session_id), json.dumps(state, indent=1))


# ---------- notes ----------

def norm_path(p, cwd):
    """Store paths relative to the project root when they live inside it."""
    p = (p or "").strip().strip("'\"")
    if not p:
        return ""
    if p.startswith("~"):
        p = os.path.expanduser(p)
    if os.path.isabs(p) and cwd:
        try:
            rel = os.path.relpath(p, cwd)
            if not rel.startswith(".."):
                p = rel
        except ValueError:
            pass
    if p.startswith("./"):
        p = p[2:]
    return p


def make_note(kind, text, files=(), session_id=None, ttl_days=None, cwd=None, created=None):
    if kind not in KINDS:
        raise ValueError("kind must be one of: " + ", ".join(KINDS))
    text = " ".join((text or "").split())
    if not text:
        raise ValueError("text is required")
    created = created or now()
    days = ttl_days if ttl_days is not None else DEFAULT_TTL_DAYS[kind]
    return {
        "id": uuid.uuid4().hex[:8],
        "created": iso(created),
        "session_id": session_id or "",
        "kind": kind,
        "text": text,
        "files": sorted({norm_path(f, cwd) for f in files if norm_path(f, cwd)}),
        "expires": iso(created + timedelta(days=days)),
        "status": "open",
    }


def is_expired(note, at=None):
    exp = parse_iso(note.get("expires"))
    return exp is not None and exp <= (at or now())


def is_live(note, at=None):
    return not is_expired(note, at) and note.get("status") != "closed"


def format_note(n):
    files = n.get("files") or []
    f = " [" + ", ".join(files[:4]) + (" ..." if len(files) > 4 else "") + "]" if files else ""
    return "- {d} {k} ({i}): {t}{f}".format(
        d=(n.get("created") or "")[:10], k=KIND_LABEL.get(n.get("kind"), "NOTE"),
        i=n.get("id"), t=n.get("text", ""), f=f)


# ---------- relevance ----------

def recent_repo_files(cwd, limit=60):
    """Files changed recently: git status + last 20 commits. Falls back to mtimes when not git."""
    files = []

    def git(*args):
        try:
            r = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True,
                               text=True, timeout=4)
            return r.stdout if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    status = git("status", "--porcelain")
    if status is not None:
        for line in status.splitlines():
            p = line[3:].strip()
            if " -> " in p:
                p = p.split(" -> ", 1)[1]
            if p:
                files.append(p.strip('"'))
        log = git("log", "-n", "20", "--name-only", "--pretty=format:") or ""
        files += [l.strip() for l in log.splitlines() if l.strip()]
    else:
        files = _recent_by_mtime(cwd, limit)
    seen, out = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out[:limit]


SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".claude",
             "target", ".next", ".cache"}


def _recent_by_mtime(cwd, limit, max_files=3000):
    found = []
    count = 0
    for root, dirs, fnames in os.walk(cwd):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in fnames:
            count += 1
            if count > max_files:
                break
            p = os.path.join(root, fn)
            try:
                found.append((os.path.getmtime(p), os.path.relpath(p, cwd)))
            except OSError:
                pass
        if count > max_files:
            break
    found.sort(reverse=True)
    return [p for _, p in found[:limit]]


def files_overlap(note_files, other_files):
    """True if any note file matches another path exactly, by suffix, or by basename."""
    others = set(other_files)
    other_base = {os.path.basename(o) for o in others}
    for f in note_files:
        if f in others or os.path.basename(f) in other_base:
            return True
        if any(o.endswith("/" + f) or f.endswith("/" + o) for o in others):
            return True
    return False


def select_for_session(notes, recent_files, cap=SESSION_CAP_CHARS, at=None):
    """Pick notes to inject at session start. Returns (text, injected_ids)."""
    live = [n for n in notes if is_live(n, at)]
    pos = {n["id"]: i for i, n in enumerate(notes)}  # file order breaks same-second ties
    chosen = {}
    for n in live:
        if n.get("kind") == "open":
            chosen[n["id"]] = n
    decisions = sorted((n for n in live if n.get("kind") == "decision"),
                       key=lambda n: n.get("created", ""), reverse=True)
    for n in decisions[:RECENT_DECISIONS]:
        chosen[n["id"]] = n
    for n in live:
        if n.get("files") and files_overlap(n["files"], recent_files):
            chosen[n["id"]] = n
    ordered = sorted(chosen.values(), key=lambda n: (n.get("created", ""), pos.get(n["id"], 0)), reverse=True)
    return render_block("Handoff notes from earlier sessions", ordered, cap)


def render_block(header, notes, cap):
    lines, ids, used = [], [], len(header) + 1
    for i, n in enumerate(notes):
        line = format_note(n)
        if used + len(line) + 1 > cap:
            rest = len(notes) - i
            lines.append("(+%d more; run note.py list)" % rest)
            break
        lines.append(line)
        ids.append(n["id"])
        used += len(line) + 1
    if not ids:
        return "", []
    return header + "\n" + "\n".join(lines), ids


def mentioned_notes(prompt, notes, exclude_ids, at=None):
    """Live notes whose file paths or distinctive basenames are mentioned in the prompt."""
    hits = []
    low = (prompt or "").lower()
    for n in notes:
        if n["id"] in exclude_ids or not is_live(n, at):
            continue
        for f in n.get("files") or []:
            base = os.path.basename(f)
            if f.lower() in low:
                hits.append(n)
                break
            # basenames must be distinctive: has an extension or is 5+ chars
            if base and ("." in base or len(base) >= 5) and re.search(
                    r"(?<![\w.-])" + re.escape(base.lower()) + r"(?![\w-])", low):
                hits.append(n)
                break
    return hits


# ---------- hook io ----------

def read_hook_input(stdin):
    try:
        data = json.load(stdin)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def emit_context(event, text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))
