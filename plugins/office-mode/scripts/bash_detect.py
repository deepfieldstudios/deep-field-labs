"""Guess which existing office documents a Bash command might change.

Deliberately generous: a needless copy costs a little disk (and is deduped by content),
a missed copy can cost someone their only version of a spreadsheet.
"""
import glob
import os
import re
import shlex
from pathlib import Path

READ_ONLY = {"cat", "head", "tail", "less", "more", "ls", "wc", "file", "stat", "du", "grep",
             "rg", "egrep", "fgrep", "open", "qlmanage", "mdls", "shasum", "md5", "sha256sum",
             "xxd", "diff", "cmp", "pdftotext", "pdfinfo", "zipinfo", "echo", "printf", "pwd",
             "which", "find", "tree", "cd", "true", "test", "["}
RUNNERS = {"python", "python3", "node", "rscript", "ruby", "bash", "sh", "zsh", "perl", "osascript"}
MAX_TARGETS = 50
MAX_SCRIPT_BYTES = 1_000_000


def _segments(command):
    """Split on ; && || | and newlines (not inside quotes, roughly)."""
    parts, buf, quote = [], [], None
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch in ";\n|&":
            parts.append("".join(buf))
            buf = []
            if command[i:i + 2] in ("&&", "||"):
                i += 1
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _words(segment):
    try:
        return shlex.split(segment, comments=False)
    except ValueError:
        return segment.split()


def _first_command(words):
    for w in words:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w) or w in ("sudo", "env", "command", "time", "nohup"):
            continue
        return os.path.basename(w).lower()
    return ""


def _writes(segment, words):
    if re.search(r"(?<![0-9&])>>?(?!&)", segment.replace("2>&1", "").replace("&>", ">")):
        return True
    cmd = _first_command(words)
    if cmd == "sed" and any(w.startswith("-i") for w in words):
        return True
    return cmd not in READ_ONLY


def path_regex(exts):
    alt = "|".join(re.escape(e.lstrip(".")) for e in sorted(exts, key=len, reverse=True))
    return re.compile(
        r"""(?P<q>["'])(?P<a>[^"'\n]*?\.(?:%s))(?P=q)"""
        r"""|(?P<b>(?:\\ |[^\s"'<>|;&()=,`])+?\.(?:%s))(?![\w.])""" % (alt, alt),
        re.IGNORECASE)


def _candidates(text, rx):
    for m in rx.finditer(text):
        raw = m.group("a") or m.group("b") or ""
        yield raw.replace("\\ ", " ")


def bash_targets(command, cwd, exts):
    """Existing files with a watched extension that `command` may modify."""
    rx = path_regex(exts)
    base = Path(cwd)
    found, seen = [], set()

    def add(raw, rel_to):
        raw = os.path.expanduser(raw.strip())
        if not raw:
            return
        pattern = raw if os.path.isabs(raw) else str(rel_to / raw)
        matches = glob.glob(pattern) if any(c in raw for c in "*?[") else [pattern]
        for p in matches:
            p = os.path.normpath(p)
            if p not in seen and os.path.isfile(p) and Path(p).suffix.lower() in exts:
                seen.add(p)
                found.append(p)

    for seg in _segments(command):
        words = _words(seg)
        cmd = _first_command(words)
        if cmd == "cd" and len(words) >= 2:
            nxt = Path(os.path.expanduser(words[1]))
            base = nxt if nxt.is_absolute() else base / nxt
            continue
        if not _writes(seg, words):
            continue
        for raw in _candidates(seg, rx):
            add(raw, base)
        # A script being run may name the files it writes: look inside it.
        if cmd in RUNNERS:
            for w in words[1:]:
                sp = Path(os.path.expanduser(w))
                sp = sp if sp.is_absolute() else base / sp
                if sp.suffix and sp.is_file() and sp.stat().st_size < MAX_SCRIPT_BYTES \
                        and sp.suffix.lower() not in exts:
                    try:
                        text = sp.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    for raw in _candidates(text, rx):
                        add(raw, base)
        if len(found) >= MAX_TARGETS:
            break
    return found[:MAX_TARGETS]
