#!/usr/bin/env python3
"""Static repo scan -> JSON. Standard library only; never executes project code.

  scan.py [--root DIR] [--out FILE|-]     default out: $ONBOARDER_HOME/scan.json
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import onboarder_lib as ol  # noqa: E402

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build",
             "target", ".next", ".nuxt", ".cache", "vendor", ".tox", ".mypy_cache",
             ".pytest_cache", "coverage", ".idea", ".vscode", ".turbo", ".svelte-kit", "out"}
KEEP_HIDDEN = {".github", ".circleci", ".gitlab"}
MAX_FILES = 20000
MAX_READ = 256 * 1024
LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock",
             "Gemfile.lock", "go.sum", "composer.lock", "uv.lock", "bun.lockb"}

LANG_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".go": "Go", ".rs": "Rust", ".rb": "Ruby",
    ".java": "Java", ".kt": "Kotlin", ".swift": "Swift", ".c": "C", ".h": "C", ".cc": "C++",
    ".cpp": "C++", ".hpp": "C++", ".cs": "C#", ".php": "PHP", ".scala": "Scala", ".ex": "Elixir",
    ".exs": "Elixir", ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".lua": "Lua",
    ".dart": "Dart", ".vue": "Vue", ".svelte": "Svelte", ".html": "HTML", ".css": "CSS",
    ".scss": "CSS", ".sql": "SQL", ".md": "Markdown", ".tf": "Terraform",
}
CODE_LANGS = set(LANG_BY_EXT.values()) - {"Markdown", "HTML", "CSS"}
SOURCE_EXTS = {e for e, l in LANG_BY_EXT.items() if l in CODE_LANGS or l in ("Vue", "Svelte")}

JS_FRAMEWORKS = {
    "next": "Next.js", "react": "React", "vue": "Vue", "svelte": "Svelte", "@sveltejs/kit": "SvelteKit",
    "nuxt": "Nuxt", "astro": "Astro", "@remix-run/react": "Remix", "express": "Express",
    "fastify": "Fastify", "koa": "Koa", "@nestjs/core": "NestJS", "hono": "Hono", "vite": "Vite",
    "webpack": "webpack", "typescript": "TypeScript", "jest": "Jest", "vitest": "Vitest",
    "mocha": "Mocha", "@playwright/test": "Playwright", "cypress": "Cypress",
    "tailwindcss": "Tailwind CSS", "prisma": "Prisma", "drizzle-orm": "Drizzle", "electron": "Electron",
    "eslint": "ESLint", "prettier": "Prettier", "wrangler": "Wrangler (Cloudflare)",
}
PY_FRAMEWORKS = {"django": "Django", "flask": "Flask", "fastapi": "FastAPI", "pytest": "pytest",
                 "sqlalchemy": "SQLAlchemy", "celery": "Celery", "pydantic": "Pydantic",
                 "streamlit": "Streamlit", "ruff": "Ruff", "black": "Black", "mypy": "mypy",
                 "uvicorn": "Uvicorn"}
RUST_FRAMEWORKS = {"tokio": "Tokio", "actix-web": "Actix Web", "axum": "Axum", "rocket": "Rocket",
                   "serde": "Serde", "clap": "clap"}
GO_FRAMEWORKS = {"github.com/gin-gonic/gin": "Gin", "github.com/labstack/echo": "Echo",
                 "github.com/gofiber/fiber": "Fiber", "github.com/spf13/cobra": "Cobra",
                 "github.com/go-chi/chi": "chi"}
RUBY_FRAMEWORKS = {"rails": "Rails", "sinatra": "Sinatra", "rspec": "RSpec", "sidekiq": "Sidekiq"}

DIR_ROLES = {
    "src": "source code", "services": "services (monorepo)", "libs": "libraries (monorepo)",
    "crates": "Rust crates (workspace)", "lib": "library code", "app": "application code", "apps": "applications (monorepo)",
    "packages": "packages (monorepo)", "plugins": "plugins", "cmd": "Go command entry points",
    "pkg": "Go packages", "internal": "internal packages", "server": "server code", "client": "client code",
    "api": "API code", "web": "web front end", "frontend": "front end", "backend": "back end",
    "test": "tests", "tests": "tests", "__tests__": "tests", "spec": "tests", "e2e": "end-to-end tests",
    "docs": "documentation", "doc": "documentation", "scripts": "helper scripts", "bin": "executables/scripts",
    "tools": "tooling", "config": "configuration", "conf": "configuration", ".github": "GitHub config / CI",
    "public": "static assets served as-is", "static": "static assets", "assets": "assets",
    "migrations": "database migrations", "db": "database", "infra": "infrastructure", "terraform": "infrastructure",
    "deploy": "deployment config", "k8s": "Kubernetes manifests", "examples": "examples",
    "components": "UI components", "pages": "page routes", "routes": "routes", "styles": "styles",
    "fixtures": "test fixtures", "data": "data files", "templates": "templates", "site": "website",
}
ENTRY_NAMES = {"main.py", "app.py", "manage.py", "wsgi.py", "asgi.py", "__main__.py", "cli.py",
               "server.py", "index.js", "index.ts", "server.js", "server.ts", "main.js", "main.ts",
               "app.js", "app.ts", "main.go", "main.rs", "lib.rs", "index.html", "worker.js", "worker.ts"}
MONOREPO_DIRS = {"packages": "package", "apps": "app", "plugins": "plugin", "services": "service",
                 "libs": "library", "crates": "crate"}
NPM_LIFECYCLE = {"preinstall", "install", "postinstall", "prepare", "prepublish", "prepublishOnly",
                 "prepack", "postpack", "preversion", "version", "postversion"}
TEST_DIR_NAMES = {"test", "tests", "__tests__", "spec", "specs", "e2e", "integration_tests"}
TEST_FILE_RE = re.compile(r"(^test_.*\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$|_spec\.rb$)")

ENV_PATTERNS = [
    re.compile(r"process\.env\.([A-Z_][A-Z0-9_]*)"),
    re.compile(r"process\.env\[['\"]([A-Z_][A-Z0-9_]*)['\"]\]"),
    re.compile(r"import\.meta\.env\.([A-Z_][A-Z0-9_]*)"),
    re.compile(r"os\.environ\[['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\]"),
    re.compile(r"os\.environ\.get\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"),
    re.compile(r"os\.getenv\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"),
    re.compile(r"os\.Getenv\(\s*\"([A-Za-z_][A-Za-z0-9_]*)\""),
    re.compile(r"env::var\(\s*\"([A-Za-z_][A-Za-z0-9_]*)\""),
    re.compile(r"ENV\[['\"]([A-Z_][A-Z0-9_]*)['\"]\]"),
    re.compile(r"ENV\.fetch\(\s*['\"]([A-Z_][A-Z0-9_]*)['\"]"),
]
ENV_EXAMPLE_NAMES = {".env.example", ".env.sample", ".env.template", ".env.dist", "example.env", "env.example"}
# Names that are noise rather than project configuration.
ENV_NOISE = {"NODE_ENV", "HOME", "PATH", "PWD", "USER", "SHELL", "TMPDIR", "TEMP", "TMP", "CI"}

KIND_RULES = [
    ("deploy", re.compile(r"deploy|publish|release|ship|upload|push", re.I)),
    ("install", re.compile(r"^(install|setup|bootstrap|deps|ci)$|install", re.I)),
    ("test", re.compile(r"test|spec|check|e2e|coverage", re.I)),
    ("lint", re.compile(r"lint|fmt|format|typecheck|tsc|clippy|vet|ruff", re.I)),
    ("build", re.compile(r"build|compile|bundle|dist", re.I)),
    ("dev", re.compile(r"^(dev|start|serve|run|watch|develop)$|dev|serve|watch", re.I)),
]


def classify(name):
    for kind, rx in KIND_RULES:
        if rx.search(name):
            return kind
    return "other"


def read_text(path, limit=MAX_READ):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


# ---------- walking ----------

def walk(root):
    """Yield repo-relative file paths, skipping vendored/build/hidden dirs."""
    n = 0
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and (not x.startswith(".") or x in KEEP_HIDDEN)
                         and not x.endswith(".egg-info"))
        for fn in sorted(files):
            n += 1
            if n > MAX_FILES:
                return
            yield os.path.relpath(os.path.join(d, fn), root)


def git(root, *args, timeout=8):
    try:
        r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


# ---------- manifests ----------

def parse_toml(text):
    if tomllib:
        try:
            return tomllib.loads(text)
        except Exception:
            pass
    return {}


def scan_package_json(root, rel, result):
    try:
        pkg = json.loads(read_text(os.path.join(root, rel)))
    except ValueError:
        return
    if not isinstance(pkg, dict):
        return
    base = os.path.dirname(rel)
    deps = {}
    for k in ("dependencies", "devDependencies", "peerDependencies"):
        if isinstance(pkg.get(k), dict):
            deps.update(pkg[k])
    for dep, label in JS_FRAMEWORKS.items():
        if dep in deps:
            result["frameworks"].add(label)
    files = set(os.listdir(os.path.join(root, base) or root))
    pm = "pnpm" if "pnpm-lock.yaml" in files else "yarn" if "yarn.lock" in files else \
        "bun" if ("bun.lockb" in files or "bun.lock" in files) else "npm"
    prefix = "cd %s && " % base if base else ""
    install = {"npm": "npm ci" if "package-lock.json" in files else "npm install",
               "pnpm": "pnpm install", "yarn": "yarn install", "bun": "bun install"}[pm]
    add_cmd(result, prefix + install, "install", rel)
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    for name in scripts:
        if name in NPM_LIFECYCLE or (name.startswith("pre") and name[3:] in scripts) or \
                (name.startswith("post") and name[4:] in scripts):
            continue  # lifecycle hooks run automatically
        if name in ("test", "start"):
            run = pm + " " + name
        else:
            run = ("npm run " + name) if pm == "npm" else (pm + " " + name)
        add_cmd(result, prefix + run, classify(name), rel, detail=str(scripts[name])[:120])
    for key in ("main", "module"):
        if isinstance(pkg.get(key), str):
            result["entry_points"].add(os.path.normpath(os.path.join(base, pkg[key])))
    b = pkg.get("bin")
    for v in (b.values() if isinstance(b, dict) else [b] if isinstance(b, str) else []):
        result["entry_points"].add(os.path.normpath(os.path.join(base, v)))
    if isinstance(pkg.get("engines"), dict):
        for k, v in pkg["engines"].items():
            result["runtime_versions"]["%s (package.json engines)" % k] = str(v)


def scan_python(root, rel, result):
    text = read_text(os.path.join(root, rel))
    low = text.lower()
    for dep, label in PY_FRAMEWORKS.items():
        if re.search(r"(^|[\s\"'\[,])" + re.escape(dep) + r"\b", low, re.M):
            result["frameworks"].add(label)
    base = os.path.dirname(rel)
    prefix = "cd %s && " % base if base else ""
    name = os.path.basename(rel)
    if name == "pyproject.toml":
        data = parse_toml(text)
        tool = data.get("tool", {}) if isinstance(data, dict) else {}
        if "poetry" in tool:
            add_cmd(result, prefix + "poetry install", "install", rel)
        elif "uv" in tool or os.path.exists(os.path.join(root, base, "uv.lock")):
            add_cmd(result, prefix + "uv sync", "install", rel)
        else:
            add_cmd(result, prefix + "pip install -e .", "install", rel)
        proj = data.get("project", {}) if isinstance(data, dict) else {}
        if isinstance(proj.get("requires-python"), str):
            result["runtime_versions"]["python (pyproject)"] = proj["requires-python"]
        for script in (proj.get("scripts") or {}):
            result["entry_points"].add("%s (console script)" % script)
        if "ruff" in tool:
            add_cmd(result, prefix + "ruff check .", "lint", rel)
    elif name.startswith("requirements") and name.endswith(".txt"):
        add_cmd(result, prefix + "pip install -r " + name, "install", rel)
    if "pytest" in low:
        add_cmd(result, prefix + "python -m pytest", "test", rel)


def scan_cargo(root, rel, result):
    text = read_text(os.path.join(root, rel))
    for dep, label in RUST_FRAMEWORKS.items():
        if re.search(r"^\s*" + re.escape(dep) + r"\s*=", text, re.M):
            result["frameworks"].add(label)
    base = os.path.dirname(rel)
    prefix = "cd %s && " % base if base else ""
    add_cmd(result, prefix + "cargo build", "build", rel)
    add_cmd(result, prefix + "cargo test", "test", rel)
    add_cmd(result, prefix + "cargo clippy", "lint", rel)


def scan_gomod(root, rel, result):
    text = read_text(os.path.join(root, rel))
    for dep, label in GO_FRAMEWORKS.items():
        if dep in text:
            result["frameworks"].add(label)
    m = re.search(r"^go\s+(\S+)", text, re.M)
    if m:
        result["runtime_versions"]["go (go.mod)"] = m.group(1)
    base = os.path.dirname(rel)
    prefix = "cd %s && " % base if base else ""
    add_cmd(result, prefix + "go build ./...", "build", rel)
    add_cmd(result, prefix + "go test ./...", "test", rel)
    add_cmd(result, prefix + "go vet ./...", "lint", rel)


def scan_gemfile(root, rel, result):
    text = read_text(os.path.join(root, rel))
    for dep, label in RUBY_FRAMEWORKS.items():
        if re.search(r"gem\s+['\"]" + re.escape(dep) + r"['\"]", text):
            result["frameworks"].add(label)
    add_cmd(result, "bundle install", "install", rel)
    if "rspec" in text:
        add_cmd(result, "bundle exec rspec", "test", rel)


def scan_dockerfile(root, rel, result):
    text = read_text(os.path.join(root, rel))
    images = re.findall(r"^\s*FROM\s+(\S+)", text, re.M | re.I)
    result["frameworks"].add("Docker")
    result["docker"].append({"file": rel, "base_images": images})
    d = os.path.dirname(rel) or "."
    add_cmd(result, "docker build -f %s %s" % (rel, d), "build", rel)


def scan_compose(root, rel, result):
    text = read_text(os.path.join(root, rel))
    services, images, in_services = [], [], False
    for line in text.splitlines():
        if re.match(r"^services:\s*$", line):
            in_services = True
            continue
        if in_services:
            if re.match(r"^\S", line):
                in_services = False
                continue
            m = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
            if m:
                services.append(m.group(1))
            m = re.match(r"^\s+image:\s*['\"]?([^\s'\"]+)", line)
            if m:
                images.append(m.group(1))
    result["frameworks"].add("Docker Compose")
    result["docker"].append({"file": rel, "services": services, "images": images})
    add_cmd(result, "docker compose -f %s up" % rel, "dev", rel)


def scan_makefile(root, rel, result):
    text = read_text(os.path.join(root, rel))
    targets = []
    for m in re.finditer(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:(?!=)", text, re.M):
        t = m.group(1)
        if t not in targets and not t.startswith("."):
            targets.append(t)
    result["make_targets"] = targets
    base = os.path.dirname(rel)
    for t in targets:
        add_cmd(result, ("make -C %s %s" % (base, t)) if base else "make " + t, classify(t), rel)


def scan_justfile(root, rel, result):
    text = read_text(os.path.join(root, rel))
    for m in re.finditer(r"^@?([A-Za-z0-9_-]+)(\s+[^:=\n]*)?:(?!=)", text, re.M):
        add_cmd(result, "just " + m.group(1), classify(m.group(1)), rel)


def scan_taskfile(root, rel, result):
    text = read_text(os.path.join(root, rel))
    in_tasks = False
    for line in text.splitlines():
        if re.match(r"^tasks:\s*$", line):
            in_tasks = True
            continue
        if in_tasks:
            if re.match(r"^\S", line):
                break
            m = re.match(r"^  ([A-Za-z0-9_:-]+):\s*$", line)
            if m:
                add_cmd(result, "task " + m.group(1), classify(m.group(1)), rel)


def add_cmd(result, cmd, kind, source, detail=""):
    if any(c["cmd"] == cmd for c in result["commands"]):
        return
    result["commands"].append({"cmd": cmd, "kind": kind, "source": source, "detail": detail,
                               "safe": kind in ("install", "test", "build", "lint")})


# ---------- CI ----------

def scan_workflow(root, rel):
    text = read_text(os.path.join(root, rel))
    name = re.search(r"^name:\s*['\"]?(.+?)['\"]?\s*$", text, re.M)
    jobs, runs, in_jobs = [], [], False
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if in_jobs and re.match(r"^\S", line):
            in_jobs = False
        if in_jobs:
            m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
            if m:
                jobs.append(m.group(1))
        m = re.match(r"^(\s*(?:-\s+)?)run:\s*(.*)$", line)
        if m:
            val = m.group(2).strip()
            if val in ("|", ">", "|-", ">-", ""):
                key_col = len(m.group(1))  # block lines are indented deeper than the `run` key
                block = []
                for nxt in lines[i + 1:]:
                    if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= key_col:
                        break
                    if nxt.strip():
                        block.append(nxt.strip())
                val = " && ".join(block[:3]) + (" ..." if len(block) > 3 else "")
            if val:
                runs.append(val.strip("'\"")[:160])
    triggers = re.search(r"^on:\s*(\[.*\]|\S+)?", text, re.M)
    return {"file": rel, "name": name.group(1) if name else os.path.basename(rel),
            "jobs": jobs, "run_steps": runs,
            "on": (triggers.group(1) or "(see file)") if triggers else ""}


# ---------- main scan ----------

def dir_role(name, ext_counter):
    if name in DIR_ROLES:
        return DIR_ROLES[name]
    if ext_counter:
        ext, _ = ext_counter.most_common(1)[0]
        lang = LANG_BY_EXT.get(ext)
        if lang:
            return "mostly %s files" % lang
        if ext:
            return "mostly %s files" % ext
    return "misc"


def scan(root):
    root = os.path.abspath(root)
    result = {
        "root": root, "scanned_at": ol.now_iso(), "file_count": 0, "languages": {},
        "frameworks": set(), "manifests": [], "entry_points": set(), "test_dirs": set(),
        "test_file_count": 0, "ci": [], "ci_other": [], "env_vars": {}, "dirs": [],
        "hotspots": {"largest": [], "churn": []}, "commands": [], "make_targets": [],
        "docker": [], "runtime_versions": {}, "is_git": False,
    }
    langs = Counter()
    sizes = []
    top_dirs = defaultdict(Counter)
    top_counts = Counter()
    sub_counts = Counter()      # second-level dirs under monorepo-style parents
    py_test_dirs = set()
    env_sources = defaultdict(set)
    files = list(walk(root))
    result["file_count"] = len(files)

    for rel in files:
        parts = rel.split(os.sep)
        name = parts[-1]
        ext = os.path.splitext(name)[1].lower()
        if ext in LANG_BY_EXT:
            langs[LANG_BY_EXT[ext]] += 1
        if len(parts) > 1:
            top_counts[parts[0]] += 1
            top_dirs[parts[0]][ext] += 1
            if parts[0] in MONOREPO_DIRS and len(parts) > 2:
                sub_counts[parts[0] + "/" + parts[1]] += 1
        if ext == ".py" and len(parts) > 1 and parts[-2] in ("test", "tests") and name.startswith("test"):
            py_test_dirs.add("/".join(parts[:-1]))
        try:
            size = os.path.getsize(os.path.join(root, rel))
        except OSError:
            size = 0
        if name not in LOCKFILES:
            sizes.append((size, rel))
        in_tests = bool(TEST_FILE_RE.search(name)) or any(p in TEST_DIR_NAMES or p == "fixtures" for p in parts[:-1])
        for p in parts[:-1]:
            if p in TEST_DIR_NAMES:
                result["test_dirs"].add("/".join(parts[:parts.index(p) + 1]))
                break
        if TEST_FILE_RE.search(name):
            result["test_file_count"] += 1
        if name in ENTRY_NAMES and len(parts) <= 3 and not any(p in TEST_DIR_NAMES for p in parts):
            result["entry_points"].add(rel)
        if len(parts) >= 3 and parts[0] == "cmd" and name == "main.go":
            result["entry_points"].add(rel)

        # manifests (top 2 levels only, to avoid nested fixtures)
        depth_ok = len(parts) <= 2
        handler = None
        if name == "package.json" and depth_ok:
            handler = scan_package_json
        elif (name == "pyproject.toml" or (name.startswith("requirements") and name.endswith(".txt"))
              or name in ("setup.py", "Pipfile")) and depth_ok:
            handler = scan_python
        elif name == "Cargo.toml" and depth_ok:
            handler = scan_cargo
        elif name == "go.mod" and depth_ok:
            handler = scan_gomod
        elif name == "Gemfile" and depth_ok:
            handler = scan_gemfile
        elif (name == "Dockerfile" or name.startswith("Dockerfile.")) and depth_ok:
            handler = scan_dockerfile
        elif name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml") and depth_ok:
            handler = scan_compose
        elif name in ("Makefile", "makefile", "GNUmakefile") and len(parts) == 1:
            handler = scan_makefile
        elif name in ("justfile", "Justfile", ".justfile") and len(parts) == 1:
            handler = scan_justfile
        elif name in ("Taskfile.yml", "Taskfile.yaml") and len(parts) == 1:
            handler = scan_taskfile
        if handler:
            result["manifests"].append(rel)
            handler(root, rel, result)

        if rel.startswith(".github/workflows/") and ext in (".yml", ".yaml"):
            result["ci"].append(scan_workflow(root, rel))
        elif rel in (".gitlab-ci.yml", ".circleci/config.yml", "Jenkinsfile", "azure-pipelines.yml",
                     ".travis.yml", "bitbucket-pipelines.yml"):
            result["ci_other"].append(rel)

        # env var names (names only; never read real .env files)
        if name in ENV_EXAMPLE_NAMES:
            for m in re.finditer(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", read_text(os.path.join(root, rel)), re.M):
                env_sources[m.group(1)].add(rel)
        elif ext in SOURCE_EXTS and size <= MAX_READ and not in_tests:
            text = read_text(os.path.join(root, rel))
            for rx in ENV_PATTERNS:
                for m in rx.finditer(text):
                    if m.group(1) not in ENV_NOISE:
                        env_sources[m.group(1)].add(rel)

    result["languages"] = dict(langs.most_common())
    code = {k: v for k, v in langs.items() if k in CODE_LANGS}
    result["primary_language"] = max(code, key=code.get) if code else (langs.most_common(1)[0][0] if langs else None)
    for d, count in sorted(top_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        result["dirs"].append({"path": d + "/", "files": count, "role": dir_role(d, top_dirs[d])})
        for sub, c in sorted((k, v) for k, v in sub_counts.items() if k.startswith(d + "/")):
            result["dirs"].append({"path": sub + "/", "files": c, "role": "%s member" % MONOREPO_DIRS[d]})
    # Python test dirs with no pytest anywhere: stdlib unittest is the likely runner.
    if "pytest" not in result["frameworks"]:
        for t in sorted(py_test_dirs)[:12]:
            parent, leaf = os.path.split(t)
            add_cmd(result, ("cd %s && " % parent if parent else "") + "python3 -m unittest discover -s " + leaf,
                    "test", t + "/")
    result["root_files"] = sorted(f for f in files if os.sep not in f)[:40]
    result["env_vars"] = {k: sorted(v)[:5] for k, v in sorted(env_sources.items())}
    sizes.sort(reverse=True)
    result["hotspots"]["largest"] = [{"path": p, "bytes": s} for s, p in sizes[:10]]

    if git(root, "rev-parse", "--is-inside-work-tree") is not None:
        result["is_git"] = True
        log = git(root, "log", "-n", "500", "--name-only", "--pretty=format:", timeout=15) or ""
        churn = Counter(l.strip() for l in log.splitlines() if l.strip())
        result["hotspots"]["churn"] = [{"path": p, "commits": c} for p, c in churn.most_common(10)]

    for k in ("frameworks", "entry_points", "test_dirs"):
        result[k] = sorted(result[k])
    # collapse nested test dirs to their shallowest ancestor
    result["test_dirs"] = [t for t in result["test_dirs"]
                           if not any(t != o and t.startswith(o + "/") for o in result["test_dirs"])]
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description="Static repo scan for ONBOARDING.md")
    p.add_argument("--root", default=".")
    p.add_argument("--out", help="output file, or - for stdout (default $ONBOARDER_HOME/scan.json)")
    args = p.parse_args(argv)
    root = os.path.abspath(args.root)
    data = scan(root)
    text = json.dumps(data, indent=1, ensure_ascii=False)
    if args.out == "-":
        print(text)
        return 0
    out = args.out or ol.scan_path(ol.home_dir(root))
    ol.atomic_write(out, text)
    print("scan written to %s (%d files, %d candidate commands)" % (out, data["file_count"], len(data["commands"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
