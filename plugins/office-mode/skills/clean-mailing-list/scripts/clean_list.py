#!/usr/bin/env python3
"""Tidy a mailing list (CSV or XLSX) into a new CSV. The original file is never changed.

  clean_list.py <list> [--email-col Email] [--name-col Name] [--sheet S] [--out cleaned.csv]

- trims stray spaces everywhere
- lower-cases email addresses and removes duplicates (ignoring capitals); when a duplicate has
  details the first copy lacks (e.g. a phone number), they are merged in
- sets aside addresses that don't look valid into a separate "-problems.csv" file
- splits a full-name column into First name / Last name when those columns don't exist
"""
import argparse
import csv
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import officefiles as of  # noqa: E402

EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                      r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,}$")
TITLES = {"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "dame", "rev"}
PARTICLES = {"van", "von", "de", "der", "den", "da", "di", "du", "la", "le", "del", "dos", "st", "st.", "mac", "bin", "al"}


def valid_email(e):
    return bool(EMAIL_RE.match(e)) and ".." not in e and not e.startswith(".") and ".@" not in e


def clean_email(raw):
    e = str(raw).strip().strip("<>").strip()
    if e.lower().startswith("mailto:"):
        e = e[7:]
    return e.replace(" ", "").lower()


def split_name(full):
    """'Dr. Jane van der Berg' -> ('Jane', 'van der Berg'); 'Smith, John' -> ('John', 'Smith')."""
    full = " ".join(str(full).split())
    if not full:
        return "", ""
    if "," in full:
        last, first = [p.strip() for p in full.split(",", 1)]
        return first, last
    parts = full.split(" ")
    while len(parts) > 1 and parts[0].lower().rstrip(".") in TITLES:
        parts = parts[1:]
    if len(parts) == 1:
        return parts[0], ""
    first, rest = parts[0], parts[1:]
    # Surname is the last word plus any particles directly before it.
    j = len(rest) - 1
    while j > 0 and rest[j - 1].lower() in PARTICLES:
        j -= 1
    middle, last = rest[:j], rest[j:]
    if middle:
        first = " ".join([first] + middle)
    return first, " ".join(last)


def find_col(header, wanted, hints):
    if wanted:
        for i, h in enumerate(header):
            if h.strip().lower() == wanted.strip().lower():
                return i
        raise SystemExit(f"Couldn't find a column called '{wanted}'. Columns: {', '.join(header)}")
    for hint in hints:
        for i, h in enumerate(header):
            if h.strip().lower() == hint:
                return i
    for hint in hints:
        for i, h in enumerate(header):
            if hint in h.strip().lower():
                return i
    return None


def clean(path, email_col=None, name_col=None, sheet=None):
    rows = [r for r in of.read_table(path, sheet) if any(str(c).strip() for c in r)]
    if not rows:
        raise SystemExit("The list is empty.")
    header = [str(h).strip() for h in rows[0]]
    body = [[str(c).strip() for c in r] + [""] * (len(header) - len(r)) for r in rows[1:]]
    ei = find_col(header, email_col, ["email", "e-mail", "email address", "mail"])
    if ei is None:  # no obvious header: pick the column that holds the most @ signs
        counts = [sum("@" in r[i] for r in body if i < len(r)) for i in range(len(header))]
        ei = counts.index(max(counts)) if counts and max(counts) else None
    if ei is None:
        raise SystemExit("Couldn't tell which column has the email addresses. Use --email-col.")
    ni = find_col(header, name_col, ["name", "full name", "contact name", "contact"])
    has_first = find_col(header, None, ["first name", "firstname", "first"]) is not None
    split = ni is not None and ni != ei and not has_first
    out_header = list(header) + (["First name", "Last name"] if split else [])

    kept, by_email, problems = [], {}, []
    stats = {"rows": len(body), "duplicates": 0, "invalid": 0, "blank": 0, "merged": 0, "fixed_case": 0}
    for n, r in enumerate(body, start=2):
        r = r[:len(header)]
        raw = r[ei]
        e = clean_email(raw)
        if e != raw.strip():
            stats["fixed_case"] += 1
        r[ei] = e
        if not e:
            stats["blank"] += 1
            problems.append(r + [f"row {n}: no email address"])
            continue
        if not valid_email(e):
            stats["invalid"] += 1
            problems.append(r + [f"row {n}: '{raw}' doesn't look like an email address"])
            continue
        if e in by_email:
            stats["duplicates"] += 1
            first = by_email[e]
            filled = False
            for i, v in enumerate(r):
                if v and not first[i]:
                    first[i] = v
                    filled = True
            stats["merged"] += filled
            continue
        if split:
            r = r + list(split_name(r[ni]))
        by_email[e] = r
        kept.append(r)
    return out_header, kept, problems, stats, header


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tidy a mailing list into a new CSV file.")
    ap.add_argument("list")
    ap.add_argument("--email-col")
    ap.add_argument("--name-col")
    ap.add_argument("--sheet")
    ap.add_argument("--out", help="Where to save the cleaned list (default: '<name> - cleaned.csv')")
    a = ap.parse_args(argv)
    src = Path(a.list)
    out = Path(a.out) if a.out else src.with_name(f"{src.stem} - cleaned.csv")
    if out.resolve() == src.resolve():
        raise SystemExit("Refusing to overwrite the original list. Choose a different --out name.")
    header, kept, problems, s, orig_header = clean(src, a.email_col, a.name_col, a.sheet)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(kept)
    prob_path = out.with_name(out.stem.replace(" - cleaned", "") + " - problems.csv")
    if problems:
        with open(prob_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(orig_header + ["Problem"])
            w.writerows(problems)
    print(f"Started with {s['rows']} contacts in {src.name}.")
    print(f"  {len(kept)} contacts kept in the cleaned list")
    print(f"  {s['duplicates']} duplicates removed (same email, ignoring capitals)"
          + (f"; details from {s['merged']} of them were merged into the first copy" if s["merged"] else ""))
    print(f"  {s['invalid']} addresses don't look valid")
    print(f"  {s['blank']} rows had no email address")
    if s["fixed_case"]:
        print(f"  {s['fixed_case']} addresses were tidied (spaces removed or made lower-case)")
    if len(header) > len(orig_header):
        print("  Added 'First name' and 'Last name' columns, split from the name column")
    print(f"\nCleaned list saved as {os.path.abspath(out)}")
    if problems:
        print(f"Rows needing a look saved as {os.path.abspath(prob_path)}")
    print(f"{src.name} itself was not changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
