---
description: Show the dependency versions docs-pin detected for this project
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/show_versions.py")
---
Pinned versions detected by docs-pin:

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/show_versions.py"`

Show the table above to the user verbatim. If any row says "declared, not locked", mention in one sentence that installing dependencies (or committing a lockfile) would give exact versions. Do not run anything else.
