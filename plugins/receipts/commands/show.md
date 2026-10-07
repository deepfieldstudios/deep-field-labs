---
description: Show the latest receipt (commands run, and the verdict on each "done" claim) for this project
argument-hint: "[session-id]"
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/show_receipt.py":*)
---
Here is the receipt produced by the receipts plugin:

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/show_receipt.py" $ARGUMENTS`

Show the receipt above to the user verbatim. Afterwards, in one or two sentences, point out any claim whose verdict is not VERIFIED. Do not run anything else.
