# Privacy policy

Applies to every plugin in the Deep Field Labs marketplace. Last updated 6 October 2026.

## Summary

The plugins run entirely on your computer. They collect nothing, send nothing to Deep
Field, and contain no analytics or tracking.

## What the plugins read

Each plugin reads only what Claude Code passes to its hooks (your prompt, the tool Claude is
about to use and its input, and the tool's output) plus, where noted, local files in the
project you are working in or Claude Code's own session transcripts on your machine.

## What the plugins store, and where

Everything is stored as plain files on your computer. Delete the folder and it is gone.

| Plugin | Stored | Location |
|---|---|---|
| secret-shield | SHA-256 fingerprints and masked forms of detected credentials, the credential type, time and where it was seen. Never the credential itself. | `~/.claude/secret-shield/` |
| receipts | Commands Claude ran, exit status and the last few hundred characters of output | `<project>/.claude/receipts/` |
| blast-radius | Risk decisions; recovery snapshots of files before risky commands (as git refs or file copies) | `<project>/.claude/blast-radius/`, `refs/blast-radius/*` |
| spend-meter | Token counts and estimated costs per session and per day | `~/.claude/spend-meter/` |
| handoff | Short notes Claude writes at the end of a session | `<project>/.claude/handoff/` |
| codebase-onboarder | Gotchas and verified commands; the ONBOARDING.md it writes | `<project>/.claude/onboarder/`, `ONBOARDING.md` |
| office-mode | Copies of documents taken before Claude changes them | `.versions/` beside the file, or `~/.claude/office-mode/` |
| diff-digest | Nothing | — |
| docs-pin | Nothing | — |
| prompt-regression | Test results and recorded model outputs | `<project>/prompt-tests/` |

## Network access

No plugin hook makes a network request. The one exception is **prompt-regression**, and
only when you run a test suite in live mode: it sends your test prompts to the Anthropic
API using your own API key. That request is between you and Anthropic and is covered by
Anthropic's terms and privacy policy. Deep Field never sees it.

## Contact

Deep Field — deepfieldstudios@gmail.com — https://deepfieldstudios.com
Issues: https://github.com/deepfieldstudios/deep-field-labs/issues
