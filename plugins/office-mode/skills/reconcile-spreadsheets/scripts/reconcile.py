#!/usr/bin/env python3
"""Match two spreadsheets (CSV or XLSX) on a key column and report what doesn't line up.

  reconcile.py <first> <second> --key "Invoice No" [--key-b "Ref"] [--sheet-a S] [--sheet-b S]
               [--columns Amount,Date] [--tolerance 0.01] [--out report.csv]

Reports rows only in the first file, rows only in the second, rows whose shared columns
disagree, and keys that appear more than once. Numbers are compared as numbers
("$1,200.00" equals "1200"), with an optional tolerance. Neither input file is changed.
"""
import argparse
import csv
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import officefiles as of  # noqa: E402


def load(path, sheet=None):
    # Keep the row numbers the person sees in Excel, even when blank rows are skipped.
    rows = [(n, r) for n, r in enumerate(of.read_table(path, sheet), start=1) if any(str(c).strip() for c in r)]
    if not rows:
        return [], []
    header = [str(h).strip() for h in rows[0][1]]
    records = []
    for i, r in rows[1:]:
        rec = {header[c]: (str(r[c]).strip() if c < len(r) else "") for c in range(len(header)) if header[c]}
        rec["__row__"] = i
        records.append(rec)
    return header, records


def find_col(header, name):
    for h in header:
        if h == name:
            return h
    low = name.strip().lower()
    for h in header:
        if h.lower() == low:
            return h
    return None


def norm_key(v):
    v = str(v).strip().lower()
    if re.fullmatch(r"-?\d+\.0+", v):
        v = v.split(".")[0]
    return v


_NUM = re.compile(r"^\(?-?[£$€¥]?\s*-?[\d,]*\.?\d+\)?%?$")


def as_number(v):
    s = str(v).strip().replace(" ", "")
    if not s or not _NUM.match(s):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[()£$€¥,%]", "", s)
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def same(a, b, tol):
    if a == b:
        return True
    na, nb = as_number(a), as_number(b)
    if na is not None and nb is not None:
        return abs(na - nb) <= tol
    return a.strip().lower() == b.strip().lower()


def reconcile(path_a, path_b, key, key_b=None, sheet_a=None, sheet_b=None, columns=None, tolerance=0.0):
    ha, ra = load(path_a, sheet_a)
    hb, rb = load(path_b, sheet_b)
    ka, kb = find_col(ha, key), find_col(hb, key_b or key)
    if not ka:
        raise SystemExit(f"Couldn't find a column called '{key}' in {Path(path_a).name}. Columns: {', '.join(ha)}")
    if not kb:
        raise SystemExit(f"Couldn't find a column called '{key_b or key}' in {Path(path_b).name}. Columns: {', '.join(hb)}")
    if columns:
        pairs = []
        for c in columns:
            ca, cb = find_col(ha, c), find_col(hb, c)
            if ca and cb:
                pairs.append((ca, cb))
    else:
        pairs = [(h, find_col(hb, h)) for h in ha if h != ka and find_col(hb, h) and find_col(hb, h) != kb]

    def index(records, k):
        idx, dups = {}, {}
        for r in records:
            nk = norm_key(r.get(k, ""))
            if not nk:
                continue
            if nk in idx:
                dups.setdefault(nk, [idx[nk]["__row__"]]).append(r["__row__"])
            else:
                idx[nk] = r
        return idx, dups

    ia, da = index(ra, ka)
    ib, db = index(rb, kb)
    result = {"only_a": [], "only_b": [], "mismatched": [], "matched": 0,
              "dups_a": da, "dups_b": db, "key_a": ka, "key_b": kb, "compared": [p[0] for p in pairs]}
    for nk, r in ia.items():
        if nk not in ib:
            result["only_a"].append(r)
            continue
        other = ib[nk]
        diffs = [(ca, r.get(ca, ""), other.get(cb, "")) for ca, cb in pairs
                 if not same(r.get(ca, ""), other.get(cb, ""), tolerance)]
        if diffs:
            result["mismatched"].append((r, other, diffs))
        else:
            result["matched"] += 1
    result["only_b"] = [r for nk, r in ib.items() if nk not in ia]
    return result


def report(res, name_a, name_b, limit=50):
    out = [f"Compared {name_a} with {name_b}, matching rows on '{res['key_a']}'"
           + (f" / '{res['key_b']}'" if res['key_b'] != res['key_a'] else "") + ".",
           f"Columns checked: {', '.join(res['compared']) or '(only the key)'}", "",
           f"  {res['matched']} rows match exactly",
           f"  {len(res['mismatched'])} rows are in both but have different values",
           f"  {len(res['only_a'])} rows are only in {name_a}",
           f"  {len(res['only_b'])} rows are only in {name_b}"]
    if res["dups_a"] or res["dups_b"]:
        out.append(f"  {len(res['dups_a'])} keys repeat in {name_a}, {len(res['dups_b'])} in {name_b} "
                   "(only the first of each was compared)")

    def section(title, items, fmt):
        if items:
            out.extend(["", title])
            for it in items[:limit]:
                out.append("  - " + fmt(it))
            if len(items) > limit:
                out.append(f"  - …and {len(items) - limit} more")
    section("Different values:", res["mismatched"], lambda m: f"{res['key_a']} {m[0][res['key_a']]}: " + "; ".join(
        f"{c} is {a or '(blank)'} in {name_a} but {b or '(blank)'} in {name_b}" for c, a, b in m[2]))
    section(f"Only in {name_a}:", res["only_a"], lambda r: f"{r[res['key_a']]} (row {r['__row__']})")
    section(f"Only in {name_b}:", res["only_b"], lambda r: f"{r[res['key_b']]} (row {r['__row__']})")
    section(f"Repeated in {name_a}:", list(res["dups_a"].items()), lambda kv: f"{kv[0]} on rows {', '.join(map(str, kv[1]))}")
    section(f"Repeated in {name_b}:", list(res["dups_b"].items()), lambda kv: f"{kv[0]} on rows {', '.join(map(str, kv[1]))}")
    return "\n".join(out)


def write_csv(res, path, name_a, name_b):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Status", "Key", "Column", f"Value in {name_a}", f"Value in {name_b}", "Row in first", "Row in second"])
        for a, b, diffs in res["mismatched"]:
            for c, va, vb in diffs:
                w.writerow(["Different", a[res["key_a"]], c, va, vb, a["__row__"], b["__row__"]])
        for r in res["only_a"]:
            w.writerow([f"Only in {name_a}", r[res["key_a"]], "", "", "", r["__row__"], ""])
        for r in res["only_b"]:
            w.writerow([f"Only in {name_b}", r[res["key_b"]], "", "", "", "", r["__row__"]])


def main(argv=None):
    ap = argparse.ArgumentParser(description="Match two spreadsheets on a key column and report differences.")
    ap.add_argument("first")
    ap.add_argument("second")
    ap.add_argument("--key", required=True, help="Column that identifies a row (e.g. 'Invoice No')")
    ap.add_argument("--key-b", help="Key column name in the second file, if different")
    ap.add_argument("--sheet-a")
    ap.add_argument("--sheet-b")
    ap.add_argument("--columns", help="Comma-separated columns to compare (default: all shared columns)")
    ap.add_argument("--tolerance", type=float, default=0.0, help="Allowed difference for numbers, e.g. 0.01")
    ap.add_argument("--out", help="Also write the differences to this CSV file")
    a = ap.parse_args(argv)
    cols = [c.strip() for c in a.columns.split(",")] if a.columns else None
    res = reconcile(a.first, a.second, a.key, a.key_b, a.sheet_a, a.sheet_b, cols, a.tolerance)
    na, nb = os.path.basename(a.first), os.path.basename(a.second)
    if na == nb:
        na, nb = "the first file", "the second file"
    print(report(res, na, nb))
    if a.out:
        write_csv(res, a.out, na, nb)
        print(f"\nThe full list of differences is saved in {os.path.abspath(a.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
