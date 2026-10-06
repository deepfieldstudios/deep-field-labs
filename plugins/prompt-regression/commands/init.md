---
description: Create a starter prompt test suite at prompt-tests/example.json
---
Scaffold a starter suite:

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/init.py" --project "$PWD"

It only writes prompt-tests/example.json and never overwrites an existing file. Then:
1. Explain the suite format briefly (name, system or prompt_file, model, cases with input and expect).
2. Look for the project's real prompts (prompts/, *.prompt.md, system prompt strings in code) and offer
   to replace the example with a suite that points at the real prompt via "prompt_file", using the
   write-prompt-tests skill to harvest realistic cases.
3. Suggest adding `prompt-tests/.results/` to .gitignore and committing `prompt-tests/.golden/`.
4. For CI, point them at the GitHub Actions snippet in this plugin's README (do not write a workflow
   file unless asked).
