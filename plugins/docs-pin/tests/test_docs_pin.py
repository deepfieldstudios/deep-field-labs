"""Tests for docs-pin. Run: python3 -m unittest discover -s tests"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import rules as R  # noqa: E402
import semver  # noqa: E402
import versions as V  # noqa: E402


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(textwrap.dedent(text) if isinstance(text, str) else json.dumps(text))
    return path


def run_hook(script, payload, env=None):
    proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)], input=json.dumps(payload),
                          capture_output=True, text=True, env=dict(os.environ, **(env or {})), timeout=30)
    out = proc.stdout.strip()
    return proc.returncode, (json.loads(out) if out else None), proc.stderr


class TempProject(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def deps(self):
        return V.detect(self.root)["deps"]


class TestSemver(unittest.TestCase):
    def test_ranges(self):
        cases = [
            ("18.2.0", ">=18.0.0", True), ("17.0.2", ">=18.0.0", False),
            ("18.3.1", ">=18.0.0 <19.0.0", True), ("19.0.0", ">=18.0.0 <19.0.0", False),
            ("1.0.0rc1", ">=1.0.0", False), ("1.3.5", ">=1.0.0", True), ("0.28.1", ">=1.0.0", False),
            ("2.0.post1", ">=2.0.0", True), ("v1.21.3", ">=1.21", True),
            ("5.0.1", "^5.0.0", True), ("6.0.0", "^5.0.0", False), ("0.2.9", "^0.2.3", True),
            ("0.3.0", "^0.2.3", False), ("1.2.9", "~1.2.3", True), ("1.3.0", "~1.2.3", False),
            ("2.9", "~=2.5", True), ("3.0", "~=2.5", False), ("4.1.0", "<4 || >=4.1", True),
            ("3.12.1", ">=3.12.0", True), ("3.11.9", ">=3.12.0", False), (None, ">=1", False),
            (None, "*", True),
        ]
        for version, rng, expected in cases:
            with self.subTest(version=version, rng=rng):
                self.assertEqual(semver.satisfies(version, rng), expected)

    def test_lowest(self):
        self.assertEqual(semver.lowest("^18.2.0"), "18.2.0")
        self.assertEqual(semver.lowest(">=1.0,<2"), "1.0")
        self.assertEqual(semver.lowest("~=2.5"), "2.5")
        self.assertEqual(semver.lowest("npm:react@^18.0.0"), "18.0.0")
        self.assertIsNone(semver.lowest("workspace:*"))


class TestParsers(TempProject):
    def test_package_lock_v3(self):
        write(self.root, "package.json", {"dependencies": {"react": "^18.2.0", "next": "14.1.0"},
                                          "devDependencies": {"typescript": "^5.3.0"}})
        write(self.root, "package-lock.json", {"lockfileVersion": 3, "packages": {
            "": {"dependencies": {"react": "^18.2.0", "next": "14.1.0"}, "devDependencies": {"typescript": "^5.3.0"}},
            "node_modules/react": {"version": "18.3.1"},
            "node_modules/next": {"version": "14.1.0"},
            "node_modules/typescript": {"version": "5.4.5"},
            "node_modules/next/node_modules/react": {"version": "99.0.0"},
            "node_modules/@types/node": {"version": "20.11.0"},
        }})
        d = self.deps()
        self.assertEqual(d["npm:react"]["version"], "18.3.1")
        self.assertEqual(d["npm:typescript"]["version"], "5.4.5")
        self.assertTrue(d["npm:typescript"]["dev"])
        self.assertNotIn("npm:@types/node", d)  # transitive, not direct
        self.assertIn("runtime:node", d)

    def test_package_lock_v1_and_node_modules_priority(self):
        write(self.root, "package.json", {"dependencies": {"express": "^4.18.0", "lodash": "^4.17.0"}})
        write(self.root, "package-lock.json", {"lockfileVersion": 1, "dependencies": {
            "express": {"version": "4.18.2"}, "lodash": {"version": "4.17.20"}}})
        write(self.root, "node_modules/lodash/package.json", {"name": "lodash", "version": "4.17.21"})
        d = self.deps()
        self.assertEqual(d["npm:express"]["version"], "4.18.2")
        self.assertEqual(d["npm:lodash"]["version"], "4.17.21")
        self.assertEqual(d["npm:lodash"]["source"], "node_modules")

    def test_pnpm_v9(self):
        write(self.root, "package.json", {"dependencies": {"next": "^14", "@tanstack/react-query": "^5"}})
        write(self.root, "pnpm-lock.yaml", """\
            lockfileVersion: '9.0'

            importers:

              .:
                dependencies:
                  '@tanstack/react-query':
                    specifier: ^5
                    version: 5.28.4(react@18.2.0)
                  next:
                    specifier: ^14
                    version: 14.1.4(react-dom@18.2.0(react@18.2.0))(react@18.2.0)

              packages/other:
                dependencies:
                  next:
                    specifier: ^13
                    version: 13.0.0

            packages:

              next@14.1.4:
                resolution: {integrity: sha512-x}
            """)
        d = self.deps()
        self.assertEqual(d["npm:next"]["version"], "14.1.4")
        self.assertEqual(d["npm:@tanstack/react-query"]["version"], "5.28.4")

    def test_pnpm_v5(self):
        self.assertEqual(V.parse_pnpm_lock("lockfileVersion: 5.4\n\nspecifiers:\n  react: ^17.0.2\n\n"
                                           "dependencies:\n  react: 17.0.2\n  react-dom: 17.0.2_react@17.0.2\n"),
                         {"react": "17.0.2", "react-dom": "17.0.2"})

    def test_yarn_classic_and_berry(self):
        classic = textwrap.dedent('''\
            # yarn lockfile v1

            "@babel/core@^7.0.0", "@babel/core@^7.12.3":
              version "7.24.0"
              resolved "https://registry.yarnpkg.com/x"

            react@^17.0.0:
              version "17.0.2"

            react@^18.2.0:
              version "18.2.0"
            ''')
        got = V.parse_yarn_lock(classic, {"react": "^18.2.0"})
        self.assertEqual(got["react"], "18.2.0")
        self.assertEqual(got["@babel/core"], "7.24.0")
        berry = '__metadata:\n  version: 6\n\n"react@npm:^18.2.0":\n  version: 18.2.0\n  resolution: "react@npm:18.2.0"\n'
        self.assertEqual(V.parse_yarn_lock(berry)["react"], "18.2.0")

    def test_requirements(self):
        write(self.root, "requirements.txt", """\
            # app deps
            Django==4.2.11
            pydantic[email]==2.6.4 ; python_version >= "3.8"
            requests>=2.31,<3
            SQLAlchemy~=2.0
            -r other.txt
            git+https://github.com/x/y.git
            """)
        d = self.deps()
        self.assertEqual(d["pypi:django"]["version"], "4.2.11")
        self.assertEqual(d["pypi:pydantic"]["version"], "2.6.4")
        self.assertIsNone(d["pypi:requests"]["version"])
        self.assertEqual(V.effective_version(d["pypi:requests"]), "2.31")
        self.assertEqual(V.effective_version(d["pypi:sqlalchemy"]), "2.0")
        self.assertIn("runtime:python", d)

    def test_pyproject_pep621_and_lock(self):
        write(self.root, "pyproject.toml", """\
            [project]
            name = "demo"
            requires-python = ">=3.12"
            dependencies = ["openai>=1.12", "pandas==2.2.1"]
            [project.optional-dependencies]
            dev = ["pytest>=8"]
            """)
        write(self.root, "uv.lock", '[[package]]\nname = "openai"\nversion = "1.30.1"\n')
        d = self.deps()
        self.assertEqual(d["pypi:openai"]["version"], "1.30.1")
        self.assertEqual(V.effective_version(d["pypi:pandas"]), "2.2.1")
        self.assertTrue(d["pypi:pytest"]["dev"])
        self.assertEqual(d["runtime:python"]["version"], "3.12")

    def test_pyproject_poetry(self):
        write(self.root, "pyproject.toml", """\
            [tool.poetry.dependencies]
            python = "^3.11"
            pydantic = "^1.10"
            fastapi = {version = "^0.110", extras = ["all"]}
            [tool.poetry.group.dev.dependencies]
            mypy = "^1.8"
            """)
        write(self.root, "poetry.lock", '[[package]]\nname = "pydantic"\nversion = "1.10.14"\n\n'
                                        '[[package]]\nname = "FastAPI"\nversion = "0.110.0"\n')
        d = self.deps()
        self.assertEqual(d["pypi:pydantic"]["version"], "1.10.14")
        self.assertEqual(d["pypi:fastapi"]["version"], "0.110.0")
        self.assertNotIn("pypi:python", d)
        self.assertEqual(d["runtime:python"]["version"], "3.11")

    def test_site_packages_wins(self):
        write(self.root, "requirements.txt", "pydantic>=1.8\n")
        os.makedirs(os.path.join(self.root, ".venv/lib/python3.12/site-packages/pydantic-2.7.1.dist-info"))
        self.assertEqual(self.deps()["pypi:pydantic"]["version"], "2.7.1")

    def test_go_mod(self):
        write(self.root, "go.mod", """\
            module example.com/app

            go 1.22

            require github.com/spf13/cobra v1.8.0

            require (
            \tgithub.com/gin-gonic/gin v1.9.1
            \tgolang.org/x/net v0.22.0 // indirect
            )
            """)
        d = self.deps()
        self.assertEqual(d["go:github.com/gin-gonic/gin"]["version"], "1.9.1")
        self.assertEqual(d["go:github.com/spf13/cobra"]["version"], "1.8.0")
        self.assertFalse(d["go:golang.org/x/net"]["direct"])
        self.assertEqual(d["runtime:go"]["version"], "1.22")

    def test_cargo_and_gemfile(self):
        write(self.root, "Cargo.toml", '[package]\nname="x"\n[dependencies]\nserde = { version = "1", features = ["derive"] }\ntokio = "1.36"\n')
        write(self.root, "Cargo.lock", '[[package]]\nname = "serde"\nversion = "1.0.197"\n\n[[package]]\nname = "tokio"\nversion = "1.36.0"\n')
        write(self.root, "Gemfile.lock", "GEM\n  remote: https://rubygems.org/\n  specs:\n    rails (7.1.3)\n      actionpack (= 7.1.3)\n    actionpack (7.1.3)\n\nDEPENDENCIES\n  rails (~> 7.1)\n\nBUNDLED WITH\n   2.5.6\n")
        d = self.deps()
        self.assertEqual(d["cargo:serde"]["version"], "1.0.197")
        self.assertEqual(d["gem:rails"]["version"], "7.1.3")
        self.assertNotIn("gem:actionpack", d)


class TestRules(TempProject):
    def setUp(self):
        super().setUp()
        self.rules = R.load_rules()

    def hits(self, rel, new, old=""):
        return [h["id"] for h in R.scan(self.root, os.path.join(self.root, rel), new, old,
                                        deps=self.deps(), rules=self.rules)]

    def test_rules_file_is_valid(self):
        self.assertGreaterEqual(len(self.rules), 20)
        for rule in self.rules:
            self.assertIn(rule["severity"], ("deny", "warn"))
            self.assertIn(":", rule["package"])

    def test_react_render_fires_by_version(self):
        code = "import ReactDOM from 'react-dom';\nReactDOM.render(<App />, el);\n"
        write(self.root, "package.json", {"dependencies": {"react-dom": "17.0.2"}})
        self.assertEqual(self.hits("src/index.tsx", code), [])
        write(self.root, "package.json", {"dependencies": {"react-dom": "^18.2.0"}})
        self.assertEqual(self.hits("src/index.tsx", code), ["react-dom-render-18"])
        write(self.root, "package.json", {"dependencies": {"react-dom": "^19.0.0"}})
        self.assertEqual(self.hits("src/index.tsx", code), ["react-dom-render-19"])

    def test_absent_package_never_fires(self):
        write(self.root, "package.json", {"dependencies": {"vue": "^3"}})
        self.assertEqual(self.hits("src/index.js", "ReactDOM.render(x, y)"), [])

    def test_existing_usage_not_reflagged(self):
        write(self.root, "package.json", {"dependencies": {"react-dom": "18.2.0"}})
        line = "ReactDOM.render(<App />, el);"
        self.assertEqual(self.hits("src/a.jsx", line + "\n// tweak", line), [])

    def test_next_router_only_in_app_dir(self):
        write(self.root, "package.json", {"dependencies": {"next": "14.1.0"}})
        code = "import { useRouter } from 'next/router'\n"
        self.assertEqual(self.hits("app/page.tsx", code), ["next-router-in-app"])
        self.assertEqual(self.hits("pages/index.tsx", code), [])

    def test_openai_python(self):
        code = "import openai\nopenai.ChatCompletion.create(model='gpt-4', messages=m)\n"
        write(self.root, "requirements.txt", "openai==0.28.1\n")
        self.assertEqual(self.hits("bot.py", code), [])
        write(self.root, "requirements.txt", "openai==1.30.1\n")
        self.assertEqual(self.hits("bot.py", code), ["openai-py-v0-api"])

    def test_pydantic_context(self):
        write(self.root, "requirements.txt", "pydantic==2.6.4\n")
        self.assertEqual(self.hits("m.py", "from pydantic import BaseModel\nx = user.dict()\n"), ["pydantic-v1-dict"])
        self.assertEqual(self.hits("m.py", "x = row.dict()\n"), [])  # no pydantic in sight
        write(self.root, "requirements.txt", "pydantic==1.10.14\n")
        self.assertEqual(self.hits("m.py", "from pydantic import BaseModel\nx = user.dict()\n"), [])

    def test_python_runtime_rule(self):
        write(self.root, "pyproject.toml", '[project]\nname="x"\nrequires-python=">=3.11"\ndependencies=[]\n')
        write(self.root, ".python-version", "3.11.8\n")
        self.assertEqual(self.hits("t.py", "datetime.utcnow()"), [])
        write(self.root, ".python-version", "3.12.2\n")
        self.assertEqual(self.hits("t.py", "datetime.utcnow()"), ["python-utcnow"])


class TestHooks(TempProject):
    def payload(self, event, tool, inp):
        return {"session_id": "s1", "transcript_path": "/dev/null", "cwd": self.root,
                "hook_event_name": event, "tool_name": tool, "tool_input": inp}

    def test_session_start_injects_table(self):
        write(self.root, "package.json", {"dependencies": {"react": "^18.2.0"}})
        write(self.root, "package-lock.json", {"packages": {"": {}, "node_modules/react": {"version": "18.3.1"}}})
        code, out, err = run_hook("session_start.py", {"session_id": "s1", "cwd": self.root,
                                                       "hook_event_name": "SessionStart", "source": "startup"})
        self.assertEqual(code, 0, err)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("| react | 18.3.1 |", ctx)
        self.assertIn("exact versions", ctx)

    def test_session_start_silent_without_manifests(self):
        code, out, _ = run_hook("session_start.py", {"cwd": self.root, "hook_event_name": "SessionStart"})
        self.assertEqual(code, 0)
        self.assertIsNone(out)

    def test_pre_deny_asks(self):
        write(self.root, "package.json", {"dependencies": {"express": "^5.0.0"}})
        code, out, err = run_hook("check_edit.py", self.payload("PreToolUse", "Write", {
            "file_path": os.path.join(self.root, "server.js"), "content": "app.del('/x', h)\n"}))
        self.assertEqual(code, 0, err)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "ask")
        self.assertIn("app.delete", hso["permissionDecisionReason"])

    def test_pre_warn_silent_by_default_and_allow_opt_in(self):
        write(self.root, "package.json", {"dependencies": {"moment": "^2.30.1"}})
        inp = {"file_path": os.path.join(self.root, "a.js"), "old_string": "x", "new_string": "import m from 'moment'"}
        _, out, _ = run_hook("check_edit.py", self.payload("PreToolUse", "Edit", inp))
        self.assertIsNone(out)
        _, out, _ = run_hook("check_edit.py", self.payload("PreToolUse", "Edit", inp), {"DOCS_PIN_WARN": "allow"})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "allow")

    def test_post_feeds_back_to_claude(self):
        write(self.root, "package.json", {"dependencies": {"moment": "^2.30.1"}})
        _, out, _ = run_hook("check_edit.py", self.payload("PostToolUse", "Edit", {
            "file_path": os.path.join(self.root, "a.js"), "old_string": "x", "new_string": "const m = require('moment')"}))
        self.assertEqual(out["decision"], "block")
        self.assertIn("maintenance mode", out["reason"])

    def test_clean_edit_and_bad_input(self):
        write(self.root, "package.json", {"dependencies": {"express": "^5.0.0"}})
        _, out, _ = run_hook("check_edit.py", self.payload("PreToolUse", "Edit", {
            "file_path": os.path.join(self.root, "a.js"), "old_string": "x", "new_string": "app.delete('/x', h)"}))
        self.assertIsNone(out)
        proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, "check_edit.py")], input="{bad",
                              capture_output=True, text=True)
        self.assertEqual((proc.returncode, proc.stdout), (0, ""))

    def test_show_versions(self):
        write(self.root, "go.mod", "module x\n\ngo 1.22\n\nrequire github.com/gin-gonic/gin v1.9.1\n")
        proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, "show_versions.py")], cwd=self.root,
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("github.com/gin-gonic/gin | 1.9.1", proc.stdout)


if __name__ == "__main__":
    unittest.main()
