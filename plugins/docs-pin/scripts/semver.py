"""Tiny version parsing and range matching (stdlib only).

Handles semver ("18.2.0", "v1.21.3", "5.0.0-beta.1"), PEP 440-ish versions
("2.5.3", "1.0.0rc1", "2.0.post1") and declared specs ("^18.2.0", "~=2.5",
">=1.0,<2"). A spec is reduced to its lowest allowed version when no exact
installed version is known.

Range syntax used in rules: comparators separated by spaces or commas,
alternatives separated by "||":  ">=13 <15", ">=2.0.0", "*", "<1 || >=3".
"""
import re

_NUM_RE = re.compile(r"^\s*[v=]?\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.\d+)*(.*)$")
_COMP_RE = re.compile(r"^(>=|<=|>|<|==|=|!=|\^|~=|~)?\s*(.+)$")


def parse(version):
    """'18.2.0' -> (18, 2, 0, pre) where pre is () for a release, else a tuple
    that sorts below () . Returns None if unparseable."""
    if version is None:
        return None
    m = _NUM_RE.match(str(version))
    if not m:
        return None
    major, minor, patch = (int(g) if g else 0 for g in m.group(1, 2, 3))
    rest = (m.group(4) or "").strip().lstrip("-.+_")
    rest = rest.split("+")[0]  # drop build metadata
    if not rest or re.fullmatch(r"(post|p|r|rev)\d*", rest, re.I):
        pre = (1,)  # release (post-releases count as the release)
    else:
        parts = re.split(r"[.\-_]", rest.lower())
        pre = (0,) + tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in parts if p)
    return (major, minor, patch, pre)


def compare(a, b):
    pa, pb = parse(a), parse(b)
    if pa is None or pb is None:
        raise ValueError("unparseable version: %r / %r" % (a, b))
    return (pa > pb) - (pa < pb)


def _match_one(version, comparator):
    comparator = comparator.strip()
    if comparator in ("", "*", "x", "latest"):
        return True
    m = _COMP_RE.match(comparator)
    op, target = m.group(1) or "=", m.group(2).strip()
    if op in ("^", "~", "~="):
        lo = lowest(comparator)
        if lo is None:
            return False
        nums = [int(n) for n in re.findall(r"\d+", target)[:3]]
        if op == "^":
            # bump the first non-zero component
            idx = next((i for i, n in enumerate(nums) if n != 0), len(nums) - 1)
        elif op == "~":
            idx = 0 if len(nums) == 1 else 1
        else:  # ~=2.5 -> <3.0 ; ~=2.5.1 -> <2.6
            idx = max(len(nums) - 2, 0)
        upper = nums[: idx + 1]
        upper[-1] += 1
        return compare(version, lo) >= 0 and compare(version, _pad(upper)) < 0
    c = compare(version, target)
    return {"=": c == 0, "==": c == 0, "!=": c != 0, ">": c > 0, ">=": c >= 0, "<": c < 0, "<=": c <= 0}[op]


def _pad(nums):
    nums = list(nums) + [0, 0, 0]
    return "%d.%d.%d" % tuple(nums[:3])


def satisfies(version, range_spec):
    """True if version falls inside range_spec. Unknown version -> False,
    except that "*" matches anything that is present."""
    range_spec = (range_spec or "*").strip()
    if range_spec in ("*", ""):
        return True
    if version is None or parse(version) is None:
        return False
    for alternative in range_spec.split("||"):
        comparators = [c for c in re.split(r"[\s,]+", _tighten(alternative)) if c]
        if all(_match_one(version, c) for c in comparators):
            return True
    return False


def _tighten(spec):
    """'>= 1.0' -> '>=1.0' so splitting on spaces keeps operator and number together."""
    return re.sub(r"(>=|<=|==|!=|~=|>|<|=|\^|~)\s+", r"\1", spec)


def lowest(spec):
    """Lowest version a declared spec allows: '^18.2.0' -> '18.2.0',
    '>=1.0,<2' -> '1.0', '18.x' -> '18.0.0', 'workspace:*' -> None."""
    if spec is None:
        return None
    spec = str(spec).strip()
    if spec.startswith(("npm:", "workspace:", "file:", "link:", "git", "http")):
        if spec.startswith("npm:") and "@" in spec[4:]:
            return lowest(spec.rsplit("@", 1)[1])
        return None
    spec = spec.split("||")[0]
    candidates = []
    for comp in re.split(r"[\s,]+", _tighten(spec)):
        m = _COMP_RE.match(comp) if comp else None
        if not m:
            continue
        op, target = m.group(1) or "=", m.group(2).replace(".x", ".0").replace(".*", ".0")
        if op in ("<", "<=", "!=") or parse(target) is None:
            continue
        candidates.append(target)
    if not candidates:
        return None
    return max(candidates, key=parse)
