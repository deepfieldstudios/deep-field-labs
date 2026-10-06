"""Risk scoring for shell commands.

Every simple command is scored 0-100 as the sum of four axes:
  reversibility  can the effect be undone locally? (rm, reset --hard, DROP TABLE ...)
  scope          does it reach shared/remote systems? (push, deploy, cloud CLIs ...)
  environment    does it target production? (prod/production/--prod, main/master)
  breadth        how much does it touch? (globs, recursion, /, ~, outside the project)

Compound commands are split on && || ; | & and newlines; the command scores the
max of its parts. Commands we recognise as read-only score near 0; commands we do
not recognise score NEUTRAL (30) so they are never auto-approved.

`assess(command, cwd)` returns an Assessment with the score, plain-English
reasons and `facts` (structured details used for previews and snapshots).
"""
import fnmatch
import os
import re
import subprocess

from shellparse import segments

NEUTRAL = 30  # unknown commands: defer to the normal permission flow

PROD_RE = re.compile(r"(?i)(?<![a-z0-9])(prod|production|prd)(?![a-z0-9])")
MAIN_BRANCHES = {"main", "master", "production", "prod", "release", "trunk"}

SAFE_COMMANDS = {
    "ls", "ll", "la", "cat", "bat", "head", "tail", "less", "more", "grep", "egrep",
    "fgrep", "rg", "ag", "ack", "wc", "pwd", "echo", "printf", "which", "whereis",
    "type", "file", "stat", "du", "df", "tree", "sort", "uniq", "cut", "tr", "diff",
    "cmp", "comm", "date", "cal", "whoami", "id", "groups", "uname", "hostname",
    "printenv", "true", "false", "test", "[", "basename", "dirname", "realpath",
    "readlink", "jq", "yq", "awk", "column", "nl", "md5", "md5sum", "shasum",
    "sha1sum", "sha256sum", "xxd", "hexdump", "od", "strings", "ps", "pgrep",
    "uptime", "history", "man", "help", "cd", "pushd", "popd", "sleep", "lsof",
    "env", "seq", "yes", "fold", "fmt", "expand", "paste", "join", "rev", "tac",
    "locate", "mdfind", "sw_vers", "defaults", "plutil", "otool", "nm", "ldd",
    "base64", "zcat", "gzcat", "unzip", "tar", "zipinfo", "pbpaste", "open",
    "node", "python", "python3", "ruby", "perl", "deno", "bun", "go", "cargo",
    "npm", "npx", "yarn", "pnpm", "make", "pip", "pip3",
}
# Interpreters / build tools in SAFE_COMMANDS are refined below; only some uses are safe.
REFINED = {"node", "python", "python3", "ruby", "perl", "deno", "bun", "go", "cargo",
           "npm", "npx", "yarn", "pnpm", "make", "pip", "pip3", "tar", "unzip",
           "open", "defaults"}

TEST_RUNNERS = {"pytest", "jest", "vitest", "mocha", "rspec", "phpunit", "tox",
                "nox", "ava", "tap", "karma", "playwright", "cypress"}
LINTERS = {"tsc", "eslint", "ruff", "mypy", "pyright", "flake8", "pylint", "black",
           "prettier", "shellcheck", "rubocop", "golangci-lint", "stylelint",
           "biome", "clippy", "isort"}
SCRIPT_SAFE = re.compile(r"^(test|tests|lint|check|typecheck|type-check|format:check|"
                         r"build|dev|start|serve|preview|storybook|coverage|test:\w+|lint:\w+)$")
SCRIPT_DEPLOY = re.compile(r"(deploy|release|publish|ship|prod)")

WRAPPERS_NOARG = {"nohup", "time", "command", "builtin", "exec", "caffeinate",
                  "unbuffer", "stdbuf", "noglob"}
SHELL_KEYWORDS = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until",
                  "!", "{", "}", "in", "esac"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
DB_CLIENTS = {"psql", "mysql", "mariadb", "sqlite3", "sqlcmd", "clickhouse-client",
              "cockroach", "mongosh", "mongo", "redis-cli", "duckdb", "pgcli", "mycli"}
SYSTEM_DIRS = ["/etc", "/usr", "/bin", "/sbin", "/System", "/Library", "/var", "/opt",
               "/private/etc", "/private/var", "/boot", "/lib", "/dev", "/Applications"]
TEMP_DIRS = ["/tmp", "/private/tmp", "/var/folders", "/private/var/folders"]
SENSITIVE_HOME = [".ssh", ".aws", ".gnupg", ".kube", ".config/gcloud", ".netrc",
                  ".npmrc", ".zshrc", ".bashrc", ".bash_profile", ".profile", ".zprofile",
                  ".zshenv", ".gitconfig", "Library/LaunchAgents", ".claude/settings.json",
                  ".claude/settings.local.json", ".docker/config.json"]


class Assessment:
    def __init__(self, score=0, reasons=None, facts=None):
        self.score = score
        self.reasons = reasons or []
        self.facts = facts or []

    def merge(self, other):
        self.score = max(self.score, other.score)
        self.reasons.extend(other.reasons)
        self.facts.extend(other.facts)
        return self

    def __repr__(self):
        return "Assessment(%d, %r)" % (self.score, self.reasons)


class Axes:
    """Accumulates the four axes for one simple command."""

    def __init__(self):
        self.rev = self.scope = self.env = self.breadth = 0
        self.floor = 0
        self.reasons = []  # (points, text)
        self.facts = []

    def add(self, axis, points, reason=None):
        setattr(self, axis, getattr(self, axis) + points)
        if reason:
            self.reasons.append((points, reason))

    def fact(self, **kw):
        self.facts.append(kw)

    @property
    def score(self):
        return max(self.floor, min(100, self.rev + self.scope + self.env + self.breadth))


# ---------------------------------------------------------------- helpers

def _home():
    return os.path.realpath(os.path.expanduser("~"))


def _within(path, root):
    root = root.rstrip("/") or "/"
    return path == root or path.startswith(root + "/")


def expand_word(word, cwd):
    """Expand ~ and $VARS; return (absolute path, unresolved_variable)."""
    w = str(word)
    if w.startswith("~"):
        w = os.path.expanduser(w)
    w = os.path.expandvars(w)
    unresolved = "$" in w
    return os.path.normpath(os.path.join(cwd, w)), unresolved


def is_temp(path):
    tmp = os.environ.get("TMPDIR", "").rstrip("/")
    return any(_within(path, t) for t in TEMP_DIRS) or bool(tmp and _within(path, tmp))


def is_sensitive(path):
    home = _home()
    for rel in SENSITIVE_HOME:
        if _within(path, os.path.join(home, rel)):
            return True
    return any(_within(path, d) for d in SYSTEM_DIRS if d not in ("/var", "/private/var")) \
        and not is_temp(path)


def classify_target(word, cwd):
    """Breadth points + reason for one path argument."""
    raw = str(word)
    path, unresolved = expand_word(word, cwd)
    real = os.path.realpath(path)
    home = _home()
    cwd_real = os.path.realpath(cwd)
    if unresolved:
        if re.match(r"^\$\{?\w+\}?/", raw) or raw.rstrip("/") in ("$HOME", "${HOME}"):
            return 30, "a path starting with an unset variable (%s) - if it is empty this means /" % raw
        return 15, "a path that depends on an unset variable (%s)" % raw
    if real == "/":
        return 40, "the filesystem root"
    if real == home:
        return 35, "your entire home directory"
    if any(real == d or _within(real, d) for d in SYSTEM_DIRS) and not is_temp(real):
        return 30, "system path %s" % path
    if is_sensitive(real):
        return 30, "sensitive path %s" % path
    if is_temp(real):
        return 0, None
    if real == cwd_real or (_within(cwd_real, real)):
        return 15, "the whole project directory" if real == cwd_real else "a parent of the project"
    if not _within(real, cwd_real):
        return 15, "outside the project (%s)" % path
    return 0, None


def has_glob(word):
    return not getattr(word, "quoted", False) and any(c in str(word) for c in "*?[")


def short_flags(words):
    """Set of single-letter flags from clusters like -rf, plus long flags."""
    chars, longs = set(), set()
    for w in words:
        w = str(w)
        if w == "--":
            break
        if w.startswith("--"):
            longs.add(w.split("=", 1)[0])
        elif w.startswith("-") and len(w) > 1:
            chars.update(w[1:])
    return chars, longs


def positional(words, takes_arg=()):
    """Non-option words (respects `--` and options that take a value)."""
    out, skip, ended = [], False, False
    for w in words:
        if skip:
            skip = False
            continue
        s = str(w)
        if ended:
            out.append(w)
        elif s == "--":
            ended = True
        elif s.startswith("-") and len(s) > 1:
            if s in takes_arg:
                skip = True
        else:
            out.append(w)
    return out


def apply_targets(ax, targets, cwd):
    """Add breadth for a list of path arguments."""
    best, why = 0, None
    for t in targets:
        pts, reason = classify_target(t, cwd)
        if pts > best:
            best, why = pts, reason
    if best:
        ax.add("breadth", best, "touches " + why)
    if any(has_glob(t) for t in targets):
        ax.add("breadth", 10, "uses a wildcard")


def _git(args, cwd, timeout=2):
    try:
        r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------- command rules

def rule_rm(ax, args, cwd, from_pipe):
    chars, longs = short_flags(args)
    targets = positional(args)
    recursive = bool({"r", "R"} & chars) or "--recursive" in longs
    ax.add("rev", 45, "permanently deletes files (rm bypasses the Trash)")
    if "f" in chars or "--force" in longs:
        ax.add("rev", 5)
    if recursive:
        ax.add("breadth", 25, "recursively")
    if not targets:
        ax.add("breadth", 15, "targets come from piped input")
    apply_targets(ax, targets, cwd)
    ax.fact(kind="rm", targets=[str(t) for t in targets], recursive=recursive,
            reason="Deletes %s" % (", ".join(map(str, targets)) or "piped paths"))


def rule_git(ax, args, cwd):
    # strip git global options
    i = 0
    gcwd = cwd
    while i < len(args):
        a = str(args[i])
        if a in ("-C",) and i + 1 < len(args):
            gcwd = expand_word(args[i + 1], cwd)[0]
            i += 2
        elif a in ("-c", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(args):
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            break
    if i >= len(args):
        return
    sub, rest = str(args[i]), args[i + 1:]
    chars, longs = short_flags(rest)
    pos = positional(rest)
    read_only = {"status", "diff", "log", "show", "blame", "grep", "ls-files", "ls-tree",
                 "ls-remote", "rev-parse", "describe", "shortlog", "cat-file",
                 "merge-base", "rev-list", "show-ref", "for-each-ref", "help", "version",
                 "whatchanged", "name-rev", "count-objects", "fsck", "check-ignore",
                 "show-branch", "var", "check-attr", "range-diff", "difftool"}
    if sub in read_only:
        return
    if sub == "fetch":
        ax.floor = 10
    elif sub == "branch":
        if "D" in chars or ("--delete" in longs and "--force" in longs) or \
                ("d" in chars and "f" in chars):
            ax.add("rev", 50, "force-deletes branch %s (unmerged commits survive only in the reflog)"
                   % (", ".join(map(str, pos)) or "?"))
        elif "d" in chars or "--delete" in longs:
            ax.floor = 20
        elif "f" in chars or "--force" in longs or "M" in chars:
            ax.add("rev", 35, "force-moves a branch")
        else:
            ax.floor = 5
    elif sub == "tag":
        ax.floor = 30 if ("d" in chars or "--delete" in longs) else 5
    elif sub == "remote":
        ax.floor = {"remove": 30, "rm": 30, "add": 20, "rename": 20, "set-url": 20}.get(
            str(pos[0]) if pos else "", 0)
    elif sub == "config":
        setting = len(pos) >= 2 and not ({"l"} & chars or {"--get", "--list", "--get-all"} & longs)
        ax.floor = (30 if "--global" in longs else 20) if setting else 0
    elif sub == "stash":
        action = str(pos[0]) if pos else "push"
        ax.floor = {"list": 0, "show": 0, "pop": 25, "apply": 25, "push": 15, "save": 15,
                    "branch": 15, "create": 0, "store": 15}.get(action, 30)
        if action == "drop":
            ax.add("rev", 50, "permanently drops a stash")
        elif action == "clear":
            ax.add("rev", 65, "permanently deletes every stash")
    elif sub in ("add", "commit", "switch", "init", "notes", "mv", "worktree", "sparse-checkout"):
        ax.floor = 25 if "--amend" in longs else 15
    elif sub == "checkout":
        discard = "--" in [str(w) for w in rest] or "f" in chars or "--force" in longs or \
            any(str(p) == "." or os.path.exists(expand_word(p, gcwd)[0]) for p in pos)
        if ("b" in chars or "B" in chars) and not ("f" in chars):
            ax.floor = 15
        elif discard:
            ax.add("rev", 55, "discards uncommitted changes to %s" % (", ".join(map(str, pos)) or "files"))
            ax.fact(kind="git_discard", repo_cwd=gcwd, reason="Discards uncommitted changes")
        else:
            ax.floor = 15
    elif sub == "restore":
        if ("S" in chars or "--staged" in longs) and not ("W" in chars or "--worktree" in longs):
            ax.floor = 15
        else:
            ax.add("rev", 55, "discards uncommitted changes to %s" % (", ".join(map(str, pos)) or "files"))
            ax.fact(kind="git_discard", repo_cwd=gcwd, reason="Discards uncommitted changes")
    elif sub == "reset":
        if "--hard" in longs:
            ax.add("rev", 70, "discards all uncommitted changes (git reset --hard)")
            ax.fact(kind="git_reset_hard", repo_cwd=gcwd,
                    reason="Discards all uncommitted changes (git reset --hard)")
        elif {"--merge", "--keep"} & longs:
            ax.add("rev", 35, "resets the working tree")
        else:
            ax.floor = 25
    elif sub == "clean":
        if "n" in chars or "--dry-run" in longs:
            return
        if "f" in chars or "--force" in longs:
            ax.add("rev", 60, "deletes untracked files (git clean)")
            if "d" in chars:
                ax.add("breadth", 10, "including untracked directories")
            if "x" in chars or "X" in chars:
                ax.add("breadth", 10, "including ignored files")
            ax.fact(kind="git_clean", repo_cwd=gcwd,
                    flags=[str(w) for w in rest if str(w).startswith("-")],
                    reason="Deletes untracked files (git clean)")
        else:
            ax.floor = 10
    elif sub in ("pull", "merge", "cherry-pick", "revert", "am", "apply"):
        ax.floor = 30
    elif sub == "rebase":
        ax.floor = 35
    elif sub == "rm":
        ax.floor = 15 if "--cached" in longs else 35
    elif sub in ("filter-branch", "filter-repo"):
        ax.add("rev", 80, "rewrites the repository's entire history")
    elif sub in ("gc", "prune") or (sub == "reflog" and pos and str(pos[0]) in ("expire", "delete")):
        ax.add("rev", 55, "permanently prunes unreachable commits")
    elif sub == "reflog":
        return
    elif sub == "update-ref":
        if "d" in chars:
            ax.add("rev", 55, "deletes a git ref")
        else:
            ax.floor = 35
    elif sub == "push":
        rule_git_push(ax, rest, gcwd)
    elif sub == "clone":
        ax.floor = 15
    else:
        ax.floor = NEUTRAL


def rule_git_push(ax, rest, cwd):
    chars, longs = short_flags(rest)
    if "n" in chars or "--dry-run" in longs:
        ax.floor = 5
        return
    pos = positional(rest, takes_arg=("-o", "--push-option", "--repo", "--receive-pack", "--exec"))
    remote = str(pos[0]) if pos else "origin"
    refspecs = [str(p) for p in pos[1:]]
    force = "f" in chars or "--force" in longs or any(r.startswith("+") for r in refspecs)
    lease = bool({"--force-with-lease", "--force-if-includes"} & longs) and not force
    delete = "d" in chars or "--delete" in longs or any(r.startswith(":") for r in refspecs)
    mirror = "--mirror" in longs
    branches = []
    for r in refspecs:
        r = r.lstrip("+")
        dst = r.split(":", 1)[1] if ":" in r else r
        if dst == "HEAD" or not dst:
            dst = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd) or "HEAD"
        branches.append(dst.replace("refs/heads/", ""))
    if not branches and not ({"--all", "--tags", "--mirror"} & longs):
        branches = [_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd) or "the current branch"]
    where = "%s/%s" % (remote, ",".join(branches)) if branches else remote
    ax.add("scope", 35, None if (force or lease or delete or mirror) else "pushes to %s" % where)
    if force:
        ax.add("rev", 40, "force-pushes to %s, rewriting remote history" % where)
    elif lease:
        ax.add("rev", 25, "force-pushes (with lease) to %s, rewriting remote history" % where)
    if delete:
        ax.add("rev", 35, "deletes remote branch %s" % where)
    if mirror:
        ax.add("rev", 45, "mirrors every ref, deleting remote refs that do not exist locally")
    if {"--all", "--tags", "--mirror"} & longs:
        ax.add("breadth", 10, "pushes many refs at once")
    if any(b in MAIN_BRANCHES for b in branches):
        ax.add("env", 20, "targets the %s branch" % next(b for b in branches if b in MAIN_BRANCHES))
    ax.fact(kind="git_push", remote=remote, branches=branches, force=force or lease,
            delete=delete,
            reason=("Force-pushes to %s, rewriting remote history" % where) if (force or lease)
            else ("Deletes remote branch %s" % where) if delete else "Pushes to %s" % where)


def sql_risk(sql):
    """Score SQL / shell-database text. Returns (points, reason)."""
    best = (0, None)
    for stmt in re.split(r";", sql):
        s = stmt.strip()
        if not s:
            continue
        m = re.search(r"(?i)\bdrop\s+(table|database|schema|index|view|collection|user|role|keyspace)\b\s*(?:if\s+exists\s+)?[\"`']?([\w.]*)", s)
        if m:
            best = max(best, (75, "drops %s %s - gone unless you have a backup" % (m.group(1).upper(), m.group(2))))
        m = re.search(r"(?i)\btruncate\s+(?:table\s+)?[\"`']?([\w.]+)", s)
        if m:
            best = max(best, (70, "empties table %s (TRUNCATE)" % m.group(1)))
        m = re.search(r"(?i)\bdelete\s+from\s+[\"`']?([\w.]+)", s)
        if m:
            if re.search(r"(?i)\bwhere\b", s):
                best = max(best, (40, "deletes rows from %s" % m.group(1)))
            else:
                best = max(best, (70, "deletes every row in %s (DELETE without WHERE)" % m.group(1)))
        m = re.search(r"(?i)\bupdate\s+[\"`']?([\w.]+)[\"`']?\s+set\b", s)
        if m:
            if re.search(r"(?i)\bwhere\b", s):
                best = max(best, (35, "updates rows in %s" % m.group(1)))
            else:
                best = max(best, (60, "updates every row in %s (UPDATE without WHERE)" % m.group(1)))
        if re.search(r"(?i)\balter\s+table\b.*\bdrop\b", s):
            best = max(best, (55, "drops a column or constraint (ALTER TABLE ... DROP)"))
        if re.search(r"(?i)\.drop\(\)|dropDatabase\(\)", s):
            best = max(best, (75, "drops a MongoDB collection or database"))
        if re.search(r"(?i)deleteMany\(\s*\{\s*\}\s*\)", s):
            best = max(best, (70, "deletes every document in a collection"))
        if re.search(r"(?i)\bflush(all|db)\b", s):
            best = max(best, (80, "wipes Redis data (FLUSHALL/FLUSHDB)"))
    return best


def rule_db(ax, cmd, args, cwd, whole):
    pts, reason = sql_risk(whole)
    if pts:
        ax.add("rev", pts, reason)
    else:
        ax.floor = NEUTRAL
    host = None
    for k, a in enumerate(args):
        a = str(a)
        if a in ("-h", "--host", "-H") and k + 1 < len(args):
            host = str(args[k + 1])
        elif a.startswith("--host="):
            host = a.split("=", 1)[1]
        m = re.match(r"^(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://(?:[^@/]*@)?([^/:?]+)", a)
        if m:
            host = m.group(1)
    if host and host not in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        ax.add("scope", 20, "on database host %s" % host)


def rule_curl(ax, cmd, args):
    method = None
    joined = [str(a) for a in args]
    for k, a in enumerate(joined):
        if a in ("-X", "--request") and k + 1 < len(joined):
            method = joined[k + 1].upper()
        elif a.startswith("-X") and len(a) > 2:
            method = a[2:].upper()
        elif a.startswith("--request="):
            method = a.split("=", 1)[1].upper()
        elif cmd in ("http", "https", "xh") and a.upper() in ("POST", "PUT", "PATCH", "DELETE"):
            method = a.upper()
        elif (a in ("-d", "-F", "-T", "--form", "--json", "--upload-file", "--post-data",
                    "--post-file") or a.startswith("--data")) and not method:
            method = "POST"
    urls = [a for a in joined if re.match(r"^(https?://|[\w.-]+\.[a-z]{2,}(/|$))", a)]
    host = re.sub(r"^https?://", "", urls[0]).split("/")[0] if urls else "a remote host"
    local = bool(re.match(r"^(localhost|127\.|0\.0\.0\.0|\[::1\])", host))
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        ax.add("scope", 15 if local else 35, "sends a %s request to %s" % (method, host))
        if method == "DELETE":
            ax.add("rev", 20, "deletes a remote resource")
    else:
        ax.floor = NEUTRAL


# cloud / deploy CLIs: (subcommand words that are read-only, mutating, destructive)
CLOUD_VERBS_READ = re.compile(r"^(describe|list|get|ls|show|status|logs?|whoami|help|version|"
                              r"info|inspect|top|explain|plan|validate|fmt|output|diff|"
                              r"tail|dev|config|history|template|search|preview|lint|"
                              r"get-caller-identity|wait|read|view|check|init|providers|"
                              r"--version|-v|--help|-h)(-|$)")
CLOUD_VERBS_DESTROY = re.compile(r"^(delete|destroy|terminate|remove|rm|rb|purge|deregister|"
                                 r"uninstall|drain|detach|revoke|disable|stop|kill|reset|"
                                 r"unpublish|wipe|erase|teardown|down)(-|$)")
DEPLOY_TOOLS = {"wrangler", "vercel", "netlify", "fly", "flyctl", "kubectl", "terraform",
                "tofu", "helm", "aws", "gcloud", "az", "firebase", "heroku", "pulumi",
                "serverless", "sls", "railway", "supabase", "eb", "doctl", "gsutil",
                "render", "cdk", "sam", "amplify", "ansible-playbook", "kustomize",
                "eksctl", "oc", "nomad", "cf"}


def rule_deploy(ax, cmd, args):
    words = [str(a) for a in positional(args, takes_arg=(
        "-n", "--namespace", "--context", "--profile", "--region", "--project", "-c",
        "--config", "-f", "--filename", "--env", "-e", "--scope", "--account", "-o",
        "--output", "-a", "--app", "-t", "--token", "-l", "--selector"))]
    sargs = [str(a) for a in args]
    chars, longs = short_flags(args)
    name = {"flyctl": "Fly.io", "fly": "Fly.io", "tofu": "OpenTofu", "sls": "Serverless",
            "aws": "AWS", "gcloud": "Google Cloud", "az": "Azure", "kubectl": "Kubernetes",
            "wrangler": "Cloudflare", "helm": "Kubernetes (Helm)", "oc": "OpenShift",
            "eb": "Elastic Beanstalk", "doctl": "DigitalOcean", "cdk": "AWS CDK",
            "sam": "AWS SAM", "gsutil": "Google Cloud Storage"}.get(cmd, cmd.capitalize())
    verbs = words[:3] or (["deploy"] if cmd == "vercel" else [])  # bare `vercel` deploys
    if not verbs:
        ax.floor = NEUTRAL
        return
    if {"--dry-run", "--dryrun", "--check"} & longs:
        ax.floor = 10
        return
    # the first word that is a known verb decides: services/resource names match neither
    action = "mutate"
    for v in verbs:
        if CLOUD_VERBS_DESTROY.match(v):
            action = "destroy"
            break
        if CLOUD_VERBS_READ.match(v):
            action = "read"
            break
    auto = bool({"--auto-approve", "--yes", "--force"} & longs) or "-auto-approve" in sargs
    if action == "read":
        ax.floor = 10
        return
    if action == "destroy":
        ax.add("scope", 40, "changes live %s infrastructure" % name)
        ax.add("rev", 50 if cmd in ("terraform", "tofu", "pulumi") else 40,
               "deletes %s resources" % name)
        if auto:
            ax.add("rev", 10, "without a confirmation step")
    else:
        deploying = any(v in ("deploy", "publish", "up", "apply", "push", "release",
                              "install", "upgrade", "rollout", "promote") for v in verbs)
        ax.add("scope", 45, ("deploys to %s" if deploying else "changes live %s resources") % name)
        if cmd in ("terraform", "tofu", "pulumi") and ("apply" in verbs or "up" in verbs):
            ax.add("rev", 10, "applies infrastructure changes")
            if auto:
                ax.add("rev", 10, "without a confirmation step")
    if {"--prod", "--production"} & longs or (cmd == "vercel" and "prod" in words):
        ax.add("env", 30, "runs against production (--prod)")
    ax.fact(kind="deploy", tool=cmd, reason="Runs %s %s" % (cmd, " ".join(verbs[:2])))


def rule_publish(ax, cmd, args):
    chars, longs = short_flags(args)
    if "--dry-run" in longs:
        ax.floor = 10
        return
    if cmd in ("npm", "yarn", "pnpm") and args and str(args[0]) == "unpublish":
        ax.add("scope", 50, "removes a published package from the public registry")
        ax.add("rev", 40, "breaks everyone who depends on it")
        return
    ax.add("scope", 50, "publishes a package to a public registry")
    ax.add("rev", 20, "published versions cannot be re-used or fully withdrawn")


def rule_docker(ax, args):
    pos = [str(p) for p in positional(args, takes_arg=("-f", "--file", "-H", "--context", "-p", "--project-name"))]
    if not pos:
        ax.floor = 5
        return
    sub = pos[0]
    chars, longs = short_flags(args)
    if sub == "push" or (sub == "buildx" and "--push" in longs) or (sub in ("image",) and len(pos) > 1 and pos[1] == "push"):
        ax.add("scope", 45, "pushes an image to a registry")
    elif sub in ("ps", "images", "logs", "inspect", "version", "info", "build", "pull",
                 "search", "stats", "top", "history", "events", "login", "tag"):
        ax.floor = 10
    elif sub in ("rm", "rmi"):
        ax.add("rev", 35, "removes containers/images")
    elif sub in ("system", "volume", "image", "container", "network", "builder") and len(pos) > 1 and pos[1] in ("prune", "rm"):
        ax.add("rev", 60 if sub in ("volume", "system") else 40,
               "deletes docker %s data" % sub)
    elif sub == "compose" and len(pos) > 1 and pos[1] == "down":
        if "v" in chars or "--volumes" in longs:
            ax.add("rev", 55, "deletes compose volumes (their data is lost)")
        else:
            ax.floor = NEUTRAL
    else:
        ax.floor = NEUTRAL


def rule_gh(ax, args):
    pos = [str(p) for p in positional(args, takes_arg=("-R", "--repo", "-X", "--method", "-H", "--header", "-f", "-F", "--field", "--raw-field", "-q", "--jq"))]
    if not pos:
        return
    joined = " ".join(pos[:2])
    method = None
    sargs = [str(a) for a in args]
    for k, a in enumerate(sargs):
        if a in ("-X", "--method") and k + 1 < len(sargs):
            method = sargs[k + 1].upper()
    if joined in ("repo delete",):
        ax.add("scope", 40, "acts on GitHub")
        ax.add("rev", 50, "deletes a GitHub repository")
    elif joined in ("release delete", "secret delete", "variable delete", "run delete", "cache delete"):
        ax.add("scope", 35, "acts on GitHub")
        ax.add("rev", 25, "deletes %s" % pos[0])
    elif pos[0] == "api":
        if method in ("POST", "PUT", "PATCH", "DELETE") or any(a in ("-f", "-F", "--field", "--raw-field") for a in sargs):
            ax.add("scope", 40, "writes to the GitHub API (%s)" % (method or "POST"))
            if method == "DELETE":
                ax.add("rev", 20, "deletes a GitHub resource")
        else:
            ax.floor = 10
    elif len(pos) > 1 and pos[1] in ("list", "view", "status", "checks", "diff", "browse", "watch", "download", "ls"):
        ax.floor = 10
    elif pos[0] in ("auth", "help", "version", "search", "browse", "status"):
        ax.floor = 10
    elif joined == "pr merge":
        ax.add("scope", 45, "merges a pull request on GitHub")
    else:
        ax.add("scope", 35, "changes things on GitHub (%s)" % joined)


def rule_ssh(ax, cmd, args, cwd, depth):
    takes = ("-p", "-i", "-l", "-o", "-F", "-J", "-L", "-R", "-D", "-b", "-c", "-E", "-e",
             "-m", "-O", "-Q", "-S", "-W", "-w", "-B", "-P")
    pos = positional(args, takes_arg=takes)
    if cmd in ("scp", "sftp"):
        dests = [str(p) for p in pos]
        if dests and re.match(r"^[^/.][^/]*:", dests[-1]):
            ax.add("scope", 30, "copies files to remote host %s" % dests[-1].split(":")[0])
        else:
            ax.floor = NEUTRAL
        return
    if not pos:
        ax.floor = NEUTRAL
        return
    host = str(pos[0])
    ax.add("scope", 40, "runs on remote host %s" % host)
    if len(pos) > 1 and depth < 4:
        # remote paths are not relative to our project, so score them against "/"
        inner = assess(" ".join(str(p) for p in pos[1:]), "/", depth + 1, remote=True)
        if inner.score >= 30:
            ax.add("rev", max(0, inner.score + 20 - 40), None)
            for r in inner.reasons:
                ax.reasons.append((inner.score, "on %s: %s" % (host, r)))


def rule_find(ax, args, cwd, depth):
    sargs = [str(a) for a in args]
    roots = []
    for a in args:
        if str(a).startswith("-") or str(a) in ("(", "!", "\\("):
            break
        roots.append(a)
    if not roots:
        roots = ["."]
    if "-delete" in sargs:
        ax.add("rev", 50, "deletes every file matched by find")
        ax.add("breadth", 20, "recursively")
        apply_targets(ax, roots, cwd)
        ax.fact(kind="find_delete", args=sargs, roots=[str(r) for r in roots],
                reason="Deletes files matched by find under %s" % ", ".join(map(str, roots)))
        return
    for flag in ("-exec", "-execdir", "-ok", "-okdir"):
        if flag in sargs:
            k = sargs.index(flag)
            end = next((j for j in range(k + 1, len(sargs)) if sargs[j] in (";", "+")), len(sargs))
            inner_cmd = " ".join(_quote(w) for w in sargs[k + 1:end])
            inner = assess(inner_cmd, cwd, depth + 1)
            if inner.score >= 30:
                ax.add("rev", inner.score, None)
                ax.add("breadth", 15, "for every file find matches")
                for r in inner.reasons:
                    ax.reasons.append((inner.score, "per matched file: " + r))
                if any(f.get("kind") == "rm" for f in inner.facts):
                    ax.fact(kind="find_delete", args=sargs[:k], roots=[str(r) for r in roots],
                            reason="Deletes files matched by find under %s" % ", ".join(map(str, roots)))
            else:
                ax.floor = max(ax.floor, inner.score)
            return
    if "-fprint" in sargs or "-fls" in sargs:
        ax.floor = 20


def _quote(w):
    w = str(w)
    return w if re.match(r"^[\w@%+=:,./{}-]+$", w) else "'" + w.replace("'", "'\\''") + "'"


def rule_redirects(ax, redirects, cwd):
    for op, target in redirects:
        t = str(target)
        if op in ("<", "<<", "<<-", "<<<", "<&", "<>") or op == ">&" and t.isdigit():
            continue
        if t in ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "-") or t.startswith("&"):
            continue
        path, unresolved = expand_word(target, cwd)
        append = op in (">>", "&>>")
        exists = os.path.isfile(path)
        pts, why = classify_target(target, cwd)
        if exists and not append:
            ax.add("rev", 45, "overwrites existing file %s" % t)
            ax.fact(kind="overwrite", path=path, reason="Overwrites existing file %s" % t)
        else:
            ax.floor = max(ax.floor, 20)
        if pts:
            ax.add("breadth", pts, "writes to " + why)
        if is_sensitive(os.path.realpath(path)) and not pts:
            ax.add("breadth", 30, "writes to sensitive file %s" % t)


SAFE_PY_MODULES = {"pytest", "unittest", "doctest", "mypy", "pyflakes", "pylint", "ruff",
                   "black", "isort", "json.tool", "http.server", "venv", "pip", "compileall",
                   "py_compile", "timeit", "pydoc", "tox", "nox", "coverage"}


def rule_interpreter(ax, cmd, args, cwd):
    sargs = [str(a) for a in args]
    if cmd in ("python", "python3") and len(sargs) >= 2 and sargs[0] == "-m":
        mod = sargs[1]
        if mod in ("pytest", "unittest", "doctest", "coverage", "tox", "nox"):
            ax.floor = 5
        elif mod in ("twine",) and len(sargs) > 2 and sargs[2] == "upload":
            rule_publish(ax, "twine", args[3:])
        elif mod in SAFE_PY_MODULES and mod != "pip":
            ax.floor = 15
        else:
            ax.floor = NEUTRAL
        return
    if sargs and sargs[0] in ("--version", "-V", "--help", "-h", "version"):
        return
    if sargs and re.search(r"blast-radius/scripts/restore\.py$", sargs[0]):
        return  # this plugin's own read-only restore lister
    inline = None
    for flag in ("-c", "-e", "--eval", "-E"):
        if flag in sargs and sargs.index(flag) + 1 < len(sargs):
            inline = sargs[sargs.index(flag) + 1]
    if inline and re.search(r"rmtree|os\.remove|os\.unlink|\.unlink\(|fs\.rm|rmSync|unlinkSync|"
                            r"rm -rf|File\.delete|FileUtils\.rm|DROP\s+TABLE|truncate", inline, re.I):
        ax.add("rev", 55, "inline %s code deletes or truncates data" % cmd)
        return
    ax.floor = NEUTRAL


def rule_pkg(ax, cmd, args):
    """npm/yarn/pnpm/bun/cargo/go/make/pip/deno."""
    pos = [str(p) for p in positional(args, takes_arg=("--prefix", "-C", "--filter", "-w", "--workspace", "-p", "--package"))]
    sub = pos[0] if pos else ""
    if cmd in ("npm", "yarn", "pnpm", "bun") and sub in ("publish", "unpublish"):
        return rule_publish(ax, cmd, args)
    if cmd == "cargo" and sub == "publish":
        return rule_publish(ax, cmd, args)
    if cmd in ("npm", "yarn", "pnpm", "bun", "deno"):
        script = None
        if sub in ("run", "run-script"):
            script = pos[1] if len(pos) > 1 else ""
        elif sub in ("test", "t", "tst", "lint", "build", "check", "typecheck", "start", "dev"):
            script = sub
        elif cmd in ("yarn", "pnpm", "bun") and sub and sub not in (
                "install", "add", "remove", "i", "x", "dlx", "exec", "create", "up", "upgrade", "link"):
            script = sub
        if sub in ("ls", "list", "outdated", "view", "info", "why", "audit", "--version", "-v", "help", "config", "whoami"):
            ax.floor = 5
            return
        if script is not None:
            if SCRIPT_DEPLOY.search(script):
                ax.add("scope", 45, "runs the '%s' script (looks like a deploy/release)" % script)
            elif SCRIPT_SAFE.match(script):
                ax.floor = 5 if script.startswith("test") else 15
            else:
                ax.floor = NEUTRAL
            return
        ax.floor = NEUTRAL
        return
    if cmd == "cargo":
        ax.floor = 5 if sub in ("test", "check", "build", "clippy", "fmt", "doc", "bench", "tree", "metadata") else NEUTRAL
        return
    if cmd == "go":
        ax.floor = 5 if sub in ("test", "vet", "build", "fmt", "list", "version", "env", "doc", "mod") and not (sub == "mod" and len(pos) > 1 and pos[1] not in ("tidy", "graph", "why", "verify", "download")) else NEUTRAL
        return
    if cmd == "make":
        target = pos[0] if pos else ""
        if SCRIPT_DEPLOY.search(target):
            ax.add("scope", 45, "runs make target '%s' (looks like a deploy/release)" % target)
        elif target in ("test", "tests", "check", "lint", "fmt", "format", "build", "all", ""):
            ax.floor = 5 if target in ("test", "tests", "check") else 15
        else:
            ax.floor = NEUTRAL
        return
    if cmd in ("pip", "pip3"):
        ax.floor = 5 if sub in ("list", "show", "freeze", "check", "--version", "help", "search", "index", "inspect") else NEUTRAL
        return
    ax.floor = NEUTRAL


# ---------------------------------------------------------------- dispatcher

def strip_prefixes(words):
    """Drop env assignments, sudo and wrappers. Returns (words, sudo, env_words)."""
    sudo = False
    env_words = []
    words = list(words)
    changed = True
    while words and changed:
        changed = False
        w = str(words[0])
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):
            env_words.append(w)
            words.pop(0)
            changed = True
        elif w in SHELL_KEYWORDS:
            words.pop(0)
            changed = True
        elif w in ("sudo", "doas"):
            sudo = True
            words.pop(0)
            while words and str(words[0]).startswith("-"):
                f = str(words.pop(0))
                if f in ("-u", "-g", "-p", "-h", "-C", "-D", "-r", "-t", "-U") and words:
                    words.pop(0)
            changed = True
        elif w == "env" and len(words) > 1:
            words.pop(0)
            while words and str(words[0]).startswith("-"):
                f = str(words.pop(0))
                if f in ("-u", "-C", "-S") and words:
                    words.pop(0)
            changed = True
        elif w in WRAPPERS_NOARG:
            words.pop(0)
            while words and str(words[0]).startswith("-"):
                words.pop(0)
            changed = True
        elif w in ("nice", "ionice", "watch", "timeout", "gtimeout", "chronic", "flock"):
            words.pop(0)
            while words and str(words[0]).startswith("-"):
                f = str(words.pop(0))
                if f in ("-n", "-c", "-s", "-k", "-d") and words:
                    words.pop(0)
            if w in ("timeout", "gtimeout", "flock") and words:
                words.pop(0)  # duration / lock file
            changed = True
        elif w == "xargs":
            words.pop(0)
            while words and str(words[0]).startswith("-"):
                f = str(words.pop(0))
                if f in ("-n", "-I", "-P", "-L", "-d", "-s", "-E", "-a") and words:
                    words.pop(0)
            env_words.append("__xargs__")
            changed = True
        elif w in ("npx", "bunx", "pnpx") or (w in ("pnpm", "yarn") and len(words) > 1 and str(words[1]) == "dlx"):
            words.pop(0)
            if words and str(words[0]) == "dlx":
                words.pop(0)
            while words and str(words[0]).startswith("-"):
                words.pop(0)
            changed = True
    return words, sudo, env_words


def assess_segment(seg, cwd, whole, depth, remote=False):
    ax = Axes()
    words, sudo, env_words = strip_prefixes(seg.words)
    from_pipe = "__xargs__" in env_words
    if words:
        cmd = os.path.basename(str(words[0]))
        args = words[1:]
        score_command(ax, cmd, args, seg, cwd, whole, depth, from_pipe)
    if not remote:
        rule_redirects(ax, seg.redirects, cwd)
    elif seg.redirects:
        ax.floor = max(ax.floor, NEUTRAL)
    base = ax.score
    if base >= 20:
        hint = next((str(w) for w in list(seg.words) + [r[1] for r in seg.redirects]
                     if PROD_RE.search(str(w))), None)
        if hint and ax.env == 0:
            ax.add("env", 25, "runs against production (%s)" % hint)
        elif hint and not any("production" in r for _, r in ax.reasons):
            ax.add("env", 10, "runs against production (%s)" % hint)
        if any("production" in r for _, r in ax.reasons):
            ax.fact(kind="prod", reason="Runs against production")
    if sudo:
        ax.floor = max(ax.floor, NEUTRAL)
        if ax.score >= 30:
            ax.add("breadth", 10, "with root privileges (sudo)")
    for f in ax.facts:
        f.setdefault("cwd", cwd)
    reasons = [r for _, r in sorted(ax.reasons, key=lambda x: -x[0])]
    return Assessment(ax.score, reasons, ax.facts)


def score_command(ax, cmd, args, seg, cwd, whole, depth, from_pipe):
    sargs = [str(a) for a in args]
    if cmd in ("rm", "unlink", "srm"):
        if cmd == "srm":
            ax.add("rev", 40, "securely erases files")
        rule_rm(ax, args, cwd, from_pipe)
    elif cmd in ("shred", "wipe"):
        ax.add("rev", 85, "irrecoverably overwrites files")
        apply_targets(ax, positional(args), cwd)
    elif cmd == "rmdir":
        ax.floor = 10
    elif cmd == "trash":
        ax.floor = 15
    elif cmd == "git":
        rule_git(ax, args, cwd)
    elif cmd in DB_CLIENTS:
        rule_db(ax, cmd, args, cwd, whole)
    elif cmd == "dd":
        of = next((a[3:] for a in sargs if a.startswith("of=")), None)
        if of and (of.startswith("/dev/") and of not in ("/dev/null", "/dev/zero")):
            ax.add("rev", 95, "writes raw bytes over device %s" % of)
        elif of and os.path.exists(expand_word(of, cwd)[0]):
            ax.add("rev", 55, "overwrites %s with raw bytes" % of)
            ax.fact(kind="overwrite", path=expand_word(of, cwd)[0], reason="Overwrites %s" % of)
        else:
            ax.floor = 40
    elif cmd.startswith("mkfs") or cmd.startswith("newfs") or cmd in ("fdisk", "sfdisk", "parted", "wipefs", "gpart"):
        ax.add("rev", 95, "formats or repartitions a disk")
    elif cmd == "diskutil":
        if sargs and re.match(r"(?i)^(erase|partition|zero|secureErase|reformat|apfs)", sargs[0]) and not sargs[0].lower().startswith("apfslist"):
            ax.add("rev", 95, "erases or repartitions a disk")
        else:
            ax.floor = 10
    elif cmd in ("chmod", "chown", "chgrp", "chflags", "setfacl"):
        chars, longs = short_flags(args)
        ax.add("rev", 20, "changes %s" % ("permissions" if cmd == "chmod" else "ownership"))
        targets = positional(args)[1:] if cmd != "chflags" else positional(args)[1:]
        if "R" in chars or "--recursive" in longs:
            ax.add("breadth", 25, "recursively")
        apply_targets(ax, targets, cwd)
    elif cmd == "find":
        rule_find(ax, args, cwd, depth)
    elif cmd == "rsync":
        chars, longs = short_flags(args)
        pos = [str(p) for p in positional(args, takes_arg=("-e", "--rsh", "--exclude", "--include", "--filter", "-f"))]
        remote = any(re.match(r"^([\w.-]+@)?[\w.-]+:", p) and not p.startswith("/") for p in pos)
        if any(l.startswith("--delete") for l in longs) or "--del" in longs:
            ax.add("rev", 55, "deletes files at the destination that are not in the source")
            if not remote and pos:
                ax.fact(kind="rm", targets=[pos[-1]], recursive=True,
                        reason="rsync --delete may remove files under %s" % pos[-1])
                apply_targets(ax, [pos[-1]], cwd)
        if remote:
            ax.add("scope", 25, "syncs to a remote host")
        if ax.score == 0:
            ax.floor = 20
    elif cmd in ("scp", "sftp", "ssh", "mosh"):
        rule_ssh(ax, cmd, args, cwd, depth)
    elif cmd in ("curl", "wget", "http", "https", "xh", "httpie"):
        rule_curl(ax, cmd, args)
    elif cmd in ("nc", "ncat", "telnet", "ftp"):
        ax.add("scope", 30, "opens a raw network connection")
    elif cmd in DEPLOY_TOOLS:
        rule_deploy(ax, cmd, args)
    elif cmd in ("twine", "gem", "poetry", "flit") and sargs and sargs[0] in ("upload", "push", "publish"):
        rule_publish(ax, cmd, args)
    elif cmd in ("docker", "podman"):
        rule_docker(ax, args)
    elif cmd == "gh":
        rule_gh(ax, args)
    elif cmd in SHELLS:
        if "-c" in sargs and sargs.index("-c") + 1 < len(sargs) and depth < 4:
            inner = assess(sargs[sargs.index("-c") + 1], cwd, depth + 1)
            ax.floor = max(inner.score, 20)
            ax.reasons.extend((inner.score, r) for r in inner.reasons)
            ax.facts.extend(inner.facts)
        elif seg.piped_in and not positional(args):
            ax.add("rev", 70, "runs a script piped in from another command (e.g. curl | sh), unseen")
        else:
            ax.floor = NEUTRAL
    elif cmd == "eval" and depth < 4:
        inner = assess(" ".join(sargs), cwd, depth + 1)
        ax.floor = max(inner.score, NEUTRAL)
        ax.reasons.extend((inner.score, r) for r in inner.reasons)
        ax.facts.extend(inner.facts)
    elif cmd in ("source", "."):
        ax.floor = NEUTRAL
    elif cmd in ("python", "python3", "node", "ruby", "perl", "deno", "bun") and not (
            cmd in ("deno", "bun") and sargs and sargs[0] in ("test", "run", "x", "publish")):
        rule_interpreter(ax, cmd, args, cwd)
    elif cmd in ("npm", "yarn", "pnpm", "bun", "deno", "cargo", "go", "make", "pip", "pip3"):
        rule_pkg(ax, cmd, args)
    elif cmd in TEST_RUNNERS:
        ax.floor = 5
    elif cmd in LINTERS:
        chars, longs = short_flags(args)
        ax.floor = 20 if ({"--fix", "--write"} & longs or "w" in chars) else 10
    elif cmd == "sed" or cmd == "perl":
        chars, longs = short_flags(args)
        if "i" in chars or "--in-place" in longs or any(a.startswith("-i") for a in sargs):
            ax.add("rev", 25, "edits files in place")
            apply_targets(ax, positional(args, takes_arg=("-e", "-f"))[1:], cwd)
        else:
            ax.floor = 0
    elif cmd == "tee":
        chars, longs = short_flags(args)
        op = ">>" if ("a" in chars or "--append" in longs) else ">"
        rule_redirects(ax, [(op, t) for t in positional(args)], cwd)
    elif cmd in ("truncate",):
        ax.add("rev", 50, "truncates files")
        apply_targets(ax, positional(args, takes_arg=("-s", "-r")), cwd)
    elif cmd in ("mv", "cp", "ln", "install"):
        pos = positional(args, takes_arg=("-t", "-S", "--suffix", "-m", "-o", "-g"))
        chars, longs = short_flags(args)
        if len(pos) >= 2:
            dest = expand_word(pos[-1], cwd)[0]
            if os.path.isfile(dest) and cmd != "ln" or (cmd == "ln" and "f" in chars and os.path.lexists(dest)):
                ax.add("rev", 40, "overwrites existing %s" % pos[-1])
                ax.fact(kind="overwrite", path=dest, reason="Overwrites %s" % pos[-1])
            else:
                ax.floor = 25 if cmd == "mv" else 15
            apply_targets(ax, pos if cmd == "mv" else pos[-1:], cwd)
        else:
            ax.floor = NEUTRAL
    elif cmd in ("kill", "pkill", "killall"):
        ax.floor = 35
    elif cmd in ("shutdown", "reboot", "halt", "poweroff"):
        ax.add("rev", 85, "shuts down or restarts the machine")
    elif cmd == "crontab":
        if "-r" in sargs:
            ax.add("rev", 70, "deletes your entire crontab")
        else:
            ax.floor = 10 if "-l" in sargs else NEUTRAL
    elif cmd in ("launchctl", "systemctl", "service"):
        if sargs and sargs[0] in ("list", "status", "print", "is-active", "show", "cat", "list-units"):
            ax.floor = 5
        elif sargs and sargs[0] in ("unload", "remove", "bootout", "stop", "disable", "mask", "kill"):
            ax.add("rev", 40, "stops or removes a system service")
        else:
            ax.floor = 35
    elif cmd in ("tar", "unzip"):
        is_extract = cmd == "unzip" or any(re.match(r"^-?[a-zA-Z]*x", a) for a in sargs[:1])
        ax.floor = 25 if is_extract else 10
    elif cmd == "open":
        ax.floor = 15
    elif cmd == "defaults":
        ax.floor = 5 if sargs and sargs[0] in ("read", "domains", "find", "read-type") else NEUTRAL
    elif cmd in ("for", "case", "select", "function"):
        ax.floor = 0
    elif cmd in SAFE_COMMANDS and cmd not in REFINED:
        ax.floor = 0
    else:
        ax.floor = NEUTRAL


def assess(command, cwd, depth=0, remote=False):
    """Score a full command line. Compound commands score their riskiest part."""
    cwd = cwd or os.getcwd()
    try:
        segs, subs = segments(command)
    except Exception:  # unparseable: never auto-approve
        return Assessment(NEUTRAL, ["could not parse the command"], [])
    total = Assessment(0, [], [])
    parts = []
    for seg in segs:
        words = [str(w) for w in seg.words]
        if words and words[0] in ("cd", "pushd") and len(words) > 1 and not remote:
            cwd = expand_word(words[1], cwd)[0]  # later parts run in the new dir
        parts.append(assess_segment(seg, cwd, command, depth, remote))
    for sub in subs:
        if depth < 4:
            parts.append(assess(sub, cwd, depth + 1, remote))
    for p in parts:
        total.merge(p)
    # order reasons: those from the riskiest part first, then others worth noting
    parts.sort(key=lambda p: -p.score)
    seen, reasons = set(), []
    for p in parts:
        if p.score < 20 and p is not parts[0]:
            continue
        for r in p.reasons:
            if r not in seen:
                seen.add(r)
                reasons.append(r)
    total.reasons = reasons
    return total


# ---------------------------------------------------------------- Write / Edit tools

def assess_file_write(tool, path, cwd):
    """Score a Write/Edit to `path`. Returns None when the path is inside cwd."""
    if not path:
        return None
    path = os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))
    real = os.path.realpath(path)
    cwd_real = os.path.realpath(cwd)
    if _within(real, cwd_real) and not is_sensitive(real):
        return None
    ax = Axes()
    exists = os.path.isfile(real)
    if is_temp(real):
        ax.floor = 15
        return Assessment(15, ["writes to a temp directory (%s)" % path], [])
    if tool == "Write":
        if exists:
            ax.add("rev", 50, "overwrites %s outside the project" % path)
        else:
            ax.add("breadth", 35, "creates a file outside the project: %s" % path)
    else:
        ax.add("rev", 40, "edits %s outside the project" % path)
    if is_sensitive(real):
        ax.add("breadth", 35, "%s is a sensitive file (shell, SSH, cloud or Claude config)" % path)
    if exists:
        ax.fact(kind="overwrite", path=real, reason="Changes %s" % path)
    reasons = [r for _, r in sorted(ax.reasons, key=lambda x: -x[0])]
    return Assessment(ax.score, reasons, ax.facts)
