---
description: List blast-radius recovery points and show how to restore them
argument-hint: "[snapshot-id]"
---
Run this with the Bash tool and show the user its output exactly as printed:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/restore.py" $ARGUMENTS --cwd "$PWD"
```

Then, in two or three sentences, explain which command would restore what they most likely want back. Do NOT run any restore, checkout, cp or update-ref command yourself unless the user explicitly asks you to; restoring overwrites current files.
