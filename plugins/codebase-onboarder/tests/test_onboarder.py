import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)
import onboarder_lib as ol  # noqa: E402
import render  # noqa: E402
import scan  # noqa: E402

WORKFLOW = """name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm ci
      - name: Test
        run: |
          npm test
          npm run lint
  deploy:
    runs-on: ubuntu-latest
    steps:
      - run: npm run deploy
"""


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)


def make_repo(root):
    write(root, "package.json", json.dumps({
        "name": "demo", "main": "src/index.js", "engines": {"node": ">=20"},
        "scripts": {"dev": "vite", "build": "vite build", "test": "vitest run", "lint": "eslint .",
                    "deploy": "wrangler deploy", "pretest": "echo pre"},
        "dependencies": {"react": "^18"}, "devDependencies": {"vite": "^5", "vitest": "^1"}}))
    write(root, "package-lock.json", "{}")
    write(root, "Makefile", "build:\n\tnpm run build\n\ntest: build\n\tnpm test\n\nVAR := 1\n.PHONY: build\n")
    write(root, ".github/workflows/ci.yml", WORKFLOW)
    write(root, ".env.example", "DATABASE_URL=postgres://\nexport STRIPE_KEY=\n# COMMENTED=1\n")
    write(root, ".env", "SECRET_SHOULD_NOT_BE_READ=1\n")
    write(root, "src/index.js", "const k = process.env.API_TOKEN; const n = process.env.NODE_ENV;\n")
    write(root, "src/util.test.js", "test('x', () => {})\n")
    write(root, "server/app.py", "import os\nos.environ['REDIS_URL']\nos.getenv('PORT')\n")
    write(root, "tests/test_app.py", "def test_x(): pass\n")
    write(root, "docs/guide.md", "# guide\n")
    write(root, "node_modules/lib/index.js", "process.env.SHOULD_SKIP\n")


class TestScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        make_repo(self.root)
        self.s = scan.scan(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_languages_frameworks(self):
        self.assertEqual(self.s["languages"]["JavaScript"], 2)
        self.assertIn("Python", self.s["languages"])
        for fw in ("React", "Vite", "Vitest"):
            self.assertIn(fw, self.s["frameworks"])
        self.assertEqual(self.s["runtime_versions"]["node (package.json engines)"], ">=20")
        self.assertIn("src/index.js", self.s["entry_points"])

    def test_commands(self):
        cmds = {c["cmd"]: c for c in self.s["commands"]}
        self.assertEqual(cmds["npm ci"]["kind"], "install")
        self.assertEqual(cmds["npm test"]["kind"], "test")
        self.assertEqual(cmds["npm run build"]["kind"], "build")
        self.assertEqual(cmds["npm run dev"]["kind"], "dev")
        self.assertEqual(cmds["npm run lint"]["kind"], "lint")
        self.assertEqual(cmds["npm run deploy"]["kind"], "deploy")
        self.assertFalse(cmds["npm run deploy"]["safe"])
        self.assertNotIn("npm run pretest", cmds)
        self.assertIn("make build", cmds)
        self.assertIn("make test", cmds)
        self.assertNotIn("make VAR", cmds)
        self.assertEqual(self.s["make_targets"], ["build", "test"])

    def test_ci(self):
        w = self.s["ci"][0]
        self.assertEqual(w["name"], "CI")
        self.assertEqual(w["jobs"], ["test", "deploy"])
        self.assertEqual(w["run_steps"], ["npm ci", "npm test && npm run lint", "npm run deploy"])

    def test_env_names_only(self):
        env = self.s["env_vars"]
        for k in ("DATABASE_URL", "STRIPE_KEY", "API_TOKEN", "REDIS_URL", "PORT"):
            self.assertIn(k, env)
        for k in ("NODE_ENV", "SHOULD_SKIP", "SECRET_SHOULD_NOT_BE_READ", "COMMENTED"):
            self.assertNotIn(k, env)
        self.assertNotIn("postgres", json.dumps(env))

    def test_dirs_tests_hotspots(self):
        dirs = {d["path"]: d for d in self.s["dirs"]}
        self.assertEqual(dirs["src/"]["role"], "source code")
        self.assertEqual(dirs["src/"]["files"], 2)
        self.assertNotIn("node_modules/", dirs)
        self.assertEqual(dirs["server/"]["role"], "server code")
        self.assertIn("tests", self.s["test_dirs"])
        self.assertEqual(self.s["test_file_count"], 2)
        self.assertFalse(self.s["is_git"])
        self.assertEqual(self.s["hotspots"]["churn"], [])
        self.assertTrue(self.s["hotspots"]["largest"])
        self.assertNotIn("package-lock.json", [h["path"] for h in self.s["hotspots"]["largest"]])

    def test_unittest_fallback_and_env_skips_tests(self):
        cmds = [c["cmd"] for c in self.s["commands"]]
        self.assertIn("python3 -m unittest discover -s tests", cmds)
        write(self.root, "tests/test_env.py", "import os\nos.environ['FIXTURE_ONLY']\n")
        self.assertNotIn("FIXTURE_ONLY", scan.scan(self.root)["env_vars"])

    def test_monorepo_members_in_map(self):
        write(self.root, "plugins/alpha/scripts/a.py", "x")
        write(self.root, "plugins/alpha/tests/test_a.py", "x")
        write(self.root, "plugins/beta/README.md", "x")
        s = scan.scan(self.root)
        dirs = {d["path"]: d for d in s["dirs"]}
        self.assertEqual(dirs["plugins/alpha/"], {"path": "plugins/alpha/", "files": 2, "role": "plugin member"})
        self.assertIn("plugins/beta/", dirs)
        self.assertIn("cd plugins/alpha && python3 -m unittest discover -s tests", [c["cmd"] for c in s["commands"]])

    def test_git_churn(self):
        def g(*a):
            subprocess.run(["git", "-C", self.root] + list(a), capture_output=True, check=True)
        try:
            g("init", "-q")
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git unavailable")
        g("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "one")
        write(self.root, "src/index.js", "// changed\n")
        g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "two")
        s = scan.scan(self.root)
        self.assertTrue(s["is_git"])
        self.assertEqual(s["hotspots"]["churn"][0], {"path": "src/index.js", "commits": 2})

    def test_cli_writes_json(self):
        out = os.path.join(self.root, "out", "scan.json")
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "scan.py"), "--root", self.root, "--out", out],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(ol.load_json(out, {})["root"], os.path.abspath(self.root))


class TestRender(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        make_repo(self.root)
        self.home = os.path.join(self.root, ".claude", "onboarder")
        os.environ["ONBOARDER_HOME"] = self.home
        self.env = dict(os.environ)

    def tearDown(self):
        os.environ.pop("ONBOARDER_HOME", None)
        self.tmp.cleanup()

    def cli(self, script, *args):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + list(args),
                           capture_output=True, text=True, cwd=self.root, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def read(self):
        with open(os.path.join(self.root, "ONBOARDING.md")) as f:
            return f.read()

    def test_sections_and_keep_block(self):
        self.cli("scan.py", "--root", self.root)
        self.cli("render.py", "--root", self.root)
        md = self.read()
        for h in ("## Stack", "## Map", "## Commands", "## Environment", "## CI", "## Hotspots", "## Gotchas"):
            self.assertIn(h, md)
        self.assertIn("`npm test` [test] unverified", md)
        self.assertIn("never run without explicit approval", md)
        self.assertIn("Last updated:", md)
        self.assertIn("`DATABASE_URL`", md)
        # human edits inside the keep block survive a re-render
        md = md.replace("_Human notes go here. This block survives every re-render._",
                        "Ask Dana before touching billing/.")
        md = md.replace("## Map", "## Map\nTHIS LINE IS OUTSIDE AND WILL GO")
        write(self.root, "ONBOARDING.md", md)
        self.cli("render.py", "--root", self.root)
        md2 = self.read()
        self.assertIn("Ask Dana before touching billing/.", md2)
        self.assertNotIn("Human notes go here", md2)
        self.assertNotIn("THIS LINE IS OUTSIDE", md2)
        self.assertEqual(md2.count(ol.KEEP_START), 1)

    def test_extract_keep_variants(self):
        self.assertIsNone(render.extract_keep("no markers"))
        self.assertEqual(render.extract_keep("a %s X %s b" % (ol.KEEP_START, ol.KEEP_END)), " X ")
        self.assertEqual(render.extract_keep("a %s Y %s b" % (ol.KEEP_START, ol.KEEP_START)), " Y ")

    def test_verify_marking(self):
        self.cli("scan.py", "--root", self.root)
        self.cli("verify.py", "mark", "npm test", "ok", "12 tests pass in 3s")
        self.cli("verify.py", "mark", "npm run build", "fail", "needs VITE_API_URL")
        self.cli("verify.py", "mark", "npm ci --legacy-peer-deps", "ok", "peer dep clash")
        self.assertIn("npm test", self.cli("verify.py", "list"))
        self.cli("render.py", "--root", self.root)
        md = self.read()
        self.assertRegex(md, r"`npm test` \[test\] verified ok \d{4}-\d\d-\d\d: 12 tests pass in 3s")
        self.assertIn("`npm run build` [build] FAILED", md)
        self.assertIn("`npm ci --legacy-peer-deps` [recorded] verified ok", md)
        self.assertIn("`npm run lint` [lint] unverified", md)

    def test_render_without_scan_fails_cleanly(self):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "render.py"), "--root", self.root],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 1)


class TestPostBash(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.home = os.path.join(self.root, "state")
        self.env = dict(os.environ, ONBOARDER_HOME=self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def hook(self, cmd, stdout="", stderr="", code=None, failure=False, sid="S"):
        payload = {"session_id": sid, "cwd": self.root, "transcript_path": "/x", "tool_name": "Bash",
                   "tool_input": {"command": cmd}}
        if failure:
            payload.update(hook_event_name="PostToolUseFailure", error="Exit code 1\n" + stderr)
        else:
            resp = {"stdout": stdout, "stderr": stderr, "interrupted": False}
            if code is not None:
                resp["exit_code"] = code
            payload.update(hook_event_name="PostToolUse", tool_response=resp)
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "post_bash.py")], input=json.dumps(payload),
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        return r

    def gotchas(self):
        return ol.load_gotchas(self.home)

    def test_failure_then_variant_success(self):
        self.hook("npm run build", stderr="Error: Cannot find module 'vite'", failure=True)
        self.hook("ls -la", stdout="x")  # unrelated success does not resolve it
        self.assertEqual(self.gotchas(), [])
        self.hook("npm install && npm run build", stdout="built in 2s")
        g = self.gotchas()
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]["kind"], "fix")
        self.assertIn("`npm run build` failed (Error: Cannot find module 'vite')", g[0]["text"])
        self.assertIn("`npm install && npm run build` worked", g[0]["text"])
        # dedupe: same sequence again adds nothing
        self.hook("npm run build", stderr="Error: Cannot find module 'vite'", failure=True)
        self.hook("npm install && npm run build", stdout="ok")
        self.assertEqual(len(self.gotchas()), 1)

    def test_exit_code_in_post_tool_use(self):
        self.hook("pytest", stdout="E   ModuleNotFoundError", code=1)
        self.hook("python -m pytest", stdout="3 passed", code=0)
        self.assertEqual(len(self.gotchas()), 1)

    def test_plain_retry_is_not_gotcha(self):
        self.hook("npm test", stderr="flaky", failure=True)
        self.hook("npm test", stdout="ok")
        self.assertEqual(self.gotchas(), [])

    def test_failures_are_per_session(self):
        self.hook("make build", stderr="error", failure=True, sid="A")
        self.hook("make build -j4", stdout="ok", sid="B")
        self.assertEqual(self.gotchas(), [])

    def test_signatures(self):
        self.hook("npm run dev", stderr="Error: listen EADDRINUSE: address already in use :::3000", failure=True)
        self.hook("python app.py", stderr="KeyError: 'DATABASE_URL'", failure=True)
        self.hook("npm ci", stderr='error vite@5: The engine "node" is incompatible with this module. Expected version "^18.0.0 || >=20.0.0". Got "16.20.0"', failure=True)
        self.hook("rails s", stderr="could not connect to server: Connection refused\n Is the server running on host localhost:5432", failure=True)
        self.hook("npm run dev", stderr="Error: listen EADDRINUSE: address already in use :::3000", failure=True)
        kinds = [g["kind"] for g in self.gotchas()]
        self.assertEqual(sorted(kinds), ["env_missing", "port_in_use", "runtime_version", "service_down"])
        texts = " ".join(g["text"] for g in self.gotchas())
        self.assertIn("Port 3000", texts)
        self.assertIn("`DATABASE_URL`", texts)
        self.assertIn("node", texts)
        self.assertIn("PostgreSQL", texts)

    def test_auto_verify_candidate(self):
        ol.save_json(ol.scan_path(self.home), {"commands": [{"cmd": "npm test", "kind": "test"},
                                                            {"cmd": "npm run build", "kind": "build"}]})
        self.hook("npm test -- --run", stdout="ok")
        self.hook("npm run build", stderr="boom", failure=True)
        v = ol.load_verified(self.home)
        self.assertEqual(v["npm test"]["status"], "ok")
        self.assertEqual(v["npm test"]["source"], "hook")
        self.assertNotIn("npm run build", v)

    def test_non_bash_and_garbage_ignored(self):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "post_bash.py")], input="nonsense",
                           capture_output=True, text=True, env=self.env)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        payload = {"tool_name": "Edit", "tool_input": {"file_path": "x"}, "cwd": self.root}
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "post_bash.py")], input=json.dumps(payload),
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(r.stdout, "")
        self.assertFalse(os.path.exists(self.home))


class TestSessionStart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def hook(self):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "session_start.py")],
                           input=json.dumps({"session_id": "s", "cwd": self.root, "hook_event_name": "SessionStart",
                                             "source": "startup"}), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] if r.stdout else None

    def test_cap_and_sections(self):
        cmds = "\n".join("- `make target%d` [build] unverified" % i for i in range(80))
        md = ("# Onboarding\n\n## Stack\n\n- STACKLINE\n\n## Commands\n\n%s\n\n## Environment\n\n- ENVLINE\n\n"
              "## Gotchas\n\n- port 3000 busy\n\n---\n_Last updated_\n" % cmds)
        write(self.root, "ONBOARDING.md", md)
        ctx = self.hook()
        self.assertLessEqual(len(ctx), 1200)
        self.assertIn("make target0", ctx)
        self.assertNotIn("STACKLINE", ctx)
        self.assertNotIn("ENVLINE", ctx)
        self.assertIn("truncated", ctx)

    def test_small_doc_includes_gotchas(self):
        write(self.root, "ONBOARDING.md", "## Commands\n\n- `npm test` [test] unverified\n\n## Gotchas\n\n- needs redis\n\n---\nx\n")
        ctx = self.hook()
        self.assertIn("needs redis", ctx)
        self.assertNotIn("---", ctx)

    def test_suggest_init_only_for_big_repos(self):
        for i in range(5):
            write(self.root, "f%d.txt" % i, "x")
        self.assertIsNone(self.hook())
        for i in range(30):
            write(self.root, "src/f%d.py" % i, "x")
        self.assertIn("/onboarder:init", self.hook())


if __name__ == "__main__":
    unittest.main()
