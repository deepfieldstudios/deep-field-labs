"""A small, forgiving shell tokenizer.

It is not a full POSIX parser. It understands enough to split a command line
into simple commands so each can be risk-scored:
  - quotes and backslash escapes
  - operators: && || | |& ; & ( ) and newlines
  - redirections (>, >>, &>, 2>, >|, <, <<, <<<) with their targets
  - heredoc bodies (skipped, they are data not commands)
  - $(...) and `...` command substitutions (collected for separate scoring)
  - comments
"""


class Word(str):
    """A shell word. `quoted` is True if any part of it was quoted."""
    quoted = False


SEPARATORS = {"&&", "||", "|", "|&", ";", "&", "(", ")", ";;"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>", "<", "<>", "<<", "<<-", "<<<", ">&", "<&"}
# longest first so ">>" wins over ">"
_OPS = sorted(SEPARATORS | REDIRECTS, key=len, reverse=True)


class Segment:
    """One simple command: its words, redirections and how it was joined."""

    def __init__(self):
        self.words = []          # list[Word]
        self.redirects = []      # list[(op, target)]
        self.piped_in = False    # True when stdin comes from a previous `|`

    def __repr__(self):
        return "Segment(%r, %r)" % (list(self.words), self.redirects)


def _match_paren(s, i):
    """s[i] == '(' ; return index of the matching ')' (or len(s))."""
    depth = 0
    quote = None
    while i < len(s):
        c = s[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(s)


def tokenize(s):
    """Return (tokens, substitutions).

    tokens: list of ("w", Word) | ("op", str) | ("redir", op, target_word)
    substitutions: list of command strings found in $(...) or backticks.
    """
    toks = []
    subs = []
    buf = []
    quoted = False
    have_word = False
    pending_heredocs = []  # [(delimiter, strip_tabs)]
    expect_redirect = None  # redirect op waiting for its target word
    i, n = 0, len(s)

    def flush():
        nonlocal buf, quoted, have_word, expect_redirect
        if not have_word:
            return
        w = Word("".join(buf))
        w.quoted = quoted
        if expect_redirect is not None:
            op = expect_redirect
            expect_redirect = None
            if op in ("<<", "<<-"):
                pending_heredocs.append((str(w), op == "<<-"))
            toks.append(("redir", op, w))
        else:
            toks.append(("w", w))
        buf, quoted, have_word = [], False, False

    while i < n:
        c = s[i]
        if c in " \t\r":
            flush()
            i += 1
            continue
        if c == "\\":
            if i + 1 < n and s[i + 1] == "\n":
                i += 2
                continue
            if i + 1 < n:
                buf.append(s[i + 1])
                have_word = True
                quoted = True
            i += 2
            continue
        if c == "\n":
            flush()
            toks.append(("op", ";"))
            i += 1
            # skip heredoc bodies that start after this newline
            for delim, strip in pending_heredocs:
                while i < n:
                    j = s.find("\n", i)
                    j = n if j < 0 else j
                    line = s[i:j]
                    i = j + 1
                    if (line.lstrip("\t") if strip else line).strip() == delim:
                        break
            pending_heredocs = []
            continue
        if c == "'":
            j = s.find("'", i + 1)
            j = n if j < 0 else j
            buf.append(s[i + 1:j])
            have_word = quoted = True
            i = j + 1
            continue
        if c == '"':
            i += 1
            have_word = quoted = True
            while i < n and s[i] != '"':
                if s[i] == "\\" and i + 1 < n and s[i + 1] in '"\\$`\n':
                    buf.append(s[i + 1])
                    i += 2
                    continue
                if s.startswith("$(", i) and not s.startswith("$((", i):
                    j = _match_paren(s, i + 1)
                    subs.append(s[i + 2:j])
                    buf.append(s[i:j + 1])
                    i = j + 1
                    continue
                if s[i] == "`":
                    j = s.find("`", i + 1)
                    j = n if j < 0 else j
                    subs.append(s[i + 1:j])
                    buf.append(s[i:j + 1])
                    i = j + 1
                    continue
                buf.append(s[i])
                i += 1
            i += 1  # closing quote
            continue
        if s.startswith("$(", i) and not s.startswith("$((", i):
            j = _match_paren(s, i + 1)
            subs.append(s[i + 2:j])
            buf.append(s[i:j + 1])
            have_word = True
            i = j + 1
            continue
        if c == "`":
            j = s.find("`", i + 1)
            j = n if j < 0 else j
            subs.append(s[i + 1:j])
            buf.append(s[i:j + 1])
            have_word = True
            i = j + 1
            continue
        if c == "#" and not have_word:
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        op = next((o for o in _OPS if s.startswith(o, i)), None)
        # "(" only acts as an operator at the start of a word (subshell)
        if op == "(" and have_word:
            op = None
        if op:
            # a pure number right before a redirect is a file descriptor
            if op in REDIRECTS and have_word and "".join(buf).isdigit() and not quoted:
                buf, have_word = [], False
            flush()
            if op in REDIRECTS:
                expect_redirect = op
            else:
                expect_redirect = None
                toks.append(("op", op))
            i += len(op)
            continue
        buf.append(c)
        have_word = True
        i += 1
    flush()
    return toks, subs


def segments(command):
    """Split a command line into Segments plus a list of substitution strings."""
    toks, subs = tokenize(command)
    segs = []
    cur = Segment()
    for t in toks:
        if t[0] == "w":
            cur.words.append(t[1])
        elif t[0] == "redir":
            cur.redirects.append((t[1], t[2]))
        else:
            if cur.words or cur.redirects:
                segs.append(cur)
            nxt = Segment()
            nxt.piped_in = t[1] in ("|", "|&")
            cur = nxt
    if cur.words or cur.redirects:
        segs.append(cur)
    return segs, subs
