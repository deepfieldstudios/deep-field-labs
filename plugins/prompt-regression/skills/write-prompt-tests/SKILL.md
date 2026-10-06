---
name: write-prompt-tests
description: Build or extend prompt regression tests (prompt-tests/*.json) for an AI feature by harvesting real inputs and outputs from logs, fixtures, docs, support tickets or the codebase and turning them into cases with meaningful assertions. Use when someone wants tests for a prompt, an eval set, golden examples, or protection against prompt/model changes breaking an AI feature.
---

# Writing prompt tests

The goal is a small set of cases that would catch a real regression, not a pile of cases that
check the model can produce text.

## 1. Find the feature and its prompt

- Locate the prompt: `prompts/`, `*.prompt.md`, files named like `*system_prompt*`, or string
  constants passed as `system=` / `messages=` to the API. Note the model id and parameters used.
- Prefer `"prompt_file"` pointing at the real prompt over copying it into `"system"`, so the tests
  follow the prompt as it changes (and the change hook knows which suite covers it).
- If the code builds the prompt from a template, test the rendered form the app actually sends
  (put fixed values in, or test the template file and pass variables in the case input).

## 2. Harvest real examples (in this order)

1. Production or staging logs / traces of requests and responses (strip personal data).
2. Fixtures, seed data, unit tests and Storybook stories that already exercise the feature.
3. Bug reports, support tickets and commit messages describing things that went wrong: each one
   is a regression case.
4. Docs and README examples of what the feature should do.
5. Only then, invented cases: edge cases the above didn't cover (empty input, very long input,
   another language, prompt injection, off-topic requests, the "should refuse" case).

Aim for 5–20 cases per suite. Cover the main job, the known past failures, and the format contract.

## 3. Choose assertions that mean something

| Need | Assertion |
|---|---|
| Must mention a fact, link, product name | `contains` (use a list for several; `ignore_case`) |
| Must not leak, apologise, mention competitors, say "as an AI" | `not_contains` |
| Shape of free text (a date, a price, a ticket id) | `regex` with `flags` |
| Machine-read output | `json_valid` then `json_schema` (`type`, `required`, `properties`, `enum`, `items`) |
| UI or SMS limits | `max_chars` |
| Classifiers, routing labels, yes/no | `equals` |
| Tone and wording should stay close to a known-good answer | `similar_to_golden` with a `threshold` (0.5–0.8; lower for creative text) |
| Judgement calls (helpful, correct, polite, follows policy) | `llm_judge` with a specific rubric |

Rules of thumb:
- Every case needs at least one assertion that would fail if the feature broke. "contains the
  word 'the'" is not a test.
- Assert on the contract (facts, format, limits), not exact phrasing, unless it is a classifier.
- Rubrics for `llm_judge` should be checkable: "States refunds are allowed within 30 days and asks
  for the order number" beats "Good answer".
- Use `similar_to_golden` sparingly; it flags any rewording.

## 4. Format

```json
{
  "name": "support-bot",
  "prompt_file": "prompts/support.md",
  "model": "claude-sonnet-5-5",
  "max_tokens": 600,
  "cases": [
    {"id": "refund-window", "input": "Can I return boots after 3 weeks?",
     "expect": [{"type": "contains", "value": "30 days"}, {"type": "max_chars", "value": 600}]},
    {"id": "multi-turn", "input": [
        {"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello! How can I help?"},
        {"role": "user", "content": "Where's order 1182?"}],
     "expect": [{"type": "regex", "pattern": "1182"}]}
  ]
}
```

Case ids become file names under `prompt-tests/.golden/<suite>/`, so keep them short and stable.

## 5. Record, run, review

1. `/prompt-regression:record` (live model, needs ANTHROPIC_API_KEY) to save golden outputs.
2. Read the golden outputs. If one is wrong, fix the prompt or the case before committing it.
3. `/prompt-regression:run` to confirm everything passes, then commit `prompt-tests/` including
   `.golden/` (but not `.results/`).
