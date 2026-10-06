---
name: redline-document
description: Explain what changed between two versions of a Word document, contract, proposal or slide deck in plain English, paragraph by paragraph, like a lawyer's redline but readable. Also works for an earlier saved copy of the same file, e.g. "what did I change in the contract since Tuesday?".
---

# Redline two versions of a document

Use this when someone asks what is different between two Word files (`.docx`), two slide decks
(`.pptx`), two text documents, or between a file and an earlier saved copy of it.

## Steps

1. Work out the two versions:
   - Two separate files: the older one first, the newer one second.
   - One file compared with an earlier copy office-mode saved: use
     `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/versions.py" --cwd "$PWD" changes "<file>" <when>`
     where `<when>` is e.g. yesterday, tuesday, "2 hours ago" or a number from
     `/office-mode:history`. That prints the summary directly; skip to step 3.
2. Run the comparison:

   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/preview.py" "<older file>" "<newer file>" --max-lines 200
   ```

   It lists paragraphs added, removed and changed, quoting just the words around each change.
3. Write the redline for the person:
   - Start with one sentence: how many paragraphs changed, were added and were removed.
   - Then the changes that matter most first: money, dates, deadlines, names, obligations,
     anything that changes who must do what. Quote the old and new wording side by side.
   - Group small wording or punctuation tweaks together in one line at the end.
   - Don't give legal advice; do point out changes that look significant ("the payment period went
     from 30 days to 14 days").
4. If they want it as a document, write it to a new file (e.g. "contract - changes.md" or a .docx
   if asked) next to the newer version and say where it is. Never change either original.
