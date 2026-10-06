---
name: reconcile-spreadsheets
description: Compare two spreadsheets or lists (Excel or CSV) that should agree, such as a bank statement against the accounts, an invoice list against payments, or this month's stock count against last month's. Lines them up by a shared column like invoice number or customer ID and reports what is missing from either side and which values don't match.
---

# Reconcile two spreadsheets

Use this when someone has two lists that should match and wants to know where they don't.

## Steps

1. Find out (or work out by looking at the first few rows) which two files to compare and which
   column identifies each row in both, e.g. "Invoice No", "Order ID", "Email". If the column has a
   different name in each file, note both. Ask only if it is genuinely unclear.
2. Run the bundled script. It reads `.csv` and `.xlsx`, never changes either file, and treats
   "$1,200.00" and "1200" as the same number:

   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/reconcile-spreadsheets/scripts/reconcile.py" \
     "<first file>" "<second file>" --key "<column>" [--key-b "<column in second file>"] \
     [--sheet-a "<sheet>"] [--sheet-b "<sheet>"] [--columns "Amount,Date"] [--tolerance 0.01] \
     --out "<first file name> - reconciliation.csv"
   ```

   - Use `--columns` to check only the columns that matter (often just the amount).
   - Use `--tolerance 0.01` for money, so rounding pennies don't show up as problems.
   - Save the detailed list with `--out` next to the first file, so the person can open it in Excel.
3. Explain the result in plain words: how many rows match, which are missing from each side,
   which disagree and by how much. Lead with the most important finding (usually money that
   doesn't match). Name the report file and the folder it is in.
4. Do not "fix" either spreadsheet unless asked. If asked, office-mode saves a copy first.
