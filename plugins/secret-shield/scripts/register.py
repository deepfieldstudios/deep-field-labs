"""The rotation register: every credential secret-shield has seen.

Stored at $SECRET_SHIELD_HOME/register.json (default ~/.claude/secret-shield/),
keyed by SHA-256 fingerprint. Raw secret values are NEVER written: only kind,
provider, masked form (first 4 + last 2 chars), where it was seen and what was
done about it.
"""
import contextlib
import datetime
import json
import os
import tempfile

try:
    import fcntl
except ImportError:  # non-POSIX: run without a lock
    fcntl = None

ROTATION = {
    "aws": ("AWS IAM console", "https://console.aws.amazon.com/iam/home#/security_credentials"),
    "github": ("GitHub tokens", "https://github.com/settings/tokens"),
    "stripe": ("Stripe API keys", "https://dashboard.stripe.com/apikeys"),
    "anthropic": ("Anthropic Console", "https://console.anthropic.com/settings/keys"),
    "openai": ("OpenAI API keys", "https://platform.openai.com/api-keys"),
    "slack": ("Slack app config (OAuth & Permissions / Incoming Webhooks)", "https://api.slack.com/apps"),
    "google": ("Google Cloud credentials", "https://console.cloud.google.com/apis/credentials"),
    "database": ("your database", "change the user's password (e.g. ALTER USER ... PASSWORD) and update DATABASE_URL"),
    "private_key": ("the key's owner", "generate a new key pair, replace the public key wherever it is trusted, delete the old one"),
    "jwt": ("the issuing service", "revoke the session/token and, if it may be long-lived, rotate the signing secret"),
    "generic": ("the issuing service", "rotate it wherever it was issued"),
}


def home():
    return os.environ.get("SECRET_SHIELD_HOME") or os.path.expanduser("~/.claude/secret-shield")


def path():
    return os.path.join(home(), "register.json")


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


@contextlib.contextmanager
def _locked():
    os.makedirs(home(), exist_ok=True)
    with open(os.path.join(home(), ".lock"), "w") as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_UN)


def load():
    try:
        with open(path()) as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("secrets"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "secrets": {}}


def _save(data):
    fd, tmp = tempfile.mkstemp(dir=home(), prefix=".register-")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path())


def record(findings, where, action):
    """Add findings to the register. `where` must already be redacted."""
    if not findings:
        return
    with _locked():
        data = load()
        ts = now()
        for f in findings:
            entry = data["secrets"].get(f.fingerprint)
            event = {"ts": ts, "where": where[:300], "action": action}
            if entry is None:
                entry = {"kind": f.kind, "provider": f.provider, "label": f.label,
                         "severity": f.severity, "masked": f.masked, "first_seen": ts,
                         "where": event["where"], "action": action, "events": [],
                         "rotated": False}
                data["secrets"][f.fingerprint] = entry
            entry["last_seen"] = ts
            entry["count"] = entry.get("count", 0) + 1
            entry["events"] = (entry.get("events", []) + [event])[-20:]
        _save(data)


def _mark(prefix, field, value):
    if len(prefix) < 6:
        return []  # too short to be an unambiguous fingerprint prefix
    with _locked():
        data = load()
        hits = [fp for fp in data["secrets"] if fp.startswith(prefix)]
        for fp in hits:
            data["secrets"][fp][field] = value
        if hits:
            _save(data)
    return hits


def mark_rotated(prefix):
    return _mark(prefix, "rotated", now())


def mark_ignored(prefix, ignored=True):
    """Ignored fingerprints (false positives) are skipped by every hook."""
    return _mark(prefix, "ignored", bool(ignored))


def ignored_fingerprints():
    return {fp for fp, e in load()["secrets"].items() if e.get("ignored")}
