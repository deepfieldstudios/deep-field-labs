#!/usr/bin/env python3
"""diff-digest: regroup a big diff by intent and risk so a reviewer reads the right 10%.

Input (pick one):
    digest.py                      merge-base(main|master) .. working tree (commits + uncommitted)
    digest.py A..B | <rev>         any git diff range
    digest.py --staged             the index
    digest.py --diff-file F        a unified diff file ('-' = stdin)
Options: --repo DIR, --json, --top N

Every hunk lands in one group:
    core-logic, tests, mechanical, config/build, docs, generated, deletions-only
and gets a 0-10 risk score with the reasons spelled out.
Standard library only.
"""
import argparse
import json
import os
import re
import subprocess
import sys

GROUPS = ["core-logic", "tests", "config/build", "deletions-only", "mechanical", "generated", "docs"]
SAFE_GROUPS = {"mechanical", "generated", "docs"}
SAFE_MAX_RISK = 2

# ---------------------------------------------------------------- git

def git(repo, *args, timeout=30):
    p = subprocess.run(["git", "-C", repo, "-c", "core.quotepath=false"] + list(args),
                       capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), p.stderr.strip()))
    return p.stdout


def default_base(repo):
    """Merge-base of HEAD with the main branch, trying the usual names."""
    for ref in ("main", "master", "origin/main", "origin/master", "origin/HEAD"):
        try:
            git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
            return git(repo, "merge-base", "HEAD", ref).strip(), ref
        except (RuntimeError, subprocess.TimeoutExpired):
            continue
    return "HEAD", None


DIFF_FLAGS = ["diff", "-M", "--no-color", "--no-ext-diff", "--unified=3"]


def get_diff(args):
    if args.diff_file:
        if args.diff_file == "-":
            return sys.stdin.read(), "stdin"
        with open(args.diff_file, encoding="utf-8", errors="replace") as f:
            return f.read(), args.diff_file
    repo = args.repo
    if args.staged:
        return git(repo, *(DIFF_FLAGS + ["--cached"])), "staged changes"
    if args.range:
        return git(repo, *(DIFF_FLAGS + args.range)), " ".join(args.range)
    base, ref = default_base(repo)
    label = ("merge-base(%s)..working tree" % ref) if ref else "HEAD..working tree"
    return git(repo, *(DIFF_FLAGS + [base])), label


# ---------------------------------------------------------------- parsing

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")


def _strip_prefix(p):
    p = p.strip()
    if p.startswith('"') and p.endswith('"'):
        p = p[1:-1]
    if p == "/dev/null":
        return None
    return p[2:] if p[:2] in ("a/", "b/") else p


def parse_diff(text):
    """Unified git diff -> list of file dicts with hunks (lines kept with their +/-/space prefix).

    Hunk bodies are consumed by the line counts in the @@ header, so content lines that happen
    to look like headers ("--- x", "diff --git") are never misread."""
    files, cur, hunk = [], None, None
    old_left = new_left = 0
    for line in text.splitlines():
        if hunk is not None and (old_left > 0 or new_left > 0):
            tag = line[:1]
            if tag == "\\":
                continue                          # "\ No newline at end of file"
            if tag == "-":
                old_left -= 1
            elif tag == "+":
                new_left -= 1
            else:                                 # context (an empty line is a blank context line)
                old_left -= 1
                new_left -= 1
                line = " " + line[1:] if tag == " " else " " + line
            hunk["lines"].append(line)
            continue
        if line.startswith("diff --git "):
            m = re.match(r'diff --git ("?a/.*?"?) ("?b/.*"?)$', line)
            a, b = (_strip_prefix(m.group(1)), _strip_prefix(m.group(2))) if m else (None, None)
            cur = {"path": b or a, "old_path": a, "status": "modified", "similarity": None,
                   "binary": False, "hunks": []}
            files.append(cur)
            hunk = None
            continue
        if cur is None:
            continue
        m = HUNK_RE.match(line)
        if m:
            hunk = {"old_start": int(m.group(1)), "new_start": int(m.group(3)),
                    "header": m.group(5).strip(), "lines": []}
            old_left = int(m.group(2)) if m.group(2) is not None else 1
            new_left = int(m.group(4)) if m.group(4) is not None else 1
            cur["hunks"].append(hunk)
        elif line.startswith("new file mode"):
            cur["status"] = "added"
        elif line.startswith("deleted file mode"):
            cur["status"] = "deleted"
        elif line.startswith("similarity index"):
            cur["similarity"] = int(re.sub(r"\D", "", line) or 0)
        elif line.startswith("rename from "):
            cur["old_path"], cur["status"] = line[12:], "renamed"
        elif line.startswith("rename to "):
            cur["path"] = line[10:]
        elif line.startswith(("Binary files", "GIT binary patch")):
            cur["binary"] = True
        elif line.startswith("--- "):
            p = _strip_prefix(line[4:])
            if p is None:
                cur["status"] = "added"
            else:
                cur["old_path"] = p
        elif line.startswith("+++ "):
            p = _strip_prefix(line[4:])
            if p is None:
                cur["status"] = "deleted"
            else:
                cur["path"] = p
    return files


# ---------------------------------------------------------------- path classification

LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "cargo.lock",
             "gemfile.lock", "composer.lock", "go.sum", "uv.lock", "pipfile.lock", "bun.lockb",
             "npm-shrinkwrap.json", "flake.lock", "podfile.lock", "mix.lock"}
GENERATED_MARKERS = ("@generated", "do not edit", "auto-generated", "autogenerated",
                     "generated by django", "code generated by", "this file was automatically generated")


def base(path):
    return os.path.basename(path or "").lower()


def is_generated_path(path):
    p, b = (path or "").lower(), base(path)
    return (b in LOCKFILES or b.endswith((".min.js", ".min.css", ".map", ".snap", ".pb.go", "_pb2.py",
                                          ".designer.cs", ".g.dart"))
            or re.search(r"(^|/)(dist|build|out|vendor|node_modules|__snapshots__|\.next)/", p) is not None)


def is_config_path(path):
    p, b = (path or "").lower(), base(path)
    return (b in {"package.json", "dockerfile", "makefile", "pom.xml", "tsconfig.json", "setup.cfg",
                  "setup.py", "go.mod", "cargo.toml", "pyproject.toml", "gemfile", ".gitignore",
                  ".dockerignore", ".editorconfig", "procfile", "justfile", "build.gradle",
                  "docker-compose.yml", ".npmrc", ".nvmrc", ".env.example"}
            or b.startswith(("dockerfile", "requirements", ".eslintrc", ".prettierrc", "jest.config",
                             "vite.config", "webpack.config", "babel.config", "tox.ini"))
            or b.endswith((".toml", ".yml", ".yaml", ".ini", ".cfg", ".gradle", ".cmake", ".bazel",
                           ".tf", ".tfvars", ".mk"))
            or p.startswith((".github/", ".gitlab/", ".circleci/", ".buildkite/")) or "/.github/" in p)


def is_docs_path(path):
    p, b = (path or "").lower(), base(path)
    return (b.endswith((".md", ".mdx", ".rst", ".txt", ".adoc"))
            or b in {"license", "licence", "authors", "changelog", "notice", "contributing"}
            or re.search(r"(^|/)docs?/", p) is not None)


def is_test_path(path):
    p, b = (path or "").lower(), base(path)
    return (re.search(r"(^|/)(tests?|__tests__|spec|specs|testing)/", p) is not None
            or b.startswith("test_") or re.search(r"(_test|_spec)\.\w+$", b) is not None
            or re.search(r"\.(test|spec)\.\w+$", b) is not None or b.startswith("conftest"))


# ---------------------------------------------------------------- hunk content analysis

IMPORT_RE = re.compile(r"^\s*(import\s|from\s+\S+\s+import\s|#include\s|using\s+[\w.]+;|use\s+[\w:{}]+|"
                       r"require\(|const\s+\w+\s*=\s*require\(|@import\s|export\s+\*\s+from\s|"
                       r"export\s+\{[^}]*\}\s+from\s|package\s+[\w.]+;?$)")
GO_IMPORT_LINE = re.compile(r'^\s*(\w+\s+)?"[\w./-]+"\s*$')
TOKEN_RE = re.compile(r"[A-Za-z_]\w*|\d+|\S")
IDENT_RE = re.compile(r"^[A-Za-z_]\w*$")
KEYWORDS = {"if", "else", "elif", "for", "while", "return", "def", "class", "function", "func", "import",
            "from", "try", "except", "catch", "finally", "throw", "raise", "new", "const", "let", "var",
            "true", "false", "none", "null", "and", "or", "not", "in", "is", "switch", "case"}


def changed(hunk):
    minus = [l[1:] for l in hunk["lines"] if l.startswith("-")]
    plus = [l[1:] for l in hunk["lines"] if l.startswith("+")]
    return minus, plus


def is_whitespace_only(minus, plus):
    squash = lambda ls: re.sub(r"\s+", "", "".join(ls))
    return bool(minus or plus) and squash(minus) == squash(plus)


def is_import_only(minus, plus):
    lines = [l for l in minus + plus if l.strip()]
    return bool(lines) and all(IMPORT_RE.match(l) or GO_IMPORT_LINE.match(l)
                               or l.strip() in ("(", ")", "import (") for l in lines)


def rename_substitution(minus, plus):
    """If every -/+ line pair differs only by one consistent identifier swap, return (old, new)."""
    if not minus or len(minus) != len(plus):
        return None
    subs = set()
    for a, b in zip(minus, plus):
        ta, tb = TOKEN_RE.findall(a), TOKEN_RE.findall(b)
        if len(ta) != len(tb):
            return None
        for x, y in zip(ta, tb):
            if x != y:
                if not (IDENT_RE.match(x) and IDENT_RE.match(y)) or x.lower() in KEYWORDS or y.lower() in KEYWORDS:
                    return None
                subs.add((x, y))
        if len(subs) > 1:
            return None
    return next(iter(subs)) if len(subs) == 1 else None


def has_generated_marker(lines):
    head = " ".join(lines[:15]).lower()
    return any(m in head for m in GENERATED_MARKERS)


def classify(file, hunk, minus, plus):
    """-> (group, kind) where kind is a short human label."""
    path = file["path"] or file["old_path"]
    if hunk.get("rename_only"):
        return "mechanical", "rename %s -> %s" % (file["old_path"], file["path"])
    if file["binary"]:
        return ("generated", "binary") if is_generated_path(path) else ("core-logic", "binary file")
    if is_generated_path(path):
        return "generated", "lockfile" if base(path) in LOCKFILES else "generated path"
    if has_generated_marker(plus if plus else minus):
        return "generated", "generated marker"
    if is_whitespace_only(minus, plus):
        return "mechanical", "whitespace/formatting only"
    if is_import_only(minus, plus):
        return "mechanical", "imports only"
    sub = rename_substitution(minus, plus)
    if sub:
        return "mechanical", "identifier rename %s -> %s" % sub
    if is_config_path(path):
        return "config/build", "config"
    if is_docs_path(path):
        return "docs", "docs"
    if is_test_path(path):
        return "tests", "tests"
    if minus and not plus:
        return "deletions-only", "%d lines removed" % len(minus)
    return "core-logic", "logic"


# ---------------------------------------------------------------- risk

SENSITIVE_RE = re.compile(
    r"auth|login|logout|passw|credential|secret|token|api[_-]?key|private[_-]?key|crypt|cipher|hmac|"
    r"hashlib|bcrypt|jwt|oauth|session|cookie|csrf|permission|privilege|\broles?\b|\badmin|\bacl\b|sudo|"
    r"chmod|payment|charge|stripe|billing|invoice|refund|credit.?card|card.?number|checkout|\bsql\b|"
    r"select\s.+\sfrom|insert\s+into|delete\s+from|execute\(|\.raw\(|cursor\(|eval\(|exec\(|"
    r"subprocess|shell\s*=\s*true|verify\s*=\s*false|sanitiz|unescape|innerhtml", re.I)
# Markup and stylesheets are full of words like "card", "role" and ":" - only real secrets count there.
MARKUP_SENSITIVE_RE = re.compile(r"passw|secret|api[_-]?key|private[_-]?key|sk_live|access[_-]?token", re.I)
MARKUP_EXT = {".html", ".htm", ".css", ".scss", ".sass", ".less", ".svg", ".xml", ".xhtml"}
CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rb", ".java", ".kt", ".rs",
            ".php", ".cs", ".c", ".cc", ".cpp", ".h", ".hpp", ".swift", ".scala", ".ex", ".exs", ".sh",
            ".vue", ".svelte", ".dart", ".lua", ".pl", ".sql"}
ERROR_RE = re.compile(r"^\s*(except\b|catch\b|\}\s*catch\b|rescue\b|finally\b|try\s*[:{]|try$)|"
                      r"if\s+err\s*!=\s*nil|\.catch\(|recover\(\)", re.I)
COND_RE = re.compile(r"(^|\W)(if|elif|else|switch|case|while|unless|when)(\W|$)|&&|\|\||[!=<>]=|\s[<>]\s|\?.+:")
API_RE = re.compile(r"^\s*(export\s+)?(public\s+|async\s+)*(def|function|func|class|interface|fn|pub\s+fn)\s+([A-Za-z]\w*)")
TESTFN_RE = re.compile(r"^\s*(def\s+test|it\(|test\(|describe\(|func\s+Test|@Test|assert|expect\()")


def risk(file, group, minus, plus, tested_stems):
    """-> (score 0..10, [reasons])"""
    reasons, score = [], 0
    n = len(minus) + len(plus)
    if group in SAFE_GROUPS:
        if n > 400 and group == "mechanical":
            return 1, ["very large mechanical hunk (%d lines): spot-check it" % n]
        return 0, []
    if file.get("binary"):
        return 0, ["binary file: not reviewable as text"]
    ext = os.path.splitext(base(file["path"] or file["old_path"]))[1]
    markup = ext in MARKUP_EXT
    changed_lines = minus + plus
    kw = MARKUP_SENSITIVE_RE if markup else SENSITIVE_RE
    hits = sorted({m.group(0).lower() for l in changed_lines for m in [kw.search(l)] if m})
    if hits:
        score += 3
        reasons.append("touches sensitive code (%s)" % ", ".join(hits[:4]))
    err_minus = sum(1 for l in minus if ERROR_RE.search(l))
    err_plus = sum(1 for l in plus if ERROR_RE.search(l))
    if err_minus > err_plus:
        score += 3
        reasons.append("error handling removed (%d except/catch lines deleted)" % (err_minus - err_plus))
    if not markup and any(COND_RE.search(l) for l in changed_lines):
        score += 2
        reasons.append("conditionals changed")
    api_minus = {m.group(4): l.strip() for l in minus for m in [API_RE.match(l)] if m and not m.group(4).startswith("_")}
    api_plus = {m.group(4): l.strip() for l in plus for m in [API_RE.match(l)] if m and not m.group(4).startswith("_")}
    sig_changed = [k for k in api_minus if api_plus.get(k) != api_minus[k]]
    if sig_changed:
        score += 2
        reasons.append("public API signature changed/removed: %s" % ", ".join(sig_changed[:3]))
    if group == "tests":
        t_minus = sum(1 for l in minus if TESTFN_RE.search(l))
        t_plus = sum(1 for l in plus if TESTFN_RE.search(l))
        if t_minus > t_plus:
            score += 3
            reasons.append("tests or assertions deleted (%d)" % (t_minus - t_plus))
    elif group in ("core-logic", "deletions-only"):
        stem = os.path.splitext(base(file["path"] or file["old_path"]))[0]
        if ext in CODE_EXT and stem and stem not in tested_stems:
            score += 1
            reasons.append("no matching test change for %s" % base(file["path"] or file["old_path"]))
    if n > 150:
        score += 2
        reasons.append("large hunk (%d changed lines)" % n)
    elif n > 50:
        score += 1
        reasons.append("large hunk (%d changed lines)" % n)
    return min(10, score), reasons


def test_stems(files):
    """Stems that a test file in this diff plausibly covers (test_auth.py, auth.test.ts -> 'auth')."""
    stems = set()
    for f in files:
        p = f["path"] or f["old_path"]
        if not is_test_path(p):
            continue
        s = os.path.splitext(base(p))[0]
        s = re.sub(r"^(test_|tests_)|(_test|_spec|\.test|\.spec)$", "", s)
        stems.add(s)
    return stems


# ---------------------------------------------------------------- digest

def first_change_line(hunk):
    new, old = hunk["new_start"], hunk["old_start"]
    for l in hunk["lines"]:
        if l.startswith(("+", "-")):
            return new, old
        new += 1
        old += 1
    return hunk["new_start"], hunk["old_start"]


def digest(files, source="diff"):
    tested = test_stems(files)
    hunks = []
    for f in files:
        hs = list(f["hunks"])
        if f["status"] == "renamed":
            hs.insert(0, {"rename_only": True, "new_start": 1, "old_start": 1, "header": "", "lines": []})
        if not hs and (f["binary"] or f["status"] in ("added", "deleted")):
            hs.append({"new_start": 1, "old_start": 1, "header": "", "lines": []})
        for h in hs:
            minus, plus = changed(h)
            group, kind = classify(f, h, minus, plus)
            score, reasons = risk(f, group, minus, plus, tested)
            line, old_line = first_change_line(h)
            hunks.append({
                "id": "h%d" % (len(hunks) + 1),
                "file": f["path"] or f["old_path"], "old_file": f["old_path"], "status": f["status"],
                "line": line if f["status"] != "deleted" else old_line, "old_line": old_line,
                "group": group, "kind": kind, "additions": len(plus), "deletions": len(minus),
                "risk": score, "reasons": reasons, "header": h.get("header", ""),
            })
    groups = {}
    for g in GROUPS:
        hs = [h for h in hunks if h["group"] == g]
        if hs:
            groups[g] = {"files": sorted({h["file"] for h in hs}), "hunks": len(hs),
                         "additions": sum(h["additions"] for h in hs),
                         "deletions": sum(h["deletions"] for h in hs),
                         "max_risk": max(h["risk"] for h in hs)}
    ranked = sorted((h for h in hunks if h["risk"] > 0),
                    key=lambda h: (-h["risk"], -(h["additions"] + h["deletions"]), h["file"], h["line"]))
    read_first = [h["id"] for h in ranked if h["risk"] >= 3][:10] or [h["id"] for h in ranked][:5]
    batch = [h["id"] for h in hunks if h["group"] in SAFE_GROUPS and h["risk"] <= SAFE_MAX_RISK]
    return {
        "schema": "diff-digest/v1", "source": source,
        "stats": {"files": len(files), "hunks": len(hunks),
                  "additions": sum(h["additions"] for h in hunks),
                  "deletions": sum(h["deletions"] for h in hunks)},
        "groups": groups, "hunks": hunks, "read_first": read_first, "batch_approve": batch,
    }


def markdown(d):
    by_id = {h["id"]: h for h in d["hunks"]}
    s = d["stats"]
    out = ["# Diff digest: %s" % d["source"], "",
           "%d files, %d hunks, +%d / -%d" % (s["files"], s["hunks"], s["additions"], s["deletions"]), "",
           "| Group | Files | Hunks | +/- | Max risk |", "|---|---:|---:|---:|---:|"]
    for g, v in d["groups"].items():
        out.append("| %s | %d | %d | +%d / -%d | %d |" % (g, len(v["files"]), v["hunks"], v["additions"],
                                                          v["deletions"], v["max_risk"]))
    out += ["", "## Read these first", ""]
    if not d["read_first"]:
        out.append("Nothing scored above zero.")
    for i, hid in enumerate(d["read_first"], 1):
        h = by_id[hid]
        out.append("%d. `%s:%d` risk **%d** (%s, +%d/-%d)%s" % (
            i, h["file"], h["line"], h["risk"], h["group"], h["additions"], h["deletions"],
            (" in `%s`" % h["header"][:60]) if h["header"] else ""))
        for r in h["reasons"]:
            out.append("   - %s" % r)
    out += ["", "## Groups", ""]
    for g, v in d["groups"].items():
        out.append("<details><summary><b>%s</b>: %d files, %d hunks, +%d/-%d, max risk %d</summary>" % (
            g, len(v["files"]), v["hunks"], v["additions"], v["deletions"], v["max_risk"]))
        out.append("")
        for h in (x for x in d["hunks"] if x["group"] == g):
            out.append("- `%s:%d` %s, +%d/-%d, risk %d" % (h["file"], h["line"], h["kind"], h["additions"],
                                                         h["deletions"], h["risk"]))
        out += ["", "</details>", ""]
    out += ["## Safe to batch-approve", ""]
    if not d["batch_approve"]:
        out.append("Nothing qualifies (mechanical, generated or docs with risk <= %d)." % SAFE_MAX_RISK)
    per_file = {}
    for hid in d["batch_approve"]:
        h = by_id[hid]
        per_file.setdefault((h["file"], h["group"]), []).append(h["kind"])
    for (f, g), kinds in per_file.items():
        out.append("- `%s` (%s): %s" % (f, g, ", ".join(sorted(set(kinds)))))
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("range", nargs="*", help="git diff range/revisions (default: merge-base with main)")
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--diff-file")
    ap.add_argument("--repo", default=os.getcwd())
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        text, source = get_diff(a)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        print("diff-digest: %s" % e, file=sys.stderr)
        return 1
    d = digest(parse_diff(text), source)
    sys.stdout.write(json.dumps(d, indent=2) + "\n" if a.json else markdown(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
