# spend-meter

Cost visibility and runaway detection for Claude Code. Surprise bills come from things
you cannot see while they happen: an agent retrying the same failing command, or a
subagent fan-out quietly burning through cache writes. spend-meter prices the live
transcript after every tool call, shows the running cost in the status line, enforces
session and daily budgets, and tells Claude to stop when it is going round in circles.

## What it does

| Piece | What it does |
|---|---|
| `scripts/usage.py` | Parses a transcript JSONL. Assistant messages are deduped by `message.id` (then `requestId`), because streaming writes one line per content block with the same usage repeated. Sums input, output, cache-write (5m and 1h split via `usage.cache_creation`) and cache-read tokens per model, and prices them from `data/prices.json`. Subagent transcripts in `<session>/subagents/*.jsonl` are folded into the session. Incremental: it remembers a byte offset per file and only reads new complete lines. |
| `scripts/statusline.py` | Status line: `$0.42 this session · 1.2M tok · budget 21% · ⚠ loop?` The budget part appears only when a budget is set, and the loop flag only for the 10 tool calls after the loop detector fires. |
| `hooks/hooks.json` → `scripts/hook.py` | `PostToolUse` and `PostToolUseFailure`, matcher `.*`. Re-prices incrementally, then runs the budget check and loop detector. |
| `/spend-meter:report` | The last 14 days across every project in `~/.claude/projects`: per-day table, per-session table, top 5 sessions with their first prompt (80 chars), split by model, subagent share, cache hit ratio. `--days N` changes the window. |

### Budgets

Once per threshold, per session (daily thresholds once per day):

- crossing `warn_at` × budget → a `systemMessage` to you;
- going over budget → `{"decision":"block"}` whose reason tells Claude to pause, summarise
  progress and what is left, and ask you whether to continue.

The daily total is the sum over all sessions of cost dated today (each message is dated
by its own timestamp, so a session that runs past midnight splits correctly). The ledger
lives in `~/.claude/spend-meter/ledger.json`.

### Loop detector

Fires an `additionalContext` nudge ("You've hit the same error N times; stop and reconsider
approach or ask the user.") once per signature when either:

- the same Bash command (whitespace collapsed, trailing `2>&1` dropped) fails 3 times
  without succeeding in between; or
- the same error signature (last non-empty line of the error output, digits stripped)
  appears 3 times in the last 10 tool results, from any tool.

## Install

```
/plugin marketplace add deepfieldstudios/deep-field-labs
/plugin install spend-meter@deep-field-labs
```

Plugins cannot set the status line themselves. Add it in `~/.claude/settings.json`,
pointing at the installed copy (or this repo):

```json
"statusLine": {
  "type": "command",
  "command": "python3 /path/to/plugins/spend-meter/scripts/statusline.py"
}
```

## Config

Global: `~/.claude/spend-meter/config.json` (move the whole state dir with `SPEND_METER_HOME`).
Per project, overriding key by key: `<project>/.claude/spend-meter.json`.

```json
{ "session_budget_usd": 10, "daily_budget_usd": 40, "warn_at": 0.8 }
```

Any key may be omitted or `null` (no budget, no blocking).

### Prices

`data/prices.json` is **editable: verify it against current Anthropic pricing** before
trusting the totals. Seeded 6 Oct 2026 from first-party API list prices. Values are USD per
million tokens; model ids match by longest prefix (`claude-opus-5-5` beats `claude-opus-5`),
with `default` for anything unknown. Cache writes are 1.25× input (5 minute) and 2× input
(1 hour); cache reads 0.1× input unless an entry gives `cache_read_usd` (Fable 5.1 and Opus
5.5 do). Messages from `<synthetic>` (locally generated) are free.

## State

Everything is under `~/.claude/spend-meter/` (or `$SPEND_METER_HOME`):
`sessions/<id>.cache.json` (offsets and deduped usage), `sessions/<id>.hook.json` (fired
thresholds, loop history), `ledger.json`, `config.json`. Nothing is written elsewhere and
nothing touches the network.

## Tests

```
cd plugins/spend-meter && python3 -m unittest discover -s tests
```

22 tests: cost math to the cent on a synthetic fixture (duplicated streaming lines, two
main-session models plus a subagent, 5m and 1h cache writes, a line with no `message.id`),
incremental parsing after appends, partial lines, new subagent files and truncation,
warn/block once semantics for session and daily budgets, config override, the loop
detector, statusline format and warm speed, the report, and a read-only parse of the
largest real transcript on the machine (no value asserts).

## Known limits

- Costs are estimates from transcript usage at list price. No Batch, fast mode, Priority
  Tier, regional or partner (Bedrock/Vertex) pricing, and no web search fees.
- The first status line render of a very large session is a full parse (about 0.3 s for a
  100 MB transcript); every later render reads only new lines (about 30 ms including
  Python start-up).
- Budget blocks fire once per threshold. If you tell Claude to carry on past the budget it
  is not asked again in that session; raise the budget to get a fresh warning point.
- The hook only runs after tool calls, so a long tool-free answer can overshoot before the
  next check.
- Failure detection relies on `PostToolUseFailure` or error-shaped `tool_response`
  payloads (`Error:` / `Exit code` prefixes, `is_error`, non-zero exit codes). A tool that
  fails silently with normal-looking output is not counted.
- The report attributes a session to the project folder it was started from.

## What a v1 would add

- Per-task cost: mark a start point (a slash command or UserPromptSubmit) and show cost since.
- Fan-out alarm: warn when N subagents are running concurrently or one subagent passes a share of the budget.
- Spend-rate alerts ($ per minute) rather than only absolute thresholds.
- A price-table refresh command that diffs against the published pricing page, run by hand.
- CSV export of the ledger for bookkeeping.
