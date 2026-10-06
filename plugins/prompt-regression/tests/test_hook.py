"""The PostToolUse nudge: triggers, nudge-once-per-file-per-session, and quiet cases."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import SCRIPTS, write_suite

HOOK = SCRIPTS / "hook_prompt_changed.py"


class TestHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name) / "app"
        (self.p / "prompts").mkdir(parents=True)
        (self.p / "src").mkdir()
        self.state = Path(self.tmp.name) / "state"
        self.env = dict(os.environ, PROMPT_REGRESSION_HOME=str(self.state))
        write_suite(self.p, {"name": "support", "prompt_file": "src/support_bot.txt",
                             "cases": [{"id": "a", "input": "x"}]})

    def tearDown(self):
        self.tmp.cleanup()

    def hook(self, tool, tool_input, session="s1", tool_response=None, cwd=None):
        payload = {"session_id": session, "cwd": str(cwd or self.p), "hook_event_name": "PostToolUse",
                   "tool_name": tool, "tool_input": tool_input, "tool_response": tool_response or {}}
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                           capture_output=True, text=True, env=self.env, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] if r.stdout.strip() else None

    def test_referenced_prompt_file_nudges_once(self):
        ti = {"file_path": str(self.p / "src" / "support_bot.txt"), "old_string": "a", "new_string": "b"}
        ctx = self.hook("Edit", ti)
        self.assertIn("src/support_bot.txt", ctx)
        self.assertIn("suite(s) support", ctx)
        self.assertIn("/prompt-regression:run", ctx)
        self.assertIsNone(self.hook("Edit", ti))                 # same file, same session: quiet
        self.assertIsNotNone(self.hook("Edit", ti, session="s2"))  # new session: nudge again
        state = json.loads((self.state / "nudged.json").read_text())
        self.assertEqual(set(state), {"s1", "s2"})

    def test_globs(self):
        self.assertIn("prompts/**", self.hook("Write", {"file_path": "prompts/triage.txt", "content": "x"}))
        self.assertIn("*.prompt.md", self.hook("Write", {"file_path": str(self.p / "src/summary.prompt.md"), "content": "x"}))
        self.assertIn("*system_prompt*", self.hook("Edit", {"file_path": "src/system_prompt.py",
                                                            "old_string": "a", "new_string": "b"}))
        self.assertIsNone(self.hook("Edit", {"file_path": "src/utils.py", "old_string": "a", "new_string": "b"}))

    def test_custom_globs_from_config(self):
        cfg = self.p / ".claude" / "prompt-regression"
        cfg.mkdir(parents=True)
        (cfg / "config.json").write_text(json.dumps({"globs": ["src/ai/*.j2"]}))
        self.assertIsNotNone(self.hook("Write", {"file_path": "src/ai/reply.j2", "content": "x"}))
        self.assertIsNone(self.hook("Write", {"file_path": "prompts/old.txt", "content": "x"}))  # defaults replaced

    def test_model_id_change(self):
        ctx = self.hook("Edit", {"file_path": "src/client.py", "old_string": 'model="claude-sonnet-4-5"',
                                 "new_string": 'model="claude-sonnet-5-5"'})
        self.assertIn("model changed claude-sonnet-4-5 → claude-sonnet-5-5", ctx)
        # Same id on both sides: not a model change.
        self.assertIsNone(self.hook("Edit", {"file_path": "src/other.py", "old_string": 'm = "claude-opus-5-5"  # x',
                                             "new_string": 'm = "claude-opus-5-5"  # y'}))
        # Docs mentioning models don't count.
        self.assertIsNone(self.hook("Edit", {"file_path": "README.md", "old_string": "a",
                                             "new_string": "use claude-haiku-5"}))
        # "claude-code" has no version number, so it is not a model id.
        self.assertIsNone(self.hook("Edit", {"file_path": "src/x.py", "old_string": "a", "new_string": "claude-code"}))

    def test_write_with_structured_patch(self):
        patch = [{"lines": [" import x", '-MODEL = "claude-3-5-haiku-20241022"', '+MODEL = "claude-haiku-5"']}]
        ctx = self.hook("Write", {"file_path": "src/cfg.py", "content": 'import x\nMODEL = "claude-haiku-5"\n'},
                        tool_response={"type": "update", "structuredPatch": patch})
        self.assertIn("claude-3-5-haiku-20241022 → claude-haiku-5", ctx)
        unchanged = [{"lines": [' MODEL = "claude-haiku-5"', "-a = 1", "+a = 2"]}]
        self.assertIsNone(self.hook("Write", {"file_path": "src/cfg2.py", "content": 'MODEL = "claude-haiku-5"\na = 2'},
                                    tool_response={"type": "update", "structuredPatch": unchanged}))

    def test_multiedit(self):
        ctx = self.hook("MultiEdit", {"file_path": "src/m.py", "edits": [
            {"old_string": "claude-opus-4-1", "new_string": "claude-opus-5-5"}]})
        self.assertIn("claude-opus-5-5", ctx)

    def test_no_suites_suggests_init(self):
        bare = Path(self.tmp.name) / "bare"
        (bare / "prompts").mkdir(parents=True)
        ctx = self.hook("Write", {"file_path": str(bare / "prompts" / "a.md"), "content": "x"}, cwd=bare)
        self.assertIn("/prompt-regression:init", ctx)

    def test_quiet_cases(self):
        self.assertIsNone(self.hook("Write", {"file_path": "prompt-tests/support.json", "content": "{}"}))
        self.assertIsNone(self.hook("Bash", {"command": "echo claude-sonnet-5-5 > prompts/a"}))
        self.assertIsNone(self.hook("Read", {"file_path": "prompts/a.txt"}))
        r = subprocess.run([sys.executable, str(HOOK)], input="{broken", capture_output=True, text=True, env=self.env)
        self.assertEqual((r.returncode, r.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
