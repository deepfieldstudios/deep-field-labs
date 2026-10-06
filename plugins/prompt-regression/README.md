# prompt-regression

Tests for AI features. Teams change a prompt or upgrade a model and quietly break the feature;
this plugin keeps a small set of real examples with checks, runs them live or offline, and
reports what got worse.

- **Suites** are plain JSON in `prompt-tests/*.json`.
- **Runner** calls the model (Claude API via stdlib `urllib`) or **replays** recorded outputs from
  `prompt-tests/.golden/`, so suites run offline and in CI with no key.
- **Compare** two runs as a Markdown report: pass-rate change, regressions, fixes, output changes,
  latency and token deltas.
- **Hook**: when Claude edits a prompt file (or swaps a `claude-...` model id in code), it is told to
  run the tests before claiming the work is done. Once per file per session.

## Install

```
/plugin marketplace add deepfieldstudios/deep-field-labs
/plugin install prompt-regression@deep-field-labs
```

## Commands and skill

| | |
|---|---|
| `/prompt-regression:init` | Writes `prompt-tests/example.json` (never overwrites). |
| `/prompt-regression:run [suite] [--provider replay\|anthropic] [--case id] [--model id]` | Runs suites, then compares with the previous run. |
| `/prompt-regression:record [suite]` | Calls the live model and saves outputs as golden copies. |
| Skill `write-prompt-tests` | Harvest real inputs/outputs from logs, fixtures, tickets and code into cases with meaningful assertions. |

Scripts directly:

```
python3 scripts/run.py --project . [suite ...] [--provider auto|anthropic|replay] [--record] [--case ID] [--model ID]
python3 scripts/compare.py old.json new.json [--out report.md] [--fail-on-regression]
python3 scripts/compare.py --latest --project .
python3 scripts/init.py --project .
```

`run.py` exits 0 when everything passes, 1 on any failure or error, 2 on a configuration problem.

## Suite format

```json
{
  "name": "support-bot",
  "prompt_file": "prompts/support.md",
  "model": "claude-sonnet-5-5",
  "max_tokens": 600,
  "temperature": 0,
  "cases": [
    {"id": "refund-window", "input": "Can I return boots after 3 weeks?",
     "expect": [
       {"type": "contains", "value": "30 days"},
       {"type": "not_contains", "value": ["as an AI"], "ignore_case": true},
       {"type": "max_chars", "value": 600},
       {"type": "llm_judge", "rubric": "Says yes and asks for the receipt", "threshold": 0.7}]},
    {"id": "status-json", "input": [{"role": "user", "content": "Order 1182 status as JSON"}],
     "expect": [
       {"type": "json_schema", "schema": {"type": "object", "required": ["status"],
         "properties": {"status": {"type": "string", "enum": ["pending", "shipped"]}}}}]}
  ]
}
```

- `prompt_file` (relative to the project root) or `system` (inline) supplies the system prompt.
- `input` is a string (one user message) or a list of `{role, content}` messages.
- `model` defaults to `claude-sonnet-5-5`; cases may override `model`, `max_tokens`, `temperature`.
- Optional `"provider": "replay"` pins a suite to replay.

Assertions (long form `{"type": ...}` or shorthand `{"contains": "x"}`):

| Type | Options |
|---|---|
| `contains` / `not_contains` | `value` (string or list), `ignore_case` |
| `regex` | `pattern`, `flags` (`i`, `m`, `s`, `x`) |
| `json_valid` | `allow_fences` (default true: accepts a ```json fenced block) |
| `json_schema` | `schema` — subset: `type` (incl. lists), `required`, `properties`, `enum`, `items` |
| `max_chars` | `value` |
| `equals` | `value`, `strip` (default true), `ignore_case` |
| `similar_to_golden` | `threshold` (default 0.8, difflib ratio); `value` to give the reference inline |
| `llm_judge` | `rubric`, `threshold` (default 0.7), `model`. Skipped (not failed) without `ANTHROPIC_API_KEY`; force-skip with `PROMPT_REGRESSION_JUDGE=off`. |

## Files it writes

```
prompt-tests/
  example.json                 # from init
  .golden/<suite>/<case>.json  # recorded outputs — commit these
  .results/<timestamp>.json    # one per run — usually gitignored
.claude/prompt-regression/nudged.json   # hook state (override dir with PROMPT_REGRESSION_HOME)
```

## Configuration

- `ANTHROPIC_API_KEY` — enables live runs, `--record` and `llm_judge`.
- `ANTHROPIC_BASE_URL` — send requests to a proxy/gateway instead of `https://api.anthropic.com`.
- `<project>/.claude/prompt-regression/config.json` — `{"globs": ["prompts/**", "*.prompt.md", "*system_prompt*"]}`
  replaces the hook's default prompt-file patterns. `dir/**` matches that folder anywhere in the tree;
  patterns without `/` match file names.

## CI (GitHub Actions)

Replay in CI needs no secrets; add the key to also run live.

```yaml
name: prompt-tests
on: [pull_request]
jobs:
  prompts:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - name: Replay golden outputs
        run: python3 path/to/prompt-regression/scripts/run.py --project . --provider replay
      - name: Live run (only when the key is configured)
        if: ${{ env.ANTHROPIC_API_KEY != '' }}
        env: { ANTHROPIC_API_KEY: "${{ secrets.ANTHROPIC_API_KEY }}" }
        run: python3 path/to/prompt-regression/scripts/run.py --project . --provider anthropic
```

## Known limits

- **Replay doesn't test a prompt change by itself**: it re-checks assertions against recorded
  outputs. Use it to catch assertion/format regressions and in CI; use a live run (or re-record) to
  test what a new prompt or model actually does.
- Only the Anthropic Messages API is implemented; no tools, images, streaming or prompt caching in
  requests. Text blocks of the reply are concatenated.
- One sample per case: no repeated runs to measure flakiness at temperature > 0.
- `json_schema` is a deliberate subset (no `$ref`, `additionalProperties`, `minItems`, formats).
- `similar_to_golden` is character-level difflib, so it penalises rewording, not meaning.
- The model-id nudge uses a regex (`claude-` plus a version digit). Ids built from variables or
  config files with other names are not seen; `.md`/`.txt` files are ignored for model ids.
- Prompt templates are sent as-is; there is no variable substitution.

## What v1 would add

- Templated prompts with per-case variables, and suites that call the app's own entrypoint.
- `--repeat N` with pass-rate thresholds per case for non-deterministic features.
- Cost estimates per run and a budget guard.
- `--baseline <git-ref>` to compare against the results on main.
- More providers (Bedrock, Vertex, OpenAI-compatible) behind the same adapter interface.
- Semantic similarity (embeddings) as an alternative to difflib.

## Tests

```
cd plugins/prompt-regression && python3 -m unittest discover -s tests
```

All network calls are mocked; nothing contacts the API.
