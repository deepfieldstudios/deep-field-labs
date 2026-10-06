"""Runner end to end (replay), record-then-replay, and the Anthropic adapter with urllib mocked."""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from helpers import api_reply, write_golden, write_suite
import init
import providers
import run

SUITE = {
    "name": "support",
    "prompt_file": "prompts/support.md",
    "model": "claude-sonnet-5-5",
    "cases": [
        {"id": "refund", "input": "Can I return boots?",
         "expect": [{"type": "contains", "value": "30 days"}, {"type": "max_chars", "value": 200},
                    {"type": "llm_judge", "rubric": "mentions receipt"}]},
        {"id": "json", "input": [{"role": "user", "content": "status of 1182 as JSON"}],
         "expect": [{"type": "json_schema", "schema": {"type": "object", "required": ["status"]}}]},
        {"id": "tone", "input": "hi", "expect": [{"type": "similar_to_golden", "threshold": 0.9}]},
    ],
}


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name)
        (self.p / "prompts").mkdir()
        (self.p / "prompts" / "support.md").write_text("You are a helpful support agent.")
        write_suite(self.p, SUITE)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("ANTHROPIC_BASE_URL", None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_main(self, *args):
        buf = io.StringIO()
        code = run.main(["--project", str(self.p), *args], out=buf)
        return code, buf.getvalue()

    def latest_results(self):
        files = sorted((self.p / "prompt-tests" / ".results").glob("*.json"))
        return json.loads(files[-1].read_text())


class TestReplay(ProjectTest):
    def test_replay_all_pass(self):
        write_golden(self.p, "support", "refund", "Yes, within 30 days with a receipt.")
        write_golden(self.p, "support", "json", '{"status": "shipped"}')
        write_golden(self.p, "support", "tone", "Hello! How can I help?")
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertIn("provider replay", out)
        self.assertIn("3/3 passed", out)
        self.assertIn("1 assertions skipped", out)  # llm_judge without a key
        res = self.latest_results()
        self.assertEqual(res["summary"], {"total": 3, "passed": 3, "failed": 0, "errors": 0, "skipped_assertions": 1})
        refund = res["cases"][0]
        self.assertEqual(refund["latency_ms"], 100)
        self.assertEqual(refund["usage"]["input_tokens"], 10)
        self.assertTrue(refund["replayed"])

    def test_replay_failures_and_missing_golden(self):
        write_golden(self.p, "support", "refund", "No returns, sorry. " * 20)
        write_golden(self.p, "support", "json", "not json")
        code, out = self.run_main()
        self.assertEqual(code, 1)
        res = self.latest_results()
        status = {c["case"]: c["status"] for c in res["cases"]}
        self.assertEqual(status, {"refund": "fail", "json": "fail", "tone": "error"})
        self.assertIn("no recorded output", out)
        failed = [a["type"] for a in res["cases"][0]["assertions"] if a["passed"] is False]
        self.assertEqual(failed, ["contains", "max_chars"])

    def test_filters(self):
        write_golden(self.p, "support", "tone", "Hello!")
        code, _ = self.run_main("support", "--case", "tone")
        self.assertEqual(code, 0)
        self.assertEqual([c["case"] for c in self.latest_results()["cases"]], ["tone"])
        code, out = self.run_main("nope")
        self.assertEqual(code, 2)
        self.assertIn("No suite matches", out)

    def test_config_errors(self):
        code, out = self.run_main("--record", "--provider", "replay")
        self.assertEqual(code, 2)
        (self.p / "prompts" / "support.md").unlink()
        code, out = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("prompt_file", out)
        empty = tempfile.TemporaryDirectory()
        buf = io.StringIO()
        self.assertEqual(run.main(["--project", empty.name], out=buf), 2)
        self.assertIn("/prompt-regression:init", buf.getvalue())
        empty.cleanup()

    def test_init_scaffold_is_a_valid_suite(self):
        d = tempfile.TemporaryDirectory()
        buf = io.StringIO()
        self.assertEqual(init.main(["--project", d.name], out=buf), 0)
        self.assertEqual(init.main(["--project", d.name], out=buf), 1)  # never overwrites
        files = list((Path(d.name) / "prompt-tests").iterdir())
        self.assertEqual([f.name for f in files], ["example.json"])
        import pr_common
        suite = pr_common.load_suite(files[0], d.name)
        self.assertEqual(len(suite["cases"]), 3)
        d.cleanup()


class TestRecordThenReplay(ProjectTest):
    def test_record_then_replay(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        replies = {"Can I return boots?": "Yes — within 30 days, bring the receipt.",
                   "status of 1182 as JSON": '{"status": "shipped"}', "hi": "Hello! How can I help?"}
        sent = []

        def fake_urlopen(req, timeout=None):
            body = json.loads(req.data)
            sent.append(body)
            if "grading the output" in body["messages"][0]["content"]:
                return api_reply('{"pass": true, "score": 0.9, "reason": "mentions the receipt"}')
            return api_reply(replies[body["messages"][-1]["content"]])

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            code, out = self.run_main("--record")
        self.assertEqual(code, 0, out)
        self.assertIn("provider anthropic", out)
        self.assertIn("(recording)", out)
        golden = json.loads((self.p / "prompt-tests/.golden/support/refund.json").read_text())
        self.assertEqual(golden["output"], replies["Can I return boots?"])
        self.assertEqual(golden["usage"], {"input_tokens": 12, "output_tokens": 7})
        self.assertEqual(golden["input"], "Can I return boots?")
        # System prompt came from prompt_file; judge call happened (key set).
        self.assertEqual(sent[0]["system"], "You are a helpful support agent.")
        self.assertEqual(len(sent), 4)
        rec = self.latest_results()
        judge = [a for a in rec["cases"][0]["assertions"] if a["type"] == "llm_judge"][0]
        self.assertTrue(judge["passed"])

        # Now offline: no key, no network, replay what was recorded.
        os.environ.pop("ANTHROPIC_API_KEY")

        def no_network(*a, **k):
            raise AssertionError("network used during replay")
        with mock.patch("urllib.request.urlopen", no_network):
            code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertIn("provider replay", out)
        res = self.latest_results()
        self.assertEqual([c["output"] for c in res["cases"]], [replies[k] for k in replies])


class TestAnthropicAdapter(unittest.TestCase):
    REQ = {"suite": "s", "case_id": "c", "model": "claude-sonnet-5-5", "system": "Be brief.",
           "messages": [{"role": "user", "content": "hi"}], "max_tokens": 50, "temperature": 0}

    def test_request_construction(self):
        p = providers.AnthropicProvider(api_key="sk-abc", url=None)
        with mock.patch.dict(os.environ, {}, clear=True):
            p = providers.AnthropicProvider(api_key="sk-abc")
            req = p.build_request(self.REQ)
        self.assertEqual(req.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(req.get_method(), "POST")
        headers = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(headers["x-api-key"], "sk-abc")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(json.loads(req.data), {"model": "claude-sonnet-5-5", "max_tokens": 50,
                                                "messages": [{"role": "user", "content": "hi"}],
                                                "system": "Be brief.", "temperature": 0})
        # No system / temperature → omitted.
        body = json.loads(p.build_request(dict(self.REQ, system="", temperature=None)).data)
        self.assertNotIn("system", body)
        self.assertNotIn("temperature", body)

    def test_base_url_and_missing_key(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "http://localhost:9/"}, clear=True):
            p = providers.AnthropicProvider(api_key="k")
            self.assertEqual(p.build_request(self.REQ).full_url, "http://localhost:9/v1/messages")
            with self.assertRaises(providers.ProviderError):
                providers.AnthropicProvider().build_request(self.REQ)

    def test_complete_parses_response(self):
        p = providers.AnthropicProvider(api_key="k")
        with mock.patch("urllib.request.urlopen", lambda req, timeout=None: api_reply("Hello", inp=3, out=1)):
            r = p.complete(self.REQ)
        self.assertEqual(r["output"], "Hello")
        self.assertEqual(r["usage"], {"input_tokens": 3, "output_tokens": 1})
        self.assertEqual(r["stop_reason"], "end_turn")
        self.assertIsInstance(r["latency_ms"], int)

    def test_retry_then_error(self):
        sleeps, calls = [], []

        def overloaded(req, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 529, "Overloaded", {}, io.BytesIO(b'{"error":"overloaded"}'))
            return api_reply("ok")
        p = providers.AnthropicProvider(api_key="k", sleep=sleeps.append)
        with mock.patch("urllib.request.urlopen", overloaded):
            self.assertEqual(p.complete(self.REQ)["output"], "ok")
        self.assertEqual(sleeps, [2])

        def bad_request(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 400, "Bad", {}, io.BytesIO(b'{"error":"bad model"}'))
        with mock.patch("urllib.request.urlopen", bad_request):
            with self.assertRaises(providers.ProviderError) as cm:
                p.complete(self.REQ)
        self.assertIn("HTTP 400", str(cm.exception))
        self.assertIn("bad model", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
