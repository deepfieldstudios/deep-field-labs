"""Shared helpers: finding and loading suites, paths for golden outputs and results, state."""
import json
import os
import re
from pathlib import Path

PLUGIN = "prompt-regression"
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_MAX_TOKENS = 1024
TESTS_DIR = "prompt-tests"
DEFAULT_GLOBS = ["prompts/**", "*.prompt.md", "*system_prompt*"]


class SuiteError(Exception):
    pass


def safe_name(s):
    """Make a suite/case name safe to use as a file name."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(s)).strip("-.")
    return s or "unnamed"


def tests_dir(project):
    return Path(project) / TESTS_DIR


def golden_dir(project):
    return tests_dir(project) / ".golden"


def results_dir(project):
    return tests_dir(project) / ".results"


def golden_path(project, suite, case_id):
    return golden_dir(project) / safe_name(suite) / f"{safe_name(case_id)}.json"


def state_dir(cwd):
    env = os.environ.get("PROMPT_REGRESSION_HOME")
    return Path(env) if env else Path(cwd) / ".claude" / PLUGIN


def load_config(cwd):
    cfg = {"globs": list(DEFAULT_GLOBS)}
    try:
        data = json.loads((Path(cwd) / ".claude" / PLUGIN / "config.json").read_text())
        if isinstance(data, dict):
            cfg.update(data)
    except (OSError, ValueError):
        pass
    return cfg


def suite_files(project):
    d = tests_dir(project)
    return sorted(p for p in d.glob("*.json") if p.is_file()) if d.is_dir() else []


def load_suite(path, project):
    """Parse and validate a suite file. Resolves the system prompt from prompt_file or system."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SuiteError(f"{Path(path).name} is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise SuiteError(f"{Path(path).name}: top level must be an object")
    name = data.get("name") or Path(path).stem
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise SuiteError(f"{name}: needs a non-empty 'cases' list")
    system = data.get("system", "")
    prompt_path = None
    if data.get("prompt_file"):
        prompt_path = (Path(project) / data["prompt_file"]).resolve()
        if not prompt_path.is_file():
            raise SuiteError(f"{name}: prompt_file {data['prompt_file']} not found")
        system = prompt_path.read_text(encoding="utf-8")
    seen = set()
    for i, c in enumerate(cases):
        if not isinstance(c, dict) or "input" not in c:
            raise SuiteError(f"{name}: case #{i + 1} needs an 'input'")
        c.setdefault("id", f"case-{i + 1}")
        if c["id"] in seen:
            raise SuiteError(f"{name}: duplicate case id '{c['id']}'")
        seen.add(c["id"])
        if not isinstance(c.get("expect", []), list):
            raise SuiteError(f"{name}/{c['id']}: 'expect' must be a list")
    return {"name": name, "file": str(path), "system": system, "prompt_file": str(prompt_path) if prompt_path else None,
            "model": data.get("model") or DEFAULT_MODEL, "provider": data.get("provider"),
            "max_tokens": int(data.get("max_tokens", DEFAULT_MAX_TOKENS)),
            "temperature": data.get("temperature"), "judge_model": data.get("judge_model"),
            "cases": cases}


def to_messages(inp):
    if isinstance(inp, str):
        return [{"role": "user", "content": inp}]
    if isinstance(inp, list) and all(isinstance(m, dict) and "role" in m and "content" in m for m in inp):
        return inp
    raise SuiteError("input must be a string or a list of {role, content} messages")
