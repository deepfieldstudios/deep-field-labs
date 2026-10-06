"""Shared logic for the receipts plugin (stdlib only).

Three jobs:
  1. classify a Bash command (test / build / lint / deploy / other) and decide
     whether its run failed;
  2. read the session transcript into an ordered timeline of runs and code edits;
  3. extract "done" claims from the final reply and give each a verdict.
"""
import datetime as _dt
import json
import math
import os
import re

KINDS = ("test", "build", "lint", "deploy")
VERDICTS = ("VERIFIED", "STALE", "FAILED", "UNBACKED")

# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def state_dir(cwd=None):
    """<cwd>/.claude/receipts, or $RECEIPTS_HOME when set (tests)."""
    home = os.environ.get("RECEIPTS_HOME")
    if home:
        return home
    return os.path.join(cwd or os.getcwd(), ".claude", "receipts")


def safe_session(session_id):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_id or "unknown"))[:120] or "unknown"


def ledger_path(cwd, session_id):
    return os.path.join(state_dir(cwd), safe_session(session_id) + ".jsonl")


def receipt_path(cwd, session_id):
    return os.path.join(state_dir(cwd), "receipt-" + safe_session(session_id) + ".md")


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_ts(value):
    """ISO timestamp -> epoch seconds, or None."""
    if not value or not isinstance(value, str):
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def read_ledger(cwd, session_id):
    entries = []
    try:
        with open(ledger_path(cwd, session_id), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return entries


# --------------------------------------------------------------------------
# Command classification
# --------------------------------------------------------------------------

# Segments starting with these never count as a test/build/... run
# (e.g. `grep pytest`, `cat Makefile`, `echo "tests pass"`).
_INERT_LEADERS = {
    "echo", "printf", "grep", "egrep", "fgrep", "rg", "ag", "cat", "less", "more",
    "head", "tail", "which", "type", "command", "man", "find", "ls", "wc", "sed",
    "awk", "jq", "open", "code", "vim", "vi", "nano", "cd", "export", "touch",
    "mkdir", "cp", "mv", "rm", "git", "true", "false", "sleep", "tee", "xargs",
}

_INSTALL_RE = re.compile(
    r"\b(pip3?|uv\s+pip|npm|pnpm|yarn|bun|uv|poetry|cargo|go|brew|apt(-get)?|gem|bundle)\s+"
    r"(install|add|i|get|ci|update|upgrade|remove|uninstall)\b"
)

_RULES = [
    # Order matters: the first kind that matches a segment wins.
    ("deploy", [
        r"\bwrangler\s+(pages\s+)?(deploy|publish)\b",
        r"\bvercel(\s+(deploy|--prod)\b|\s*$)",
        r"\bnetlify\s+deploy\b",
        r"\b(fly|flyctl)\s+deploy\b",
        r"\bgit\s+push\b",
        r"\bkubectl\s+apply\b",
        r"\bterraform\s+apply\b",
        r"\bfirebase\s+deploy\b",
        r"\b(npm|pnpm|yarn)\s+publish\b",
    ]),
    ("test", [
        r"\bpytest\b",
        r"\bpython[\d.]*\s+(-\S+\s+)*-m\s+(pytest|unittest)\b",
        r"\b(jest|vitest|mocha|rspec|phpunit|tox|nox)\b",
        r"\b(npm|pnpm|yarn|bun)\s+(run\s+)?test\b",
        r"\bnpm\s+t\b",
        r"\b(go|deno|bun)\s+test\b",
        r"\bcargo\s+(test|nextest)\b",
        r"\bmvnw?\b.*\b(test|verify)\b",
        r"\b(\./)?gradlew?\b.*\btest\b",
        r"\bmake\s+(\S+\s+)*(test|check)\b",
        r"\b(rails|rake|bundle\s+exec\s+rake)\s+(test|spec)\b",
    ]),
    ("lint", [
        r"\btsc\b.*--noEmit\b",
        r"\b(eslint|ruff|flake8|mypy|pyright|pylint|golangci-lint|stylelint|biome|shellcheck|rubocop)\b",
        r"\b(npm|pnpm|yarn|bun)\s+(run\s+)?(lint|typecheck|type-check|check-types|tsc)\b",
        r"\bcargo\s+(clippy|check|fmt\s+--check)\b",
        r"\bgo\s+vet\b",
        r"\b(prettier|black)\b.*--check\b",
    ]),
    ("build", [
        r"\b(npm|pnpm|yarn|bun)\s+(run\s+)?build\b",
        r"\btsc\b",
        r"\bcargo\s+build\b",
        r"\bgo\s+build\b",
        r"(^|\s)g?make(\s|$)",
        r"\b(vite|next|astro|nuxt|remix|svelte-kit)\s+build\b",
        r"\bwebpack\b",
        r"\b(\./)?gradlew?\b.*\b(build|assemble)\b",
        r"\bmvnw?\b.*\b(package|install|compile)\b",
        r"\bdocker\s+(compose\s+)?build\b",
        r"\bpython[\d.]*\s+-m\s+build\b",
        r"\bhugo(\s|$)",
    ]),
]
_COMPILED = [(kind, [re.compile(p) for p in pats]) for kind, pats in _RULES]

_SPLIT_RE = re.compile(r"&&|\|\||;|\||\n")
_ENV_PREFIX_RE = re.compile(r"^(\s*[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+")
_WRAPPER_RE = re.compile(r"^\s*(sudo|time|nice|env|exec|command|caffeinate)\s+")


def _segment_kind(segment):
    seg = _ENV_PREFIX_RE.sub("", segment.strip())
    while True:
        stripped = _WRAPPER_RE.sub("", seg)
        if stripped == seg:
            break
        seg = stripped
    if not seg:
        return "other"
    first = seg.split()[0].split("/")[-1]
    # `git push` is the one git verb we care about.
    if first in _INERT_LEADERS and not (first == "git" and re.search(r"\bgit\s+push\b", seg)):
        return "other"
    if _INSTALL_RE.search(seg):
        return "other"
    for kind, patterns in _COMPILED:
        if any(p.search(seg) for p in patterns):
            return kind
    return "other"


def classify(command):
    """Return (primary_kind, kinds) for a shell command line.

    A compound command (`npm run build && npm test`) can prove several kinds,
    so all of them are returned; the primary kind is the first one found.
    """
    kinds = []
    for segment in _SPLIT_RE.split(command or ""):
        kind = _segment_kind(segment)
        if kind != "other" and kind not in kinds:
            kinds.append(kind)
    return (kinds[0] if kinds else "other"), kinds


# --------------------------------------------------------------------------
# Failure detection
# --------------------------------------------------------------------------

_FAIL_PATTERNS = [re.compile(p, re.M) for p in (
    r"\b[1-9]\d*\s+(failed|failing|failures?|errors?)\b",   # pytest/jest/mocha/tsc summaries
    r"\bFAILED\b",                                            # unittest, pytest node ids
    r"^\s*(FAIL|ERROR)\b",                                    # jest/vitest file lines
    r"^\s*(Error|error|ERROR)(\[\w+\])?:",                    # node, rustc, generic
    r"\berror TS\d+\b",                                       # tsc
    r"\bnpm ERR!",
    r"\bERR_PNPM_\w+",
    r"\bELIFECYCLE\b",
    r"Traceback \(most recent call last\)",
    r"\b[Bb]uild failed\b",
    r"\bCommand failed\b",
    r"^fatal:",                                              # git
    r"!\s*\[rejected\]",                                     # git push
    r"\bpanicked at\b",                                      # rust/go
    r"\bexit (code|status) [1-9]\d*\b",
)]
_EXIT_LINE_RE = re.compile(r"^\s*Exit code:?\s*(\d+)", re.M)
_EXIT_KEYS = ("exit_code", "exitCode", "returncode", "returnCode", "code", "status")


def _as_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_as_text(v) for v in value)
    if isinstance(value, dict):
        if "text" in value:
            return _as_text(value.get("text"))
        return "\n".join(_as_text(value.get(k)) for k in ("stdout", "stderr", "output", "content") if k in value)
    return str(value)


def output_text(tool_response):
    """Flatten any tool_response shape into one string."""
    return _as_text(tool_response)


def output_fails(text):
    """True when output text looks like a failed run."""
    return any(p.search(text or "") for p in _FAIL_PATTERNS)


def detect_status(tool_response, is_error=None):
    """Return dict(exit_code, failed, source) from whatever the tool returned.

    Priority: explicit exit code > is_error/interrupted flags > output heuristics.
    """
    exit_code = None
    if isinstance(tool_response, dict):
        for key in _EXIT_KEYS:
            val = tool_response.get(key)
            if isinstance(val, bool):
                continue
            if isinstance(val, int):
                exit_code = val
                break
            if isinstance(val, str) and val.strip().lstrip("-").isdigit():
                exit_code = int(val.strip())
                break
        if is_error is None and isinstance(tool_response.get("is_error"), bool):
            is_error = tool_response.get("is_error")
        if tool_response.get("interrupted") is True:
            return {"exit_code": exit_code, "failed": True, "source": "interrupted"}
    text = output_text(tool_response)
    if exit_code is None:
        m = _EXIT_LINE_RE.search(text)
        if m:
            exit_code = int(m.group(1))
    if exit_code is not None:
        return {"exit_code": exit_code, "failed": exit_code != 0, "source": "exit_code"}
    if is_error is True:
        return {"exit_code": None, "failed": True, "source": "is_error"}
    if output_fails(text):
        return {"exit_code": None, "failed": True, "source": "output"}
    return {"exit_code": None, "failed": False, "source": "assumed"}


def tail(text, limit=400):
    text = (text or "").strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def make_entry(command, tool_response, tool_use_id=None, is_error=None, ts=None):
    """Build one ledger row."""
    kind, kinds = classify(command)
    status = detect_status(tool_response, is_error)
    return {
        "ts": ts or now_iso(),
        "tool_use_id": tool_use_id,
        "command": command,
        "kind": kind,
        "kinds": kinds,
        "exit_code": status["exit_code"],
        "failed": status["failed"],
        "status_source": status["source"],
        "tail": tail(output_text(tool_response)),
    }


# --------------------------------------------------------------------------
# Transcript -> timeline
# --------------------------------------------------------------------------

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# Editing prose does not invalidate a test or build run.
_NON_CODE_EXT = {".md", ".markdown", ".txt", ".rst", ".adoc"}


def is_code_path(path):
    if not path:
        return True
    return os.path.splitext(str(path))[1].lower() not in _NON_CODE_EXT


def load_transcript(path):
    lines = []
    if not path:
        return lines
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    lines.append(obj)
    except OSError:
        pass
    return lines


def _content(line):
    msg = line.get("message")
    if isinstance(msg, dict):
        return msg.get("content")
    return None


def _is_real_prompt(line):
    """A user line typed by the human (not a tool_result carrier)."""
    if line.get("type") != "user" or line.get("isMeta"):
        return False
    content = _content(line)
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    return False


def build_timeline(lines):
    """Return (bash_runs, edits, final_text) from transcript lines.

    Positions are line_index + block_index/1000 so ordering survives
    multi-block messages.
    """
    results = {}
    for line in lines:
        content = _content(line)
        if line.get("type") == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    results[block.get("tool_use_id")] = {
                        "is_error": block.get("is_error"),
                        "text": _as_text(block.get("content")),
                        "raw": line.get("toolUseResult"),
                    }

    runs, edits = [], []
    last_prompt = -1
    for i, line in enumerate(lines):
        if _is_real_prompt(line):
            last_prompt = i
        content = _content(line)
        if line.get("type") != "assistant" or not isinstance(content, list):
            continue
        for j, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            inp = block.get("input") if isinstance(block.get("input"), dict) else {}
            pos = i + j / 1000.0
            res = results.get(block.get("id"), {})
            if name == "Bash":
                runs.append({"pos": pos, "ts": line.get("timestamp"), "tool_use_id": block.get("id"),
                             "command": inp.get("command", ""), "result": res})
            elif name in EDIT_TOOLS:
                path = inp.get("file_path") or inp.get("notebook_path")
                if res.get("is_error") is True:
                    continue  # a rejected/failed edit changed nothing
                if is_code_path(path):
                    edits.append({"pos": pos, "ts": line.get("timestamp"), "file_path": path, "tool": name})

    # Final reply = assistant text after the last tool call in this turn.
    buf = []
    for line in lines[last_prompt + 1:]:
        content = _content(line)
        if isinstance(content, str) and line.get("type") == "assistant":
            buf.append(content)
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype in ("tool_use", "tool_result"):
                buf = []
            elif btype == "text" and line.get("type") == "assistant":
                buf.append(block.get("text") or "")
    return runs, edits, "\n".join(buf).strip()


def merge_runs(transcript_runs, ledger, lines):
    """Combine transcript Bash calls with ledger rows into one ordered run list.

    Ledger rows are matched by tool_use_id, then by identical command text.
    Transcript calls with no ledger row (e.g. a failing command for which
    PostToolUse never fired) are classified from the transcript result.
    Ledger rows absent from the transcript are placed by timestamp.
    """
    unused = list(ledger)
    by_id = {e.get("tool_use_id"): e for e in ledger if e.get("tool_use_id")}
    merged = []
    for run in transcript_runs:
        entry = by_id.get(run["tool_use_id"]) if run["tool_use_id"] else None
        if entry is None:
            entry = next((e for e in unused if e.get("command") == run["command"]
                          and not e.get("tool_use_id")), None)
        if entry is not None:
            if entry in unused:
                unused.remove(entry)
            row = dict(entry)
        else:
            res = run["result"]
            response = res.get("raw") if isinstance(res.get("raw"), dict) else res.get("text", "")
            row = make_entry(run["command"], response, run["tool_use_id"], res.get("is_error"), run["ts"])
            row["from_transcript"] = True
        row["pos"] = run["pos"]
        merged.append(row)

    stamps = [(parse_ts(l.get("timestamp")), i) for i, l in enumerate(lines)]
    stamps = [(t, i) for t, i in stamps if t is not None]
    for entry in unused:
        ts = parse_ts(entry.get("ts"))
        if ts is None or not stamps:
            pos = math.inf  # no way to order it: assume it is the latest thing
        else:
            before = [i for t, i in stamps if t <= ts]
            pos = (max(before) + 0.5) if before else -0.5
        row = dict(entry)
        row["pos"] = pos
        merged.append(row)
    merged.sort(key=lambda r: r["pos"])
    return merged


# --------------------------------------------------------------------------
# Claims and verdicts
# --------------------------------------------------------------------------

_CLAIM_PATTERNS = {
    "test": [
        r"\b(all\s+)?(the\s+)?(\d+\s+)?((unit|integration|e2e|end-to-end)\s+)?tests?\s+(now\s+|all\s+|still\s+)*"
        r"(pass(es|ed)?|are\s+passing|is\s+passing|(are\s+|is\s+)?green|succeed(s|ed)?)\b",
        r"\btest\s+suite\s+(now\s+)?(pass(es|ed)?|is\s+(green|passing))\b",
        r"\b\d+\s*/\s*\d+\s+tests?\s+pass(ing|ed)?\b",
        r"\btests?\s+(are|is)\s+all\s+passing\b",
    ],
    "build": [
        r"\bbuild\s+(now\s+|still\s+)*(succeeds|succeeded|passes|passed|works|is\s+(green|passing|successful|clean))\b",
        r"\bbuilds?\s+(successfully|cleanly|without\s+errors)\b",
        r"\bsuccessfully\s+built\b",
        r"\bcompiles\s+(cleanly|successfully|without\s+(errors|warnings))\b",
    ],
    "lint": [
        r"\blint(ing|er)?\s+(is\s+|now\s+|still\s+)*(clean|pass(es|ed)?|passing)\b",
        r"\bno\s+(lint|linting|linter|type)\s+(errors|warnings|issues)\b",
        r"\btype-?check(s|ing)?\s+(now\s+)?(pass(es|ed)?|cleanly|clean|successfully|is\s+clean)\b",
        r"\b(it|code|now|project|everything|this|that)\s+type-?checks\b",
    ],
    "deploy": [
        r"\b(successfully\s+)?(re)?deployed\b",
        r"\bdeploy(ment)?\s+(succeeded|is\s+(complete|live|done)|complete[d]?|went\s+through)\b",
        r"\bpushed\s+(it\s+|the\s+\w+\s+)?to\s+(main|master|origin|remote|production|prod|github)\b",
    ],
}
_CLAIM_RES = {k: [re.compile(p, re.I) for p in v] for k, v in _CLAIM_PATTERNS.items()}

# Words just before a match that make it not a claim ("if tests pass", "hasn't been deployed").
_HEDGE_RE = re.compile(
    r"(\bnot\b|n't\b|\bnever\b|\bno\s+longer\b|\bif\b|\bonce\b|\bwhen\b|\bwhether\b|\buntil\b|\bunless\b|"
    r"\bshould\b|\bwill\b|\bwould\b|\bcould\b|\bmight\b|\bmay\b|\bneeds?\s+to\b|\bto\s+make\b|\bensure\b|"
    r"\bmake\s+sure\b|\bverify\b|\bcheck\b|\bexpect\b|\bhope\b|\bonly\b|\bran\s+out\b|\?)",
    re.I,
)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def extract_claims(text):
    """Return [{kind, phrase}] for each un-hedged success claim in text."""
    text = _INLINE_CODE_RE.sub(" ", _CODE_FENCE_RE.sub(" ", text or ""))
    claims, seen = [], set()
    for kind in KINDS:
        for rx in _CLAIM_RES[kind]:
            for m in rx.finditer(text):
                # Sentence prefix (back to the last . ! ? newline) for hedge words.
                start = max(text.rfind(c, 0, m.start()) for c in ".!?\n") + 1
                prefix = text[start:m.start()]
                suffix = text[m.end():m.end() + 2]
                if _HEDGE_RE.search(prefix) or suffix.strip().startswith("?"):
                    continue
                phrase = m.group(0).strip()
                key = (kind, phrase.lower())
                if key not in seen:
                    seen.add(key)
                    claims.append({"kind": kind, "phrase": phrase})
    return claims


def verdict_for(kind, runs, edits):
    """Return (verdict, run_or_None, detail)."""
    relevant = [r for r in runs if kind in (r.get("kinds") or [r.get("kind")])]
    if not relevant:
        return "UNBACKED", None, "no %s command was run this session" % kind
    last = relevant[-1]
    later_edits = [e for e in edits if e["pos"] > last["pos"]]
    if last.get("failed"):
        code = last.get("exit_code")
        how = ("exit %s" % code) if code is not None else ("detected from %s" % last.get("status_source", "output"))
        return "FAILED", last, "the last %s run `%s` failed (%s)" % (kind, short(last.get("command")), how)
    if later_edits:
        files = sorted({os.path.basename(str(e.get("file_path") or "?")) for e in later_edits})
        return "STALE", last, "the last %s run `%s` happened before you edited %s" % (
            kind, short(last.get("command")), ", ".join(files[:5]))
    return "VERIFIED", last, "`%s` passed after the last code edit" % short(last.get("command"))


def short(command, limit=80):
    command = " ".join(str(command or "").split())
    return command if len(command) <= limit else command[: limit - 3] + "..."


def evaluate(claims, runs, edits):
    results = []
    for claim in claims:
        verdict, run, detail = verdict_for(claim["kind"], runs, edits)
        results.append(dict(claim, verdict=verdict, detail=detail,
                            command=run.get("command") if run else None))
    return results


def render_receipt(session_id, runs, results, mode):
    out = ["# Receipt for session %s" % session_id, "", "Generated %s, mode `%s`." % (now_iso(), mode), ""]
    out += ["## Claims in the final reply", ""]
    if results:
        out += ["| Verdict | Kind | Claim | Evidence |", "|---|---|---|---|"]
        for r in results:
            out.append("| %s | %s | %s | %s |" % (r["verdict"], r["kind"], _cell(r["phrase"]), _cell(r["detail"])))
    else:
        out.append("No test/build/lint/deploy claims found.")
    out += ["", "## Runs this session (%d)" % len(runs), ""]
    if runs:
        out += ["| # | Time | Kind | Status | Command |", "|---|---|---|---|---|"]
        for n, r in enumerate(runs, 1):
            status = "FAIL" if r.get("failed") else "ok"
            if r.get("exit_code") is not None:
                status += " (exit %s)" % r["exit_code"]
            kinds = ",".join(r.get("kinds") or []) or r.get("kind", "other")
            out.append("| %d | %s | %s | %s | `%s` |" % (n, r.get("ts") or "", kinds, status,
                                                      _cell(short(r.get("command"), 100))))
    else:
        out.append("No Bash commands recorded.")
    return "\n".join(out) + "\n"


def _cell(text):
    return str(text or "").replace("|", "\\|").replace("\n", " ")
