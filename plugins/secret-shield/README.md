# secret-shield

Stops credentials leaving the machine, and keeps a register of the ones that need rotating.

API keys end up pasted into prompts, hard-coded into files, put in URLs, committed and
pushed. secret-shield checks every one of those exits:

| Hook | Where | What it does |
|---|---|---|
| UserPromptSubmit | your prompt | **Blocks** a prompt that contains a credential. The reason names the kind and a masked form, e.g. `Anthropic API key (sk-a...ez)`, and tells you to use an env var. |
| PreToolUse | Write / Edit / MultiEdit / NotebookEdit | **Denies** writing a literal secret into a file, unless the file is a `.env*` file (templates like `.env.example` don't count) or is gitignored (`git check-ignore`). |
| PreToolUse | Bash | **Denies** `git commit` when the staged diff adds a secret, and `git push` when the outgoing commits add one. **Denies** literal secrets sent to the network (curl, wget, ssh, scp, nc, gh, aws ...), written into a file that isn't ignored, or put in a commit message. |
| PreToolUse | WebFetch / WebSearch / `mcp__*` | **Denies** a call when any input string, at any depth, contains a secret. |
| PostToolUse | Read / Bash | If tool output held a secret, adds `additionalContext` telling Claude not to repeat it, warns you, and logs the exposure. |

Test-mode keys (`sk_test_`, `rk_test_`) are low severity. They get a warning and are never
blocked.

## Detectors

| Detector | Matches |
|---|---|
| AWS | access key IDs (`AKIA`/`ASIA` + 16), and secret keys next to `aws_secret` |
| GitHub | `ghp_` `gho_` `ghu_` `ghs_` `ghr_` `github_pat_` |
| Slack | tokens (`xox[baprs]-`) and webhooks |
| Stripe | `sk_live_` and `rk_live_` (`sk_test_` is low severity) |
| Anthropic | `sk-ant-` |
| OpenAI | `sk-proj-`, `sk-svcacct-`, and legacy `sk-` + 48 characters |
| Google | API keys (`AIza` + 35) |
| Private keys | PEM blocks |
| JWTs | `eyJ…` |
| Generic | `password` / `secret` / `token` / `api_key` … assignments with Shannon entropy ≥ 3.5 and length ≥ 16 |
| Database URLs | postgres, mongo, mysql, redis and amqp URLs with an embedded password |

These are treated as placeholders and ignored: `xxxx`, `<your-key>`, `example`, `${VAR}`,
`{{ var }}`, `$(cmd)`, `process.env.X`, `os.environ[...]`, `...`, repeated characters, and
dummy DB passwords (`password`, `postgres`, `root`). Plain identifiers like `MockPasswordManager`
are ignored too. In a test over 2,000+ files of the Python standard library, nothing was flagged.

## Rotation register

The register is `~/.claude/secret-shield/register.json` (or `$SECRET_SHIELD_HOME/register.json`),
mode 600. Each entry is keyed by the **SHA-256 fingerprint** of the value and holds the kind,
provider, masked form (first 4 + last 2 characters), first and last seen, a count, and the last
20 events (`where`: prompt / file path / tool / URL, and `action`: `blocked`,
`exposed-in-context` or `warned`). **The raw secret is never written anywhere.** Locations are
redacted before they're stored.

`/secret-shield:register` prints the entries that still need action. Exposed ones come first,
marked `ROTATE NOW`. Each entry shows its rotation link (AWS IAM console,
github.com/settings/tokens, dashboard.stripe.com/apikeys, console.anthropic.com/settings/keys,
platform.openai.com/api-keys, Slack app config, Google Cloud credentials), followed by a
rotation checklist. Options:

- `--rotated <fp>` marks a credential as rotated.
- `--ignore <fp>` marks a false positive, which every hook then skips. `--unignore <fp>` undoes it.
- `--all` includes rotated and ignored entries.

Every block message includes the fingerprint prefix you need for `--ignore`.

To check whether a key you hold is in the register, without pasting it anywhere:
`printf %s "$MY_KEY" | shasum -a 256`.

## Install

```
/plugin marketplace add deepfieldstudios/deep-field-labs
/plugin install secret-shield@deep-field-labs
```

It needs Python 3 (standard library only). It uses git for the commit and push checks.
Hooks make no network calls.

## Config

There is no config file in v0.1.

- `SECRET_SHIELD_HOME` moves the register.
- Putting `secret-shield:allow` in a prompt sends it anyway. The secret is logged as
  `exposed-in-context`.
- `/secret-shield:register --ignore <fp>` handles false positives.

## Known limits

- **It's regex-based.** It misses credentials with no recognisable shape, unless they're
  assigned to a secret-looking name. Short or low-entropy passwords are deliberately not
  flagged.
- **The push check is approximate.** It scans the commits between `@{u}` and HEAD (or commits
  on no remote), up to 200 of them. It doesn't resolve explicit refspecs such as
  `git push origin other-branch`.
- **Bash coverage is limited.** A secret produced at runtime, such as `curl -d @file` or
  `cat key | curl`, isn't visible in the command text. The PostToolUse check only sees it if
  it's printed.
- **The fingerprint is a plain, unsalted SHA-256.** That lets you match it yourself, but a weak
  password's fingerprint could be brute-forced. Real API keys are far too long for that.
- The `.env*` and gitignore exemption trusts your `.gitignore`. It doesn't check whether the
  file is already tracked.
- A blocked prompt is gone from the conversation. You need to re-type it without the secret.

## What v1 would add

- Live validity checks, opt-in and run outside the hooks: is this key still active?
- Auto-rotation helpers for providers with APIs (AWS IAM, GitHub fine-grained tokens).
- A pre-commit hook installer, so the same check protects commits made outside Claude.
- Per-project config: extra detectors, allowed paths, and severity overrides.
- Optional salted fingerprints, and an entropy-only detector for unknown token formats.
- Redacting secrets from transcripts after the fact.
