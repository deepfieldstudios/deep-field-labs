"""Plain-English previews for csv, xlsx, docx, pptx, text and unknown files."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import PLUGIN_ROOT, make_docx, make_pptx, make_xlsx  # noqa: E402

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import officefiles as of  # noqa: E402
import preview  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class TestXlsx(Base):
    def budget(self, name, rows, extra=None):
        sheets = {"Sheet1": rows}
        sheets.update(extra or {})
        p = self.d / name
        make_xlsx(p, sheets)
        return p

    def base_rows(self):
        rows = [["Item", "Month", "Cost"]] + [[f"Line {i}", "Jan", 100 + i] for i in range(2, 14)]
        rows.append(["Rent", "Jan", 1200])                       # row 14
        rows.append(["Total", "", ("=SUM(C2:C14)", 3477)])        # row 15
        return rows

    def test_reader(self):
        p = self.budget("b.xlsx", self.base_rows())
        book = of.read_xlsx(p)
        self.assertEqual(list(book), ["Sheet1"])
        self.assertEqual(book["Sheet1"][(14, 3)], ("1,200", None))
        self.assertEqual(book["Sheet1"][(15, 3)], ("3,477", "=SUM(C2:C14)"))
        self.assertEqual(book["Sheet1"][(1, 1)], ("Item", None))

    def test_changed_cell(self):
        old = self.budget("old.xlsx", self.base_rows())
        rows = self.base_rows()
        rows[13][2] = 1450
        rows[14][2] = ("=SUM(C2:C14)", 3727)
        new = self.budget("new.xlsx", rows)
        s = preview.summarize(old, new, name="budget.xlsx")
        self.assertIn("Sheet1!C14 (Cost): 1,200 → 1,450", s)
        self.assertIn("Sheet1!C15 (Cost): 3,477 → 3,727", s)
        self.assertIn("Sheet1: 2 cells changed", s)
        self.assertTrue(s.startswith("What changed in budget.xlsx"))

    def test_formula_change(self):
        old = self.budget("old.xlsx", self.base_rows())
        rows = self.base_rows()
        rows[14][2] = ("=SUM(C2:C13)", 3477)
        new = self.budget("new.xlsx", rows)
        self.assertIn("C15 (Cost): formula =SUM(C2:C14) → =SUM(C2:C13)", preview.summarize(old, new))

    def test_inserted_row_is_one_change(self):
        old = self.budget("old.xlsx", self.base_rows())
        rows = self.base_rows()
        rows.insert(5, ["New line", "Jan", 55])
        new = self.budget("new.xlsx", rows)
        s = preview.summarize(old, new)
        self.assertIn('Sheet1: Added row 6: "New line, Jan, 55"', s)
        self.assertNotIn("cells changed", s)

    def test_removed_row_and_sheets(self):
        old = self.budget("old.xlsx", self.base_rows(), {"Notes": [["hello"]]})
        rows = self.base_rows()
        del rows[3]
        new = self.budget("new.xlsx", rows, {"Summary": [["a"], ["b"]]})
        s = preview.summarize(old, new)
        self.assertIn('Added sheet "Summary" (2 rows)', s)
        self.assertIn('Removed sheet "Notes"', s)
        self.assertIn("Sheet1: Removed row 4", s)


class TestCsv(Base):
    def test_cells_and_rows(self):
        old, new = self.d / "a.csv", self.d / "b.csv"
        old.write_text("Name,Amount\nRent,1200\nFood,300\nGym,40\n")
        new.write_text("Name,Amount\nRent,1450\nFood,300\nTravel,90\n")
        s = preview.summarize(old, new, name="costs.csv")
        self.assertIn("B2 (Amount): 1200 → 1450", s)
        self.assertIn("A4 (Name): Gym → Travel", s)
        self.assertIn("B4 (Amount): 40 → 90", s)

    def test_rows_added_removed(self):
        old, new = self.d / "a.csv", self.d / "b.csv"
        old.write_text("id,v\n1,a\n2,b\n3,c\n")
        new.write_text("id,v\n1,a\n3,c\n4,d\n5,e\n")
        s = preview.summarize(old, new)
        self.assertIn('Removed row 3: "2, b"', s)
        self.assertIn('Added row 4: "4, d"', s)
        self.assertIn('Added row 5: "5, e"', s)

    def test_output_is_capped(self):
        old, new = self.d / "a.csv", self.d / "b.csv"
        old.write_text("v\n" + "\n".join(str(i) for i in range(500)))
        new.write_text("v\n" + "\n".join(str(i * 7) for i in range(500)))
        s = preview.summarize(old, new, max_lines=10)
        self.assertLessEqual(len(s.splitlines()), 12)
        self.assertIn("more changes", s)


class TestDocx(Base):
    def test_paragraph_changes(self):
        old, new = self.d / "old.docx", self.d / "new.docx"
        make_docx(old, ["Services Agreement", "The client will pay within 30 days of the invoice date.",
                        "This clause is removed later.", "Signed by both parties."])
        make_docx(new, ["Services Agreement", "The client will pay within 14 days of the invoice date.",
                        "Signed by both parties.", "Late payments incur a 2% monthly fee."])
        self.assertEqual(of.read_docx_paragraphs(old)[0], "Services Agreement")
        s = preview.summarize(old, new, name="contract.docx")
        self.assertIn('Changed paragraph 2: "The client will pay within 30 days of the invoice date." → '
                      '"The client will pay within 14 days of the invoice date."', s)
        self.assertIn('Removed paragraph (was paragraph 3): "This clause is removed later."', s)
        self.assertIn('Added paragraph 4: "Late payments incur a 2% monthly fee."', s)

    def test_long_paragraph_snippet(self):
        old, new = self.d / "old.docx", self.d / "new.docx"
        words = [f"w{i}" for i in range(60)]
        make_docx(old, [" ".join(words)])
        words[30] = "CHANGED"
        make_docx(new, [" ".join(words)])
        s = preview.summarize(old, new)
        self.assertIn('"…w24 w25 w26 w27 w28 w29 w30 w31', s)
        self.assertIn("CHANGED", s)
        self.assertNotIn("w0 ", s)


class TestPptxTextOther(Base):
    def test_slides(self):
        old, new = self.d / "old.pptx", self.d / "new.pptx"
        make_pptx(old, [["Q3 Results", "Revenue up 10%"], ["Next steps", "Hire two people"]])
        make_pptx(new, [["Q3 Results", "Revenue up 12%"], ["Next steps", "Hire two people"], ["Questions?"]])
        s = preview.summarize(old, new)
        self.assertIn("Slide count 2 → 3", s)
        self.assertIn('Slide 1: Changed text box line 2: "Revenue up 10%" → "Revenue up 12%"', s)
        self.assertIn('Added slide 3: "Questions?"', s)

    def test_markdown(self):
        old, new = self.d / "a.md", self.d / "b.md"
        old.write_text("# Plan\n\nWe launch in May.\n\nBudget is small.\n")
        new.write_text("# Plan\n\nWe launch in June.\n\nBudget is small.\n\nRisks: none yet.\n")
        s = preview.summarize(old, new, name="plan.md")
        self.assertIn('Changed paragraph 2: "We launch in May." → "We launch in June."', s)
        self.assertIn('Added paragraph 4: "Risks: none yet."', s)

    def test_binary_and_missing(self):
        old, new = self.d / "a.pdf", self.d / "b.pdf"
        old.write_bytes(b"%PDF-1.4\x00\x01" * 10)
        new.write_bytes(b"%PDF-1.4\x00\x02" * 20)
        self.assertIn("can't show the content changes", preview.summarize(old, new))
        self.assertIn("was deleted", preview.summarize(old, self.d / "nope.pdf"))
        self.assertIn("no changes", preview.summarize(old, old))

    def test_corrupt_office_file_falls_back(self):
        old, new = self.d / "a.xlsx", self.d / "b.xlsx"
        old.write_bytes(b"not a zip")
        new.write_bytes(b"also not a zip")
        self.assertIn("can't show the content changes", preview.summarize(old, new))

    def test_cli(self):
        old, new = self.d / "a.txt", self.d / "b.txt"
        old.write_text("one\ntwo\n")
        new.write_text("one\nthree\n")
        p = subprocess.run([sys.executable, str(PLUGIN_ROOT / "scripts" / "preview.py"), str(old), str(new)],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn('"two" → "three"', p.stdout)


if __name__ == "__main__":
    unittest.main()
