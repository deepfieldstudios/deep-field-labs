#!/usr/bin/env python3
"""Plain-English before/after summary of two versions of a document.

Usage: preview.py <old> <new> [--name "budget.xlsx"] [--max-lines 40]

Spreadsheets (.csv/.tsv/.xlsx): sheets added/removed, rows added/removed, changed cells
  ("Sheet1!C14: 1,200 → 1,450"). Word (.docx): paragraph changes. PowerPoint (.pptx):
  slide text changes. Text/Markdown: paragraph changes. Anything else: size change only.
"""
import argparse
import difflib
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import officefiles as of  # noqa: E402

TEXT_EXT = {".md", ".txt", ".markdown", ".rtf", ".json", ".html", ".htm", ".xml", ".yaml", ".yml"}
MAX_LINES = 40
MAX_CHARS = 4000


def _q(s, n=120):
    s = " ".join(str(s).split())
    return f'"{s[:n - 1]}…"' if len(s) > n else f'"{s}"'


def _words_snippet(old, new, context=6):
    """Show just the changed region of a paragraph: ('…within 30 days…', '…within 14 days…')."""
    a, b = old.split(), new.split()
    ops = [op for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != "equal"]
    if not ops:
        return _q(old), _q(new)
    i1, i2 = ops[0][1], ops[-1][2]
    j1, j2 = ops[0][3], ops[-1][4]
    s1, s2 = max(0, i1 - context), max(0, j1 - context)
    e1, e2 = min(len(a), i2 + context), min(len(b), j2 + context)

    def fmt(words, s, e):
        text = " ".join(words[s:e])
        if s > 0:
            text = "…" + text
        if e < len(words):
            text = text + "…"
        return _q(text, 200) if text else "(nothing)"
    return fmt(a, s1, e1), fmt(b, s2, e2)


# ---------- paragraphs (docx, text, slides) ----------

def diff_paragraphs(old, new, noun="paragraph", prefix=""):
    lines, counts = [], {"added": 0, "removed": 0, "changed": 0}
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        pairs = min(i2 - i1, j2 - j1) if tag == "replace" else 0
        for k in range(pairs):
            o, n = _words_snippet(old[i1 + k], new[j1 + k])
            lines.append(f"{prefix}Changed {noun} {j1 + k + 1}: {o} → {n}")
            counts["changed"] += 1
        for k in range(i1 + pairs, i2):
            lines.append(f"{prefix}Removed {noun} (was {noun} {k + 1}): {_q(old[k])}")
            counts["removed"] += 1
        for k in range(j1 + pairs, j2):
            lines.append(f"{prefix}Added {noun} {k + 1}: {_q(new[k])}")
            counts["added"] += 1
    return lines, counts


def text_blocks(path):
    with open(path, "rb") as fh:
        text = fh.read().decode("utf-8", errors="replace").replace("\r\n", "\n")
    blocks = [" ".join(b.split("\n")).strip() for b in text.split("\n\n")]
    blocks = [b for b in blocks if b]
    # A file with no blank lines (lists, logs) reads better line by line.
    if len(blocks) <= 1:
        blocks = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return blocks


def _counts_line(counts, noun):
    parts = []
    for key in ("changed", "added", "removed"):
        n = counts.get(key, 0)
        if n:
            parts.append(f"{n} {noun}{'s' if n != 1 else ''} {key}")
    return ", ".join(parts)


# ---------- grids (csv, xlsx) ----------

def _trim(row):
    row = list(row)
    while row and (row[-1] in ("", None)):
        row.pop()
    return tuple(row)


def _row_text(row, n=100):
    vals = [v for v in row if v not in ("", None)]
    return _q(", ".join(vals), n) if vals else "(empty row)"


def diff_grid(old_vals, new_vals, old_forms=None, new_forms=None, sheet=None, header=None):
    """Align rows with difflib (so an inserted row is one change, not a cascade), then compare cells."""
    lines = []
    counts = {"cells": 0, "rows_added": 0, "rows_removed": 0}
    where = f"{sheet}!" if sheet else ""
    label = f"{sheet}: " if sheet else ""
    old_forms = old_forms or [[None] * len(r) for r in old_vals]
    new_forms = new_forms or [[None] * len(r) for r in new_vals]

    def col_name(c):
        if header and c < len(header) and header[c]:
            return f" ({header[c]})"
        return ""

    def cell_changes(oi, ni):
        orow, nrow = old_vals[oi], new_vals[ni]
        of_, nf = old_forms[oi], new_forms[ni]
        width = max(len(orow), len(nrow))
        for c in range(width):
            ov = orow[c] if c < len(orow) else ""
            nv = nrow[c] if c < len(nrow) else ""
            ofm = of_[c] if c < len(of_) else None
            nfm = nf[c] if c < len(nf) else None
            ref = f"{where}{of.col_letter(c + 1)}{ni + 1}{col_name(c)}"
            if ofm != nfm:
                lines.append(f"{ref}: formula {ofm or '(none)'} → {nfm or '(none)'}"
                             + (f" (value {ov or '(empty)'} → {nv or '(empty)'})" if ov != nv else ""))
                counts["cells"] += 1
            elif ov != nv:
                lines.append(f"{ref}: {ov or '(empty)'} → {nv or '(empty)'}")
                counts["cells"] += 1

    okeys = [_trim(r) for r in old_vals]
    nkeys = [_trim(r) for r in new_vals]
    sm = difflib.SequenceMatcher(None, okeys, nkeys, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            # Values match; only report formula edits where the row did not move.
            for k in range(i2 - i1):
                if i1 + k == j1 + k:
                    cell_changes(i1 + k, j1 + k)
            continue
        pairs = min(i2 - i1, j2 - j1) if tag == "replace" else 0
        for k in range(pairs):
            cell_changes(i1 + k, j1 + k)
        for k in range(i1 + pairs, i2):
            lines.append(f"{label}Removed row {k + 1}: {_row_text(old_vals[k])}")
            counts["rows_removed"] += 1
        for k in range(j1 + pairs, j2):
            lines.append(f"{label}Added row {k + 1}: {_row_text(new_vals[k])}")
            counts["rows_added"] += 1
    return lines, counts


def _looks_like_header(row):
    vals = [v for v in row if v]
    if not vals:
        return False
    for v in vals:
        try:
            float(v.replace(",", ""))
            return False
        except ValueError:
            pass
    return True


def _grid_counts_line(c):
    parts = []
    if c["cells"]:
        parts.append(f"{c['cells']} cell{'s' if c['cells'] != 1 else ''} changed")
    if c["rows_added"]:
        parts.append(f"{c['rows_added']} row{'s' if c['rows_added'] != 1 else ''} added")
    if c["rows_removed"]:
        parts.append(f"{c['rows_removed']} row{'s' if c['rows_removed'] != 1 else ''} removed")
    return ", ".join(parts)


def summarize_csv(old, new):
    o, n = of.read_csv(old), of.read_csv(new)
    header = n[0] if n and _looks_like_header(n[0]) else None
    lines, counts = diff_grid(o, n, header=header)
    head = _grid_counts_line(counts)
    return ([head] if head else []) + lines


def summarize_xlsx(old, new):
    ob, nb = of.read_xlsx(old), of.read_xlsx(new)
    out, totals = [], []
    for s in nb:
        if s not in ob:
            rows = len(of.cells_to_rows(nb[s])[0])
            out.append(f"Added sheet \"{s}\" ({rows} row{'s' if rows != 1 else ''})")
    for s in ob:
        if s not in nb:
            out.append(f"Removed sheet \"{s}\"")
    for s in nb:
        if s not in ob:
            continue
        ov, ofm = of.cells_to_rows(ob[s])
        nv, nfm = of.cells_to_rows(nb[s])
        header = nv[0] if nv and _looks_like_header(nv[0]) else None
        lines, counts = diff_grid(ov, nv, ofm, nfm, sheet=s, header=header)
        head = _grid_counts_line(counts)
        if head:
            totals.append(f"{s}: {head}")
        out.extend(lines)
    return totals + out


def summarize_docx(old, new):
    lines, counts = diff_paragraphs(of.read_docx_paragraphs(old), of.read_docx_paragraphs(new))
    head = _counts_line(counts, "paragraph")
    return ([head] if head else []) + lines


def summarize_pptx(old, new):
    o, n = of.read_pptx_slides(old), of.read_pptx_slides(new)
    out = []
    sm = difflib.SequenceMatcher(None, [tuple(s) for s in o], [tuple(s) for s in n], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        pairs = min(i2 - i1, j2 - j1) if tag == "replace" else 0
        for k in range(pairs):
            lines, _ = diff_paragraphs(o[i1 + k], n[j1 + k], noun="text box line",
                                       prefix=f"Slide {j1 + k + 1}: ")
            out.extend(lines)
        for k in range(i1 + pairs, i2):
            out.append(f"Removed slide (was slide {k + 1}): {_q(' / '.join(o[k]) or '(no text)')}")
        for k in range(j1 + pairs, j2):
            out.append(f"Added slide {k + 1}: {_q(' / '.join(n[k]) or '(no text)')}")
    if len(o) != len(n):
        out.insert(0, f"Slide count {len(o)} → {len(n)}")
    return out


def summarize_text(old, new):
    lines, counts = diff_paragraphs(text_blocks(old), text_blocks(new))
    head = _counts_line(counts, "paragraph")
    return ([head] if head else []) + lines


def _is_text(path):
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(4096)
        if b"\0" in chunk:
            return False
        chunk.decode("utf-8")
        return True
    except (OSError, UnicodeDecodeError):
        return False


def summarize(old, new, name=None, max_lines=MAX_LINES, max_chars=MAX_CHARS):
    """Return a plain-English summary string. Never raises on odd files."""
    old, new = Path(old), Path(new)
    name = name or new.name
    if not new.exists():
        return f"{name} was deleted. An earlier copy is saved and can be restored."
    if not old.exists():
        return f"{name} is a new file."
    if old.read_bytes() == new.read_bytes():
        return f"{name}: no changes."
    ext = Path(name).suffix.lower() or new.suffix.lower()
    try:
        if ext in (".csv", ".tsv"):
            lines = summarize_csv(old, new)
        elif ext in (".xlsx", ".xlsm"):
            lines = summarize_xlsx(old, new)
        elif ext == ".docx":
            lines = summarize_docx(old, new)
        elif ext == ".pptx":
            lines = summarize_pptx(old, new)
        elif ext in TEXT_EXT or _is_text(new):
            lines = summarize_text(old, new)
        else:
            lines = None
    except Exception:
        lines = None  # corrupt or unusual file: fall back to size only
    if lines is None:
        return (f"{name} changed (size {_size(old)} → {_size(new)}). "
                "I can't show the content changes for this type of file.")
    if not lines:
        return f"{name}: the content looks the same (only formatting or hidden details changed)."
    shown, total = [], 0
    for ln in lines[:max_lines]:
        if total + len(ln) > max_chars:
            break
        shown.append(ln)
        total += len(ln) + 1
    more = len(lines) - len(shown)
    body = "\n".join(f"- {ln}" for ln in shown)
    if more > 0:
        body += f"\n- …and {more} more change{'s' if more != 1 else ''}"
    return f"What changed in {name}:\n{body}"


def _size(p):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from om_common import human_size
    return human_size(Path(p).stat().st_size)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Plain-English before/after summary of two document versions.")
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--name", help="Name to use for the file in the summary")
    ap.add_argument("--max-lines", type=int, default=MAX_LINES)
    a = ap.parse_args(argv)
    print(summarize(a.old, a.new, name=a.name, max_lines=a.max_lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
