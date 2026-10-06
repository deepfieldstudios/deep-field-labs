# office-mode

Claude for people who work in spreadsheets, documents, PDFs and slides, not code.

The two fears it addresses: Claude overwriting your only copy of something, and not being able
to tell what changed. With office-mode on:

- **Every change to a document saves a copy first.** Before Claude rewrites or edits a document
  (spreadsheet, Word, slides, PDF, text/Markdown; never source code), or runs a command that looks
  like it will change, move or delete one, the current file is copied
  into a hidden `.versions` folder next to it. You see a short note like
  *"Saved a copy of budget.xlsx (restore with /office-mode:restore)"*.
- **Changes are explained in plain English.** After the change, Claude gets a before/after
  summary ("Sheet1!C14: 1,200 → 1,450", "Changed paragraph 2: …30 days… → …14 days…") and is told
  to explain it without jargon.
- **Anything can be put back**, using everyday words: `/office-mode:restore budget.xlsx yesterday`.
  Restoring saves the current file first, so it can be undone too.
- **Jargon-free by default.** At the start of every session Claude is told to avoid words like
  "diff" and "repo", to name files plainly and say which folder they are in.

## Install

```
/plugin marketplace add deepfieldstudios/deep-field-labs
/plugin install office-mode@deep-field-labs
```

## What's included

| Piece | What it does |
|---|---|
| `PreToolUse` hook (Write, Edit, MultiEdit, Bash) | Saves a copy of each existing file about to change. Never blocks anything. |
| `PostToolUse` hook (same tools) | Compares the saved copy with the file now and hands Claude a plain-English summary. |
| `SessionStart` hook | Sets the plain-language working style. |
| `/office-mode:history [file]` | Lists saved copies ("Tuesday 14:02 · 48 KB · before an edit"). |
| `/office-mode:restore <file> <when>` | Puts a copy back. `<when>`: `yesterday`, `tuesday`, `tuesday 14:02`, `2 hours ago`, `latest`, `oldest`, `undo`, a number from the history, or a date. |
| Skill: reconcile-spreadsheets | Match two CSV/Excel lists on a key column; report missing and mismatched rows. |
| Skill: clean-mailing-list | De-duplicate emails (ignoring capitals), trim, flag bad addresses, split names. Writes a new file. |
| Skill: redline-document | Plain-English redline of two Word/PowerPoint/text versions. |

Scripts can also be run directly:

```
python3 scripts/versions.py --cwd <folder> list [file]
python3 scripts/versions.py --cwd <folder> restore <file> <when>
python3 scripts/versions.py --cwd <folder> changes <file> <when>   # what differs from that copy
python3 scripts/preview.py <old> <new> [--name budget.xlsx]
```

## Where copies are kept

Default ("beside"): `<folder of the file>/.versions/<file name>/<YYYY-MM-DD_HHMMSS>__<sha8>.<ext>`
plus an `index.jsonl` (original path, time, size, sha256, session id, reason). Copies are deduped
by content, and the newest 50 per file are kept.

Central: set `"store": "central"` and everything goes to `~/.claude/office-mode/versions/<file
name>__<path hash>/` instead (override the root with `OFFICE_MODE_HOME`). Use this for folders
synced with Dropbox/OneDrive/iCloud or tracked in git, where a `.versions` folder would be noise.

Small bookkeeping for the hooks lives in `<project>/.claude/office-mode/pending.json`.

## Configuration

`~/.claude/office-mode/config.json` (global) and `<project>/.claude/office-mode/config.json`
(project, wins) — all keys optional:

```json
{
  "store": "beside",
  "retention": 50,
  "max_file_mb": 200,
  "extensions": [".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".docx", ".doc", ".pptx", ".ppt",
                 ".pdf", ".numbers", ".pages", ".key", ".odt", ".ods", ".odp", ".rtf", ".md", ".txt"]
}
```

Only files with these `extensions` are ever versioned, whether Claude changes them with
Write/Edit or a Bash command. Source code (`.py`, `.js`, ...) is never copied, since version
control covers it. Documents are versioned everywhere, including inside git working trees,
because spreadsheets and documents are rarely committed. Setting `extensions` replaces the list.

## Known limits

- **Bash detection is a heuristic.** It looks for watched file names in the command, in heredocs,
  and inside a script being run (`python3 fix.py`). A script that builds file names at runtime
  (`f"{month}.xlsx"`) or walks a folder is not caught. It errs towards saving too much.
- Spreadsheet previews read stored values and formulas, not formatting, comments, charts, merged
  cells or number formats; dates appear as Excel serial numbers. `.xls`, `.numbers`, `.pages`,
  `.key` and `.pdf` are saved and restorable but only get a "size changed" summary.
- Bundle-style documents (old `.numbers`/`.pages` saved as folders) are not copied.
- The beside store writes a `.versions` folder into the person's own folders, which is outside the
  marketplace's usual `.claude/` state rule. That is deliberate (copies stay with the file and move
  with the folder) and switchable to central.
- Restoring while the file is open in Excel/Word may need the app to reopen the file.
- Edits made outside Claude (in Excel itself) are not versioned.

## What v1 would add

- Real formatting awareness for xlsx (number/date formats, so serials show as dates).
- PDF text extraction for summaries (needs a stdlib PDF text reader or optional tool).
- A "safe copy" mode: Claude edits `budget (Claude).xlsx` and the person accepts it.
- Pruning by age and total size, and a `/office-mode:cleanup` command.
- A simple HTML before/after view for spreadsheets with the changed cells highlighted.
- Coverage for MCP tools that write files (Google Drive, SharePoint connectors).

## Tests

```
cd plugins/office-mode && python3 -m unittest discover -s tests
```

Fixtures (xlsx, docx, pptx) are generated in the tests with `zipfile`; nothing touches the network.
