"""Stdlib-only readers for office documents: CSV, XLSX, DOCX, PPTX.

XLSX/DOCX/PPTX are zip files of XML. We match XML tags by local name so both the usual
"transitional" and the rarer "strict" OOXML namespaces work.
"""
import csv
import io
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET


def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _attr(el, name):
    """Attribute by local name (ignores namespace prefix)."""
    for k, v in el.attrib.items():
        if local(k) == name:
            return v
    return None


# ---------- spreadsheet cell references ----------

def col_letter(n):
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def col_number(letters):
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


_REF = re.compile(r"^([A-Za-z]+)(\d+)$")


def parse_ref(ref):
    m = _REF.match(ref or "")
    if not m:
        return None
    return int(m.group(2)), col_number(m.group(1))


def format_number(s):
    """'1200' -> '1,200'; '1450.5' -> '1,450.5'; non-numbers unchanged."""
    try:
        x = float(s)
    except (TypeError, ValueError):
        return s
    if x != x or x in (float("inf"), float("-inf")):
        return s
    if x == int(x) and abs(x) < 1e15:
        return f"{int(x):,}"
    return f"{x:,.10g}"


# ---------- XLSX ----------

def _resolve_target(base_dir, target):
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(base_dir, target))


def read_xlsx(path, raw=False):
    """Return {sheet_name: {(row, col): (value, formula_or_None)}} in workbook order.

    Numbers are shown with thousands separators ("1,200") unless raw=True ("1200").
    """
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root:
                if local(si.tag) == "si":
                    shared.append("".join(t.text or "" for t in si.iter() if local(t.tag) == "t"))
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
                rels[rel.get("Id")] = _resolve_target("xl", rel.get("Target", ""))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        sheets = []
        for el in wb.iter():
            if local(el.tag) == "sheet":
                rid = _attr(el, "id")
                target = rels.get(rid) or f"xl/worksheets/sheet{len(sheets) + 1}.xml"
                sheets.append((el.get("name") or f"Sheet{len(sheets) + 1}", target))
        out = {}
        for name, target in sheets:
            cells = {}
            if target in names:
                root = ET.fromstring(z.read(target))
                for c in root.iter():
                    if local(c.tag) != "c":
                        continue
                    pos = parse_ref(c.get("r"))
                    if not pos:
                        continue
                    ctype = c.get("t", "n")
                    v = f = None
                    inline = None
                    for child in c:
                        tag = local(child.tag)
                        if tag == "v":
                            v = child.text
                        elif tag == "f":
                            f = child.text
                        elif tag == "is":
                            inline = "".join(t.text or "" for t in child.iter() if local(t.tag) == "t")
                    if ctype == "s" and v is not None:
                        try:
                            val = shared[int(v)]
                        except (ValueError, IndexError):
                            val = v
                    elif ctype == "inlineStr":
                        val = inline or ""
                    elif ctype == "b":
                        val = "TRUE" if v == "1" else "FALSE"
                    elif ctype in ("str", "e"):
                        val = v or ""
                    else:
                        val = (v if raw else format_number(v)) if v is not None else ""
                        if raw and re.fullmatch(r"-?\d+\.0+", val or ""):
                            val = val.split(".")[0]
                    formula = ("=" + f) if f else None
                    if val != "" or formula:
                        cells[pos] = (val, formula)
            out[name] = cells
        return out


def cells_to_rows(cells):
    """Sparse {(r,c): (v,f)} -> (values rows, formula rows), both dense lists of lists."""
    if not cells:
        return [], []
    max_r = max(r for r, _ in cells)
    max_c = max(c for _, c in cells)
    vals = [[""] * max_c for _ in range(max_r)]
    forms = [[None] * max_c for _ in range(max_r)]
    for (r, c), (v, f) in cells.items():
        vals[r - 1][c - 1] = v
        forms[r - 1][c - 1] = f
    return vals, forms


# ---------- CSV ----------

def read_csv(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8-sig", errors="replace")
    delim = ","
    if str(path).lower().endswith(".tsv"):
        delim = "\t"
    else:
        try:
            delim = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
        except csv.Error:
            pass
    return [row for row in csv.reader(io.StringIO(text), delimiter=delim)]


def read_table(path, sheet=None):
    """Rows (lists of strings) from a CSV/TSV or one XLSX sheet (first sheet by default)."""
    low = str(path).lower()
    if low.endswith((".xlsx", ".xlsm")):
        book = read_xlsx(path, raw=True)
        if not book:
            return []
        if sheet is None:
            name = next(iter(book))
        elif sheet in book:
            name = sheet
        else:
            matches = [n for n in book if n.lower() == str(sheet).lower()]
            if not matches:
                raise KeyError(f"No sheet called '{sheet}'. Sheets: {', '.join(book)}")
            name = matches[0]
        vals, _ = cells_to_rows(book[name])
        return vals
    return read_csv(path)


# ---------- DOCX ----------

def read_docx_paragraphs(path):
    """Non-empty paragraph texts from word/document.xml, in order (tables included)."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    paras = []
    for p in root.iter():
        if local(p.tag) != "p":
            continue
        parts = []
        for el in p.iter():
            tag = local(el.tag)
            if tag == "t":
                parts.append(el.text or "")
            elif tag == "tab":
                parts.append("\t")
            elif tag in ("br", "cr"):
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            paras.append(text)
    return paras


# ---------- PPTX ----------

def read_pptx_slides(path):
    """List of slides, each a list of paragraph texts, in presentation order."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        order = []
        if "ppt/presentation.xml" in names and "ppt/_rels/presentation.xml.rels" in names:
            rels = {r.get("Id"): _resolve_target("ppt", r.get("Target", ""))
                    for r in ET.fromstring(z.read("ppt/_rels/presentation.xml.rels"))}
            for el in ET.fromstring(z.read("ppt/presentation.xml")).iter():
                if local(el.tag) == "sldId":
                    t = rels.get(_attr(el, "id"))
                    if t:
                        order.append(t)
        if not order:
            def num(n):
                m = re.search(r"(\d+)\.xml$", n)
                return int(m.group(1)) if m else 0
            order = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)), key=num)
        slides = []
        for target in order:
            if target not in names:
                continue
            root = ET.fromstring(z.read(target))
            paras = []
            for p in root.iter():
                if local(p.tag) == "p":
                    text = "".join(t.text or "" for t in p.iter() if local(t.tag) == "t").strip()
                    if text:
                        paras.append(text)
            slides.append(paras)
        return slides
