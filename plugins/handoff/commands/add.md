---
description: Add a handoff note (decision, dead end, open item or gotcha) for future sessions
argument-hint: "[what to remember]"
---
The user wants to save a handoff note. Their input: $ARGUMENTS

1. If the input is empty, ask what to remember. Otherwise work out from it (and this session's context):
   - kind: `decision` (chosen approach and why), `dead_end` (tried, failed, why), `open` (unfinished), or `gotcha` (surprising trap)
   - text: one or two plain sentences a future session can act on
   - files: the repo paths it relates to, if any
2. Run, quoting the text:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/note.py" add --kind <kind> --text "<text>" --files <path> <path>
```

3. Report the id it prints in one line. Do not add more than the user asked for.
