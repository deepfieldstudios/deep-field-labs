---
description: Run the prompt regression tests (all suites, or one) and report what passed, failed or regressed
argument-hint: "[suite] [--provider replay|anthropic] [--case id] [--model id]"
---
Run the project's prompt tests:

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" --project "$PWD" $ARGUMENTS

Provider defaults to `auto`: the live Claude API when ANTHROPIC_API_KEY is set (or the suite says so),
otherwise `replay` from recorded golden outputs. Replay only proves assertions still hold for the recorded
outputs; to test a prompt or model change for real, a live run is needed — say which one you ran.

Then:
1. Report the pass rate and each failing case with the failed assertion and why.
2. If there is an earlier run, compare with it:
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/compare.py" --latest --project "$PWD"`
   and summarise regressions, fixes and notable output, latency or token changes.
3. Do not edit assertions just to make tests pass. If a change in output is intended, say so and suggest
   `/prompt-regression:record` to update the golden outputs.
4. Exit code 1 means failures; 2 means a configuration problem (no suites, bad JSON, missing prompt_file).
