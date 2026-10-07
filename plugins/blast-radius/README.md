# blast-radius

Risk-scored permissions for Claude Code.

People approve dangerous commands because they are tired of approving safe ones, or they
turn prompts off altogether. blast-radius scores every Bash command, and every Write/Edit
outside the project, from 0 to 100:

| Tier | Score | What happens |
|---|---|---|
| low | < 30 | Left to Claude Code's normal permissions. With `auto_allow` on (opt-in), auto-approved with no prompt. Covers `ls`, `cat`, `grep`, `git status/diff/log`, test runners, linters, `git add/commit`. |
| medium | 30–69 | No decision. The normal permission flow runs, with a one-line note on why it's medium. |
| high | ≥ 70 | You're asked, with a plain-English preview. For local destructive commands it takes a recovery snapshot first. |

Example `ask` reasons:

```
blast-radius 75/100 (high). Deletes 312 files (2 not tracked by git) under ./build.
Recovery point 20261006T190614Z-d0c7 (git ref refs/blast-radius/20261006T190614Z-d0c7) - see /blast-radius:restore

blast-radius 95/100 (high). Force-pushes to origin/main, rewriting remote history. Targets the main branch

blast-radius 100/100 (high). Deletes every row in users (DELETE without WHERE). Runs against production (db.prod.internal)
```

## How scoring works

Each simple command is scored on four axes, which are added together and capped at 100:

- **Reversibility.** `rm` (rm -rf scores more), `git reset --hard`, `git clean -f`,
  `git push --force`, `git branch -D`, `git checkout -- .`, DROP / TRUNCATE / DELETE or UPDATE
  without WHERE in `psql`, `mysql` or `sqlite3` strings, `dd`, `mkfs`, `chmod/chown -R`, `>` over
  an existing file, `find -delete`, `rsync --delete`, `shred`, `docker volume rm`.
- **Scope.** `git push`, wrangler / vercel / netlify / fly / kubectl / terraform / helm / pulumi,
  mutating verbs in aws / gcloud / az, `ssh` (the remote command is scored too),
  curl/wget with POST, PUT, PATCH or DELETE, `npm publish`, `docker push`, `gh` writes.
- **Environment.** `prod`, `production` or `--prod` anywhere in a command that already does
  something, plus pushes to main, master or release.
- **Breadth.** Globs, recursive flags, `/`, `~`, system and dotfile paths, paths outside the
  project, and `$VAR/` where the variable is unset (an empty value turns it into `/`).

The parser splits compound commands on `&&`, `||`, `;`, `|`, `&` and newlines. It understands
`sudo`/`env`/`xargs`/`timeout`/`npx` prefixes, `cd` between steps, `bash -c '...'`, `eval`,
`$(...)` and backticks, `find -exec`, redirects and heredocs. The whole command gets the score
of its riskiest part. **A command it does not recognise scores 30 (medium), so nothing it
doesn't understand is ever auto-approved.**

## Previews (read-only)

- `rm`: walks the targets (globs expanded, capped at 5000 files and 2s), counts the files, and
  uses `git ls-files` to count how many are not tracked by git.
- `git reset --hard`: lists the tracked files with uncommitted changes, from `git status --porcelain`.
- `git clean`: runs `git clean -n` with the same flags, which is a dry run.
- `find ... -delete`: runs the same `find` without `-delete` and counts the matches.
- `>` / `mv` / `cp` over an existing file: shows the current size and line count.

## Snapshots

Before a high-risk local destructive command is put to you:

- **Inside a git repo:** the hook commits the working tree (tracked, untracked, and any
  *ignored* files the command would delete, if they total under 50 MB) through a temporary
  `GIT_INDEX_FILE`. It runs `git add -A`, `write-tree` and `commit-tree`, and stores the result at
  `refs/blast-radius/<timestamp>`. Your working tree, index, HEAD and stash are not touched.
- **Outside git** (or when the target contains `.git` itself): it copies the files to
  `<project>/.claude/blast-radius/snapshots/<id>/` if they total under 50 MB. Otherwise the
  reason says `no snapshot (too large)`.

`/blast-radius:restore [id]` lists the recovery points and prints the exact commands, for example
`git checkout refs/blast-radius/<id> -- .`, or a `cp -Rp` from the copy. It never restores
anything by itself.

## Install

```
/plugin marketplace add deepfieldstudios/deep-field-labs
/plugin install blast-radius@deep-field-labs
```

It needs Python 3 (standard library only) and git. Hooks make no network calls.

## Config

Optional, at `<project>/.claude/blast-radius.json`:

```json
{
  "thresholds": { "low": 30, "high": 70 },
  "allow": ["^make dev$"],
  "ask":   ["\\bnpm install\\b"],
  "deny":  ["terraform\\s+destroy"],
  "auto_allow": false,
  "snapshot": { "enabled": true, "max_mb": 50 },
  "preview_max_files": 5000,
  "show_medium": true
}
```

`allow`, `ask` and `deny` are Python regexes matched against the full command. `deny` wins
over `allow`. **`auto_allow` is off by default:** blast-radius only adds warnings, previews and snapshots, and
never approves anything on your behalf. Set `"auto_allow": true` in a project config, or
`BLAST_RADIUS_AUTO_ALLOW=1` in your environment for every project, to have low-risk commands
approved without a prompt.

State lives in `<project>/.claude/blast-radius/`, or in `$BLAST_RADIUS_HOME` if that is set:

- `decisions.jsonl`: every decision, with score, tier and reasons.
- `snapshots.jsonl`: an index of the recovery points.
- `snapshots/<id>/`: file copies of non-git snapshots.

## Known limits

- **It's heuristics, not a sandbox.** A script that deletes files (`python cleanup.py`) scores
  as unknown (medium), not high. Only inline `-c`/`-e` code is inspected.
- **With `auto_allow` on, a low-tier `allow` skips Claude Code's own prompt for that command.**
  Your settings' deny rules still apply, and any other hook's `deny` (secret-shield's, for
  example) still wins. It is off by default.
- **Snapshots are only as good as the moment they're taken.** They happen when the hook runs,
  not right before execution. Copy snapshots skip anything over the size or file cap.
  `git add -A` hashes large untracked files into `.git/objects`. Snapshot refs keep those
  objects alive until you delete the ref.
- Remote state (pushes, deploys, databases) can't be snapshotted. The hook just asks clearly.
- Command parsing is a pragmatic tokenizer, not bash. Exotic syntax may score as unknown.
- The production check is keyword-based (`prod`, `production`, `prd`). An environment called
  `live` isn't detected.

## What v1 would add

- Snapshot pruning (keep the last N, or N days) and a `--restore <id>` mode that runs the restore
  after confirmation.
- Learned allowlists: suggest `allow` rules from commands you approve again and again.
- Per-project "production markers" (hostnames, kube contexts, AWS profiles) in the config.
- Deeper previews: `terraform plan` summaries, row counts for SQL DELETE (`EXPLAIN`/`COUNT(*)`
  through a read-only connection), and listing the remote commits a force-push would drop.
- A PostToolUse check that the snapshot covered what was actually deleted.
