---
description: Re-scan the repo and re-render ONBOARDING.md (keeps verified marks, gotchas and the keep block)
---
Refresh ONBOARDING.md:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scan.py" --root .
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render.py" --root .
```

Then report in two or three lines what changed in the Commands section (new or removed candidates) and any new gotcha candidates. Do not run commands unless the user asks; use `/onboarder:init` for verification.
