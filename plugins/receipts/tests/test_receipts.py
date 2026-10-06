"""Tests for the receipts plugin. Run: python3 -m unittest discover -s tests"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import receipts_lib as lib  # noqa: E402


def run_hook(script, payload, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=30)
    out = proc.stdout.strip()
    return proc.returncode, (json.loads(out) if out else None), proc.stderr


class TestClassify(unittest.TestCase):
    CASES = {
        "pytest -q": "test",
        "python -m pytest tests/": "test",
        "python3 -m unittest discover -s tests": "test",
        "npx jest --ci": "test",
        "npx vitest run": "test",
        "npm test": "test",
        "pnpm test": "test",
        "yarn test:unit": "test",
        "npm run test -- --watch=false": "test",
        "go test ./...": "test",
        "cargo test": "test",
        "bundle exec rspec": "test",
        "vendor/bin/phpunit": "test",
        "mvn test": "test",
        "./gradlew test": "test",
        "npm run build": "build",
        "tsc -p .": "build",
        "cargo build --release": "build",
        "go build ./cmd/app": "build",
        "make": "build",
        "make all": "build",
        "vite build": "build",
        "npx next build": "build",
        "npx eslint .": "lint",
        "ruff check .": "lint",
        "flake8 src": "lint",
        "mypy src": "lint",
        "tsc --noEmit": "lint",
        "npx wrangler deploy": "deploy",
        "npx wrangler pages deploy dist": "deploy",
        "vercel --prod": "deploy",
        "netlify deploy --prod": "deploy",
        "fly deploy": "deploy",
        "git push origin main": "deploy",
        "kubectl apply -f k8s/": "deploy",
        "terraform apply -auto-approve": "deploy",
        "ls -la": "other",
        "grep -r pytest .": "other",
        "pip install pytest": "other",
        "npm install": "other",
        "git status": "other",
        "echo 'all tests pass'": "other",
    }

    def test_cases(self):
        for command, expected in self.CASES.items():
            with self.subTest(command=command):
                self.assertEqual(lib.classify(command)[0], expected)

    def test_compound_and_env(self):
        kind, kinds = lib.classify("cd web && CI=1 npm run build && npm test 2>&1 | tail -20")
        self.assertEqual(kind, "build")
        self.assertEqual(kinds, ["build", "test"])
        self.assertEqual(lib.classify("sudo -E time pytest")[0], "test")


class TestFailureDetection(unittest.TestCase):
    def test_exit_code_fields(self):
        self.assertTrue(lib.detect_status({"stdout": "", "exit_code": 1})["failed"])
        self.assertTrue(lib.detect_status({"stdout": "", "exitCode": "2"})["failed"])
        self.assertFalse(lib.detect_status({"stdout": "1 failed", "returncode": 0})["failed"])

    def test_flags(self):
        self.assertTrue(lib.detect_status({"stdout": "ok", "interrupted": True})["failed"])
        self.assertTrue(lib.detect_status({"stdout": "ok", "is_error": True})["failed"])
        self.assertTrue(lib.detect_status("ok", is_error=True)["failed"])

    def test_output_heuristics(self):
        failing = [
            "===== 2 failed, 10 passed in 1.2s =====",
            "FAILED (failures=1)",
            "Tests:       1 failed, 4 passed, 5 total",
            "src/a.ts(3,1): error TS2322: Type 'x' is not assignable",
            "Error: Cannot find module 'foo'",
            "npm ERR! code ELIFECYCLE",
            "Traceback (most recent call last):\n  File ...",
            "error[E0308]: mismatched types",
            " ! [rejected]        main -> main (fetch first)",
            "Exit code 1\nsomething broke",
        ]
        for text in failing:
            with self.subTest(text=text):
                self.assertTrue(lib.detect_status({"stdout": text, "stderr": "", "interrupted": False})["failed"])
                self.assertTrue(lib.detect_status(text)["failed"])

    def test_passing_output(self):
        passing = [
            "===== 12 passed in 0.8s =====",
            "Ran 5 tests in 0.01s\n\nOK",
            "Tests:       5 passed, 5 total",
            "Found 0 errors.",
            "0 failed, 3 passed",
        ]
        for text in passing:
            with self.subTest(text=text):
                self.assertFalse(lib.detect_status({"stdout": text, "stderr": ""})["failed"])


class TestClaims(unittest.TestCase):
    def kinds(self, text):
        return sorted({c["kind"] for c in lib.extract_claims(text)})

    def test_positive(self):
        self.assertEqual(self.kinds("Done. All tests pass."), ["test"])
        self.assertEqual(self.kinds("The tests are passing now."), ["test"])
        self.assertEqual(self.kinds("The build succeeds and lint is clean."), ["build", "lint"])
        self.assertEqual(self.kinds("Everything type-checks."), ["lint"])
        self.assertEqual(self.kinds("I deployed it to production."), ["deploy"])
        self.assertEqual(self.kinds("After the fix, all 14 tests passed."), ["test"])

    def test_hedged_and_negated(self):
        for text in ["Once the tests pass, merge it.", "Tests don't pass yet.",
                     "Not all tests pass.", "It has not been deployed.",
                     "Run pytest to make sure the tests pass.", "Do the tests pass?",
                     "The build should succeed now."]:
            with self.subTest(text=text):
                self.assertEqual(self.kinds(text), [])


class HookFixture(unittest.TestCase):
    """Builds a temp project with a transcript and a ledger home."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "state")
        self.env = {"RECEIPTS_HOME": self.home}
        self.transcript = os.path.join(self.tmp.name, "t.jsonl")
        self.lines = []
        self.session = "sess-1"
        self.n = 0
        self.user("please fix the bug and run the tests")

    def tearDown(self):
        self.tmp.cleanup()

    def _ts(self):
        self.n += 1
        return "2026-10-06T10:%02d:00.000Z" % self.n

    def user(self, text):
        self.lines.append({"type": "user", "timestamp": self._ts(), "message": {"role": "user", "content": text}})

    def tool(self, name, inp, result="ok", is_error=False):
        tid = "toolu_%d" % (len(self.lines) + 1)
        ts = self._ts()
        self.lines.append({"type": "assistant", "timestamp": ts, "message": {
            "role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
        self.lines.append({"type": "user", "timestamp": ts, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": result, "is_error": is_error}]}})
        return tid

    def bash(self, command, stdout="", exit_code=None, is_error=False, record=True):
        """A Bash call in the transcript, plus the PostToolUse ledger write."""
        tid = self.tool("Bash", {"command": command}, stdout, is_error)
        if record:
            response = {"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False}
            if exit_code is not None:
                response["exit_code"] = exit_code
            code, out, err = run_hook("record_run.py", {
                "session_id": self.session, "transcript_path": self.transcript, "cwd": self.tmp.name,
                "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": tid,
                "tool_input": {"command": command}, "tool_response": response}, self.env)
            self.assertEqual(code, 0, err)
            self.assertIsNone(out)

    def edit(self, path="src/app.py"):
        self.tool("Edit", {"file_path": os.path.join(self.tmp.name, path), "old_string": "a", "new_string": "b"})

    def say(self, text):
        self.lines.append({"type": "assistant", "timestamp": self._ts(), "message": {
            "role": "assistant", "content": [{"type": "text", "text": text}]}})

    def stop(self, active=False, mode=None):
        with open(self.transcript, "w") as fh:
            for line in self.lines:
                fh.write(json.dumps(line) + "\n")
        env = dict(self.env)
        if mode:
            env["RECEIPTS_MODE"] = mode
        code, out, err = run_hook("check_claims.py", {
            "session_id": self.session, "transcript_path": self.transcript, "cwd": self.tmp.name,
            "hook_event_name": "Stop", "stop_hook_active": active}, env)
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        return out

    def receipt(self):
        with open(os.path.join(self.home, "receipt-%s.md" % self.session)) as fh:
            return fh.read()


class TestVerdicts(HookFixture):
    def test_ledger_written(self):
        self.bash("pytest -q", "3 passed", exit_code=0)
        with open(os.path.join(self.home, self.session + ".jsonl")) as fh:
            rows = [json.loads(line) for line in fh]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "test")
        self.assertFalse(rows[0]["failed"])
        self.assertTrue(rows[0]["tool_use_id"].startswith("toolu_"))

    def test_verified(self):
        self.edit()
        self.bash("pytest -q", "===== 3 passed in 0.1s =====")
        self.say("Fixed. All tests pass.")
        self.assertIsNone(self.stop())
        self.assertIn("| VERIFIED | test |", self.receipt())

    def test_stale(self):
        self.bash("pytest -q", "3 passed")
        self.edit()
        self.say("Done, all tests pass.")
        out = self.stop()
        self.assertEqual(out["decision"], "block")
        self.assertIn("STALE", out["reason"])
        self.assertIn("app.py", out["reason"])

    def test_doc_edit_does_not_stale(self):
        self.bash("pytest -q", "3 passed")
        self.edit("README.md")
        self.say("All tests pass.")
        self.assertIsNone(self.stop())

    def test_failed(self):
        self.edit()
        self.bash("pytest -q", "===== 1 failed, 2 passed =====")
        self.say("All tests pass now.")
        out = self.stop()
        self.assertEqual(out["decision"], "block")
        self.assertIn("FAILED", out["reason"])

    def test_failed_from_transcript_only(self):
        # PostToolUse may not fire for a failing command: the transcript still shows it.
        self.edit()
        self.bash("npm run build", "Exit code 1\nsrc/x.ts: error TS2304", is_error=True, record=False)
        self.say("The build succeeds.")
        out = self.stop()
        self.assertIn("FAILED", out["reason"])

    def test_unbacked(self):
        self.edit()
        self.say("I deployed the site and the build passes.")
        out = self.stop()
        self.assertEqual(out["decision"], "block")
        self.assertIn("UNBACKED", out["reason"])
        self.assertIn("deploy", out["reason"])
        self.assertIn("build", out["reason"])

    def test_latest_run_wins(self):
        self.bash("pytest", "1 failed")
        self.edit()
        self.bash("pytest", "4 passed", exit_code=0)
        self.say("All tests pass.")
        self.assertIsNone(self.stop())

    def test_no_claims_no_output(self):
        self.edit()
        self.say("I changed the handler; I have not run anything yet.")
        self.assertIsNone(self.stop())
        self.assertIn("No test/build/lint/deploy claims found.", self.receipt())

    def test_stop_hook_active_no_loop(self):
        self.edit()
        self.say("All tests pass.")
        out = self.stop(active=True)
        self.assertNotIn("decision", out)
        self.assertIn("UNBACKED", out["systemMessage"])

    def test_warn_mode(self):
        self.edit()
        self.say("All tests pass.")
        out = self.stop(mode="warn")
        self.assertNotIn("decision", out)
        self.assertIn("UNBACKED", out["systemMessage"])

    def test_only_final_reply_counts(self):
        self.say("All tests pass, I think.")  # earlier text, followed by a tool call
        self.edit()
        self.say("I edited the file.")
        self.assertIsNone(self.stop())

    def test_bad_input_never_crashes(self):
        proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, "check_claims.py")], input="not json",
                              capture_output=True, text=True, env=dict(os.environ, **self.env))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_show_script(self):
        self.say("All tests pass.")
        self.stop()
        proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, "show_receipt.py")], capture_output=True,
                              text=True, env=dict(os.environ, **self.env), cwd=self.tmp.name)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("# Receipt for session sess-1", proc.stdout)


if __name__ == "__main__":
    unittest.main()
