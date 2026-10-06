"""Deprecated-API rules: load data/rules.json and scan edits against it."""
import fnmatch
import json
import os
import re

import semver
import versions as V

RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "rules.json")
_FLAGS = {"i": re.I, "m": re.M, "s": re.S}


def load_rules(path=None):
    path = path or os.environ.get("DOCS_PIN_RULES") or RULES_PATH
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rules = []
    for rule in data.get("rules", []):
        flags = 0
        for ch in rule.get("flags", ""):
            flags |= _FLAGS.get(ch, 0)
        try:
            rule = dict(rule, _rx=re.compile(rule["pattern"], flags),
                        _ctx=re.compile(rule["context"]) if rule.get("context") else None)
        except (re.error, KeyError):
            continue  # a broken rule must not disable the others
        rules.append(rule)
    return rules


def _glob_match(path, cwd, globs):
    if not globs:
        return True
    p = os.path.abspath(path)
    rel = os.path.relpath(p, cwd) if cwd and p.startswith(os.path.abspath(cwd) + os.sep) else p
    rel = rel.replace(os.sep, "/")
    base = os.path.basename(rel)
    return any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(base, g) for g in globs)


def package_matches(rule, deps):
    """(matched, version) - the package is present at a version inside rule['range']."""
    rec = deps.get(rule.get("package"))
    if rec is None:
        return False, None
    version = V.effective_version(rec)
    rng = rule.get("range") or "*"
    if rng.strip() == "*":
        return True, version
    return semver.satisfies(version, rng), version


def scan(cwd, file_path, new_text, old_text="", deps=None, rules=None, file_text=""):
    """Return hits for patterns that new_text introduces (more matches than old_text).

    file_text is the rest of the file, used only for a rule's `context` check
    (e.g. only flag `.dict()` in files that mention pydantic).
    """
    deps = V.detect(cwd)["deps"] if deps is None else deps
    rules = load_rules() if rules is None else rules
    hits = []
    for rule in rules:
        if not _glob_match(file_path or "", cwd, rule.get("globs")):
            continue
        ok, version = package_matches(rule, deps)
        if not ok:
            continue
        new_n = len(rule["_rx"].findall(new_text or ""))
        if new_n == 0 or new_n <= len(rule["_rx"].findall(old_text or "")):
            continue
        if rule["_ctx"] and not (rule["_ctx"].search(new_text or "") or rule["_ctx"].search(file_text or "")):
            continue
        match = rule["_rx"].search(new_text).group(0)
        hits.append({"id": rule["id"], "package": rule["package"], "version": version,
                     "severity": rule.get("severity", "warn"), "message": rule["message"],
                     "match": match.strip()[:80]})
    return hits


def format_hits(hits, file_path):
    lines = ["docs-pin: %s uses APIs that do not match the installed versions:" % os.path.basename(file_path or "?")]
    for h in hits:
        name = h["package"].split(":", 1)[-1]
        lines.append("- [%s] `%s` (%s %s): %s" % (h["severity"], h["match"], name, h["version"] or "present",
                                                  h["message"]))
    return "\n".join(lines)
