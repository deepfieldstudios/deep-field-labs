---
description: List live handoff notes for this project (decisions, dead ends, open items, gotchas)
argument-hint: "[--all] [--kind decision|dead_end|open|gotcha]"
---
Run this and show the output to the user as-is (it is already compact):

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/note.py" list $ARGUMENTS
```

If an open item is clearly done, offer to close it with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/note.py" close <id>`. To drop expired notes, `note.py prune`.
