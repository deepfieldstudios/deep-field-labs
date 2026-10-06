"""Assertion checks for model outputs.

Each assertion is {"type": "<kind>", ...options} or the shorthand {"<kind>": value}.
evaluate() returns {"type", "passed": True | False | None (skipped), "detail"}.

Kinds: contains, not_contains, regex, json_valid, json_schema, max_chars, equals,
       similar_to_golden, llm_judge.
"""
import difflib
import json
import os
import re

KINDS = ("contains", "not_contains", "regex", "json_valid", "json_schema", "max_chars",
         "equals", "similar_to_golden", "llm_judge")


def normalize(a):
    if not isinstance(a, dict):
        raise ValueError(f"assertion must be an object, got {a!r}")
    if "type" in a:
        return dict(a)
    keys = [k for k in a if k in KINDS]
    if len(keys) != 1:
        raise ValueError(f"can't tell the assertion type of {a!r}")
    k = keys[0]
    out = {k2: v for k2, v in a.items() if k2 != k}
    out["type"] = k
    v = a[k]
    if k in ("contains", "not_contains", "equals"):
        out["value"] = v
    elif k == "regex":
        out["pattern"] = v
    elif k == "json_schema":
        out["schema"] = v
    elif k == "max_chars":
        out["value"] = v
    elif k == "similar_to_golden":
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out["threshold"] = v
    elif k == "llm_judge":
        out["rubric"] = v
    return out


def _r(a, passed, detail=""):
    return {"type": a["type"], "passed": passed, "detail": detail}


def extract_json(text, allow_fences=True):
    s = text.strip()
    if allow_fences:
        m = re.match(r"^```(?:json|JSON)?\s*\n(.*?)\n?```$", s, re.S)
        if m:
            s = m.group(1).strip()
    return json.loads(s)


# ---------- JSON schema subset: type, required, properties, enum, items ----------

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and v.is_integer()),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def schema_errors(value, schema, path="$"):
    errs = []
    if not isinstance(schema, dict):
        return errs
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES.get(x, lambda v: False)(value) for x in types):
            return [f"{path}: expected {' or '.join(types)}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} is not one of {schema['enum']!r}")
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errs.append(f"{path}: missing required property '{req}'")
        for k, sub in (schema.get("properties") or {}).items():
            if k in value:
                errs.extend(schema_errors(value[k], sub, f"{path}.{k}"))
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            errs.extend(schema_errors(item, schema["items"], f"{path}[{i}]"))
    return errs


# ---------- the checks ----------

def _values(a):
    v = a.get("value")
    return v if isinstance(v, list) else [v]


def evaluate(assertion, output, ctx=None):
    """ctx: {"golden": str|None, "judge": callable(rubric, output, case) -> dict, "case": dict}."""
    ctx = ctx or {}
    try:
        a = normalize(assertion)
    except ValueError as exc:
        return {"type": "invalid", "passed": False, "detail": str(exc)}
    kind = a["type"]
    out = output or ""
    ignore_case = bool(a.get("ignore_case"))
    fold = (lambda s: s.lower()) if ignore_case else (lambda s: s)

    if kind == "contains":
        missing = [v for v in _values(a) if fold(str(v)) not in fold(out)]
        return _r(a, not missing, f"missing {missing!r}" if missing else "")
    if kind == "not_contains":
        found = [v for v in _values(a) if fold(str(v)) in fold(out)]
        return _r(a, not found, f"found {found!r}" if found else "")
    if kind == "regex":
        flags = 0
        for ch in str(a.get("flags", "")):
            flags |= {"i": re.I, "m": re.M, "s": re.S, "x": re.X}.get(ch, 0)
        if ignore_case:
            flags |= re.I
        try:
            ok = re.search(a.get("pattern", ""), out, flags) is not None
        except re.error as exc:
            return _r(a, False, f"bad pattern: {exc}")
        return _r(a, ok, "" if ok else f"no match for /{a.get('pattern')}/")
    if kind == "json_valid":
        try:
            extract_json(out, a.get("allow_fences", True))
            return _r(a, True)
        except ValueError as exc:
            return _r(a, False, f"not valid JSON: {exc}")
    if kind == "json_schema":
        try:
            data = extract_json(out, a.get("allow_fences", True))
        except ValueError as exc:
            return _r(a, False, f"not valid JSON: {exc}")
        errs = schema_errors(data, a.get("schema", {}))
        return _r(a, not errs, "; ".join(errs[:5]))
    if kind == "max_chars":
        limit = int(a.get("value", 0))
        return _r(a, len(out) <= limit, f"{len(out)} chars > {limit}" if len(out) > limit else "")
    if kind == "equals":
        exp = str(a.get("value", ""))
        got = out
        if a.get("strip", True):
            exp, got = exp.strip(), got.strip()
        ok = fold(exp) == fold(got)
        return _r(a, ok, "" if ok else f"expected {exp[:80]!r}, got {got[:80]!r}")
    if kind == "similar_to_golden":
        golden = a.get("value", ctx.get("golden"))
        if golden is None:
            return _r(a, False, "no golden output recorded (run /prompt-regression:record)")
        threshold = float(a.get("threshold", 0.8))
        ratio = difflib.SequenceMatcher(None, str(golden), out, autojunk=False).ratio()
        return _r(a, ratio >= threshold, f"similarity {ratio:.2f} (threshold {threshold:.2f})")
    if kind == "llm_judge":
        judge = ctx.get("judge")
        if os.environ.get("PROMPT_REGRESSION_JUDGE", "").lower() in ("off", "0", "false") \
                or not os.environ.get("ANTHROPIC_API_KEY") or judge is None:
            return _r(a, None, "skipped: set ANTHROPIC_API_KEY to score with the LLM judge")
        try:
            verdict = judge(a.get("rubric", ""), out, ctx.get("case") or {}, a)
        except Exception as exc:
            return _r(a, False, f"judge failed: {exc}")
        threshold = float(a.get("threshold", 0.7))
        score = verdict.get("score")
        ok = bool(verdict.get("pass")) if score is None else float(score) >= threshold
        return _r(a, ok, f"score {score} — {verdict.get('reason', '')}".strip())
    return {"type": kind, "passed": False, "detail": f"unknown assertion type '{kind}'"}
