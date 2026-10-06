"""spend-meter tests. Run from the plugin dir: python3 -m unittest discover -s tests"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(PLUGIN, "scripts")
FIXTURES = os.path.join(PLUGIN, "tests", "fixtures")
sys.path.insert(0, SCRIPTS)
import usage  # noqa: E402

# Hand-computed from tests/fixtures (see prices.json):
#  msg_A opus-5-5 (deduped, output 500): 1000*4 + 500*20 + 8000*8 (1h) + 2000*5 (5m) + 50000*0.20 = 98000
#  msg_B haiku-4-5 (deduped):            20000*1 + 1000*5 + 4000*1.25 (5m) + 100000*0.1        = 40000
#  req_C opus-5-5 (no message.id):        10*4 + 10*20                                          =   240
#  msg_S sonnet-5-5 subagent:             1000*2 + 1000*10                                      = 12000
EXPECTED_COST = (98000 + 40000 + 240 + 12000) / 1e6   # $0.15024


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def assistant_line(mid, model, inp, out, ts=None):
    return json.dumps({"type": "assistant", "timestamp": ts or now_iso(), "requestId": "r_" + mid,
                       "message": {"id": mid, "model": model, "role": "assistant",
                                   "content": [{"type": "text", "text": "x"}],
                                   "usage": {"input_tokens": inp, "output_tokens": out}}}) + "\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="spend-meter-test-")
        self.home = os.path.join(self.tmp, "home")
        os.environ["SPEND_METER_HOME"] = self.home
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.proj, ".claude"))
        self.transcript = os.path.join(self.tmp, "sess-fixture.jsonl")
        shutil.copy(os.path.join(FIXTURES, "sess-fixture.jsonl"), self.transcript)
        shutil.copytree(os.path.join(FIXTURES, "sess-fixture"), os.path.join(self.tmp, "sess-fixture"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("SPEND_METER_HOME", None)

    def run_script(self, name, payload):
        env = dict(os.environ, SPEND_METER_HOME=self.home)
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, name)], input=json.dumps(payload),
                           capture_output=True, text=True, env=env, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def hook(self, payload):
        out = self.run_script("hook.py", payload)
        return json.loads(out) if out.strip() else {}

    def set_budget(self, **cfg):
        with open(os.path.join(self.proj, ".claude", "spend-meter.json"), "w") as f:
            json.dump(cfg, f)


class TestCostMath(Base):
    def test_exact_cost_and_dedupe(self):
        s = usage.summarize(usage.update_session(self.transcript, "sess-fixture"))
        self.assertEqual(round(s["total"]["cost"], 2), round(EXPECTED_COST, 2))
        self.assertAlmostEqual(s["total"]["cost"], EXPECTED_COST, places=9)
        self.assertEqual(s["total"]["messages"], 4)               # A, B, C, S; synthetic dropped
        self.assertEqual(set(s["per_model"]), {"claude-opus-5-5", "claude-haiku-4-5", "claude-sonnet-5-5"})
        self.assertEqual(s["per_model"]["claude-opus-5-5"]["output"], 510)   # 500 + 10, dupes collapsed
        self.assertEqual(s["per_model"]["claude-haiku-4-5"]["cache_read"], 100000)
        self.assertAlmostEqual(s["total"]["subagent_cost"], 0.012, places=9)
        self.assertAlmostEqual(s["per_date"]["2026-10-02"], 0.00024, places=9)

    def test_first_prompt_skips_commands(self):
        s = usage.summarize(usage.update_session(self.transcript, "sess-fixture"))
        self.assertTrue(s["meta"]["first_prompt"].startswith("Fix the failing build please"))

    def test_price_prefix_matching(self):
        self.assertEqual(usage.price_for("claude-opus-5-5")["matched"], "claude-opus-5-5")
        self.assertEqual(usage.price_for("claude-opus-5")["matched"], "claude-opus-5")
        self.assertEqual(usage.price_for("claude-fable-5-1-preview")["matched"], "claude-fable-5-1")
        self.assertEqual(usage.price_for("claude-haiku-4-5-20251001")["matched"], "claude-haiku-4-5")
        self.assertEqual(usage.price_for("some-new-model")["matched"], "default")
        p = usage.price_for("claude-sonnet-5")
        self.assertAlmostEqual(p["cache_write_5m"], 2.5)
        self.assertAlmostEqual(p["cache_write_1h"], 4.0)
        self.assertAlmostEqual(p["cache_read"], 0.2)


class TestIncremental(Base):
    def test_append_matches_full_reparse(self):
        usage.update_session(self.transcript, "sess-fixture")
        with open(self.transcript, "a") as f:
            f.write(assistant_line("msg_B_dup_check", "claude-opus-5-5", 100, 100, "2026-10-02T10:00:00Z"))
            f.write(assistant_line("msg_B_dup_check", "claude-opus-5-5", 100, 100, "2026-10-02T10:00:00Z"))
            f.write(assistant_line("msg_D", "claude-haiku-4-5", 1000, 0, "2026-10-02T10:00:01Z")[:-30])  # partial
        inc = usage.summarize(usage.update_session(self.transcript, "sess-fixture"))
        full = usage.summarize(usage.update_session(self.transcript, "x", persist=False))
        self.assertAlmostEqual(inc["total"]["cost"], full["total"]["cost"], places=12)
        self.assertAlmostEqual(inc["total"]["cost"], EXPECTED_COST + 0.0024, places=9)
        # Completing the partial line is picked up on the next pass, exactly once.
        with open(self.transcript, "a") as f:
            f.write(assistant_line("msg_D", "claude-haiku-4-5", 1000, 0, "2026-10-02T10:00:01Z")[-30:])
        inc2 = usage.summarize(usage.update_session(self.transcript, "sess-fixture"))
        self.assertAlmostEqual(inc2["total"]["cost"], EXPECTED_COST + 0.0024 + 0.001, places=9)

    def test_new_subagent_file_and_truncation(self):
        usage.update_session(self.transcript, "sess-fixture")
        with open(os.path.join(self.tmp, "sess-fixture", "subagents", "agent-b2.jsonl"), "w") as f:
            f.write(assistant_line("msg_S2", "claude-haiku-4-5", 1000, 0))
        s = usage.summarize(usage.update_session(self.transcript, "sess-fixture"))
        self.assertAlmostEqual(s["total"]["cost"], EXPECTED_COST + 0.001, places=9)
        with open(self.transcript, "w") as f:                        # rewritten smaller
            f.write(assistant_line("only", "claude-haiku-4-5", 1000, 0))
        s = usage.summarize(usage.update_session(self.transcript, "sess-fixture"))
        self.assertAlmostEqual(s["total"]["cost"], 0.001 + 0.001 + 0.012, places=9)

    def test_statusline_fast_on_big_transcript(self):
        big = os.path.join(self.tmp, "big.jsonl")
        with open(big, "w") as f:
            for i in range(20000):
                f.write(assistant_line("m%d" % i, "claude-opus-5-5", 3, 50))
                f.write(json.dumps({"type": "user", "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t%d" % i, "content": "y" * 400}]}}) + "\n")
        import statusline
        statusline.render({"transcript_path": big, "session_id": "big"})          # cold
        with open(big, "a") as f:
            f.write(assistant_line("tail", "claude-opus-5-5", 3, 50))
        t0 = time.perf_counter()
        line = statusline.render({"transcript_path": big, "session_id": "big"})
        self.assertLess(time.perf_counter() - t0, 0.1, "warm statusline render too slow")
        self.assertIn("this session", line)


class TestStatusline(Base):
    PATTERN = re.compile(r"^\$\d+\.\d{2} this session · \d+(\.\d)?[kMB]? tok( · budget \d+%)?( · ⚠ loop\?)?$")

    def test_format_plain(self):
        out = self.run_script("statusline.py", {"transcript_path": self.transcript,
                                                "session_id": "sess-fixture", "cwd": self.proj,
                                                "model": {"id": "claude-opus-5-5"}}).strip()
        self.assertRegex(out, self.PATTERN)
        self.assertTrue(out.startswith("$0.15 this session · 188k tok"), out)

    def test_format_with_budget_and_loop(self):
        self.set_budget(session_budget_usd=1.0)
        for _ in range(3):
            self.hook({"hook_event_name": "PostToolUseFailure", "session_id": "sess-fixture",
                       "transcript_path": self.transcript, "cwd": self.proj, "tool_name": "Bash",
                       "tool_input": {"command": "npm test"}, "error": "Exit code 1\nboom"})
        out = self.run_script("statusline.py", {"transcript_path": self.transcript,
                                                "session_id": "sess-fixture", "cwd": self.proj}).strip()
        self.assertRegex(out, self.PATTERN)
        self.assertIn("budget 15%", out)
        self.assertTrue(out.endswith("⚠ loop?"))

    def test_tolerates_missing_fields(self):
        self.assertEqual(self.run_script("statusline.py", {}).strip(), "spend-meter: no transcript yet")
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "statusline.py")], input="not json",
                           capture_output=True, text=True, env=dict(os.environ, SPEND_METER_HOME=self.home))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "spend-meter: unavailable")


class TestBudgets(Base):
    def ev(self, **kw):
        e = {"hook_event_name": "PostToolUse", "session_id": "sess-fixture",
             "transcript_path": self.transcript, "cwd": self.proj, "tool_name": "Read",
             "tool_input": {"file_path": "/x"}, "tool_response": {"content": "ok"}}
        e.update(kw)
        return e

    def test_session_warn_then_block_once_each(self):
        self.set_budget(session_budget_usd=0.20, warn_at=0.8)    # warn at $0.16
        self.assertEqual(self.hook(self.ev()), {})                # $0.150
        with open(self.transcript, "a") as f:
            f.write(assistant_line("w1", "claude-opus-5-5", 0, 1000))   # +$0.02 -> $0.170
        out = self.hook(self.ev())
        self.assertIn("systemMessage", out)
        self.assertIn("85%", out["systemMessage"])
        self.assertNotIn("decision", out)
        self.assertEqual(self.hook(self.ev()), {})                # warned once only
        with open(self.transcript, "a") as f:
            f.write(assistant_line("w2", "claude-opus-5-5", 0, 2500))   # +$0.05 -> $0.220
        out = self.hook(self.ev())
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("summarise", out["reason"])
        self.assertIn("ask the user", out["reason"])
        self.assertEqual(self.hook(self.ev()), {})                # blocked once only

    def test_jump_straight_past_budget_blocks_without_extra_warn(self):
        self.set_budget(session_budget_usd=0.10)
        out = self.hook(self.ev())
        self.assertEqual(out.get("decision"), "block")
        self.assertNotIn("systemMessage", out)
        self.assertEqual(self.hook(self.ev()), {})

    def test_daily_budget_aggregates_sessions(self):
        self.set_budget(daily_budget_usd=0.05)
        t2 = os.path.join(self.tmp, "today2.jsonl")
        with open(t2, "w") as f:
            f.write(assistant_line("d1", "claude-opus-5-5", 0, 1500))   # $0.03 today
        self.assertEqual(self.hook(self.ev(session_id="today2", transcript_path=t2)), {})
        t3 = os.path.join(self.tmp, "today3.jsonl")
        with open(t3, "w") as f:
            f.write(assistant_line("d2", "claude-opus-5-5", 0, 600))    # +$0.012 -> 84%
        out = self.hook(self.ev(session_id="today3", transcript_path=t3))
        self.assertIn("daily", out.get("systemMessage", ""))
        with open(t3, "a") as f:
            f.write(assistant_line("d3", "claude-opus-5-5", 0, 1000))   # +$0.02 -> over
        out = self.hook(self.ev(session_id="today3", transcript_path=t3))
        self.assertEqual(out.get("decision"), "block")
        led = usage.read_json(os.path.join(self.home, "ledger.json"))
        self.assertEqual(set(led), {"today2", "today3"})

    def test_global_config_and_project_override(self):
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"session_budget_usd": 100, "warn_at": 0.5}, f)
        cfg = usage.load_config(self.proj)
        self.assertEqual((cfg["session_budget_usd"], cfg["warn_at"]), (100, 0.5))
        self.set_budget(session_budget_usd=0.1)
        cfg = usage.load_config(self.proj)
        self.assertEqual((cfg["session_budget_usd"], cfg["warn_at"]), (0.1, 0.5))

    def test_no_budget_no_output_and_bad_input_safe(self):
        self.assertEqual(self.hook(self.ev()), {})
        self.assertEqual(self.hook({}), {})
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "hook.py")], input="{broken",
                           capture_output=True, text=True, env=dict(os.environ, SPEND_METER_HOME=self.home))
        self.assertEqual((p.returncode, p.stdout), (0, ""))


class TestLoopDetector(Base):
    def bash(self, cmd, failed=True, err="Exit code 1\nError: Cannot find module 'left-pad'",
             event="PostToolUseFailure"):
        e = {"hook_event_name": event if failed else "PostToolUse", "session_id": "loop",
             "transcript_path": self.transcript, "cwd": self.proj, "tool_name": "Bash",
             "tool_input": {"command": cmd}}
        if failed and event == "PostToolUseFailure":
            e["error"] = err
        elif failed:
            e["tool_response"] = {"stdout": "", "stderr": err, "exit_code": 1}
        else:
            e["tool_response"] = {"stdout": "ok", "stderr": "", "interrupted": False}
        return self.hook(e)

    def ctx(self, out):
        return (out.get("hookSpecificOutput") or {}).get("additionalContext")

    def test_same_command_three_failures_once(self):
        self.assertIsNone(self.ctx(self.bash("npm  test")))
        self.assertIsNone(self.ctx(self.bash("npm test")))
        out = self.bash("npm test 2>&1")
        self.assertIn("You've hit the same error 3 times", self.ctx(out))
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUseFailure")
        self.assertIsNone(self.ctx(self.bash("npm test")))         # once per signature

    def test_success_resets_command_streak(self):
        self.bash("make", err="first problem")
        self.bash("make", err="second problem")
        self.bash("make", failed=False)
        self.assertIsNone(self.ctx(self.bash("make", err="third problem")))

    def test_same_error_signature_different_commands_digits_stripped(self):
        self.bash("python a.py", err="Traceback\nKeyError: 12", event="PostToolUse")
        self.bash("python b.py", err="Traceback\nKeyError: 345", event="PostToolUse")
        out = self.bash("python c.py", err="Traceback\nKeyError: 6", event="PostToolUse")
        self.assertIn("same error 3 times", self.ctx(out))
        self.assertIn("KeyError:", self.ctx(out))
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")

    def test_signature_window_is_last_ten(self):
        self.bash("a1", err="Boom")
        self.bash("a2", err="Boom")
        for i in range(9):
            self.hook({"hook_event_name": "PostToolUse", "session_id": "loop", "tool_name": "Read",
                       "tool_input": {}, "tool_response": "fine"})
        self.assertIsNone(self.ctx(self.bash("a3", err="Boom")))   # first Boom fell out of window

    def test_non_bash_tool_errors_count(self):
        for i in range(2):
            self.hook({"hook_event_name": "PostToolUse", "session_id": "loop", "tool_name": "Edit",
                       "tool_input": {}, "tool_response": "Error: String to replace not found in file %d" % i})
        out = self.hook({"hook_event_name": "PostToolUse", "session_id": "loop", "tool_name": "Edit",
                         "tool_input": {}, "tool_response": "Error: String to replace not found in file 9"})
        self.assertIn("same error 3 times", self.ctx(out))

    def test_signature_helper(self):
        import hook
        self.assertEqual(hook.signature("a\n\n  Error 404 at line 12  \n\n"), "Error at line")
        self.assertEqual(hook.signature(""), "")


class TestReport(Base):
    def test_report_markdown_and_json(self):
        projects = os.path.join(self.tmp, "projects")
        os.makedirs(os.path.join(projects, "-tmp-proj"))
        t = os.path.join(projects, "-tmp-proj", "s1.jsonl")
        with open(t, "w") as f:
            f.write(json.dumps({"type": "user", "timestamp": now_iso(),
                                "message": {"role": "user", "content": "Build the thing " + "x" * 200}}) + "\n")
            f.write(assistant_line("r1", "claude-opus-5-5", 1000, 1000))
            f.write(assistant_line("r2", "claude-haiku-4-5", 1000, 1000))
            f.write(assistant_line("old", "claude-opus-5-5", 1000, 1000, "2020-01-01T00:00:00Z"))
        env = dict(os.environ, SPEND_METER_HOME=self.home)
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "report.py"), "--projects-dir", projects,
                            "--json"], capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        r = json.loads(p.stdout)
        self.assertAlmostEqual(r["total"]["window_cost"], 0.024 + 0.006, places=9)   # old msg excluded
        self.assertEqual(len(r["sessions"]), 1)
        self.assertEqual(len(r["sessions"][0]["first_prompt"]), 80)
        self.assertEqual(set(r["per_model"]), {"claude-opus-5-5", "claude-haiku-4-5"})
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "report.py"), "--projects-dir", projects],
                           capture_output=True, text=True, env=env)
        for heading in ("## Per day", "## Top 5 sessions", "## By model", "## Sessions", "cache hit ratio"):
            self.assertIn(heading, p.stdout)


class TestRealTranscript(unittest.TestCase):
    """Parse one real transcript (read-only) to validate against the live format. No value asserts."""

    def test_parse_real(self):
        files = sorted(glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")),
                       key=lambda p: os.path.getsize(p), reverse=True)
        if not files:
            self.skipTest("no real transcripts on this machine")
        s = usage.summarize(usage.update_session(files[0], persist=False))
        self.assertIsInstance(s["total"]["cost"], float)
        self.assertGreaterEqual(s["total"]["cost"], 0.0)
        for m in s["per_model"].values():
            self.assertTrue(all(isinstance(m[k], int) for k in ("input", "output", "cache_write", "cache_read")))


if __name__ == "__main__":
    unittest.main()
