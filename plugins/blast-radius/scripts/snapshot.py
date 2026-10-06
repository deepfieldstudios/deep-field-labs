"""Recovery points taken before a high-risk local destructive command.

Git targets: a commit of the whole working tree (tracked + untracked, plus any
ignored files the command would delete, size permitting) built through a
*temporary* index, stored at refs/blast-radius/<id>. The user's working tree,
index, HEAD and stash are never touched.

Non-git targets: files are copied to <state>/snapshots/<id>/files/ if the
total is under the size limit (default 50 MB).
"""
import datetime
import json
import os
import shutil
import subprocess
import tempfile
import uuid

from preview import expand_targets, git_root, walk_files

LOCAL_KINDS = {"rm", "find_delete", "overwrite", "git_reset_hard", "git_clean", "git_discard"}


def new_id():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:4]


def _git(args, cwd, env=None, timeout=20, stdin=None):
    r = subprocess.run(["git"] + args, cwd=cwd, env=env, capture_output=True,
                       timeout=timeout, input=stdin)
    if r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args[0], r.stderr.decode("utf-8", "replace").strip()))
    return r.stdout.decode("utf-8", "replace").strip()


def _size(files):
    total = 0
    for f in files:
        try:
            total += os.lstat(f).st_size
        except OSError:
            pass
    return total


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def targets_of(facts):
    """(absolute target paths, repo-wide?) for the local destructive facts."""
    paths, repo_wide = [], []
    for f in facts:
        kind = f.get("kind")
        cwd = f.get("cwd") or os.getcwd()
        if kind == "rm":
            paths.extend(expand_targets(f.get("targets", []), cwd))
        elif kind == "find_delete":
            paths.extend(expand_targets(f.get("roots", ["."]), cwd))
        elif kind == "overwrite":
            paths.append(f["path"])
        elif kind in ("git_reset_hard", "git_clean", "git_discard"):
            repo_wide.append((f.get("repo_cwd") or cwd, kind == "git_clean" and any(
                "x" in fl or "X" in fl for fl in f.get("flags", []))))
    return [p for p in paths if os.path.lexists(p)], repo_wide


def git_snapshot(repo, force_paths, message, max_bytes, snap_id):
    """Commit the working tree via a temp index; returns (ref, note)."""
    index = _git(["rev-parse", "--git-path", "index"], repo)
    index = index if os.path.isabs(index) else os.path.join(repo, index)
    tmp = tempfile.mkdtemp(prefix="blast-radius-")
    note = ""
    try:
        tmp_index = os.path.join(tmp, "index")
        if os.path.exists(index):
            shutil.copy2(index, tmp_index)
        env = dict(os.environ, GIT_INDEX_FILE=tmp_index,
                   GIT_AUTHOR_NAME="blast-radius", GIT_AUTHOR_EMAIL="blast-radius@localhost",
                   GIT_COMMITTER_NAME="blast-radius", GIT_COMMITTER_EMAIL="blast-radius@localhost")
        env.pop("GIT_DIR", None)
        _git(["add", "-A"], repo, env=env, timeout=30)
        if force_paths:
            rel = [os.path.relpath(os.path.realpath(p), repo) for p in force_paths]
            out = _git(["ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--"] + rel,
                       repo, env=env)
            ignored = [f for f in out.split("\0") if f]
            if ignored:
                size = _size(os.path.join(repo, f) for f in ignored)
                if size <= max_bytes:
                    _git(["add", "-f", "--pathspec-from-file=-", "--pathspec-file-nul"], repo,
                         env=env, timeout=30, stdin="\0".join(ignored).encode())
                    note = " incl. %d ignored file%s" % (len(ignored), "" if len(ignored) == 1 else "s")
                else:
                    note = " (%d ignored files, %s, not included: over the size limit)" % (
                        len(ignored), _human(size))
        tree = _git(["write-tree"], repo, env=env)
        parent = []
        try:
            parent = ["-p", _git(["rev-parse", "--verify", "-q", "HEAD"], repo)]
        except RuntimeError:
            pass  # unborn branch: no parent
        commit = _git(["commit-tree", tree] + parent + ["-m", message], repo, env=env)
        ref = "refs/blast-radius/" + snap_id
        _git(["update-ref", ref, commit], repo)
        return ref, note
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def copy_snapshot(paths, dest, cwd, max_bytes, cap):
    """Copy files under `paths` into dest/files/. Returns (roots, note) or (None, note)."""
    files, capped = walk_files(paths, cap=cap)
    if capped:
        return None, "no snapshot (more than %d files)" % cap
    size = _size(files)
    if size > max_bytes:
        return None, "no snapshot (too large: %s > %s)" % (_human(size), _human(max_bytes))
    roots = []
    base = os.path.join(dest, "files")
    for p in paths:
        p = os.path.abspath(p)
        rel = os.path.relpath(p, cwd)
        rel = rel if not rel.startswith("..") else os.path.join("_abs", p.lstrip("/"))
        roots.append({"original": p, "copy": os.path.join(base, rel)})
    for f in files:
        f = os.path.abspath(f)
        rel = os.path.relpath(f, cwd)
        rel = rel if not rel.startswith("..") else os.path.join("_abs", f.lstrip("/"))
        out = os.path.join(base, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if os.path.islink(f):
            os.symlink(os.readlink(f), out)
        else:
            shutil.copy2(f, out)
    return roots, "%d file%s, %s" % (len(files), "" if len(files) == 1 else "s", _human(size))


def take(facts, cwd, state_dir, command, max_mb=50, cap=5000):
    """Create a recovery point for the local destructive parts of a command.

    Returns a dict describing the snapshot (also appended to snapshots.jsonl),
    or None if nothing local is at risk.
    """
    if not any(f.get("kind") in LOCAL_KINDS for f in facts):
        return None
    max_bytes = int(max_mb * 1024 * 1024)
    paths, repo_wide = targets_of(facts)
    snap_id = new_id()
    record = {"id": snap_id, "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "cwd": cwd, "command": command[:500], "type": "none", "note": ""}
    message = "blast-radius snapshot %s before: %s" % (snap_id, command[:200])

    # group targets by repo; a target that contains the .git dir cannot use git
    repos = {}
    outside = []
    for p in paths:
        root = git_root(p)
        if root and not os.path.realpath(os.path.join(root, ".git")).startswith(
                os.path.realpath(p).rstrip("/") + "/") and os.path.realpath(p) != root:
            repos.setdefault(root, []).append(p)
        else:
            outside.append(p)
    for rcwd, include_ignored in repo_wide:
        root = git_root(rcwd)
        if root:
            repos.setdefault(root, [])
            if include_ignored:
                repos[root].append(root)

    notes = []
    try:
        for root, force in repos.items():
            ref, note = git_snapshot(root, force, message, max_bytes, snap_id)
            record.setdefault("git", []).append({"repo": root, "ref": ref, "paths": force})
            notes.append("git ref %s%s" % (ref, note))
        if outside:
            dest = os.path.join(state_dir, "snapshots", snap_id)
            # never keep the copy inside something the command is about to delete
            if any(os.path.realpath(dest).startswith(os.path.realpath(p).rstrip("/") + "/") for p in outside):
                dest = os.path.join(os.path.expanduser("~/.claude/blast-radius/snapshots"), snap_id)
            roots, note = copy_snapshot(outside, dest, cwd, max_bytes, cap)
            if roots is not None:
                record["copy"] = {"dir": dest, "roots": roots}
                notes.append("copied to %s (%s)" % (dest, note))
            else:
                notes.append(note)
    except Exception as e:  # snapshot failure must not block the decision
        notes.append("snapshot failed: %s" % e)
    record["type"] = "+".join(k for k in ("git", "copy") if k in record) or "none"
    record["note"] = "; ".join(notes)
    if record["type"] != "none":
        os.makedirs(state_dir, exist_ok=True)
        with open(os.path.join(state_dir, "snapshots.jsonl"), "a") as fh:
            fh.write(json.dumps(record) + "\n")
    return record
