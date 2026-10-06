---
description: Scan this repo and build a living ONBOARDING.md, verifying the safe commands
---
Build ONBOARDING.md for the current repo.

1. Scan and render:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scan.py" --root .
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render.py" --root .
```

2. Read the `## Commands` section of ONBOARDING.md. Try only the **safe** candidates, in this order: install, then build, then test, then lint. Never run anything marked `deploy`, anything that publishes, pushes, migrates a real database, or sends network traffic beyond fetching dependencies. Skip `dev`/long-running server commands (note them as not tried). Run each with a sensible timeout. If a command needs an env var or a service, do not invent secrets; record the failure and the reason.

3. Record each outcome (one line note: what happened, how long, any prerequisite you found):

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify.py" mark "<exact command>" ok "<note>"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify.py" mark "<exact command>" fail "<why>"
```

If a different command turned out to be the working one (e.g. it needs `--legacy-peer-deps`), mark that exact command ok too.

4. Re-render: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render.py" --root .`

5. Skim the result for wrong guesses (directory roles, entry points). Put corrections and anything a newcomer must know inside the `<!-- onboarder:keep -->` block, which survives re-renders. Finish with a short summary: what is verified, what failed and why.
