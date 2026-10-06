"""Detect the dependency versions a project actually uses (stdlib only).

detect(cwd) returns {"deps": {key: record}, "files": [manifest names]} where
key is "<ecosystem>:<name>" (npm, pypi, cargo, go, gem, runtime) and record is
  {eco, name, version, spec, direct, dev, source}
version is the exact installed/locked version when one could be found,
otherwise None (spec then holds the declared range).
"""
import glob
import json
import os
import re
import sys

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover - older interpreters skip TOML files
    tomllib = None

import semver


def norm_py(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _json(path):
    text = _read(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _toml(path):
    text = _read(path)
    if text is None or tomllib is None:
        return None
    try:
        return tomllib.loads(text)
    except Exception:
        return None


def effective_version(rec):
    """Exact version if known, else the lowest version the declared spec allows."""
    return rec.get("version") or semver.lowest(rec.get("spec"))


class _Deps:
    def __init__(self):
        self.deps = {}
        self.files = []

    def add(self, eco, name, spec=None, version=None, direct=True, dev=False, source=None):
        key = "%s:%s" % (eco, name)
        rec = self.deps.get(key)
        if rec is None:
            rec = self.deps[key] = {"eco": eco, "name": name, "version": None, "spec": None,
                                    "direct": False, "dev": dev, "source": source}
        if spec and not rec["spec"]:
            rec["spec"] = spec
        if version and not rec["version"]:
            rec["version"] = version
            rec["source"] = source
        if direct and not rec["direct"]:
            rec["direct"] = True
            rec["dev"] = dev
        elif direct and not dev:
            rec["dev"] = False
        return rec

    def set_version(self, eco, name, version, source):
        """Pin an exact version onto an existing record (higher-priority source first)."""
        rec = self.deps.get("%s:%s" % (eco, name))
        if rec is not None and version and not rec["version"]:
            rec["version"] = version
            rec["source"] = source


# --------------------------------------------------------------------------
# npm
# --------------------------------------------------------------------------

def parse_package_lock(data):
    """package-lock.json -> ({name: version}, {name: spec of root deps})."""
    versions, root = {}, {}
    if not isinstance(data, dict):
        return versions, root
    packages = data.get("packages")
    if isinstance(packages, dict):
        top = packages.get("") or {}
        for field in ("dependencies", "devDependencies", "optionalDependencies"):
            for name, spec in (top.get(field) or {}).items():
                root[name] = spec
        for path, info in packages.items():
            if not path or not isinstance(info, dict):
                continue
            # only top-level installs: "node_modules/x" or "node_modules/@s/x"
            m = re.fullmatch(r"node_modules/((?:@[^/]+/)?[^/]+)", path)
            if m and info.get("version"):
                versions[m.group(1)] = info["version"]
    deps_v1 = data.get("dependencies")
    if isinstance(deps_v1, dict):  # lockfileVersion 1
        for name, info in deps_v1.items():
            if isinstance(info, dict) and info.get("version") and name not in versions:
                versions[name] = info["version"]
    return versions, root


def _clean_pnpm_version(value):
    value = value.strip().strip("'\"")
    value = value.split("(")[0].split("_")[0]
    return value if semver.parse(value) and not value.startswith(("link:", "file:")) else None


def parse_pnpm_lock(text):
    """pnpm-lock.yaml (v5, v6, v9) -> {name: version} for the root importer.

    Pragmatic indentation-based reader, not a YAML parser.
    """
    versions = {}
    section_indent = None   # indent of the active dependencies: line
    current = None          # package awaiting a nested `version:` line
    importer = None         # importer key while inside `importers:`
    in_importers = False
    for raw in (text or "").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0:
            in_importers = line == "importers:"
            importer = None
            section_indent = 0 if line in ("dependencies:", "devDependencies:", "optionalDependencies:") else None
            current = None
            continue
        if in_importers and indent == 2 and line.endswith(":"):
            importer = line[:-1].strip("'\"")
            section_indent = None
            continue
        if in_importers and indent == 4 and importer == "." and line in (
                "dependencies:", "devDependencies:", "optionalDependencies:"):
            section_indent = 4
            current = None
            continue
        if section_indent is None:
            continue
        if indent <= section_indent:
            section_indent = None
            current = None
            continue
        if indent == section_indent + 2:
            key, _, value = line.partition(":")
            name = key.strip().strip("'\"")
            value = value.strip()
            if value:  # v5: `react: 18.2.0`
                v = _clean_pnpm_version(value)
                if v:
                    versions[name] = v
                current = None
            else:
                current = name
        elif indent == section_indent + 4 and current and line.startswith("version:"):
            v = _clean_pnpm_version(line.split(":", 1)[1])
            if v:
                versions[current] = v
    return versions


def parse_yarn_lock(text, wanted=None):
    """yarn.lock (classic v1 and berry) -> {name: version}.

    wanted maps name -> declared spec; when several versions of a package are
    locked, the block whose header carries that spec wins.
    """
    wanted = wanted or {}
    candidates = {}  # name -> [(specs, version)]
    header, specs = None, []
    for raw in (text or "").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        if not raw.startswith((" ", "\t")):
            header = raw.rstrip(":").strip()
            specs = []
            for entry in header.split(","):
                entry = entry.strip().strip("'\"")
                at = entry.rfind("@")
                if at > 0:
                    name, spec = entry[:at], entry[at + 1:]
                    specs.append((name, spec[4:] if spec.startswith("npm:") else spec))
            continue
        m = re.match(r'^\s+version:?\s+"?([^"\s]+)"?', raw)
        if m and specs:
            for name, spec in specs:
                candidates.setdefault(name, []).append((spec, m.group(1)))
            specs = []
    versions = {}
    for name, items in candidates.items():
        want = wanted.get(name)
        chosen = next((v for s, v in items if want and s == want), None)
        if chosen is None:
            chosen = max((v for _, v in items), key=lambda v: semver.parse(v) or (0, 0, 0, ()))
        versions[name] = chosen
    return versions


def _npm(cwd, d):
    pkg = _json(os.path.join(cwd, "package.json"))
    lock = _json(os.path.join(cwd, "package-lock.json"))
    if pkg is None and lock is None:
        return
    declared = {}
    if isinstance(pkg, dict):
        d.files.append("package.json")
        for field, dev in (("dependencies", False), ("devDependencies", True), ("peerDependencies", False),
                           ("optionalDependencies", False)):
            for name, spec in (pkg.get(field) or {}).items():
                declared[name] = spec
                d.add("npm", name, spec=str(spec), dev=dev, source="package.json")
        engines = pkg.get("engines") or {}
        node_spec = engines.get("node") if isinstance(engines, dict) else None
    else:
        node_spec = None
    # Exact versions, highest priority first: node_modules, then lockfiles.
    for name in list(declared):
        installed = _json(os.path.join(cwd, "node_modules", name, "package.json"))
        if isinstance(installed, dict) and installed.get("version"):
            d.set_version("npm", name, installed["version"], "node_modules")
    if lock is not None:
        d.files.append("package-lock.json")
        versions, root = parse_package_lock(lock)
        for name, spec in root.items():
            if name not in declared:
                d.add("npm", name, spec=str(spec), source="package-lock.json")
                declared[name] = spec
        for name, version in versions.items():
            if name in declared:
                d.set_version("npm", name, version, "package-lock.json")
    pnpm = _read(os.path.join(cwd, "pnpm-lock.yaml"))
    if pnpm is not None:
        d.files.append("pnpm-lock.yaml")
        for name, version in parse_pnpm_lock(pnpm).items():
            if name in declared:
                d.set_version("npm", name, version, "pnpm-lock.yaml")
    yarn = _read(os.path.join(cwd, "yarn.lock"))
    if yarn is not None:
        d.files.append("yarn.lock")
        for name, version in parse_yarn_lock(yarn, declared).items():
            if name in declared:
                d.set_version("npm", name, version, "yarn.lock")
    # Node runtime: version files, then engines.
    node_version, node_source = None, ("package.json engines" if node_spec else "package.json")
    for fname in (".nvmrc", ".node-version"):
        text = _read(os.path.join(cwd, fname))
        if text and semver.parse(text.strip().lstrip("v")):
            node_version, node_source = text.strip().lstrip("v"), fname
            break
    rec = d.add("runtime", "node", spec=node_spec, version=node_version, source=node_source)
    rec["direct"] = True


# --------------------------------------------------------------------------
# Python
# --------------------------------------------------------------------------

_REQ_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([^;]*?)\s*(;.*)?$")


def parse_requirement(line):
    """'pydantic[email]==2.5.3 ; python_version>"3.8"' -> (name, spec, exact_version)."""
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith(("-", "git+", "http")):
        return None
    if " @ " in line:  # PEP 508 direct reference
        return (norm_py(line.split(" @ ")[0].split("[")[0].strip()), None, None)
    m = _REQ_RE.match(line)
    if not m:
        return None
    name, spec = norm_py(m.group(1)), (m.group(3) or "").replace(" ", "") or None
    exact = None
    if spec:
        em = re.fullmatch(r"===?([^,*]+)", spec)
        if em:
            exact = em.group(1)
    return (name, spec, exact)


def parse_requirements(text):
    out = []
    for line in (text or "").splitlines():
        parsed = parse_requirement(line)
        if parsed:
            out.append(parsed)
    return out


def parse_pyproject(data):
    """pyproject dict -> [(name, spec, dev)] for PEP 621 and Poetry layouts."""
    out = []
    if not isinstance(data, dict):
        return out
    project = data.get("project") or {}
    for req in project.get("dependencies") or []:
        parsed = parse_requirement(req)
        if parsed:
            out.append((parsed[0], parsed[1], False))
    for reqs in (project.get("optional-dependencies") or {}).values():
        for req in reqs or []:
            parsed = parse_requirement(req)
            if parsed:
                out.append((parsed[0], parsed[1], True))
    for reqs in (data.get("dependency-groups") or {}).values():
        for req in reqs or []:
            if isinstance(req, str):
                parsed = parse_requirement(req)
                if parsed:
                    out.append((parsed[0], parsed[1], True))
    poetry = (data.get("tool") or {}).get("poetry") or {}

    def poetry_table(table, dev):
        for name, spec in (table or {}).items():
            if name.lower() == "python":
                continue
            if isinstance(spec, dict):
                spec = spec.get("version")
            out.append((norm_py(name), spec if isinstance(spec, str) else None, dev))

    poetry_table(poetry.get("dependencies"), False)
    poetry_table(poetry.get("dev-dependencies"), True)
    for group in (poetry.get("group") or {}).values():
        poetry_table((group or {}).get("dependencies"), True)
    return out


def parse_toml_lock(data):
    """poetry.lock / uv.lock -> {name: version}."""
    versions = {}
    for pkg in (data or {}).get("package") or []:
        if isinstance(pkg, dict) and pkg.get("name") and pkg.get("version"):
            versions[norm_py(pkg["name"])] = str(pkg["version"])
    return versions


def _site_packages(cwd):
    """Installed versions from a project virtualenv's *.dist-info folders."""
    versions = {}
    for venv in (".venv", "venv", "env"):
        pattern = os.path.join(cwd, venv, "lib", "python*", "site-packages", "*.dist-info")
        for path in glob.glob(pattern):
            stem = os.path.basename(path)[: -len(".dist-info")]
            name, _, version = stem.partition("-")
            if version:
                versions[norm_py(name)] = version
    return versions


def _python(cwd, d):
    found = False
    for fname in ("requirements.txt", "requirements-dev.txt", "dev-requirements.txt"):
        text = _read(os.path.join(cwd, fname))
        if text is None:
            continue
        found = True
        d.files.append(fname)
        dev = "dev" in fname
        for name, spec, exact in parse_requirements(text):
            d.add("pypi", name, spec=spec, version=exact, dev=dev, source=fname)
    requires_python = None
    pyproject = _toml(os.path.join(cwd, "pyproject.toml"))
    if pyproject is not None:
        found = True
        d.files.append("pyproject.toml")
        for name, spec, dev in parse_pyproject(pyproject):
            d.add("pypi", name, spec=spec, dev=dev, source="pyproject.toml")
        requires_python = (pyproject.get("project") or {}).get("requires-python") or \
            (((pyproject.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}).get("python")
    if not found:
        return
    # Exact versions: the venv wins, then lockfiles.
    for name, version in _site_packages(cwd).items():
        d.set_version("pypi", name, version, "site-packages")
    for fname in ("poetry.lock", "uv.lock"):
        lock = _toml(os.path.join(cwd, fname))
        if lock is not None:
            d.files.append(fname)
            for name, version in parse_toml_lock(lock).items():
                d.set_version("pypi", name, version, fname)
    # Python runtime: .python-version, else requires-python, else this interpreter.
    text = _read(os.path.join(cwd, ".python-version"))
    version, source = None, None
    if text and semver.parse(text.strip().split()[0] if text.strip() else ""):
        version, source = text.strip().split()[0], ".python-version"
    elif requires_python and semver.lowest(requires_python):
        version, source = semver.lowest(requires_python), "requires-python"
    else:
        version, source = "%d.%d.%d" % sys.version_info[:3], "interpreter"
    rec = d.add("runtime", "python", version=version, source=source)
    rec["direct"] = True


# --------------------------------------------------------------------------
# Rust, Go, Ruby
# --------------------------------------------------------------------------

def _cargo(cwd, d):
    manifest = _toml(os.path.join(cwd, "Cargo.toml"))
    if manifest is None:
        return
    d.files.append("Cargo.toml")
    for table, dev in (("dependencies", False), ("dev-dependencies", True), ("build-dependencies", True)):
        for name, spec in (manifest.get(table) or {}).items():
            if isinstance(spec, dict):
                spec = spec.get("version")
            # Cargo's bare "1.2" means ^1.2
            d.add("cargo", name, spec=("^" + spec) if isinstance(spec, str) and spec[:1].isdigit() else spec,
                  dev=dev, source="Cargo.toml")
    lock = _toml(os.path.join(cwd, "Cargo.lock"))
    if lock is not None:
        d.files.append("Cargo.lock")
        for pkg in lock.get("package") or []:
            if isinstance(pkg, dict):
                d.set_version("cargo", pkg.get("name"), pkg.get("version"), "Cargo.lock")


def parse_go_mod(text):
    """go.mod -> (go_version, [(module, version, indirect)])."""
    go_version, mods = None, []
    in_block = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        indirect = "// indirect" in line
        line = line.split("//", 1)[0].strip()
        if not line:
            continue
        if line.startswith("go ") and not in_block:
            go_version = line.split()[1]
            continue
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue
        if line.startswith("require "):
            line = line[len("require "):].strip()
        elif not in_block:
            continue
        parts = line.split()
        if len(parts) >= 2:
            mods.append((parts[0], parts[1], indirect))
    return go_version, mods


def _go(cwd, d):
    text = _read(os.path.join(cwd, "go.mod"))
    if text is None:
        return
    d.files.append("go.mod")
    go_version, mods = parse_go_mod(text)
    for module, version, indirect in mods:
        d.add("go", module, version=version.lstrip("v"), direct=not indirect, source="go.mod")
    if go_version:
        d.add("runtime", "go", version=go_version, source="go.mod")


def parse_gemfile_lock(text):
    """Gemfile.lock -> ({gem: version}, [direct gem names])."""
    versions, direct = {}, []
    section = None
    for raw in (text or "").splitlines():
        if not raw.strip():
            continue
        if not raw.startswith(" "):
            section = raw.strip()
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if section in ("GEM", "PATH", "GIT") and indent == 4:
            m = re.match(r"^\s{4}([^\s(]+) \(([^)]+)\)", raw)
            if m:
                versions.setdefault(m.group(1), m.group(2).split("-")[0])
        elif section == "DEPENDENCIES" and indent == 2:
            direct.append(raw.strip().split()[0].rstrip("!"))
    return versions, direct


def _ruby(cwd, d):
    text = _read(os.path.join(cwd, "Gemfile.lock"))
    if text is None:
        return
    d.files.append("Gemfile.lock")
    versions, direct = parse_gemfile_lock(text)
    for name in direct:
        d.add("gem", name, version=versions.get(name), source="Gemfile.lock")


def detect(cwd):
    d = _Deps()
    for parser in (_npm, _python, _cargo, _go, _ruby):
        try:
            parser(cwd, d)
        except Exception:
            continue  # one bad manifest must not hide the others
    return {"deps": d.deps, "files": d.files}


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def top_direct(deps, cap=40):
    """Runtimes first, then runtime deps, then dev deps; alphabetical within."""
    direct = [r for r in deps.values() if r["direct"]]
    order = {"runtime": 0}
    direct.sort(key=lambda r: (order.get(r["eco"], 1), r["dev"], r["eco"], r["name"].lower()))
    return direct[:cap], max(len(direct) - cap, 0)


def render_table(info, cap=40):
    rows, hidden = top_direct(info["deps"], cap)
    if not rows:
        return None
    out = ["| Package | Version | Ecosystem | From |", "|---|---|---|---|"]
    for r in rows:
        if r["version"]:
            version = r["version"]
        elif r["spec"]:
            version = "%s (declared, not locked)" % r["spec"]
        else:
            version = "present, version unknown"
        eco = r["eco"] + (" dev" if r["dev"] else "")
        out.append("| %s | %s | %s | %s |" % (r["name"], version, eco, r.get("source") or ""))
    if hidden:
        out.append("")
        out.append("(%d more direct dependencies not shown; run /docs-pin:versions for all.)" % hidden)
    return "\n".join(out)
