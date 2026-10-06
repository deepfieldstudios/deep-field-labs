# handoff

Memory across Claude Code sessions, without the noise. Each session can leave a few
structured notes (decisions, dead ends, open items, gotchas). The next session gets only
the ones that matter: open items, recent decisions, and notes about files you are
working on now.

## How it works

| Hook | What it does |
|---|---|
| `SessionStart` | Loads live notes and injects a capped block (~1500 chars, newest first) headed "Handoff notes from earlier sessions": every open item, the 5 most recent decisions, and any note whose files overlap recently changed files (`git status` + last 20 commits; newest mtimes when not a git repo). |
| `UserPromptSubmit` | If your prompt names a path or distinctive basename that appears in a note not yet shown this session, injects those notes. Each note is injected at most once per session. |
| `Stop` | Once per session, if the transcript shows files were touched (Edit/Write/Read, plus path-like tokens in Bash) and no note exists yet, it blocks once with a short reason. The reason lists the touched files and any failed attempts, and asks Claude to end its reply with a fenced `handoff` block (format below). **No tool call is needed, so there is no permission prompt.** On the follow-up Stop (`stop_hook_active` true), the hook reads that block from `last_assistant_message` (or the transcript if that is missing), saves the notes with the session id, and shows "handoff: saved N note(s)". It never blocks again in that session. |

The block Claude writes looks like this. Use 1-5 lines, kinds as needed; `dead end` and
`dead-end` are accepted; the `| files:` part is optional. A block holding the single line
`skip` records that nothing was worth keeping.

````
```handoff
decision: Cache tokens in Redis, not memory | files: app.py, cache/redis.py
dead_end: pickle sessions broke on py3.12
open: rotate the signing key
gotcha: tests need TZ=UTC
```
````

Notes live in `<project>/.claude/handoff/notes.jsonl`, one JSON object per line:
`id, created, session_id, kind, text, files, expires, status`. Per-session state
(injected ids, whether the stop prompt fired) is in `.claude/handoff/state/`.

Default expiry: decision 90 days, dead_end 60, gotcha 180, open 21. Closed and expired
notes are never injected.

## Install

```
/plugin marketplace add deepfieldstudios/deep-field-labs
/plugin install handoff@deep-field-labs
```

## Use

- `/handoff:notes` lists live notes (`--all` includes closed and expired).
- `/handoff:add <what to remember>` saves one note.
- CLI for manual use, from the project root (the Stop hook does not need it):
  ```
  python3 scripts/note.py add --kind decision --text "..." --files src/a.py src/b.py [--ttl-days 30]
  python3 scripts/note.py list | close <id> | prune | skip
  ```
  All writes are atomic (temp file + rename).

## Config

- `HANDOFF_HOME` overrides the store directory (used by the tests).
- `HANDOFF_SESSION_ID` or `--session` ties a manual CLI note to a session.

## Known limits

- If Claude ignores the request or garbles the block, nothing is saved and the session is
  not asked again. Lines that do not start with a known kind are dropped, as are template
  lines left as `<...>`. At most 5 notes are kept, all from the last `handoff` fence.
- `/handoff:add` still runs `note.py` through Bash, so it can trigger a permission prompt.
  That is acceptable because the user asked for it.
- Relevance is file overlap plus recency. There is no semantic matching, so a note with no
  files only surfaces if it is open or one of the 5 latest decisions.
- Failure detection in the transcript is heuristic: `is_error` results, or Bash output that
  looks like an error and is followed by a different command.
- Bash path extraction is token-based. It can pick up a path-like argument that is not
  really a file, and it misses paths built with variables or globs.
- Notes are per project directory and are not shared between machines. Add
  `.claude/handoff/` to `.gitignore`, or commit `notes.jsonl` if the team wants to share it.
- Basename matching on prompts ignores short names with no extension (fewer than 5 chars),
  to avoid false hits.

## v1 roadmap

- `/handoff:review` to merge duplicates and promote recurring gotchas into CLAUDE.md.
- Weight notes by how often they were useful when injected, and decay the ones never used.
- Optional shared store (a committed file, or a team directory) with author attribution.
- Detect "decision reversed" so a newer decision automatically closes the one it replaces.
