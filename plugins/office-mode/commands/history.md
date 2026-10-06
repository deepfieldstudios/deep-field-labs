---
description: Show the saved earlier copies of a file (or of every file in this folder)
argument-hint: "[file name]"
---
The person wants to see the earlier copies office-mode has saved.

Run this with the Bash tool (leave the file name off if none was given):

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/versions.py" --cwd "$PWD" list $ARGUMENTS

Then explain the result in plain words: which file, which folder it is in, how many copies there are
and when they were saved (use the day names and times shown). Do not paste the raw output unless it
is short. Finish by saying they can put any copy back, for example "restore budget.xlsx yesterday",
and that putting a copy back can itself be undone.
