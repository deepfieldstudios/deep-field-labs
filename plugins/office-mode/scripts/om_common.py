"""Shared helpers for office-mode: config, the version store, and human-friendly formatting.

Version store layout (one folder per file):
    beside mode (default):  <dir-of-file>/.versions/<filename>/<YYYY-MM-DD_HHMMSS>__<sha8>.<ext>
    central mode:           $OFFICE_MODE_HOME/versions/<filename>__<pathhash8>/<same names>
Each folder holds index.jsonl, one JSON object per saved copy.
"""
import datetime as _dt
import hashlib
import json
import os
import shutil
from pathlib import Path

PLUGIN = "office-mode"

DEFAULT_CONFIG = {
    # "beside" keeps copies next to the file in a hidden .versions folder;
    # "central" keeps them all under $OFFICE_MODE_HOME/versions (default ~/.claude/office-mode/versions).
    "store": "beside",
    "retention": 50,          # copies kept per file
    "max_file_mb": 200,       # larger files are not copied
    # Only document types are ever versioned (by Write/Edit/MultiEdit and by Bash commands).
    # Source code is never copied: version control covers it. Documents are versioned everywhere,
    # including inside git working trees, because people rarely commit their spreadsheets.
    "extensions": [".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".docx", ".doc", ".pptx", ".ppt",
                   ".pdf", ".numbers", ".pages", ".key", ".odt", ".ods", ".odp", ".rtf",
                   ".md", ".txt"],
}

SKIP_PARTS = {".versions", ".git", "node_modules"}


def home():
    """Global state root (central store + global config)."""
    return Path(os.environ.get("OFFICE_MODE_HOME") or Path.home() / ".claude" / PLUGIN)


def project_state_dir(cwd):
    return Path(cwd) / ".claude" / PLUGIN


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def load_config(cwd=None):
    """Defaults, overlaid by global config.json, overlaid by the project's config.json."""
    cfg = dict(DEFAULT_CONFIG)
    sources = [home() / "config.json"]
    if cwd:
        sources.append(project_state_dir(cwd) / "config.json")
    for src in sources:
        data = _read_json(src, {})
        if isinstance(data, dict):
            cfg.update(data)
    exts = cfg.get("extensions") or []
    cfg["extensions"] = [e.lower() if e.startswith(".") else "." + e.lower() for e in exts]
    return cfg


def is_office_file(path, cfg):
    return Path(path).suffix.lower() in cfg["extensions"]


def should_skip(path):
    """Never version our own store, git internals or dependency folders."""
    p = Path(path)
    if SKIP_PARTS.intersection(p.parts):
        return True
    try:
        p.resolve().relative_to(home().resolve())
        return True
    except (ValueError, OSError):
        return False


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def abspath(path, cwd=None):
    p = Path(os.path.expanduser(str(path)))
    if not p.is_absolute():
        p = Path(cwd or os.getcwd()) / p
    return Path(os.path.normpath(str(p)))


def version_dir_for(path, cfg):
    p = abspath(path)
    if cfg.get("store") == "central":
        tag = hashlib.sha256(str(p).encode("utf-8")).hexdigest()[:8]
        return home() / "versions" / f"{p.name}__{tag}"
    return p.parent / ".versions" / p.name


def now_local():
    return _dt.datetime.now().astimezone()


def parse_time(s):
    t = _dt.datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.astimezone()
    return t


def read_index(vdir):
    """Entries (oldest first) whose saved copy still exists."""
    out = []
    idx = Path(vdir) / "index.jsonl"
    if not idx.exists():
        return out
    with open(idx, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if (Path(vdir) / e.get("version", "")).is_file():
                e["_path"] = str(Path(vdir) / e["version"])
                out.append(e)
    out.sort(key=lambda e: e.get("time", ""))
    return out


def _write_index(vdir, entries):
    idx = Path(vdir) / "index.jsonl"
    tmp = idx.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps({k: v for k, v in e.items() if not k.startswith("_")}) + "\n")
    os.replace(tmp, idx)


def prune(vdir, keep):
    entries = read_index(vdir)
    if len(entries) <= keep:
        return 0
    drop, kept = entries[:-keep], entries[-keep:]
    for e in drop:
        try:
            os.remove(e["_path"])
        except OSError:
            pass
    _write_index(vdir, kept)
    return len(drop)


def snapshot(path, cfg, session_id="", reason="", now=None):
    """Save a copy of an existing file. Returns {"saved", "entry", "version_path"} or None.

    Deduped by content hash: if an identical copy is already stored, nothing new is written
    and the existing entry is returned with saved=False.
    """
    p = abspath(path)
    if not p.is_file() or should_skip(p):
        return None
    size = p.stat().st_size
    if size > float(cfg.get("max_file_mb", 200)) * 1024 * 1024:
        return None
    sha = sha256_file(p)
    vdir = version_dir_for(p, cfg)
    entries = read_index(vdir)
    for e in entries:
        if e.get("sha") == sha:
            return {"saved": False, "entry": e, "version_path": e["_path"]}
    now = now or now_local()
    vdir.mkdir(parents=True, exist_ok=True)
    name = f"{now.strftime('%Y-%m-%d_%H%M%S')}__{sha[:8]}{p.suffix}"
    dest = vdir / name
    shutil.copy2(p, dest)
    entry = {"version": name, "original": str(p), "time": now.isoformat(timespec="seconds"),
             "size": size, "sha": sha, "session_id": session_id or "", "reason": reason or ""}
    with open(vdir / "index.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    prune(vdir, int(cfg.get("retention", 50)))
    entry["_path"] = str(dest)
    return {"saved": True, "entry": entry, "version_path": str(dest)}


# ---------- human formatting ----------

def human_size(n):
    n = float(n or 0)
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            if unit == "bytes":
                return f"{int(n)} bytes"
            return f"{n:.0f} {unit}" if n >= 10 else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def human_time(t, now=None):
    """'Today 14:02', 'Yesterday 09:10', 'Tuesday 14:02', '5 Sep 14:02', '5 Sep 2025 14:02'."""
    now = (now or now_local()).astimezone()
    t = t.astimezone(now.tzinfo)
    hm = t.strftime("%H:%M")
    days = (now.date() - t.date()).days
    if days == 0:
        return f"Today {hm}"
    if days == 1:
        return f"Yesterday {hm}"
    if 1 < days < 7:
        return f"{t.strftime('%A')} {hm}"
    if t.year == now.year:
        return f"{t.day} {t.strftime('%b')} {hm}"
    return f"{t.day} {t.strftime('%b %Y')} {hm}"


REASONS = {
    "Write": "before Claude rewrote it",
    "Edit": "before an edit",
    "MultiEdit": "before an edit",
    "Bash": "before a command changed it",
    "restore": "before restoring an older copy",
}


def human_reason(reason):
    return REASONS.get(reason, reason or "saved")
