"""Tests for secret-shield.

Fake credentials are generated at runtime from a seeded RNG so this file never
contains anything that looks like a real key (and cannot trip scanners itself).
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import detectors  # noqa: E402

_rng = random.Random(1234)
ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def rnd(n, alpha=ALNUM):
    return "".join(_rng.choice(alpha) for _ in range(n))


FAKE = {
    "aws_access_key": "AK" + "IA" + rnd(16, UPPER),
    "aws_secret_key": "aws_secret_access_key = " + rnd(40, ALNUM + "/+"),
    "github_token": "gh" + "p_" + rnd(36),
    "github_pat": "github" + "_pat_" + rnd(82, ALNUM + "_"),
    "slack_token": "xo" + "xb-" + rnd(12, "0123456789") + "-" + rnd(24),
    "stripe_live_key": "sk" + "_live_" + rnd(24),
    "stripe_test_key": "sk" + "_test_" + rnd(24),
    "anthropic_key": "sk-" + "ant-api03-" + rnd(90, ALNUM + "-_"),
    "openai_proj": "sk-" + "proj-" + rnd(60),
    "openai_legacy": "sk-" + rnd(48),
    "google_api_key": "AI" + "za" + rnd(35),
    "private_key": "-----BEGIN " + "RSA PRIVATE KEY-----\n" + rnd(64) + "\n" + rnd(64) + "\n-----END RSA PRIVATE KEY-----",
    "jwt": "ey" + "J" + rnd(20) + ".eyJ" + rnd(30) + "." + rnd(40),
    "generic_secret": 'api_key = "%s"' % rnd(24),
    "database_url": "postgres://admin:%s@db.internal:5432/app" % rnd(18),
    "mongo_url": "mongodb+srv://app:%s@cluster0.example.net/db" % rnd(18),
}
EXPECTED_KIND = {"github_pat": "github_token", "openai_proj": "openai_key", "openai_legacy": "openai_key",
                 "mongo_url": "database_url"}

PLACEHOLDERS = [
    "AKIAIOSFODNN7EXAMPLE",
    'api_key = "<your-key>"',
    "password = process.env.DB_PASSWORD",
    'token = "${GITHUB_TOKEN}"',
    "sk-ant-" + "x" * 40,
    "gh" + "p_" + "x" * 36,
    'secret_key = "my_secret_key_name_here"',
    "postgres://user:password@localhost/db",
    'OPENAI_API_KEY="sk-...your key here..."',
    'password = "{{ vault_db_password }}"',
    'api_key = os.environ["STRIPE_KEY"]',
    "token: $(cat ~/.token)",
]


def run(script, payload, home):
    env = dict(os.environ, SECRET_SHIELD_HOME=home)
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")
    return subprocess.run(["git"] + list(args), cwd=cwd, env=env, check=True, capture_output=True,
                          text=True).stdout


class TestDetectors(unittest.TestCase):
    def test_each_detector_positive(self):
        for name, text in FAKE.items():
            with self.subTest(name=name):
                found = detectors.find_secrets("config: %s\n" % text)
                self.assertEqual(len(found), 1, found)
                self.assertEqual(found[0].kind, EXPECTED_KIND.get(name, name))

    def test_placeholders_negative(self):
        for text in PLACEHOLDERS:
            with self.subTest(text=text):
                self.assertEqual(detectors.find_secrets(text), [])

    def test_severity(self):
        self.assertEqual(detectors.find_secrets(FAKE["stripe_test_key"])[0].severity, "low")
        self.assertEqual(detectors.find_secrets(FAKE["stripe_live_key"])[0].severity, "high")

    def test_entropy_rule(self):
        self.assertLess(detectors.entropy("aaaaaaaaaaaaaaaa"), 1)
        self.assertGreater(detectors.entropy(rnd(32)), 4)
        # low-entropy but long value: not flagged; high-entropy value: flagged
        self.assertEqual(detectors.find_secrets('password = "abababababababab"'), [])
        high = "k9Qz2Lm4Xp7Rt1Vb8Nw3"  # 20 distinct chars: log2(20) = 4.3 bits/char
        self.assertGreaterEqual(detectors.entropy(high), 3.5)
        self.assertEqual(len(detectors.find_secrets('password = "%s"' % high)), 1)
        self.assertEqual(len(detectors.find_secrets("DB_PASSWORD=%s" % high)), 1)  # unquoted env style
        self.assertEqual(detectors.find_secrets('password = "MockPasswordManager"'), [])  # identifier
        # too short
        self.assertEqual(detectors.find_secrets('password = "%s"' % rnd(10)), [])

    def test_mask_and_fingerprint(self):
        f = detectors.find_secrets(FAKE["github_token"])[0]
        self.assertEqual(f.masked, FAKE["github_token"][:4] + "..." + FAKE["github_token"][-2:])
        self.assertEqual(len(f.fingerprint), 64)

    def test_anthropic_not_double_counted_as_openai(self):
        self.assertEqual([f.kind for f in detectors.find_secrets(FAKE["anthropic_key"])], ["anthropic_key"])

    def test_redact(self):
        text = "use %s now" % FAKE["github_token"]
        self.assertNotIn(FAKE["github_token"], detectors.redact(text))

    def test_scan_nested(self):
        found = detectors.scan_obj({"a": [{"b": FAKE["slack_token"]}], "c": 3})
        self.assertEqual([f.kind for f in found], ["slack_token"])


class HookCase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.repo = tempfile.mkdtemp()
        git(self.repo, "init", "-q")
        with open(os.path.join(self.repo, ".gitignore"), "w") as fh:
            fh.write("secrets/\n")

    def tearDown(self):
        shutil.rmtree(self.home)
        shutil.rmtree(self.repo)

    def register_text(self):
        p = os.path.join(self.home, "register.json")
        if not os.path.exists(p):
            return ""
        with open(p) as fh:
            return fh.read()

    def pre(self, tool, ti):
        return run("pretool_hook.py", {"hook_event_name": "PreToolUse", "cwd": self.repo,
                                       "tool_name": tool, "tool_input": ti}, self.home)

    def decision(self, out):
        return (out or {}).get("hookSpecificOutput", {}).get("permissionDecision")


class TestPromptHook(HookCase):
    def test_blocks_prompt_with_secret(self):
        key = FAKE["anthropic_key"]
        out = run("prompt_hook.py", {"hook_event_name": "UserPromptSubmit",
                                     "prompt": "here is my key %s please set it up" % key}, self.home)
        self.assertEqual(out["decision"], "block")
        self.assertIn("Anthropic API key", out["reason"])
        self.assertIn(key[:4] + "..." + key[-2:], out["reason"])
        self.assertNotIn(key, out["reason"])
        self.assertIn("ANTHROPIC_API_KEY", out["reason"])

    def test_clean_prompt_passes(self):
        self.assertIsNone(run("prompt_hook.py", {"prompt": "refactor the login form"}, self.home))

    def test_test_key_warns_only(self):
        out = run("prompt_hook.py", {"prompt": "use " + FAKE["stripe_test_key"]}, self.home)
        self.assertNotIn("decision", out)
        self.assertIn("systemMessage", out)

    def test_escape_hatch(self):
        out = run("prompt_hook.py", {"prompt": "secret-shield:allow " + FAKE["github_token"]}, self.home)
        self.assertNotIn("decision", out)
        self.assertIn("exposed-in-context", self.register_text())


class TestWriteHook(HookCase):
    def test_write_into_source_denied(self):
        out = self.pre("Write", {"file_path": os.path.join(self.repo, "config.py"),
                                 "content": 'STRIPE = "%s"\n' % FAKE["stripe_live_key"]})
        self.assertEqual(self.decision(out), "deny")
        self.assertIn("STRIPE_SECRET_KEY", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_write_into_env_allowed(self):
        content = "GITHUB_TOKEN=%s\n" % FAKE["github_token"]
        self.assertIsNone(self.pre("Write", {"file_path": os.path.join(self.repo, ".env"), "content": content}))
        self.assertIsNone(self.pre("Write", {"file_path": os.path.join(self.repo, ".env.local"), "content": content}))

    def test_env_example_denied(self):
        out = self.pre("Write", {"file_path": os.path.join(self.repo, ".env.example"),
                                 "content": "GITHUB_TOKEN=%s\n" % FAKE["github_token"]})
        self.assertEqual(self.decision(out), "deny")

    def test_gitignored_path_allowed(self):
        out = self.pre("Write", {"file_path": os.path.join(self.repo, "secrets", "keys.json"),
                                 "content": '{"k": "%s"}' % FAKE["openai_proj"]})
        self.assertIsNone(out)

    def test_edit_new_string_scanned(self):
        out = self.pre("Edit", {"file_path": os.path.join(self.repo, "app.js"), "old_string": "x",
                                "new_string": "const k = '%s'" % FAKE["google_api_key"]})
        self.assertEqual(self.decision(out), "deny")

    def test_placeholder_write_allowed(self):
        self.assertIsNone(self.pre("Write", {"file_path": os.path.join(self.repo, "a.py"),
                                             "content": 'api_key = os.environ["OPENAI_API_KEY"]\n'}))


class TestBashHook(HookCase):
    def test_commit_with_staged_secret_denied(self):
        with open(os.path.join(self.repo, "settings.py"), "w") as fh:
            fh.write('AWS_KEY = "%s"\n' % FAKE["aws_access_key"])
        git(self.repo, "add", "settings.py")
        out = self.pre("Bash", {"command": "git add -A && git commit -m 'add settings'"})
        self.assertEqual(self.decision(out), "deny")
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("settings.py", reason)
        self.assertIn("AWS access key ID", reason)
        self.assertNotIn(FAKE["aws_access_key"], reason)

    def test_clean_commit_passes(self):
        with open(os.path.join(self.repo, "ok.py"), "w") as fh:
            fh.write("print('hi')\n")
        git(self.repo, "add", "ok.py")
        self.assertIsNone(self.pre("Bash", {"command": "git commit -m ok"}))

    def test_push_scans_unpushed_commits(self):
        with open(os.path.join(self.repo, "k.txt"), "w") as fh:
            fh.write(FAKE["slack_token"] + "\n")
        git(self.repo, "add", "k.txt")
        git(self.repo, "commit", "-qm", "oops")
        out = self.pre("Bash", {"command": "git push origin main"})
        self.assertEqual(self.decision(out), "deny")

    def test_curl_with_literal_secret_denied(self):
        out = self.pre("Bash", {"command": "curl -H 'Authorization: Bearer %s' https://api.example.org/x"
                                           % FAKE["openai_proj"]})
        self.assertEqual(self.decision(out), "deny")

    def test_echo_secret_into_env_allowed(self):
        self.assertIsNone(self.pre("Bash", {"command": "echo 'GITHUB_TOKEN=%s' >> .env" % FAKE["github_token"]}))

    def test_echo_secret_into_source_denied(self):
        out = self.pre("Bash", {"command": "echo 'k=%s' > config.ini" % FAKE["github_token"]})
        self.assertEqual(self.decision(out), "deny")

    def test_env_var_reference_ok(self):
        self.assertIsNone(self.pre("Bash", {"command": 'curl -H "Authorization: Bearer $OPENAI_API_KEY" https://api.openai.com/v1/models'}))


class TestExternalTools(HookCase):
    def test_webfetch_denied(self):
        out = self.pre("WebFetch", {"url": "https://example.org/?key=" + FAKE["google_api_key"], "prompt": "read"})
        self.assertEqual(self.decision(out), "deny")

    def test_mcp_nested_denied(self):
        out = self.pre("mcp__slack__post_message", {"channel": "x", "blocks": [{"text": FAKE["anthropic_key"]}]})
        self.assertEqual(self.decision(out), "deny")

    def test_mcp_clean_passes(self):
        self.assertIsNone(self.pre("mcp__notion__search", {"query": "roadmap"}))


class TestPostToolAndRegister(HookCase):
    def test_read_exposure_registered_without_raw_value(self):
        key = FAKE["github_token"]
        out = run("posttool_hook.py", {"hook_event_name": "PostToolUse", "tool_name": "Read",
                                       "tool_input": {"file_path": "/x/.env"},
                                       "tool_response": {"file": {"content": "GITHUB_TOKEN=%s\n" % key}}},
                  self.home)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Do not repeat", ctx)
        self.assertNotIn(key, json.dumps(out))
        text = self.register_text()
        self.assertNotIn(key, text)
        data = json.loads(text)
        fp = detectors.find_secrets(key)[0].fingerprint
        self.assertEqual(data["secrets"][fp]["action"], "exposed-in-context")
        self.assertEqual(data["secrets"][fp]["where"], "Read /x/.env")

    def test_bash_output_and_command_redacted(self):
        key = FAKE["stripe_live_key"]
        run("posttool_hook.py", {"tool_name": "Bash", "tool_input": {"command": "echo %s" % key},
                                 "tool_response": {"stdout": key, "stderr": ""}}, self.home)
        self.assertNotIn(key, self.register_text())
        self.assertIn("exposed-in-context", self.register_text())

    def test_register_never_holds_raw_values(self):
        for name in ("aws_access_key", "private_key", "database_url", "jwt"):
            run("prompt_hook.py", {"prompt": "x " + FAKE[name]}, self.home)
        text = self.register_text()
        for name in ("aws_access_key", "jwt"):
            self.assertNotIn(FAKE[name], text)
        self.assertNotIn(FAKE["private_key"].splitlines()[1], text)
        self.assertNotIn(FAKE["database_url"].split(":")[2].split("@")[0], text)
        self.assertEqual(len(json.loads(text)["secrets"]), 4)

    def test_show_register_and_ignore(self):
        key = FAKE["openai_proj"]
        run("prompt_hook.py", {"prompt": key}, self.home)
        env = dict(os.environ, SECRET_SHIELD_HOME=self.home)
        show = os.path.join(SCRIPTS, "show_register.py")
        out = subprocess.run([sys.executable, show], capture_output=True, text=True, env=env).stdout
        self.assertIn("platform.openai.com/api-keys", out)
        self.assertIn("Rotation checklist", out)
        self.assertNotIn(key, out)
        fp = detectors.find_secrets(key)[0].fingerprint
        subprocess.run([sys.executable, show, "--ignore", fp[:12]], check=True, capture_output=True, env=env)
        self.assertIsNone(run("prompt_hook.py", {"prompt": key}, self.home))  # now ignored
        subprocess.run([sys.executable, show, "--rotated", fp[:12]], check=True, capture_output=True, env=env)
        self.assertIn('"rotated": "20', self.register_text())

    def test_bad_input_never_crashes(self):
        for script in ("prompt_hook.py", "pretool_hook.py", "posttool_hook.py"):
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)], input="{oops",
                               capture_output=True, text=True, env=dict(os.environ, SECRET_SHIELD_HOME=self.home))
            self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
