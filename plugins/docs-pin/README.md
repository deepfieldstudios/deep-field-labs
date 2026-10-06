# docs-pin

Version-correct APIs. Claude often writes code against the version of a library it remembers best, not the one the project has installed. docs-pin tells Claude the installed versions at the start of a session and checks each edit for APIs that are deprecated or removed in those versions.

## What it does

1. **SessionStart.** Reads the manifests and lockfiles in the project root and adds a compact "Pinned versions in this project" table to Claude's context. The table lists runtimes first, then direct dependencies, then dev dependencies, up to 40 rows. It also tells Claude to write for exactly those versions and to read the `node_modules` or `site-packages` source when it is unsure.
   - **npm:** `package.json` plus `package-lock.json` (v1 to v3: `packages[""]` and the `node_modules/x` entries), `pnpm-lock.yaml` (v5, v6, v9, read line by line rather than with a YAML parser) and `yarn.lock` (classic and berry). When `node_modules/<pkg>/package.json` exists, its version wins.
   - **Python:** `requirements.txt` (and `requirements-dev.txt`), `pyproject.toml` (PEP 621, dependency-groups, Poetry), `poetry.lock` and `uv.lock`. A project virtualenv (`.venv`, `venv` or `env`) and its `*.dist-info` folders win.
   - **Other ecosystems:** `Cargo.toml` with `Cargo.lock`, `go.mod`, and `Gemfile.lock`.
   - **Runtimes:**
     - Node: `.nvmrc`, `.node-version`, or `engines.node`.
     - Python: `.python-version`, then `requires-python`, then the interpreter running the hook.
     - Go: the `go` directive.
2. **PreToolUse on Edit|Write|MultiEdit.** Scans the new text against `data/rules.json`, which holds 32 rules covering React 18 and 19, the Next.js App Router, the OpenAI SDK in Python (1.x) and Node (4.x), Pydantic v2, Express 5, moment, request, Node `fs.exists`, Python 3.12, pandas 2, SQLAlchemy 2, Tailwind 4 and the legacy Anthropic Text Completions API. A rule fires only when:
   - its package is present at a version inside its range;
   - the file matches its globs;
   - the edit adds the pattern rather than keeping a call that was already there;
   - the rule's optional `context` regex appears in the file (this is how `.dict()` is only flagged in files that use pydantic).

   A `deny` rule returns `permissionDecision: "ask"`, with the reason shown to the user. docs-pin never hard-denies an edit.
3. **PostToolUse on Edit|Write|MultiEdit.** The same scan runs again after the edit lands, and every hit, warn or deny, is fed back to Claude through `{"decision":"block","reason":...}` so it can fix the call. This is where `warn` rules surface.

`/docs-pin:versions` prints the full table, with no row cap.

## Why warn rules report after the edit

The brief suggested returning `permissionDecision: "allow"` with a reason for warn rules. That auto-approves an edit that the user's permission mode might otherwise have prompted for, and Claude never sees an allow reason. So by default a warn rule says nothing before the edit and tells Claude afterwards. Set `DOCS_PIN_WARN=allow` to get the literal allow-with-reason behaviour.

## Install

```
/plugin marketplace add deepfieldstudios/deep-field-labs
/plugin install docs-pin@deep-field-labs
```

## Config

| Env var | Effect |
|---|---|
| `DOCS_PIN_WARN=allow` | Warn-only hits return `allow` plus a reason in PreToolUse. By default they are silent there and reported in PostToolUse |
| `DOCS_PIN_RULES=/path/rules.json` | Use a different rules file |

A rule in `data/rules.json` has these fields:

- `id`
- `package`: `npm:`, `pypi:`, `cargo:`, `go:`, `gem:` or `runtime:` followed by the name
- `range`: comparators such as `>=18.0.0 <19.0.0`, `^5`, `~=2.5`, `*`, joined with `||`
- `pattern`: a regex
- `flags`: `i`, `m`, `s`
- `globs`
- `context`: optional regex
- `message`
- `severity`: `deny` or `warn`

docs-pin writes no state.

## Known limits

- Only the project root (`cwd`) is read. Monorepo workspaces, nested `package.json` files and pnpm importers other than `.` are ignored.
- When nothing is locked or installed, the lowest version the declared spec allows stands in for the real one (for example `^18.2.0` becomes `18.2.0`), and the table marks the row "declared, not locked".
- The pnpm and yarn parsers use pattern matching, not a YAML parser. TOML files need Python 3.11+ (`tomllib`); on older versions they are skipped.
- The rules are regexes over the new text. They do not understand scope or imports, so the pandas `.append` and SQLAlchemy `session.query` rules are heuristics and are set to `warn`.
- When no Python version is pinned, the Python runtime falls back to the interpreter running the hook, which may not be the project's.
- PostToolUse feedback arrives after the edit has landed. Claude fixes the call in a follow-up edit.

## v1 roadmap

- Workspace awareness: pick the nearest manifest to the file being edited, and read every pnpm/yarn/npm workspace.
- Generate rules from changelogs and type definitions in `node_modules` (for example `@deprecated` JSDoc tags), instead of a hand-written list.
- Per-project rule overrides and suppressions, such as an inline `docs-pin: ignore` comment.
- Cache the parsed versions, keyed on lockfile modification times.
- Check imports of symbols that do not exist in the installed version, from `.d.ts` files and `__all__`.
