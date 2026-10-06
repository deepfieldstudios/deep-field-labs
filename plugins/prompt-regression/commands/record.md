---
description: Call the live model and save its outputs as the golden copies used for replay and similarity checks
argument-hint: "[suite] [--case id] [--model id]"
---
Record golden outputs from the live model. This calls the Claude API (it costs tokens) and needs
ANTHROPIC_API_KEY. If the key is not set, say so and stop.

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" --project "$PWD" --record --provider anthropic $ARGUMENTS

Afterwards:
1. List which golden files were written under prompt-tests/.golden/ and show the first lines of a few
   outputs so the user can judge whether they are good examples.
2. Point out any case whose assertions failed while recording: a golden copy of a bad answer will make
   future runs compare against the wrong thing.
3. Remind the user to review and commit prompt-tests/.golden/ so CI can replay it.
