# Build conventions for every plugin in this marketplace

## Layout (per plugin, under plugins/<name>/)
.claude-plugin/plugin.json   name, description, version "0.1.0", author {"name":"Deep Field"}, license "MIT", keywords
hooks/hooks.json             optional; {"description": "...", "hooks": {Event: [{"matcher": "...", "hooks": [{"type":"command","command":"python3","args":["${CLAUDE_PLUGIN_ROOT}/scripts/x.py"],"timeout":10}]}]}}
commands/<cmd>.md            slash commands: frontmatter `description:` (+ optional `argument-hint:`), body is the prompt; may run scripts via ${CLAUDE_PLUGIN_ROOT}
skills/<skill>/SKILL.md      frontmatter `name:` and `description:`
scripts/                     Python 3 STANDARD LIBRARY ONLY. No pip installs, no network calls in hooks.
tests/test_*.py              unittest, runnable with `python3 -m unittest discover -s tests` from the plugin dir. Feed hooks fake stdin JSON via subprocess.
README.md                    what it does, install, config, limits, what a v1 would add.

## Hook I/O contract (Claude Code)
stdin is JSON: session_id, transcript_path, cwd, hook_event_name, plus per event:
- PreToolUse: tool_name, tool_input
- PostToolUse: tool_name, tool_input, tool_response
- UserPromptSubmit: prompt
- Stop / SubagentStop: stop_hook_active (bool) — if true, do NOT block again (avoid loops)
- SessionStart: source ("startup"|"resume"|"clear"|"compact")
- SessionEnd: reason

stdout JSON (exit 0):
- PreToolUse: {"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"|"deny"|"ask","permissionDecisionReason":"..."}}
  PreToolUse may also rewrite input: add "updatedInput": {...} inside hookSpecificOutput.
- PostToolUse: {"decision":"block","reason":"..."} feeds reason back to Claude; or {"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"..."}}
- UserPromptSubmit / SessionStart: {"hookSpecificOutput":{"hookEventName":"<event>","additionalContext":"..."}}
- Stop: {"decision":"block","reason":"..."} makes Claude continue with reason as instruction.
- Any event: {"systemMessage":"..."} shows a message to the user.
Exit 2 = blocking error, stderr goes to Claude. Hooks must never crash the session: wrap main in try/except and exit 0 on internal errors.

Tool names: Bash (tool_input.command), Edit (file_path, old_string, new_string), Write (file_path, content), Read, WebFetch (url, prompt), WebSearch, mcp__<server>__<tool>.

## Transcript (transcript_path) is JSONL
Lines have "type": "user" | "assistant" | others. Assistant lines: message.model, message.usage {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}, message.content = list of blocks ("text", "tool_use" {id,name,input}, "thinking"). User lines with message.content list may hold "tool_result" blocks {tool_use_id, content, is_error}. Tolerate missing fields everywhere.

## State
Per-project state goes under <cwd>/.claude/<plugin-name>/ ; global state under ~/.claude/<plugin-name>/ . Allow override via env var <PLUGIN>_HOME for tests. Never write outside these.

## Quality bar
A prototype, but it must actually work: every hook script tested with realistic fake input, tests passing. Prefer simple, readable code with brief comments. No placeholders or TODO stubs in the core path.
