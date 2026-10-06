"""Plain-English previews of what a risky command would actually touch.

Previews only ever *look*: they walk directories, run `git ls-files`,
`git status`, `git clean -n` and `find` without -delete. Each walk is capped
(default 5000 files) and time-limited so the hook stays fast.
"""
import glob
import os
import subprocess
import time

from scoring import expand_word, has_glob


def _run(args, cwd, timeout=3):
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout)
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def git_root(path):
    d = path if os.path.isdir(path) else os.path.dirname(path)
    while d and not os.path.isdir(d):
        d = os.path.dirname(d)
    out = _run(["git", "rev-parse", "--show-toplevel"], d or "/")
    return os.path.realpath(out.strip()) if out else None


def expand_targets(words, cwd):
    """Turn rm-style arguments into absolute paths (globs expanded)."""
    out = []
    for w in words:
        path, unresolved = expand_word(w, cwd)
        if unresolved:
            continue
        if has_glob(w):
            out.extend(sorted(os.path.abspath(p) for p in glob.glob(path)))
        else:
            out.append(path)
    return out


def walk_files(paths, cap=5000, budget=2.0):
    """Return (files, capped) - absolute file paths under `paths`, at most `cap`."""
    files, deadline = [], time.monotonic() + budget
    for p in paths:
        if os.path.islink(p) or os.path.isfile(p):
            files.append(p)
        elif os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                for n in names:
                    files.append(os.path.join(root, n))
                    if len(files) >= cap:
                        return files, True
                if time.monotonic() > deadline:
                    return files, True
        if len(files) >= cap:
            return files, True
    return files, False


def tracked_files(paths, root):
    """Set of absolute tracked paths under `paths` (None if git is unavailable)."""
    rel = [os.path.relpath(os.path.realpath(p), root) for p in paths]
    out = _run(["git", "ls-files", "-z", "--"] + rel, root, timeout=4)
    if out is None:
        return None
    return {os.path.join(root, f) for f in out.split("\0") if f}


def _plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _short(path, cwd):
    rel = os.path.relpath(path, cwd)
    return "./" + rel if not rel.startswith("..") else path


def preview_rm(fact, cap=5000):
    cwd = fact.get("cwd") or os.getcwd()
    targets = expand_targets(fact.get("targets", []), cwd)
    if not fact.get("targets"):
        return "Deletes files whose names come from piped input (cannot preview)"
    if not targets:
        return "Deletes %s (the path depends on a variable, cannot preview)" % " ".join(fact["targets"])
    existing = [t for t in targets if os.path.lexists(t)]
    missing = [t for t in targets if not os.path.lexists(t)]
    if not existing:
        return "Nothing to delete: %s does not exist" % ", ".join(_short(t, cwd) for t in missing)
    if any(os.path.realpath(t) in ("/", os.path.realpath(os.path.expanduser("~"))) for t in existing):
        return "Deletes everything under %s" % ", ".join(existing)
    files, capped = walk_files(existing, cap)
    count = ("%d+" % len(files)) if capped else str(len(files))
    noun = "file" if len(files) == 1 and not capped else "files"
    parts = ["Deletes %s %s" % (count, noun)]
    # how many are not tracked by git (and so cannot be restored with git checkout)
    root = git_root(existing[0])
    if root:
        tracked = tracked_files(existing, root)
        if tracked is not None:
            untracked = sum(1 for f in files if os.path.realpath(f) not in tracked
                            and f not in tracked)
            parts.append("(%s not tracked by git)" % ("all" if untracked == len(files) and files
                                                      else "%d%s" % (untracked, "+" if capped else "")))
    else:
        parts.append("(not in a git repo)")
    where = ", ".join(_short(t, cwd) for t in existing[:3]) + (" ..." if len(existing) > 3 else "")
    parts.append(("under " if any(os.path.isdir(t) for t in existing) else "") + where)
    if missing:
        parts.append("(%s missing)" % _plural(len(missing), "target"))
    return " ".join(parts)


def preview_git_reset(fact):
    cwd = fact.get("repo_cwd") or fact.get("cwd")
    out = _run(["git", "status", "--porcelain"], cwd)
    if out is None:
        return fact["reason"]
    changed = [l for l in out.splitlines() if l and not l.startswith("??")]
    if not changed:
        return "Resets to HEAD (no uncommitted changes to tracked files right now)"
    return "Discards uncommitted changes to %s: %s%s" % (
        _plural(len(changed), "tracked file"), ", ".join(l[3:] for l in changed[:5]),
        " ..." if len(changed) > 5 else "")


def preview_git_clean(fact):
    cwd = fact.get("repo_cwd") or fact.get("cwd")
    flags = [f.replace("f", "") for f in fact.get("flags", []) if f.startswith("-") and not f.startswith("--")]
    flags = [f for f in flags if f not in ("-", "")]
    out = _run(["git", "clean", "-n"] + flags, cwd)
    if out is None:
        return fact["reason"]
    paths = [l[len("Would remove "):] for l in out.splitlines() if l.startswith("Would remove ")]
    if not paths:
        return "git clean would remove nothing right now"
    return "Deletes %s: %s%s" % (_plural(len(paths), "untracked path"), ", ".join(paths[:5]),
                                 " ..." if len(paths) > 5 else "")


def preview_find(fact, cap=5000):
    args = list(fact.get("args", []))
    if any(a in args for a in ("-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fls", "-fprintf")):
        return fact["reason"]
    args = [a for a in args if a != "-delete"]
    cwd = fact.get("cwd") or os.getcwd()
    try:
        p = subprocess.Popen(["find"] + args, cwd=cwd, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
        n, deadline = 0, time.monotonic() + 3
        for _ in p.stdout:
            n += 1
            if n >= cap or time.monotonic() > deadline:
                break
        p.kill()
        p.wait()
        p.stdout.close()
    except OSError:
        return fact["reason"]
    return "Deletes %s%s matched by find under %s" % (
        n, "+" if n >= cap else "", ", ".join(fact.get("roots", ["."])))


def preview_overwrite(fact):
    path = fact["path"]
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            lines = sum(1 for _ in fh)
    except OSError:
        return fact["reason"]
    return "%s (currently %s, %d bytes)" % (fact["reason"], _plural(lines, "line"), size)


def build(facts, cap=5000):
    """Return a list of preview sentences for the given facts."""
    out = []
    for f in facts:
        kind = f.get("kind")
        try:
            if kind == "rm":
                text = preview_rm(f, cap)
            elif kind == "git_reset_hard":
                text = preview_git_reset(f)
            elif kind == "git_discard":
                text = preview_git_reset(f).replace("Discards uncommitted changes to",
                                                    "May discard uncommitted changes to")
            elif kind == "git_clean":
                text = preview_git_clean(f)
            elif kind == "find_delete":
                text = preview_find(f, cap)
            elif kind == "overwrite":
                text = preview_overwrite(f)
            else:
                text = f.get("reason")
        except Exception:  # a preview must never break the hook
            text = f.get("reason")
        if text and text not in out:
            out.append(text)
    return out
