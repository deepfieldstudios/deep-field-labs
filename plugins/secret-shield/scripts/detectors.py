"""Credential detectors.

find_secrets(text) -> list[Finding]. Each finding carries the kind, provider,
severity, the raw value (kept in memory only - never written anywhere), a
masked form (first 4 + last 2 characters) and a SHA-256 fingerprint.

Severity: "high" (live credentials), "medium" (JWTs, generic secrets),
"low" (test-mode keys such as sk_test_; warned about, never blocked).
"""
import hashlib
import math
import re


class Finding:
    def __init__(self, kind, provider, severity, value, start, end, label):
        self.kind = kind
        self.provider = provider
        self.severity = severity
        self.value = value
        self.start = start
        self.end = end
        self.label = label

    @property
    def fingerprint(self):
        return hashlib.sha256(self.value.encode("utf-8")).hexdigest()

    @property
    def masked(self):
        return mask(self.value)

    def describe(self):
        return "%s (%s)" % (self.label, self.masked)

    def __repr__(self):
        return "Finding(%s, %s)" % (self.kind, self.masked)


def mask(value):
    v = value.strip()
    if "PRIVATE KEY" in v:
        return "-----BEGIN ... PRIVATE KEY----- (%d chars)" % len(v)
    if len(v) <= 8:
        return v[:1] + "..." + v[-1:]
    return v[:4] + "..." + v[-2:]


def entropy(s):
    """Shannon entropy in bits per character."""
    if not s:
        return 0.0
    counts = {}
    for c in s:
        counts[c] = counts.get(c, 0) + 1
    n = float(len(s))
    return -sum((k / n) * math.log2(k / n) for k in counts.values())


PLACEHOLDER_RE = re.compile(
    r"(?i)(x{4,}|example|your[_-]?|placeholder|changeme|change[_-]me|dummy|sample|"
    r"redacted|fake|insert|replace|todo|\.\.\.|\*{3,}|<[^>]*>|\$\{|\{\{|\$\(|"
    r"process\.env|os\.environ|getenv|env\[|ENV\[|secrets\.|vault:|"
    r"(.)\2{5,}|0123456789|abcdefgh|12345678)")


def is_placeholder(value):
    v = value.strip()
    if v.startswith("$") or v.startswith("%") and v.endswith("%"):
        return True
    return bool(PLACEHOLDER_RE.search(v))


# (kind, provider, severity, label, regex, group holding the secret)
DETECTORS = [
    ("private_key", "private_key", "high", "private key",
     re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----[\s\S]{20,8000}?-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
                r"|-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[A-Za-z0-9+/=\s]{40,}"), 0),
    ("aws_access_key", "aws", "high", "AWS access key ID",
     re.compile(r"(?<![A-Z0-9])((?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[0-9A-Z]{16})(?![A-Z0-9])"), 1),
    ("aws_secret_key", "aws", "high", "AWS secret access key",
     re.compile(r"(?i)aws.{0,20}?secret.{0,20}?[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])"), 1),
    ("github_token", "github", "high", "GitHub token",
     re.compile(r"(?<![A-Za-z0-9_])(gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{60,255})(?![A-Za-z0-9_])"), 1),
    ("slack_token", "slack", "high", "Slack token",
     re.compile(r"(?<![A-Za-z0-9])(xox[baprse]-[A-Za-z0-9-]{10,250})"), 1),
    ("slack_webhook", "slack", "high", "Slack webhook URL",
     re.compile(r"(https://hooks\.slack\.com/services/T[A-Za-z0-9_]+/B[A-Za-z0-9_]+/[A-Za-z0-9_]{16,})"), 1),
    ("stripe_live_key", "stripe", "high", "Stripe live key",
     re.compile(r"(?<![A-Za-z0-9])((?:sk|rk)_live_[A-Za-z0-9]{20,250})(?![A-Za-z0-9])"), 1),
    ("stripe_test_key", "stripe", "low", "Stripe test key",
     re.compile(r"(?<![A-Za-z0-9])((?:sk|rk)_test_[A-Za-z0-9]{20,250})(?![A-Za-z0-9])"), 1),
    ("anthropic_key", "anthropic", "high", "Anthropic API key",
     re.compile(r"(?<![A-Za-z0-9])(sk-ant-(?:api|admin)\d{0,3}-[A-Za-z0-9_-]{20,250}|sk-ant-[A-Za-z0-9_-]{32,250})"), 1),
    ("openai_key", "openai", "high", "OpenAI API key",
     re.compile(r"(?<![A-Za-z0-9])(sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,250}|sk-[A-Za-z0-9]{48})(?![A-Za-z0-9])"), 1),
    ("google_api_key", "google", "high", "Google API key",
     re.compile(r"(?<![A-Za-z0-9_-])(AIza[0-9A-Za-z_-]{35})(?![0-9A-Za-z_-])"), 1),
    ("database_url", "database", "high", "database URL with password",
     re.compile(r"((?:postgres(?:ql)?|mongodb(?:\+srv)?|mysql|mariadb|redis|rediss|amqps?)://[^\s:/@\"'<>]+:([^\s@/\"'<>]{3,})@[^\s/\"'<>]+)"), 2),
    ("jwt", "jwt", "medium", "JSON Web Token",
     re.compile(r"(?<![A-Za-z0-9_-])(eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"), 1),
    ("generic_secret", "generic", "medium", "hard-coded secret",
     re.compile(r"(?i)\b([A-Za-z0-9_.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|"
                r"access[_-]?key|auth[_-]?key|client[_-]?secret|private[_-]?key)[A-Za-z0-9_.-]*)"
                r"[\"']?\s*(?::=|=>|[:=])\s*([\"']?)([^\s\"'`,;()\[\]{}<>]{16,})\2"), 3),
]

DUMMY_PASSWORDS = {"password", "pass", "passwd", "pwd", "secret", "changeme", "postgres",
                   "root", "admin", "user", "test", "mysql", "mongo", "redis", "guest",
                   "123456", "12345678", "pw", "dbpass", "dbpassword"}

_IDENTIFIER = re.compile(r"^[a-z_.-]+$|^[A-Z_.-]+$|^[A-Za-z][a-z]+(?:[A-Z][a-z]+)+[0-9]*$")


def _generic_ok(value):
    if len(value) < 16 or entropy(value) < 3.5:
        return False
    if _IDENTIFIER.match(value):  # snake_case / CONSTANT / camelCase words
        return False
    if "://" in value or value.startswith("/") or value.startswith("./"):
        return False
    if re.match(r"^[\w.-]+\.[\w.-]+\(", value):  # function call
        return False
    return True


def find_secrets(text, include_low=True):
    """All non-placeholder secrets in `text`, earliest first, no overlaps."""
    if not text or not isinstance(text, str):
        return []
    found = []
    for kind, provider, severity, label, rx, group in DETECTORS:
        if severity == "low" and not include_low:
            continue
        for m in rx.finditer(text):
            value = m.group(group)
            if not value:
                continue
            start, end = m.span(group)
            if kind == "generic_secret":
                if not _generic_ok(value):
                    continue
                # unquoted values must look like a token (letters + digits), not code
                if not m.group(2) and (not re.search(r"\d", value) or not re.search(r"[A-Za-z]", value)
                                       or (text[end:end + 1] and text[end:end + 1] in "(.[")):
                    continue
            if kind != "private_key" and is_placeholder(value):
                continue
            if kind == "database_url" and value.lower() in DUMMY_PASSWORDS:  # host may say "example"
                continue
            if kind == "private_key" and re.search(r"(?i)\.\.\.|example|<", value[30:200]):
                continue
            # earlier detectors are more specific; skip anything overlapping them
            if any(not (end <= f.start or start >= f.end) for f in found):
                continue
            found.append(Finding(kind, provider, severity, value, start, end, label))
    found.sort(key=lambda f: f.start)
    return found


def redact(text):
    """Replace every secret in text with its masked form."""
    out = text
    for f in sorted(find_secrets(text), key=lambda f: -f.start):
        out = out[:f.start] + "[" + f.masked + "]" + out[f.end:]
    return out


def strings_in(obj, depth=0):
    """All string values inside a JSON-like structure (keys included)."""
    if depth > 20:
        return
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                yield k
            yield from strings_in(v, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from strings_in(v, depth + 1)


def scan_obj(obj, include_low=True):
    """Find secrets in every string inside obj, de-duplicated by fingerprint."""
    seen, out = set(), []
    for s in strings_in(obj):
        for f in find_secrets(s, include_low):
            if f.fingerprint not in seen:
                seen.add(f.fingerprint)
                out.append(f)
    return out


ENV_HINT = {
    "aws_access_key": "AWS_ACCESS_KEY_ID", "aws_secret_key": "AWS_SECRET_ACCESS_KEY",
    "github_token": "GITHUB_TOKEN", "slack_token": "SLACK_TOKEN",
    "slack_webhook": "SLACK_WEBHOOK_URL", "stripe_live_key": "STRIPE_SECRET_KEY",
    "stripe_test_key": "STRIPE_SECRET_KEY", "anthropic_key": "ANTHROPIC_API_KEY",
    "openai_key": "OPENAI_API_KEY", "google_api_key": "GOOGLE_API_KEY",
    "database_url": "DATABASE_URL", "jwt": "AUTH_TOKEN", "generic_secret": "MY_SECRET",
    "private_key": "a key file (e.g. ~/.ssh/id_ed25519) referenced by path",
}


def env_hint(kind):
    return ENV_HINT.get(kind, "an environment variable")
