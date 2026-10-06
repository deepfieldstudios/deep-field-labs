"""Natural-language version selection, restore, undo, and the list command."""
import datetime as dt
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import PLUGIN_ROOT  # noqa: E402

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import om_common as om  # noqa: E402
import versions  # noqa: E402

TZ = dt.datetime(2026, 10, 6, 12, 0).astimezone().tzinfo


def at(day, h, m=0):
    return dt.datetime(2026, 10, day, h, m, tzinfo=TZ)


# 6 Oct 2026 is a Tuesday.
NOW = at(6, 15, 0)
TIMES = [at(4, 10), at(5, 9), at(5, 17, 30), at(6, 12), at(6, 14, 30)]


def fake_entries():
    return [{"version": f"v{i}.txt", "time": t.isoformat(), "sha": f"{i:x}" * 64, "reason": "Edit"}
            for i, t in enumerate(TIMES)]


class TestSelect(unittest.TestCase):
    def pick(self, s):
        e = versions.select_version(fake_entries(), s, now=NOW)
        return om.parse_time(e["time"]) if e else None

    def test_relative_words(self):
        self.assertEqual(self.pick("yesterday"), at(5, 17, 30))
        self.assertEqual(self.pick("yesterday 9:00"), at(5, 9))
        self.assertEqual(self.pick("yesterday at 9am"), at(5, 9))
        self.assertEqual(self.pick("today"), at(6, 14, 30))
        self.assertEqual(self.pick("today 12:05"), at(6, 12))

    def test_weekdays(self):
        self.assertEqual(self.pick("sunday"), at(4, 10))
        self.assertEqual(self.pick("Monday"), at(5, 17, 30))
        self.assertEqual(self.pick("the version from monday 09:00"), at(5, 9))
        self.assertEqual(self.pick("tuesday"), at(6, 14, 30))   # today is Tuesday
        self.assertIsNone(self.pick("last tuesday"))           # a week ago: nothing saved then
        self.assertEqual(self.pick("Tuesday 12:00"), at(6, 12))  # as shown by `list`

    def test_ago(self):
        self.assertEqual(self.pick("2 hours ago"), at(6, 12))
        self.assertEqual(self.pick("an hour ago"), at(6, 12))
        self.assertEqual(self.pick("30 minutes ago"), at(6, 14, 30))
        self.assertEqual(self.pick("half an hour ago"), at(6, 14, 30))
        self.assertEqual(self.pick("1 day ago"), at(5, 9))
        self.assertIsNone(self.pick("10 days ago"))

    def test_numbers_names_and_dates(self):
        self.assertEqual(self.pick("1"), at(6, 14, 30))
        self.assertEqual(self.pick("#2"), at(6, 12))
        self.assertEqual(self.pick("latest"), at(6, 14, 30))
        self.assertEqual(self.pick("oldest"), at(4, 10))
        self.assertEqual(self.pick("2026-10-05"), at(5, 17, 30))
        self.assertEqual(self.pick("2026-10-05 09:10"), at(5, 9))
        self.assertEqual(self.pick("2222"), at(5, 17, 30))  # hash code prefix
        self.assertEqual(self.pick("v1.txt"), at(5, 9))
        self.assertIsNone(self.pick("whenever"))
        self.assertIsNone(self.pick("99"))


class TestHumanFormatting(unittest.TestCase):
    def test_times_and_sizes(self):
        self.assertEqual(om.human_time(at(6, 14, 2), NOW), "Today 14:02")
        self.assertEqual(om.human_time(at(5, 9, 0), NOW), "Yesterday 09:00")
        self.assertEqual(om.human_time(at(1, 14, 2), NOW), "Thursday 14:02")
        self.assertEqual(om.human_time(dt.datetime(2026, 9, 5, 8, 0, tzinfo=TZ), NOW), "5 Sep 08:00")
        self.assertEqual(om.human_size(512), "512 bytes")
        self.assertEqual(om.human_size(1536), "1.5 KB")
        self.assertEqual(om.human_size(25 * 1024 * 1024), "25 MB")


class TestRestore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        os.environ["OFFICE_MODE_HOME"] = str(self.dir / "home")
        self.cfg = dict(om.DEFAULT_CONFIG)
        self.f = self.dir / "letter.txt"
        for i, (text, t) in enumerate([("first", at(5, 9)), ("second", at(6, 12))]):
            self.f.write_text(text)
            om.snapshot(self.f, self.cfg, reason="Edit", now=t)
        self.f.write_text("third")

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("OFFICE_MODE_HOME", None)

    def restore(self, when):
        buf = io.StringIO()
        code = versions.cmd_restore(str(self.f), when, self.cfg, str(self.dir), now=NOW, out=buf)
        return code, buf.getvalue()

    def test_restore_and_undo(self):
        code, msg = self.restore("yesterday")
        self.assertEqual(code, 0)
        self.assertEqual(self.f.read_text(), "first")
        self.assertIn("Restored letter.txt", msg)
        self.assertIn("Yesterday 09:00", msg)
        self.assertIn("undo", msg)
        # The overwritten "third" was saved before restoring.
        shas = [e["reason"] for e in versions.entries_for(self.f, self.cfg)]
        self.assertIn("restore", shas)
        code, _ = self.restore("undo")
        self.assertEqual(code, 0)
        self.assertEqual(self.f.read_text(), "third")
        # Undoing the undo goes back again.
        self.restore("undo")
        self.assertEqual(self.f.read_text(), "first")

    def test_restore_deleted_file(self):
        self.f.unlink()
        code, _ = self.restore("5 hours ago")
        self.assertEqual(code, 0)
        self.assertEqual(self.f.read_text(), "first")

    def test_no_match(self):
        code, msg = self.restore("last christmas")
        self.assertEqual(code, 1)
        self.assertEqual(self.f.read_text(), "third")
        self.assertIn("couldn't find", msg)

    def test_list_output(self):
        buf = io.StringIO()
        versions.cmd_list(str(self.f), self.cfg, str(self.dir), now=NOW, out=buf)
        text = buf.getvalue()
        self.assertIn("Saved copies of letter.txt", text)
        self.assertIn("Today 12:00", text)
        self.assertIn("Yesterday 09:00", text)
        self.assertIn("before an edit", text)
        buf = io.StringIO()
        versions.cmd_list(None, self.cfg, str(self.dir), now=NOW, out=buf)
        self.assertIn("letter.txt", buf.getvalue())
        self.assertIn("2 copies", buf.getvalue())

    def test_cli_end_to_end(self):
        env = dict(os.environ)
        run = lambda *a: subprocess.run([sys.executable, str(PLUGIN_ROOT / "scripts" / "versions.py"),
                                         "--cwd", str(self.dir), *a], capture_output=True, text=True, env=env)
        p = run("restore", "letter.txt", "oldest")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.f.read_text(), "first")
        p = run("changes", "letter.txt", "latest")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("letter.txt", p.stdout)
        p = run("list", "letter.txt")
        self.assertIn("before restoring an older copy", p.stdout)


if __name__ == "__main__":
    unittest.main()
