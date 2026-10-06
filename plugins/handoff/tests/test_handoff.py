import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)
import handoff_lib as hl  # noqa: E402
import transcript  # noqa: E402


def run_script(name, args=(), stdin=None, env=None, cwd=None):
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, name)] + list(args),
                       input=json.dumps(stdin) if stdin is not None else None,
                       capture_output=True, text=True, env=e, cwd=cwd, timeout=30)
    return r


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = self.tmp.name
        self.home = os.path.join(self.cwd, "store")
        self.env = {"HANDOFF_HOME": self.home}
        os.environ["HANDOFF_HOME"] = self.home

    def tearDown(self):
        os.environ.pop("HANDOFF_HOME", None)
        self.tmp.cleanup()

    def note(self, *args):
        r = run_script("note.py", args, env=self.env, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def write_transcript(self, entries):
        p = os.path.join(self.cwd, "t.jsonl")
        with open(p, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
            f.write("not json\n")
        return p


def tool_use(tid, name, inp):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def tool_result(tid, text, is_error=False):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": is_error}]}}


class TestNoteCLI(Base):
    def test_add_list_close_prune(self):
        out = self.note("add", "--kind", "decision", "--text", "Use SQLite not Postgres", "--files", "db.py", "app/models.py")
        nid = out.split()[2]
        self.note("add", "--kind", "open", "--text", "Migrate auth tests", "--session", "s1")
        listed = self.note("list")
        self.assertIn("Use SQLite", listed)
        self.assertIn("OPEN", listed)
        self.assertIn("db.py", listed)
        notes = hl.load_notes(self.home)
        open_id = [n for n in notes if n["kind"] == "open"][0]["id"]
        self.note("close", open_id)
        self.assertNotIn("Migrate auth", self.note("list"))
        self.assertIn("[closed]", self.note("list", "--all"))
        # expire the decision and prune it
        notes = hl.load_notes(self.home)
        for n in notes:
            if n["id"] == nid:
                n["expires"] = hl.iso(hl.now() - timedelta(days=1))
        hl.save_notes(self.home, notes)
        self.assertNotIn("SQLite", self.note("list"))
        self.assertIn("pruned 1", self.note("prune"))
        self.assertEqual(len(hl.load_notes(self.home)), 1)
        # add with --session marks session state
        self.assertTrue(hl.load_state(self.home, "s1").get("noted"))

    def test_close_unknown_and_bad_kind(self):
        r = run_script("note.py", ["close", "nope"], env=self.env, cwd=self.cwd)
        self.assertEqual(r.returncode, 1)
        r = run_script("note.py", ["add", "--kind", "idea", "--text", "x"], env=self.env, cwd=self.cwd)
        self.assertNotEqual(r.returncode, 0)

    def test_skip_marks_state(self):
        self.note("skip", "--session", "abc")
        self.assertTrue(hl.load_state(self.home, "abc").get("skipped"))

    def test_atomic_file_is_valid_jsonl(self):
        for i in range(5):
            self.note("add", "--kind", "gotcha", "--text", "g%d" % i)
        with open(hl.notes_path(self.home)) as f:
            rows = [json.loads(l) for l in f]
        self.assertEqual(len(rows), 5)
        self.assertFalse([p for p in os.listdir(self.home) if p.startswith(".tmp-")])


class TestExpiry(unittest.TestCase):
    def test_default_ttls(self):
        t0 = hl.now()
        for kind, days in hl.DEFAULT_TTL_DAYS.items():
            n = hl.make_note(kind, "x", created=t0)
            self.assertEqual(hl.parse_iso(n["expires"]), hl.parse_iso(hl.iso(t0 + timedelta(days=days))))
            self.assertFalse(hl.is_expired(n, t0 + timedelta(days=days - 1)))
            self.assertTrue(hl.is_expired(n, t0 + timedelta(days=days + 1)))

    def test_paths_made_relative(self):
        n = hl.make_note("decision", "x", ["/repo/src/a.py", "./b.py"], cwd="/repo")
        self.assertEqual(n["files"], ["b.py", "src/a.py"])


class TestRanking(unittest.TestCase):
    def mk(self, kind, text, days_ago, files=()):
        return hl.make_note(kind, text, files, created=hl.now() - timedelta(days=days_ago))

    def test_open_recent_decisions_and_overlap(self):
        notes = [self.mk("decision", "dec%d" % i, i) for i in range(8)]
        notes.append(self.mk("open", "open-item", 10))
        notes.append(self.mk("gotcha", "gotcha-overlap", 3, ["src/server.py"]))
        notes.append(self.mk("gotcha", "gotcha-other", 1, ["other.py"]))
        notes.append(self.mk("dead_end", "dead-overlap-basename", 2, ["lib/util.js"]))
        closed = self.mk("open", "closed-item", 1)
        closed["status"] = "closed"
        notes.append(closed)
        text, ids = hl.select_for_session(notes, ["src/server.py", "web/util.js"])
        self.assertTrue(text.startswith("Handoff notes from earlier sessions"))
        for want in ["dec0", "dec4", "open-item", "gotcha-overlap", "dead-overlap-basename"]:
            self.assertIn(want, text)
        for unwanted in ["dec5", "dec7", "gotcha-other", "closed-item"]:
            self.assertNotIn(unwanted, text)
        # newest first
        self.assertLess(text.index("dec0"), text.index("dec1"))
        self.assertLess(text.index("dec4"), text.index("open-item"))
        self.assertEqual(len(ids), 8)

    def test_cap(self):
        notes = [self.mk("open", "item %d " % i + "x" * 150, i) for i in range(30)]
        text, ids = hl.select_for_session(notes, [])
        self.assertLessEqual(len(text), hl.SESSION_CAP_CHARS + 40)
        self.assertIn("more; run note.py list", text)
        self.assertLess(len(ids), 30)
        self.assertIn("item 0 ", text)  # newest kept

    def test_expired_excluded(self):
        n = self.mk("open", "old", 30)  # open ttl 21d
        text, ids = hl.select_for_session([n], [])
        self.assertEqual(ids, [])


class TestSessionStartHook(Base):
    def test_injects_and_records(self):
        self.note("add", "--kind", "decision", "--text", "Chose Vite over webpack", "--files", "vite.config.ts")
        r = run_script("session_start.py", stdin={"session_id": "S", "cwd": self.cwd,
                                                  "hook_event_name": "SessionStart", "source": "startup"},
                       env=self.env)
        out = json.loads(r.stdout)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("Chose Vite", ctx)
        self.assertEqual(len(hl.load_state(self.home, "S")["injected"]), 1)

    def test_silent_without_notes_and_on_garbage(self):
        r = run_script("session_start.py", stdin={"session_id": "S", "cwd": self.cwd}, env=self.env)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "session_start.py")], input="{bad",
                           capture_output=True, text=True, env=dict(os.environ, **self.env))
        self.assertEqual(r.returncode, 0)


class TestPromptMention(Base):
    def test_mention_injects_once(self):
        self.note("add", "--kind", "gotcha", "--text", "payments.py retries double-charge", "--files", "src/payments.py")
        self.note("add", "--kind", "gotcha", "--text", "unrelated", "--files", "src/other.py")
        payload = {"session_id": "P", "cwd": self.cwd, "hook_event_name": "UserPromptSubmit",
                   "prompt": "Can you refactor payments.py to use the new client?"}
        r = run_script("prompt_submit.py", stdin=payload, env=self.env)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("double-charge", ctx)
        self.assertNotIn("unrelated", ctx)
        r2 = run_script("prompt_submit.py", stdin=payload, env=self.env)
        self.assertEqual(r2.stdout, "")  # deduped

    def test_already_injected_at_start_is_skipped(self):
        self.note("add", "--kind", "open", "--text", "finish router.ts", "--files", "router.ts")
        run_script("session_start.py", stdin={"session_id": "Q", "cwd": self.cwd}, env=self.env)
        r = run_script("prompt_submit.py", stdin={"session_id": "Q", "cwd": self.cwd,
                                                  "prompt": "look at router.ts"}, env=self.env)
        self.assertEqual(r.stdout, "")

    def test_basename_boundaries(self):
        n = hl.make_note("gotcha", "x", ["src/app.py"])
        self.assertTrue(hl.mentioned_notes("edit src/app.py now", [n], set()))
        self.assertTrue(hl.mentioned_notes("is app.py ok?", [n], set()))
        self.assertFalse(hl.mentioned_notes("see myapp.pyc", [n], set()))
        self.assertFalse(hl.mentioned_notes("the app is slow", [n], set()))


class TestTranscript(Base):
    def test_parse_files_and_failures(self):
        p = self.write_transcript([
            {"type": "summary"},
            tool_use("1", "Read", {"file_path": os.path.join(self.cwd, "src/a.py")}),
            tool_result("1", "contents"),
            tool_use("2", "Edit", {"file_path": os.path.join(self.cwd, "src/a.py"), "old_string": "a", "new_string": "b"}),
            tool_result("2", "ok"),
            tool_use("3", "Bash", {"command": "python3 -m pytest tests/test_a.py -q && cat config/settings.toml"}),
            tool_result("3", "FAILED tests/test_a.py::test_x - AssertionError"),
            tool_use("4", "Bash", {"command": "python3 -m pytest tests/test_a.py -q -k smoke"}),
            tool_result("4", "1 passed"),
            tool_use("5", "Write", {"file_path": "/elsewhere/notes.txt", "content": "x"}),
            tool_result("5", "permission denied", is_error=True),
            tool_use("6", "Bash", {"command": "curl https://example.com/x.json | jq . -r"}),
            tool_result("6", "{}"),
        ])
        res = transcript.parse(p, self.cwd)
        self.assertEqual(res["files"][:2], ["src/a.py", "tests/test_a.py"])
        self.assertIn("config/settings.toml", res["files"])
        self.assertIn("/elsewhere/notes.txt", res["files"])
        self.assertFalse(any("example.com" in f for f in res["files"]))
        self.assertEqual(len(res["failures"]), 2)
        self.assertTrue(res["failures"][0].startswith("Bash `python3 -m pytest"))
        self.assertIn("Write `/elsewhere/notes.txt` -> permission denied", res["failures"][1])
        self.assertFalse(res["wrote_note"])

    def test_error_output_without_different_followup_is_not_failure(self):
        p = self.write_transcript([
            tool_use("1", "Bash", {"command": "make build"}),
            tool_result("1", "error: something"),
        ])
        self.assertEqual(transcript.parse(p, self.cwd)["failures"], [])

    def test_missing_transcript(self):
        res = transcript.parse(os.path.join(self.cwd, "missing.jsonl"), self.cwd)
        self.assertEqual(res, {"files": [], "failures": [], "wrote_note": False})

    def test_detects_note_command(self):
        p = self.write_transcript([
            tool_use("1", "Edit", {"file_path": "x.py"}),
            tool_use("2", "Bash", {"command": 'python3 "/p/scripts/note.py" add --kind open --text "y"'}),
        ])
        self.assertTrue(transcript.parse(p, self.cwd)["wrote_note"])


class TestStopHook(Base):
    def payload(self, tpath, active=False, sid="T"):
        return {"session_id": sid, "cwd": self.cwd, "transcript_path": tpath,
                "hook_event_name": "Stop", "stop_hook_active": active}

    def test_blocks_once_with_file_list(self):
        p = self.write_transcript([tool_use("1", "Edit", {"file_path": os.path.join(self.cwd, "app.py")}),
                                   tool_result("1", "ok")])
        r = run_script("stop.py", stdin=self.payload(p), env=self.env)
        out = json.loads(r.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("app.py", out["reason"])
        self.assertIn("```handoff", out["reason"])
        self.assertIn("No tool calls", out["reason"])
        self.assertNotIn("note.py", out["reason"])
        self.assertLess(len(out["reason"]), 700)
        # follow-up stop with no block in the reply: nothing saved, never blocks again
        r2 = run_script("stop.py", stdin=self.payload(p, active=True), env=self.env)
        self.assertEqual(r2.stdout, "")
        r3 = run_script("stop.py", stdin=self.payload(p), env=self.env)
        self.assertEqual(r3.stdout, "")
        self.assertEqual(hl.load_notes(self.home), [])

    def block_then(self, p, **extra):
        first = run_script("stop.py", stdin=self.payload(p), env=self.env)
        self.assertEqual(json.loads(first.stdout)["decision"], "block")
        pl = self.payload(p, active=True)
        pl.update(extra)
        return run_script("stop.py", stdin=pl, env=self.env)

    def test_collects_block_from_last_assistant_message(self):
        p = self.write_transcript([tool_use("1", "Edit", {"file_path": os.path.join(self.cwd, "app.py")})])
        reply = ("Done.\n\n```handoff\n"
                 "decision: Cache tokens in Redis, not memory | files: app.py, cache/redis.py\n"
                 "dead end: pickle for sessions broke on py3.12\n"
                 "- open: rotate the signing key\n"
                 "gotcha: <surprising trap>\n"
                 "nonsense line\n"
                 "idea: not a kind\n"
                 "```")
        r = self.block_then(p, last_assistant_message=reply)
        self.assertEqual(json.loads(r.stdout), {"systemMessage": "handoff: saved 3 note(s)"})
        notes = hl.load_notes(self.home)
        self.assertEqual([n["kind"] for n in notes], ["decision", "dead_end", "open"])
        self.assertEqual(notes[0]["files"], ["app.py", "cache/redis.py"])
        self.assertEqual(notes[0]["text"], "Cache tokens in Redis, not memory")
        self.assertTrue(all(n["session_id"] == "T" for n in notes))
        # never again, and no duplicate save
        r2 = run_script("stop.py", stdin=self.payload(p, active=True), env=self.env)
        self.assertEqual(r2.stdout, "")
        self.assertEqual(len(hl.load_notes(self.home)), 3)

    def test_collects_block_from_transcript_fallback(self):
        entries = [tool_use("1", "Edit", {"file_path": "a.py"}), tool_result("1", "ok"),
                   {"type": "user", "message": {"content": "Stop hook feedback: ..."}},
                   {"type": "assistant", "message": {"content": [{"type": "text", "text": "Summary."}]}},
                   {"type": "assistant", "message": {"content": [
                       {"type": "text", "text": "```handoff\ngotcha: tests need TZ=UTC | files: a.py\n```"}]}}]
        p = self.write_transcript(entries)
        r = self.block_then(p)
        self.assertIn("saved 1", r.stdout)
        self.assertEqual(hl.load_notes(self.home)[0]["kind"], "gotcha")

    def test_skip_line(self):
        p = self.write_transcript([tool_use("1", "Edit", {"file_path": "a.py"})])
        r = self.block_then(p, last_assistant_message="All set.\n```handoff\nskip\n```")
        self.assertEqual(r.stdout, "")
        self.assertEqual(hl.load_notes(self.home), [])
        self.assertTrue(hl.load_state(self.home, "T").get("skipped"))

    def test_parse_block_caps_and_uses_last_fence(self):
        import stop
        text = "```handoff\nopen: old\n```\n" + "```handoff\n" + "\n".join("open: item %d" % i for i in range(9)) + "\n```"
        notes, skipped = stop.parse_block(text)
        self.assertFalse(skipped)
        self.assertEqual(len(notes), 5)
        self.assertEqual(notes[0][1], "item 0")
        self.assertEqual(stop.parse_block("no fence here"), ([], False))

    def test_stop_hook_active_never_blocks(self):
        p = self.write_transcript([tool_use("1", "Write", {"file_path": "a.py"})])
        r = run_script("stop.py", stdin=self.payload(p, active=True), env=self.env)
        self.assertEqual(r.stdout, "")
        self.assertFalse(hl.load_state(self.home, "T").get("stop_blocked"))

    def test_no_files_no_block(self):
        p = self.write_transcript([{"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}])
        r = run_script("stop.py", stdin=self.payload(p), env=self.env)
        self.assertEqual(r.stdout, "")

    def test_note_already_written_no_block(self):
        p = self.write_transcript([tool_use("1", "Edit", {"file_path": "a.py"})])
        self.note("add", "--session", "T", "--kind", "decision", "--text", "done")
        self.assertEqual(run_script("stop.py", stdin=self.payload(p), env=self.env).stdout, "")

    def test_skip_prevents_block(self):
        p = self.write_transcript([tool_use("1", "Edit", {"file_path": "a.py"})])
        self.note("skip", "--session", "T")
        self.assertEqual(run_script("stop.py", stdin=self.payload(p), env=self.env).stdout, "")

    def test_failures_listed(self):
        p = self.write_transcript([
            tool_use("1", "Bash", {"command": "npm run build"}),
            tool_result("1", "Error: Cannot find module 'vite'", is_error=True),
            tool_use("2", "Bash", {"command": "npm install && npm run build"}),
            tool_result("2", "built"),
            tool_use("3", "Edit", {"file_path": "vite.config.ts"}),
        ])
        out = json.loads(run_script("stop.py", stdin=self.payload(p), env=self.env).stdout)
        self.assertIn("Cannot find module", out["reason"])


if __name__ == "__main__":
    unittest.main()
