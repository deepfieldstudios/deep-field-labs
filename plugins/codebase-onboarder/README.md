# codebase-onboarder

A living `ONBOARDING.md` for humans and agents. It starts with a static scan of the repo,
then gets more accurate as commands are run: candidate commands are marked verified when
they succeed, and gotchas are captured when a command fails and a variant then works.

## What it produces

`ONBOARDING.md` at the repo root, with these sections:

- **Stack**: languages by file count, frameworks and tools (from package.json, pyproject,
  requirements, Cargo, go.mod, Gemfile, Dockerfile, docker-compose), required runtime
  versions, manifests, entry points, test dirs.
- **Map**: top-level directories (plus members of `plugins/`, `packages/`, `apps/` and similar)
  with file counts and a guessed role.
- **Commands**: install, dev, test, build, lint and deploy candidates from package.json
  scripts, Makefile targets, justfile, Taskfile, and language defaults. Each one is
  `unverified` until it has been run. Deploy commands are flagged and never run by `/onboarder:init`.
- **Environment**: env var *names* from `.env.example`-style files and from source
  (`process.env.X`, `os.environ[...]`, `os.getenv`, `os.Getenv`, `env::var`, `ENV[...]`).
  Real `.env` files are never read, and test files are excluded.
- **CI**: GitHub Actions workflows with jobs and `run:` steps. Other CI files are listed.
- **Hotspots**: the most-changed files from `git log` (skipped outside git) and the largest files.
- **Gotchas**: candidates captured by the hook.
- A keep block. Anything between `<!-- onboarder:keep -->` and `<!-- /onboarder:keep -->`
  survives every re-render, so put human corrections there.

## Hooks

| Hook | What it does |
|---|---|
| `SessionStart` | If `ONBOARDING.md` exists, injects its Commands and Gotchas sections (capped at 1200 chars). If not, and the repo has more than 20 files, suggests `/onboarder:init`. |
| `PostToolUse` / `PostToolUseFailure` (Bash) | Records failing commands per session. When a later *variant* succeeds (same program and similar text, or a command that contains the failed one), it saves a gotcha such as "`npm run build` failed (Cannot find module 'vite'); `npm install && npm run build` worked". It also matches known signatures: missing env var, port in use, a required node/python/etc. version, and a service that is not running (Postgres, Redis, MySQL, Mongo, Docker). Gotchas are deduplicated by key. A successful run of a scanned candidate command marks it verified ok. |

## Install

```
/plugin marketplace add deepfieldstudios/deep-field-labs
/plugin install codebase-onboarder@deep-field-labs
```

## Use

- `/onboarder:init`: scans, renders, tries the safe commands (install, build, test, lint),
  records the outcomes, then re-renders.
- `/onboarder:refresh`: re-scans and re-renders. Verified marks, gotchas and the keep block
  are kept.
- Scripts (stdlib only):
  ```
  python3 scripts/scan.py --root . [--out FILE|-]
  python3 scripts/render.py --root . [--scan FILE] [--out FILE]
  python3 scripts/verify.py mark "<cmd>" ok|fail "<note>"
  python3 scripts/verify.py list
  ```

## Config and state

State lives in `<repo>/.claude/onboarder/`: `scan.json`, `verified.json`, `gotchas.jsonl`,
and `state/<session>.json` (recent failures). `ONBOARDER_HOME` overrides the location.
To retire a gotcha, set its `"status"` to `"dismissed"` in `gotchas.jsonl`.

## Known limits

- The scan is static and heuristic. YAML (workflows, compose, Taskfile) is parsed with
  line regexes, not a real parser, so unusual layouts can be missed. Directory roles are
  guesses from names or the dominant file type.
- Manifests are only read in the top two directory levels. Deeper monorepo packages show
  up in the Map but their scripts are not parsed.
- Whether `PostToolUse` fires for a failing Bash command depends on the Claude Code version.
  The plugin also registers `PostToolUseFailure` and reads `exit_code` when it is present.
- Variant matching can pair a failure with an unrelated later command that uses the same
  program. That is why captured gotchas are labelled *candidate*.
- Hotspot churn reads only the last 500 commits.

## v1 roadmap

- A `/onboarder:gotchas` review command to accept or dismiss candidates.
- Parse nested workspace manifests (npm/pnpm workspaces, Cargo workspaces, go.work).
- Prefer the command CI actually runs when it differs from the local candidate.
- Flag staleness: warn at session start when manifests changed after the last scan.
