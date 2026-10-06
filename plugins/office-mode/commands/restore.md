---
description: Put back an earlier copy of a file, e.g. "budget.xlsx yesterday" or "report.docx 2 hours ago"
argument-hint: "<file name> [when: yesterday | tuesday | 2 hours ago | undo | a number from the history]"
---
The person wants to put back an earlier copy of a file. Their request: $ARGUMENTS

1. If they did not say which file, ask. If they did not say which copy, first run
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/versions.py" --cwd "$PWD" list "<file>"`
   and ask which one they want, describing the options by day and time.
2. Before restoring, show what would change by running
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/versions.py" --cwd "$PWD" changes "<file>" <when>`
   and summarise it in a couple of plain sentences.
3. Restore it:
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/versions.py" --cwd "$PWD" restore "<file>" <when>`
   `<when>` can be words like yesterday, tuesday, "tuesday 14:02", "2 hours ago", latest, oldest,
   undo (reverses the last restore), or a number from the list (1 = newest).
4. Tell them plainly which file was put back, where it is, which copy (day and time) it now matches,
   and that the version they had a moment ago was saved too, so they can undo this.

If the file is open in Excel, Word, PowerPoint, Numbers or Pages, remind them to close and reopen it to
see the restored copy.
