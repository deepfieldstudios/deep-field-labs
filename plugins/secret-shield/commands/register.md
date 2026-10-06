---
description: Show the secret-shield rotation register (fingerprints only) with a rotation checklist
argument-hint: "[--all | --rotated <fingerprint> | --ignore <fingerprint> | --unignore <fingerprint>]"
---
Run this with the Bash tool and show the user its output exactly as printed:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/show_register.py" $ARGUMENTS
```

Then summarise in two or three sentences: how many credentials need rotating (those marked "ROTATE NOW (exposed)" first), and the provider link for each. Never ask the user to paste a secret, and never print one: the register only holds masked values and fingerprints, and that is all you should refer to.
