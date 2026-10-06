---
description: Digest the current diff by intent and risk, then review the riskiest hunks properly
argument-hint: "[git range | --staged | --diff-file F]"
---
Step 1. Run this with the Bash tool and read the whole output:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/digest.py" $ARGUMENTS
```

If it fails (not a git repo, no main/master branch), say why in one line and ask which range to use.

Step 2. For every hunk under "Read these first", open the file at the given line with the Read tool (read enough surrounding code to understand it, and the matching test file if one exists). Do not review from the digest text alone.

Step 3. Write the review, in this shape and nothing more:

- **Verdict**: one line (approve / approve with nits / changes needed).
- **Findings**: one bullet per real problem in the top-risk hunks: `file:line`, what is wrong, why it matters, the fix. Focus on bugs, removed error handling, security or permission changes, behaviour changes without tests, and deleted tests. If a flagged hunk is fine, say so in a few words rather than inventing an issue.
- **Missing tests**: specific tests that should exist for the changed behaviour.
- **Batch-approve**: one line per group from "Safe to batch-approve" (for example "mechanical: 14 hunks, imports and whitespace"). Do not review these hunk by hunk unless something in them looked wrong while reading.

Keep it short. Do not restate the digest tables.
