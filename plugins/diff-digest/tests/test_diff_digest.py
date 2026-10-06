"""diff-digest tests. Run from the plugin dir: python3 -m unittest discover -s tests"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(PLUGIN, "scripts")
sys.path.insert(0, SCRIPTS)
import digest  # noqa: E402

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)

HELPERS = textwrap.dedent('''\
    def slugify(text):
        return "-".join(text.lower().split())


    def title_case(text):
        return " ".join(w.capitalize() for w in text.split())
''') * 3

AUTH_V1 = textwrap.dedent('''\
    import hashlib


    def check_password(user, password):
        try:
            digest = hashlib.sha256(password.encode()).hexdigest()
        except UnicodeError:
            return False
        if user.password_hash == digest and user.active:
            return True
        return False
''')
AUTH_V2 = textwrap.dedent('''\
    import hashlib


    def check_password(user, password, allow_inactive=False):
        digest = hashlib.sha256(password.encode()).hexdigest()
        if user.password_hash == digest or allow_inactive:
            return True
        return False
''')
VIEWS_V1 = "import os\nimport sys\n\n\ndef index():\n    return os.getcwd()\n"
VIEWS_V2 = "import os\nimport sys\nimport json\n\n\ndef index():\n    return os.getcwd()\n"
MODELS_V1 = "class User:\n    def __init__(self, name):\n        self.name = name\n        self.active = True\n"
MODELS_V2 = "class User:\n    def __init__(self, name):\n        self.name = name  \n        self.active   =   True\n"
REPORT_V1 = "def build(rows):\n    total = 0\n    for r in rows:\n        total += r\n    return total\n"
REPORT_V2 = "def build(rows):\n    grand_total = 0\n    for r in rows:\n        grand_total += r\n    return grand_total\n"
BILLING_V1 = "def charge(amount):\n    return round(amount, 2)\n"
BILLING_V2 = "def charge(amount, currency='usd'):\n    if amount <= 0:\n        raise ValueError(amount)\n    return round(amount, 2)\n"
TEST_BILLING_V1 = "from app.billing import charge\n\n\ndef test_charge():\n    assert charge(1.005) == 1.0\n"
TEST_BILLING_V2 = TEST_BILLING_V1 + "\n\ndef test_charge_rejects_zero():\n    import pytest\n    with pytest.raises(ValueError):\n        charge(0)\n"
LOCK_V1 = json.dumps({"name": "x", "lockfileVersion": 3, "packages": {"a": {"version": "1.0.0"}}}, indent=2) + "\n"
LOCK_V2 = json.dumps({"name": "x", "lockfileVersion": 3, "packages": {"a": {"version": "1.1.0"},
                                                                       "b": {"version": "2.0.0"}}}, indent=2) + "\n"


class Repo(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="diff-digest-test-")
        self.g("init", "-q", "-b", "main")
        self.write({"utils/helpers.py": HELPERS, "app/auth.py": AUTH_V1, "app/views.py": VIEWS_V1,
                    "app/models.py": MODELS_V1, "app/report.py": REPORT_V1, "app/billing.py": BILLING_V1,
                    "tests/test_billing.py": TEST_BILLING_V1, "package-lock.json": LOCK_V1,
                    "README.md": "# App\n\nHello.\n"})
        self.commit("base")
        self.g("checkout", "-q", "-b", "feature")
        self.g("mv", "utils/helpers.py", "utils/strings.py")
        self.commit("rename helpers")
        self.write({"app/auth.py": AUTH_V2, "app/views.py": VIEWS_V2, "app/models.py": MODELS_V2,
                    "app/report.py": REPORT_V2, "package-lock.json": LOCK_V2,
                    "app/billing.py": BILLING_V2, "tests/test_billing.py": TEST_BILLING_V2})
        self.commit("feature work")
        # Uncommitted working-tree change must be included by the default mode.
        self.write({"README.md": "# App\n\nHello.\n\nNow with billing.\n"})

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def g(self, *args):
        return subprocess.run(["git", "-C", self.repo, "-c", "commit.gpgsign=false"] + list(args),
                              check=True, capture_output=True, text=True, env=GIT_ENV).stdout

    def write(self, files):
        for p, c in files.items():
            full = os.path.join(self.repo, p)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(c)

    def commit(self, msg):
        self.g("add", "-A")
        self.g("commit", "-q", "-m", msg)

    def run_digest(self, *args):
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "digest.py"), "--repo", self.repo] + list(args),
                           capture_output=True, text=True, env=GIT_ENV)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def digest_json(self, *args):
        return json.loads(self.run_digest("--json", *args))

    def by_file(self, d):
        out = {}
        for h in d["hunks"]:
            out.setdefault(h["file"], []).append(h)
        return out


class TestGrouping(Repo):
    def test_groups_and_mechanical_detection(self):
        d = self.digest_json()
        self.assertIn("merge-base(main)", d["source"])
        f = self.by_file(d)
        groups = lambda p: {h["group"] for h in f[p]}
        self.assertEqual(groups("utils/strings.py"), {"mechanical"})
        self.assertTrue(f["utils/strings.py"][0]["kind"].startswith("rename utils/helpers.py -> utils/strings.py"))
        self.assertEqual([h["kind"] for h in f["app/views.py"]], ["imports only"])
        self.assertEqual([h["kind"] for h in f["app/models.py"]], ["whitespace/formatting only"])
        self.assertEqual([h["kind"] for h in f["app/report.py"]], ["identifier rename total -> grand_total"])
        self.assertEqual(groups("package-lock.json"), {"generated"})
        self.assertEqual(groups("tests/test_billing.py"), {"tests"})
        self.assertEqual(groups("app/auth.py"), {"core-logic"})
        self.assertEqual(groups("app/billing.py"), {"core-logic"})
        self.assertEqual(groups("README.md"), {"docs"})                    # working tree included

    def test_risk_ordering_and_reasons(self):
        d = self.digest_json()
        hs = {h["id"]: h for h in d["hunks"]}
        top = hs[d["read_first"][0]]
        self.assertEqual(top["file"], "app/auth.py")
        joined = " | ".join(top["reasons"])
        for needle in ("sensitive", "error handling removed", "conditionals changed",
                       "public API signature", "no matching test change"):
            self.assertIn(needle, joined)
        self.assertGreaterEqual(top["risk"], 8)
        billing = [h for h in d["hunks"] if h["file"] == "app/billing.py"][0]
        self.assertFalse(any("no matching test" in r for r in billing["reasons"]))   # test changed
        self.assertLess(billing["risk"], top["risk"])
        risks = [hs[i]["risk"] for i in d["read_first"]]
        self.assertEqual(risks, sorted(risks, reverse=True))
        self.assertTrue(all(hs[i]["group"] not in ("mechanical", "generated", "docs") for i in d["read_first"]))

    def test_batch_approve_list(self):
        d = self.digest_json()
        hs = {h["id"]: h for h in d["hunks"]}
        files = {hs[i]["file"] for i in d["batch_approve"]}
        self.assertEqual(files, {"utils/strings.py", "app/views.py", "app/models.py", "app/report.py",
                                 "package-lock.json", "README.md"})
        self.assertTrue(all(hs[i]["risk"] <= 2 for i in d["batch_approve"]))

    def test_deleted_tests_flagged(self):
        self.write({"tests/test_billing.py": "from app.billing import charge\n"})
        d = self.digest_json()
        t = [h for h in d["hunks"] if h["file"] == "tests/test_billing.py"]
        self.assertTrue(any("tests or assertions deleted" in r for h in t for r in h["reasons"]), t)

    def test_json_schema(self):
        d = self.digest_json()
        self.assertEqual(d["schema"], "diff-digest/v1")
        self.assertEqual(set(d), {"schema", "source", "stats", "groups", "hunks", "read_first", "batch_approve"})
        self.assertEqual(set(d["stats"]), {"files", "hunks", "additions", "deletions"})
        for g, v in d["groups"].items():
            self.assertIn(g, digest.GROUPS)
            self.assertEqual(set(v), {"files", "hunks", "additions", "deletions", "max_risk"})
        keys = {"id", "file", "old_file", "status", "line", "old_line", "group", "kind", "additions",
                "deletions", "risk", "reasons", "header"}
        ids = set()
        for h in d["hunks"]:
            self.assertEqual(set(h), keys)
            self.assertIsInstance(h["line"], int)
            self.assertTrue(0 <= h["risk"] <= 10)
            self.assertIsInstance(h["reasons"], list)
            ids.add(h["id"])
        self.assertTrue(set(d["read_first"]) <= ids and set(d["batch_approve"]) <= ids)
        self.assertEqual(d["stats"]["hunks"], len(d["hunks"]))

    def test_markdown_sections(self):
        md = self.run_digest()
        for s in ("# Diff digest:", "| Group | Files | Hunks | +/- | Max risk |", "## Read these first",
                  "## Groups", "<details>", "## Safe to batch-approve"):
            self.assertIn(s, md)
        first = md.split("## Read these first")[1].split("\n1. ")[1]
        self.assertTrue(first.startswith("`app/auth.py:"), first[:80])

    def test_staged_range_and_diff_file_modes(self):
        self.g("add", "README.md")
        d = self.digest_json("--staged")
        self.assertEqual({h["file"] for h in d["hunks"]}, {"README.md"})
        d = self.digest_json("main..feature")
        self.assertNotIn("README.md", {h["file"] for h in d["hunks"]})
        self.assertIn("app/auth.py", {h["file"] for h in d["hunks"]})
        patch = os.path.join(self.repo, "..", os.path.basename(self.repo) + ".patch")
        with open(patch, "w") as f:
            f.write(self.g("diff", "-M", "main", "feature"))
        try:
            d2 = self.digest_json("--diff-file", patch)
            self.assertEqual([(h["file"], h["group"]) for h in d2["hunks"]],
                             [(h["file"], h["group"]) for h in d["hunks"]])
        finally:
            os.unlink(patch)


class TestParser(unittest.TestCase):
    def test_content_lines_that_look_like_headers(self):
        text = textwrap.dedent('''\
            diff --git a/x.sql b/x.sql
            --- a/x.sql
            +++ b/x.sql
            @@ -1,2 +1,2 @@
            --- old comment
            +++ new comment
             SELECT 1;
            diff --git a/y.py b/y.py
            new file mode 100644
            --- /dev/null
            +++ b/y.py
            @@ -0,0 +1 @@
            +print(1)
            \\ No newline at end of file
            ''')
        files = digest.parse_diff(text)
        self.assertEqual([f["path"] for f in files], ["x.sql", "y.py"])
        self.assertEqual(files[0]["hunks"][0]["lines"], ["--- old comment", "+++ new comment", " SELECT 1;"])
        self.assertEqual(files[1]["status"], "added")

    def test_rename_substitution_rules(self):
        self.assertEqual(digest.rename_substitution(["a = foo(x)"], ["a = bar(x)"]), ("foo", "bar"))
        self.assertIsNone(digest.rename_substitution(["a = foo(x)"], ["a = bar(y)"]))      # two swaps
        self.assertIsNone(digest.rename_substitution(["if a: x"], ["while a: x"]))         # keyword
        self.assertIsNone(digest.rename_substitution(["x = 1"], ["x = 2"]))                # literal


class TestPushHook(Repo):
    def hook(self, cmd, response=None, event="PostToolUse"):
        payload = {"hook_event_name": event, "session_id": "s", "cwd": self.repo, "tool_name": "Bash",
                   "tool_input": {"command": cmd},
                   "tool_response": response if response is not None else {"stdout": "ok", "stderr": ""}}
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "push_hook.py")], input=json.dumps(payload),
                           capture_output=True, text=True, env=GIT_ENV, timeout=20)
        self.assertEqual(p.returncode, 0)
        return json.loads(p.stdout) if p.stdout.strip() else {}

    def make_big(self):
        self.write({"app/big.py": "".join("x%d = %d\n" % (i, i) for i in range(400))})
        self.commit("big")

    def test_small_diff_no_suggestion(self):
        self.assertEqual(self.hook("git push -u origin feature"), {})

    def test_big_diff_suggests_review(self):
        self.make_big()
        out = self.hook("git push -u origin feature")
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("/diff-digest:review", ctx)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("hookSpecificOutput", self.hook('gh pr create --title "x" --body "y"'))

    def test_ignored_cases(self):
        self.make_big()
        self.assertEqual(self.hook("git status"), {})
        self.assertEqual(self.hook("echo pushing"), {})
        self.assertEqual(self.hook("git push", response="Error: Exit code 1\nrejected"), {})
        self.assertEqual(self.hook("git push", response={"stdout": "", "stderr": "x", "exit_code": 1}), {})
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "push_hook.py")], input="nope",
                           capture_output=True, text=True)
        self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_not_a_repo_is_silent(self):
        tmp = tempfile.mkdtemp()
        try:
            payload = {"hook_event_name": "PostToolUse", "cwd": tmp, "tool_name": "Bash",
                       "tool_input": {"command": "git push"}, "tool_response": {"stdout": ""}}
            p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "push_hook.py")],
                               input=json.dumps(payload), capture_output=True, text=True)
            self.assertEqual((p.returncode, p.stdout), (0, ""))
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
