# receipts

Proof behind "done". Agents say "all tests pass", "the build succeeds" or "deployed" without having run the command, or after a run that failed. receipts keeps a ledger of every Bash command in the session and, when Claude finishes a reply, checks each claim in that reply against it.

## What it does

1. **Ledger (PostToolUse on Bash).** Every command is appended to `<cwd>/.claude/receipts/<session_id>.jsonl` with a timestamp, the command, the exit code when the tool response carries one, an `is_error`/interrupted flag, the last 400 characters of output, and a kind:
   - `test`: pytest, unittest, jest, vitest, mocha, npm/pnpm/yarn/bun test, go test, cargo test, rspec, phpunit, mvn test, gradle test, make test
   - `build`: npm run build, tsc, cargo build, go build, make, vite/next/astro build, webpack, docker build
   - `lint`: eslint, ruff, flake8, mypy, pyright, tsc --noEmit, npm run lint/typecheck, cargo clippy/check, go vet
   - `deploy`: wrangler deploy / pages deploy, vercel, netlify deploy, fly deploy, git push, kubectl apply, terraform apply, firebase deploy, npm publish
   - `other`: everything else
   A compound command (`npm run build && npm test`) counts as every kind it contains. When no exit code is available, failure is read from the output (`2 failed`, `FAILED`, `Error:`, `error TS2322`, `npm ERR!`, a Python traceback, `! [rejected]`, and similar).
2. **Claim check (Stop).** The hook reads the transcript, takes the final assistant text of this turn, and pulls out claims such as "all tests pass", "tests are passing", "build succeeds", "lint is clean", "type-checks" and "deployed". Hedged phrases ("once the tests pass", "should build", "hasn't been deployed") are ignored. Each claim gets a verdict:
   - `VERIFIED`: the latest run of that kind passed, and no code was edited after it.
   - `STALE`: the latest run passed, but an Edit/Write/MultiEdit to a code file came after it. Edits to `.md`/`.txt`/`.rst` files do not count.
   - `FAILED`: the latest run of that kind failed.
   - `UNBACKED`: no run of that kind this session.
   If any claim is not VERIFIED, the hook blocks the stop and tells Claude which claims lack evidence, and asks it to run the command or correct the claim. It never blocks twice in a row: when `stop_hook_active` is true it only shows a message to the user.
3. **Receipt.** Every stop writes `<cwd>/.claude/receipts/receipt-<session_id>.md`, a table of claims with their verdicts and of every run.

Commands that appear in the transcript but never reached the ledger are still counted. This matters because some Claude Code versions skip PostToolUse for a failing command. They are classified from the transcript's `tool_result` (`is_error` and the output text).

## Install

```
/plugin marketplace add deepfieldstudios/deep-field-labs
/plugin install receipts@deep-field-labs
```

`/receipts:show [session-id]` prints the latest receipt, or the receipt for the session you name.

## Config

| Env var | Values | Default |
|---|---|---|
| `RECEIPTS_MODE` | `block`: block the stop until claims are backed. `warn`: show a systemMessage only. `off`: only write the receipt | `block` |
| `RECEIPTS_HOME` | Directory for the ledger and receipts, used by the tests | `<cwd>/.claude/receipts` |

Add `.claude/receipts/` to `.gitignore`.

## Known limits

- Claims are matched with regexes. Unusual wording ("green across the board") is missed, and phrases like "the deployed site" can be flagged by mistake. "Fixed" is not checked, because it does not map to one command.
- A pipe hides the exit code: in `npm test | tail` the status comes from `tail`. In that case receipts falls back to reading the output.
- Code changed by Bash (`sed -i`, codegen, `git checkout`) does not count as an edit, so it does not make a run STALE.
- A run is not tied to the files it covers. Running `pytest tests/test_a.py` backs "all tests pass".
- Claude Code may write the final message to the transcript after the Stop hook runs. When the transcript has no final text, the hook falls back to `last_assistant_message` if the payload carries it.
- Ledger rows that cannot be matched to the transcript are ordered by timestamp.

## v1 roadmap

- Scope matching: a claim about "the API tests" needs a run that covered that path, and a partial run cannot back "all tests".
- Configurable kinds and claim phrases per project, in `.claude/receipts.json`.
- Track Bash commands that write files as edits, using a git status snapshot before and after each command.
- A SubagentStop variant, so subagent reports are checked before the parent relays them.
- Attach the receipt to PR descriptions and commit trailers.
