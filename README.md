# Deep Field Labs

Ten plugins for Claude Code, each aimed at a problem people hit once they run agents
on real work. Each one is small, uses only the Python standard library, makes no network
calls from its hooks, and is covered by tests.

## Install

In Claude Code:

```
/plugin marketplace add deepfieldstudios/deep-field-labs
/plugin install secret-shield@deep-field-labs
```

Install only the ones you want. Each plugin's README covers its settings and limits.
Python 3.9 or later is required (3.11+ for docs-pin's `pyproject.toml` parsing).

## The plugins

### Guardrails

| Plugin | What it does |
|---|---|
| [secret-shield](plugins/secret-shield) | Blocks API keys and other credentials from leaving through prompts, files, web requests or commits. Keeps a rotation register that stores fingerprints, never the keys themselves. |
| [receipts](plugins/receipts) | Records every command Claude runs, then checks claims like "all tests pass" or "deployed" against those runs before the turn ends. |
| [blast-radius](plugins/blast-radius) | Scores each shell command for risk. Safe ones run without a prompt; dangerous ones get a plain-English preview and a git snapshot first. |
| [docs-pin](plugins/docs-pin) | Reads your lockfile so Claude writes code for the versions you actually have, and flags deprecated APIs as they're written. |

### Cost and review

| Plugin | What it does |
|---|---|
| [spend-meter](plugins/spend-meter) | Live cost per session in the status line, budget caps, and a warning when the same error keeps repeating. |
| [diff-digest](plugins/diff-digest) | Regroups a large diff by purpose (logic, tests, mechanical, config) and ranks hunks by risk, so you read the lines that matter first. |
| [prompt-regression](plugins/prompt-regression) | Golden-example tests for prompts. Edit a prompt or change a model and Claude is nudged to run the suite. |

### Continuity

| Plugin | What it does |
|---|---|
| [handoff](plugins/handoff) | Saves decisions, dead ends and open items at the end of a session, and loads only the relevant ones next time. |
| [codebase-onboarder](plugins/codebase-onboarder) | Builds an ONBOARDING.md with commands that have actually been run, and adds gotchas as sessions find them. |

### For non-developers

| Plugin | What it does |
|---|---|
| [office-mode](plugins/office-mode) | Saves a copy of every spreadsheet or document before Claude changes it, explains changes in plain words ("Sheet1!C14: 1,200 → 1,450"), and restores by date ("restore Tuesday's budget"). |

## Status

These are version 0.1 prototypes. They are tested, but they haven't been through many
real sessions yet. Hooks that block (receipts, secret-shield, blast-radius) can be set to
warn instead; see each README. Bug reports and pull requests are welcome.

## Running the tests

```
cd plugins/<name> && python3 -m unittest discover -s tests
```

`CONVENTIONS.md` describes the layout and hook contract every plugin follows.

## Licence

MIT. Built by [Deep Field](https://deepfieldstudios.com).
