"""Bundled skill scripts: reconcile two spreadsheets, clean a mailing list."""
import csv
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import PLUGIN_ROOT, make_xlsx  # noqa: E402

RECONCILE = PLUGIN_ROOT / "skills" / "reconcile-spreadsheets" / "scripts" / "reconcile.py"
CLEAN = PLUGIN_ROOT / "skills" / "clean-mailing-list" / "scripts" / "clean_list.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rec = load(RECONCILE, "reconcile")
cl = load(CLEAN, "clean_list")


class TestReconcile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.a = self.d / "invoices.csv"
        self.a.write_text("Invoice No,Customer,Amount\n1001,Acme,\"$1,200.00\"\n1002,Bolt,300\n"
                          "1003,Cora,50.004\n1004,Dune,75\n1004,Dune,75\n")
        self.b = self.d / "payments.xlsx"
        make_xlsx(self.b, {"Paid": [["invoice no", "Customer", "Amount"],
                                    [1001, "ACME", 1200], [1002, "Bolt", 310],
                                    [1003, "Cora", 50], [1005, "Echo", 20]]})

    def tearDown(self):
        self.tmp.cleanup()

    def test_reconcile_csv_vs_xlsx(self):
        res = rec.reconcile(self.a, self.b, "Invoice No", tolerance=0.01)
        self.assertEqual(res["matched"], 2)  # 1001 (number formats + case) and 1003 (within tolerance)
        self.assertEqual([m[0]["Invoice No"] for m in res["mismatched"]], ["1002"])
        self.assertEqual(res["mismatched"][0][2], [("Amount", "300", "310")])
        self.assertEqual([r["Invoice No"] for r in res["only_a"]], ["1004"])
        self.assertEqual([r["invoice no"] for r in res["only_b"]], ["1005"])
        self.assertEqual(res["dups_a"], {"1004": [5, 6]})

    def test_without_tolerance(self):
        res = rec.reconcile(self.a, self.b, "Invoice No")
        self.assertEqual(len(res["mismatched"]), 2)

    def test_columns_filter_and_missing_key(self):
        res = rec.reconcile(self.a, self.b, "Invoice No", columns=["Customer"])
        self.assertEqual(res["matched"], 3)
        with self.assertRaises(SystemExit):
            rec.reconcile(self.a, self.b, "Nope")

    def test_cli_report_and_csv(self):
        out = self.d / "report.csv"
        p = subprocess.run([sys.executable, str(RECONCILE), str(self.a), str(self.b), "--key", "Invoice No",
                            "--tolerance", "0.01", "--out", str(out)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("2 rows match exactly", p.stdout)
        self.assertIn("Amount is 300 in invoices.csv but 310 in payments.xlsx", p.stdout)
        with out.open() as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows[0][0], "Status")
        self.assertEqual(len(rows), 4)


class TestCleanList(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_split_name(self):
        self.assertEqual(cl.split_name("Jane Smith"), ("Jane", "Smith"))
        self.assertEqual(cl.split_name("Dr. Jane van der Berg"), ("Jane", "van der Berg"))
        self.assertEqual(cl.split_name("Smith, John"), ("John", "Smith"))
        self.assertEqual(cl.split_name("Mary Ann Lee"), ("Mary Ann", "Lee"))
        self.assertEqual(cl.split_name("Cher"), ("Cher", ""))

    def test_email_validation(self):
        for good in ["a@b.co", "first.last+tag@example.org"]:
            self.assertTrue(cl.valid_email(good), good)
        for bad in ["a@b", "no-at.com", "a..b@c.com", "a@b.c", ""]:
            self.assertFalse(cl.valid_email(bad), bad)
        self.assertEqual(cl.clean_email("  Jane@Example.COM "), "jane@example.com")
        self.assertEqual(cl.clean_email("mailto:x@y.com"), "x@y.com")

    def test_clean_csv(self):
        src = self.d / "contacts.csv"
        src.write_text("Name,Email,Phone\n Jane Smith ,Jane@Example.com,\n"
                       "Jane S,jane@example.COM ,0123\nBob,bob@,\nAmy Lee,,\nTom Jones,tom@jones.io,999\n")
        header, kept, problems, stats, _ = cl.clean(src)
        self.assertEqual(header, ["Name", "Email", "Phone", "First name", "Last name"])
        self.assertEqual([r[1] for r in kept], ["jane@example.com", "tom@jones.io"])
        self.assertEqual(kept[0], ["Jane Smith", "jane@example.com", "0123", "Jane", "Smith"])  # phone merged
        self.assertEqual(stats["duplicates"], 1)
        self.assertEqual(stats["merged"], 1)
        self.assertEqual(stats["invalid"], 1)
        self.assertEqual(stats["blank"], 1)
        self.assertEqual(len(problems), 2)

    def test_cli_xlsx_and_original_untouched(self):
        src = self.d / "list.xlsx"
        make_xlsx(src, {"Sheet1": [["Full name", "E-mail"], ["Ann Bee", "ANN@bee.com"], ["Ann B", "ann@bee.com"],
                                   ["Cal Dee", "cal@dee"]]})
        before = src.read_bytes()
        p = subprocess.run([sys.executable, str(CLEAN), str(src)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(src.read_bytes(), before)
        out = self.d / "list - cleaned.csv"
        with out.open() as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows, [["Full name", "E-mail", "First name", "Last name"],
                                ["Ann Bee", "ann@bee.com", "Ann", "Bee"]])
        self.assertTrue((self.d / "list - problems.csv").exists())
        self.assertIn("1 duplicates removed", p.stdout)
        self.assertIn("was not changed", p.stdout)

    def test_refuses_to_overwrite(self):
        src = self.d / "x.csv"
        src.write_text("Email\na@b.com\n")
        p = subprocess.run([sys.executable, str(CLEAN), str(src), "--out", str(src)], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(src.read_text(), "Email\na@b.com\n")


if __name__ == "__main__":
    unittest.main()
