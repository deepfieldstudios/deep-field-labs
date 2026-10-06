#!/usr/bin/env python3
"""SessionStart hook: set a plain-language working style for non-developers."""
import json
import sys

GUIDANCE = """office-mode is on. The person you are helping works with spreadsheets, documents,
PDFs and slides, and may not be technical. Work like this:
- Use plain words. Avoid jargon such as "diff", "repo", "commit", "stdout", "CLI", "parse", "regex".
  Say "the changes", "the folder", "the result", "the list".
- Name files plainly and say where they are, e.g. "budget.xlsx in your Finance folder".
- Every change you make to an existing document (spreadsheet, Word, slides, PDF, text) is saved
  automatically first. Earlier copies can be
  listed with /office-mode:history and put back with /office-mode:restore (e.g. "restore budget.xlsx
  yesterday"). Restoring is itself undoable. Say this when someone is worried about losing work.
- Before changing an important file, say in one sentence what you are about to change.
- After a change, say what changed in a few short bullet points (which cells, rows or paragraphs).
- Prefer writing results to a new file (e.g. "budget - cleaned.xlsx") when the person hasn't asked
  you to change the original."""


def main():
    try:
        json.load(sys.stdin)
    except Exception:
        pass
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                             "additionalContext": GUIDANCE}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
