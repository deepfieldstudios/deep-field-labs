"""Versioning hooks: Write/Edit/Bash copies, dedupe, retention, central store, post-change summaries."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import PLUGIN_ROOT, make_xlsx  # noqa: E402

SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def run_hook(script, payload, env):
    p = subprocess.run([sys.executable, str(SCRIPTS / script)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


class HookTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "Finance"
        self.dir.mkdir()
        self.home = Path(self.tmp.name) / "om-home"
        self.env = dict(os.environ, OFFICE_MODE_HOME=str(self.home))

    def tearDown(self):
        self.tmp.cleanup()

    def pre(self, tool, tool_input, session="s1"):
        return run_hook("pre_tool.py", {"session_id": session, "cwd": str(self.dir),
                                        "hook_event_name": "PreToolUse", "tool_name": tool,
                                        "tool_input": tool_input}, self.env)

    def post(self, tool, tool_input, session="s1"):
        return run_hook("post_tool.py", {"session_id": session, "cwd": str(self.dir),
                                         "hook_event_name": "PostToolUse", "tool_name": tool,
                                         "tool_input": tool_input, "tool_response": {}}, self.env)

    def versions(self, name):
        vdir = self.dir / ".versions" / name
        return sorted(p.name for p in vdir.iterdir() if p.name != "index.jsonl") if vdir.exists() else []

    def index(self, name):
        with open(self.dir / ".versions" / name / "index.jsonl") as fh:
            return [json.loads(l) for l in fh if l.strip()]


class TestWriteEditVersioning(HookTestBase):
    def test_write_existing_file_saves_copy_with_index(self):
        f = self.dir / "notes.md"
        f.write_text("Hello\n")
        out = self.pre("Write", {"file_path": str(f), "content": "Bye\n"})
        self.assertEqual(out["systemMessage"], "Saved a copy of notes.md (restore with /office-mode:restore)")
        self.assertNotIn("hookSpecificOutput", out)  # never blocks or overrides permissions
        names = self.versions("notes.md")
        self.assertEqual(len(names), 1)
        self.assertRegex(names[0], r"^\d{4}-\d{2}-\d{2}_\d{6}__[0-9a-f]{8}\.md$")
        e = self.index("notes.md")[0]
        for key in ("original", "time", "size", "sha", "session_id", "reason"):
            self.assertIn(key, e)
        self.assertEqual(e["original"], str(f))
        self.assertEqual(e["size"], 6)
        self.assertEqual(e["session_id"], "s1")
        self.assertEqual(e["reason"], "Write")
        self.assertEqual((self.dir / ".versions" / "notes.md" / names[0]).read_text(), "Hello\n")

    def test_relative_path_and_edit(self):
        (self.dir / "a.txt").write_text("one")
        out = self.pre("Edit", {"file_path": "a.txt", "old_string": "one", "new_string": "two"})
        self.assertIn("a.txt", out["systemMessage"])
        self.assertEqual(self.index("a.txt")[0]["reason"], "Edit")

    def test_dedupe_by_hash(self):
        f = self.dir / "a.txt"
        f.write_text("same")
        self.pre("Edit", {"file_path": str(f)})
        out = self.pre("Edit", {"file_path": str(f)})
        self.assertIsNone(out)  # identical content: no new copy, no message
        self.assertEqual(len(self.versions("a.txt")), 1)
        f.write_text("different")
        self.pre("Edit", {"file_path": str(f)})
        self.assertEqual(len(self.versions("a.txt")), 2)

    def test_source_code_is_never_versioned(self):
        for name in ("m.py", "app.js", "Makefile"):
            f = self.dir / name
            f.write_text("print(1)\n")
            self.assertIsNone(self.pre("Edit", {"file_path": str(f), "old_string": "1", "new_string": "2"}))
            self.assertIsNone(self.pre("Write", {"file_path": str(f), "content": "x"}))
        self.assertFalse((self.dir / ".versions").exists())

    def test_xlsx_edit_is_versioned_even_in_git_repo(self):
        (self.dir / ".git").mkdir()  # documents are versioned inside git working trees too
        f = self.dir / "budget.xlsx"
        make_xlsx(f, {"Sheet1": [["Item", "Cost"], ["Rent", 1200]]})
        out = self.pre("Edit", {"file_path": str(f)})
        self.assertIn("budget.xlsx", out["systemMessage"])
        self.assertEqual(len(self.versions("budget.xlsx")), 1)

    def test_extensions_configurable(self):
        cfgdir = self.dir / ".claude" / "office-mode"
        cfgdir.mkdir(parents=True)
        (cfgdir / "config.json").write_text(json.dumps({"extensions": ["xlsx", ".py"]}))
        (self.dir / "m.py").write_text("a")
        (self.dir / "n.txt").write_text("a")
        self.assertIsNotNone(self.pre("Edit", {"file_path": str(self.dir / "m.py")}))
        self.assertIsNone(self.pre("Edit", {"file_path": str(self.dir / "n.txt")}))

    def test_new_file_is_not_versioned(self):
        out = self.pre("Write", {"file_path": str(self.dir / "new.md"), "content": "x"})
        self.assertIsNone(out)
        self.assertFalse((self.dir / ".versions").exists())

    def test_retention(self):
        import om_common as om
        cfg = dict(om.DEFAULT_CONFIG, retention=3)
        f = self.dir / "r.txt"
        for i in range(6):
            f.write_text(f"v{i}")
            om.snapshot(f, cfg, reason="Edit",
                        now=om.now_local().replace(second=i % 60, minute=(i // 60)))
        names = self.versions("r.txt")
        self.assertEqual(len(names), 3)
        self.assertEqual(len(self.index("r.txt")), 3)
        kept = sorted((self.dir / ".versions" / "r.txt" / n).read_text() for n in names)
        self.assertEqual(kept, ["v3", "v4", "v5"])

    def test_retention_from_project_config(self):
        cfgdir = self.dir / ".claude" / "office-mode"
        cfgdir.mkdir(parents=True)
        (cfgdir / "config.json").write_text(json.dumps({"retention": 2}))
        f = self.dir / "c.txt"
        for i in range(4):
            f.write_text("x" * (i + 1))
            self.pre("Edit", {"file_path": str(f)})
        self.assertEqual(len(self.versions("c.txt")), 2)

    def test_central_store(self):
        self.home.mkdir()
        (self.home / "config.json").write_text(json.dumps({"store": "central"}))
        f = self.dir / "budget.xlsx"
        make_xlsx(f, {"Sheet1": [["Item", "Cost"], ["Rent", 1200]]})
        out = self.pre("Write", {"file_path": str(f)})
        self.assertIn("budget.xlsx", out["systemMessage"])
        self.assertFalse((self.dir / ".versions").exists())
        dirs = list((self.home / "versions").iterdir())
        self.assertEqual(len(dirs), 1)
        self.assertTrue(dirs[0].name.startswith("budget.xlsx__"))

    def test_bad_input_never_crashes(self):
        p = subprocess.run([sys.executable, str(SCRIPTS / "pre_tool.py")], input="not json",
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "")


class TestBashDetection(HookTestBase):
    def setUp(self):
        super().setUp()
        make_xlsx(self.dir / "budget.xlsx", {"Sheet1": [["A"], [1]]})
        (self.dir / "list.csv").write_text("a,b\n1,2\n")
        (self.dir / "old.csv").write_text("x\n")
        (self.dir / "My Report.docx").write_bytes(b"PK fake")

    def test_rm_and_mv_snapshot(self):
        out = self.pre("Bash", {"command": "rm budget.xlsx"})
        self.assertEqual(out["systemMessage"], "Saved a copy of budget.xlsx (restore with /office-mode:restore)")
        out = self.pre("Bash", {"command": 'mv "My Report.docx" archive/'})
        self.assertIn("My Report.docx", out["systemMessage"])

    def test_glob_and_escaped_space(self):
        out = self.pre("Bash", {"command": "rm *.csv"})
        self.assertIn("list.csv", out["systemMessage"])
        self.assertIn("old.csv", out["systemMessage"])
        (self.dir / "My Report.docx").write_bytes(b"PK changed")
        out = self.pre("Bash", {"command": r"cp template.docx My\ Report.docx"})
        self.assertIn("My Report.docx", out["systemMessage"])

    def test_read_only_commands_are_ignored(self):
        self.assertIsNone(self.pre("Bash", {"command": "cat list.csv | head -5"}))
        self.assertIsNone(self.pre("Bash", {"command": "ls -la && wc -l list.csv"}))

    def test_redirect_counts_as_write(self):
        out = self.pre("Bash", {"command": "echo 3,4 >> list.csv"})
        self.assertIn("list.csv", out["systemMessage"])

    def test_python_inline_and_script(self):
        out = self.pre("Bash", {"command": "python3 -c \"import openpyxl; wb=openpyxl.load_workbook('budget.xlsx'); wb.save('budget.xlsx')\""})
        self.assertIn("budget.xlsx", out["systemMessage"])
        (self.dir / "fix.py").write_text("import csv\nopen('old.csv','w').write('y')\n")
        out = self.pre("Bash", {"command": "python3 fix.py"})
        self.assertIn("old.csv", out["systemMessage"])

    def test_heredoc_and_cd(self):
        sub = self.dir / "sub"
        sub.mkdir()
        (sub / "deep.csv").write_text("q\n")
        cmd = "cd sub && python3 - <<'EOF'\nopen('deep.csv','a').write('r')\nEOF"
        out = self.pre("Bash", {"command": cmd})
        self.assertIn("deep.csv", out["systemMessage"])


class TestPostToolSummary(HookTestBase):
    def test_write_then_summary(self):
        f = self.dir / "costs.csv"
        f.write_text("Item,Amount\nRent,1200\nFood,300\n")
        self.pre("Write", {"file_path": str(f)})
        f.write_text("Item,Amount\nRent,1450\nFood,300\nTravel,90\n")
        out = self.post("Write", {"file_path": str(f)})
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("B2 (Amount): 1200 → 1450", ctx)
        self.assertIn("Added row 4", ctx)
        self.assertIn(str(self.dir), ctx)
        self.assertIn("plain", ctx)
        # Pending entry is consumed: a second PostToolUse says nothing.
        self.assertIsNone(self.post("Write", {"file_path": str(f)}))

    def test_unchanged_file_no_summary(self):
        f = self.dir / "n.txt"
        f.write_text("same")
        self.pre("Edit", {"file_path": str(f)})
        self.assertIsNone(self.post("Edit", {"file_path": str(f)}))

    def test_bash_delete_summary(self):
        f = self.dir / "gone.csv"
        f.write_text("a\n")
        self.pre("Bash", {"command": "rm gone.csv"})
        f.unlink()
        out = self.post("Bash", {"command": "rm gone.csv"})
        self.assertIn("gone.csv was deleted", out["hookSpecificOutput"]["additionalContext"])

    def test_other_session_not_mixed(self):
        f = self.dir / "s.txt"
        f.write_text("1")
        self.pre("Edit", {"file_path": str(f)}, session="A")
        f.write_text("2")
        self.assertIsNone(self.post("Edit", {"file_path": str(f)}, session="B"))


class TestSessionStart(unittest.TestCase):
    def test_guidance(self):
        out = run_hook("session_start.py", {"hook_event_name": "SessionStart", "source": "startup"}, dict(os.environ))
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("/office-mode:restore", ctx)
        self.assertIn("diff", ctx)  # listed as a word to avoid


if __name__ == "__main__":
    unittest.main()
