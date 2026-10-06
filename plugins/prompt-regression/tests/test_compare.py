"""compare.py: pass-rate change, regressions, fixes, output diffs, deltas, --latest."""
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import helpers  # noqa: F401
import compare


def case(name, passed, output, latency=100, inp=10, out=5, failed=None):
    return {"suite": "s", "case": name, "status": "pass" if passed else "fail", "passed": passed,
            "output": output, "latency_ms": latency, "usage": {"input_tokens": inp, "output_tokens": out},
            "assertions": [] if passed else [{"type": failed or "contains", "passed": False, "detail": "missing ['30 days']"}]}


def results(cases, started="2026-10-06T10:00:00"):
    return {"started_at": started, "cases": cases,
            "summary": {"total": len(cases), "passed": sum(c["passed"] for c in cases)}}


class TestCompare(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name) / "prompt-tests" / ".results"
        self.d.mkdir(parents=True)
        self.old = self.d / "2026-10-06T100000.json"
        self.new = self.d / "2026-10-06T110000.json"
        self.old.write_text(json.dumps(results([
            case("refund", True, "Within 30 days.\nBring a receipt."),
            case("json", False, "oops", failed="json_valid"),
            case("same", True, "unchanged"),
            case("dropped", True, "x")])))
        time.sleep(0.01)
        self.new.write_text(json.dumps(results([
            case("refund", False, "Within 14 days.\nBring a receipt.", latency=150, inp=12, out=9),
            case("json", True, '{"ok": true}'),
            case("same", True, "unchanged"),
            case("brand-new", True, "y")], started="2026-10-06T11:00:00")))

    def tearDown(self):
        self.tmp.cleanup()

    def test_report(self):
        md, regressed = compare.report(self.old, self.new)
        self.assertTrue(regressed)
        self.assertIn("**Regressions found.**", md)
        self.assertIn("| Pass rate | 3/4 (75.0%) | 3/4 (75.0%) | +0.0 pts |", md)
        self.assertIn("## Regressed (1)", md)
        self.assertIn("- `s/refund` — contains (missing ['30 days'])", md)
        self.assertIn("## Fixed (1)", md)
        self.assertIn("- `s/json` — was: json_valid", md)
        self.assertIn("## New cases (1)", md)
        self.assertIn("## Removed cases (1)", md)
        self.assertIn("## Output changes (2)", md)
        self.assertIn("-Within 30 days.", md)
        self.assertIn("+Within 14 days.", md)
        self.assertNotIn("### `s/same`", md)
        # Latency over shared cases: old avg 100, new (150+100+100)/3 = 116.67
        self.assertIn("| Avg latency (shared cases) | 100 ms | 117 ms | +17 ms (+17%) |", md)
        self.assertIn("| Input tokens | 30 | 32 | +2 (+7%) |", md)

    def test_no_regressions(self):
        md, regressed = compare.report(self.old, self.old)
        self.assertFalse(regressed)
        self.assertIn("**No regressions.**", md)
        self.assertNotIn("## Output changes", md)

    def test_cli_latest_and_ci_flag(self):
        buf = io.StringIO()
        project = str(Path(self.tmp.name))
        self.assertEqual(compare.main(["--latest", "--project", project], out=buf), 0)
        self.assertIn(self.new.name, buf.getvalue())
        out = Path(self.tmp.name) / "report.md"
        code = compare.main([str(self.old), str(self.new), "--out", str(out), "--fail-on-regression"], out=io.StringIO())
        self.assertEqual(code, 1)
        self.assertTrue(out.read_text().startswith("# Prompt regression report"))

    def test_truncates_long_diffs(self):
        big_old = "\n".join(f"line {i}" for i in range(100))
        big_new = "\n".join(f"LINE {i}" for i in range(100))
        self.old.write_text(json.dumps(results([case("big", True, big_old)])))
        self.new.write_text(json.dumps(results([case("big", True, big_new)])))
        md, _ = compare.report(self.old, self.new)
        self.assertIn("more lines", md)
        self.assertLess(md.count("\n"), 60)


if __name__ == "__main__":
    unittest.main()
