#!/usr/bin/env python3
"""spend-meter core: parse Claude Code transcripts into per-model token usage and USD cost.

Design notes
- Streaming writes one transcript line per content block, each repeating the same
  message.id / requestId and usage. We key every assistant message by message.id
  (falling back to requestId, then uuid) and keep one record per key.
- Parsing is incremental: we remember a byte offset per file and only read new,
  complete lines. The cache lives under SPEND_METER_HOME/sessions/<session>.cache.json.
- Subagent transcripts live next to the main one at <session>/subagents/*.jsonl and
  are folded into the session total.

Usable as a library (hook, statusline, report) or CLI:
    python3 usage.py <transcript.jsonl> [--json]
"""
import glob
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PRICES_PATH = os.path.join(os.path.dirname(HERE), "data", "prices.json")
CACHE_VERSION = 2

# Record layout for one deduped assistant message (compact list to keep cache small).
M_MODEL, M_IN, M_OUT, M_CW5, M_CW1, M_CR, M_DATE, M_SUB = range(8)


# ---------------------------------------------------------------- paths / io

def home_dir():
    return os.environ.get("SPEND_METER_HOME") or os.path.join(
        os.path.expanduser("~"), ".claude", "spend-meter")


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    """Atomic write so a concurrent statusline/hook never sees half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def safe_id(s):
    return "".join(c for c in str(s) if c.isalnum() or c in "-_.")[:120] or "unknown"


# ---------------------------------------------------------------- pricing

_PRICES = None


def load_prices(path=None):
    global _PRICES
    if path is None and _PRICES is not None:
        return _PRICES
    data = read_json(path or PRICES_PATH, {}) or {}
    if path is None:
        _PRICES = data
    return data


def price_for(model, prices=None):
    """Longest-prefix match of the model id against prices.json, else 'default'."""
    prices = prices or load_prices()
    models = prices.get("models", {})
    mult = {"cache_write_5m": 1.25, "cache_write_1h": 2.0, "cache_read": 0.1}
    mult.update(prices.get("multipliers", {}))
    best = None
    for prefix in models:
        if prefix != "default" and (model or "").startswith(prefix):
            if best is None or len(prefix) > len(best):
                best = prefix
    entry = models.get(best or "default") or {"input": 0.0, "output": 0.0}
    inp = float(entry.get("input", 0.0))
    return {
        "input": inp,
        "output": float(entry.get("output", 0.0)),
        "cache_write_5m": float(entry.get("cache_write_5m_usd", inp * mult["cache_write_5m"])),
        "cache_write_1h": float(entry.get("cache_write_1h_usd", inp * mult["cache_write_1h"])),
        "cache_read": float(entry.get("cache_read_usd", inp * mult["cache_read"])),
        "matched": best or "default",
    }


def record_cost(rec, prices=None):
    p = price_for(rec[M_MODEL], prices)
    return (rec[M_IN] * p["input"] + rec[M_OUT] * p["output"]
            + rec[M_CW5] * p["cache_write_5m"] + rec[M_CW1] * p["cache_write_1h"]
            + rec[M_CR] * p["cache_read"]) / 1_000_000.0


# ---------------------------------------------------------------- parsing

def _int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def local_date(ts):
    """ISO timestamp -> local YYYY-MM-DD (today if missing/unparseable)."""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return datetime.now().strftime("%Y-%m-%d")


def usage_record(obj, is_sub=False):
    """Build (key, record) from an assistant transcript line, or None."""
    msg = obj.get("message") or {}
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None
    key = msg.get("id") or obj.get("requestId") or obj.get("uuid")
    if not key or msg.get("model") == "<synthetic>":   # locally generated, never billed
        return None
    cw_total = _int(usage.get("cache_creation_input_tokens"))
    cc = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else None
    if cc and ("ephemeral_1h_input_tokens" in cc or "ephemeral_5m_input_tokens" in cc):
        cw1 = _int(cc.get("ephemeral_1h_input_tokens"))
        cw5 = _int(cc.get("ephemeral_5m_input_tokens"))
        # Anything not broken down is billed as the 5m rate.
        cw5 += max(0, cw_total - cw1 - cw5)
    else:
        cw1, cw5 = 0, cw_total
    rec = [msg.get("model") or "unknown", _int(usage.get("input_tokens")),
           _int(usage.get("output_tokens")), cw5, cw1,
           _int(usage.get("cache_read_input_tokens")), local_date(obj.get("timestamp")),
           1 if is_sub else 0]
    return key, rec


def merge_record(messages, key, rec):
    """Dedupe: a repeated key keeps the larger output count (streaming grows, never shrinks)."""
    old = messages.get(key)
    if old is None or rec[M_OUT] >= old[M_OUT]:
        messages[key] = rec


def first_prompt_text(obj):
    """Human prompt text from a user line, or None (skips tool results, meta, commands)."""
    if obj.get("isMeta") or obj.get("isSidechain"):
        return None
    content = (obj.get("message") or {}).get("content")
    text = None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                text = b.get("text")
                break
            if isinstance(b, dict) and b.get("type") == "tool_result":
                return None
    if not text:
        return None
    text = " ".join(text.split())
    if text.startswith("<") and ("command" in text[:40] or "system-reminder" in text[:40]
                                 or "local-command" in text[:40]):
        return None
    return text or None


def parse_incremental(path, entry, messages, meta, is_sub=False):
    """Read complete lines appended to `path` since entry['offset']; mutates in place."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size == entry.get("offset", 0):
        return
    with open(path, "rb") as f:
        f.seek(entry.get("offset", 0))
        chunk = f.read()
    end = chunk.rfind(b"\n")
    if end < 0:
        return                                  # no complete line yet
    want_prompt = not meta.get("first_prompt") and not is_sub
    for raw in chunk[:end].split(b"\n"):
        # Cheap substring filters before json.loads keep big transcripts fast.
        has_usage = b'"usage"' in raw
        if not has_usage and not (want_prompt and b'"user"' in raw) and b'"cwd"' not in raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("cwd") and not meta.get("cwd") and not is_sub:
            meta["cwd"] = obj["cwd"]
        ts = obj.get("timestamp")
        if ts and not is_sub:
            meta.setdefault("first_ts", ts)
            meta["last_ts"] = ts
        t = obj.get("type")
        if t == "assistant" and has_usage:
            r = usage_record(obj, is_sub)
            if r:
                merge_record(messages, r[0], r[1])
        elif t == "user" and want_prompt:
            p = first_prompt_text(obj)
            if p:
                meta["first_prompt"] = p
                want_prompt = False
    entry["offset"] = entry.get("offset", 0) + end + 1


def subagent_files(transcript_path):
    base = transcript_path[:-6] if transcript_path.endswith(".jsonl") else transcript_path
    return sorted(glob.glob(os.path.join(base, "subagents", "*.jsonl")))


# ---------------------------------------------------------------- session cache

def cache_path(session_id):
    return os.path.join(home_dir(), "sessions", safe_id(session_id) + ".cache.json")


def update_session(transcript_path, session_id=None, persist=True):
    """Incrementally bring a session's cache up to date and return it."""
    session_id = session_id or os.path.basename(transcript_path).rsplit(".jsonl", 1)[0]
    path = cache_path(session_id)
    cache = read_json(path) if persist else None
    if not cache or cache.get("v") != CACHE_VERSION or cache.get("transcript") != transcript_path:
        cache = {"v": CACHE_VERSION, "session_id": session_id, "transcript": transcript_path,
                 "files": {}, "messages": {}, "meta": {}}
    targets = [(transcript_path, False)] + [(p, True) for p in subagent_files(transcript_path)]
    # A file that shrank was rewritten: rebuild from scratch rather than guess.
    for p, _ in targets:
        try:
            if os.path.getsize(p) < cache["files"].get(p, {}).get("offset", 0):
                cache["files"], cache["messages"], cache["meta"] = {}, {}, {}
                break
        except OSError:
            pass
    files, messages, meta = cache["files"], cache["messages"], cache["meta"]
    before = {k: e.get("offset", 0) for k, e in files.items()}
    for p, is_sub in targets:
        entry = files.setdefault(p, {"offset": 0})
        parse_incremental(p, entry, messages, meta, is_sub)
    changed = any(files[k].get("offset", 0) != before.get(k) for k in files)
    if persist and (changed or not os.path.exists(path)):
        write_json(path, cache)
    return cache


def summarize(cache, prices=None, since=None):
    """Totals, per-model and per-date breakdown for a session cache.

    since: optional YYYY-MM-DD; messages dated earlier are ignored (report windows)."""
    per_model, per_date = {}, {}
    total = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "cost": 0.0,
             "subagent_cost": 0.0, "messages": 0}
    for rec in cache.get("messages", {}).values():
        if since and rec[M_DATE] < since:
            continue
        c = record_cost(rec, prices)
        m = per_model.setdefault(rec[M_MODEL], {"input": 0, "output": 0, "cache_write": 0,
                                                "cache_read": 0, "cost": 0.0, "messages": 0})
        for agg in (m, total):
            agg["input"] += rec[M_IN]
            agg["output"] += rec[M_OUT]
            agg["cache_write"] += rec[M_CW5] + rec[M_CW1]
            agg["cache_read"] += rec[M_CR]
            agg["cost"] += c
            agg["messages"] += 1
        if rec[M_SUB]:
            total["subagent_cost"] += c
        per_date[rec[M_DATE]] = per_date.get(rec[M_DATE], 0.0) + c
    total["tokens"] = total["input"] + total["output"] + total["cache_write"] + total["cache_read"]
    denom = total["input"] + total["cache_write"] + total["cache_read"]
    total["cache_hit_ratio"] = (total["cache_read"] / denom) if denom else 0.0
    return {"session_id": cache.get("session_id"), "total": total, "per_model": per_model,
            "per_date": per_date, "meta": cache.get("meta", {})}


# ---------------------------------------------------------------- budgets / ledger

DEFAULT_CONFIG = {"session_budget_usd": None, "daily_budget_usd": None, "warn_at": 0.8}


def load_config(cwd=None):
    """Global config, overridden key-by-key by <cwd>/.claude/spend-meter.json."""
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(read_json(os.path.join(home_dir(), "config.json"), {}) or {})
    if cwd:
        cfg.update(read_json(os.path.join(cwd, ".claude", "spend-meter.json"), {}) or {})
    try:
        cfg["warn_at"] = float(cfg.get("warn_at") or 0.8)
    except (TypeError, ValueError):
        cfg["warn_at"] = 0.8
    return cfg


def ledger_path():
    return os.path.join(home_dir(), "ledger.json")


def update_ledger(session_id, per_date, cwd=None):
    """ledger.json: {session_id: {"dates": {date: cost}, "cwd": ..., "updated": iso}}."""
    led = read_json(ledger_path(), {}) or {}
    row = led.get(session_id) or {}
    rounded = {d: round(c, 6) for d, c in per_date.items()}
    if row.get("dates") != rounded:
        led[session_id] = {"dates": rounded, "cwd": cwd or row.get("cwd"),
                           "updated": datetime.now().isoformat(timespec="seconds")}
        write_json(ledger_path(), led)
    return led


def daily_total(led, date=None):
    date = date or datetime.now().strftime("%Y-%m-%d")
    return sum((row.get("dates") or {}).get(date, 0.0) for row in led.values())


# ---------------------------------------------------------------- formatting

def fmt_tokens(n):
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= div:
            v = n / div
            return ("%.1f%s" % (v, unit)) if v < 100 else ("%d%s" % (v, unit))
    return str(int(n))


def fmt_usd(x):
    return ("$%.2f" % x) if x < 1000 else ("$%.0f" % x)


def main(argv):
    if not argv:
        print("usage: usage.py <transcript.jsonl> [--json]", file=sys.stderr)
        return 2
    cache = update_session(argv[0], persist=False)
    s = summarize(cache)
    if "--json" in argv:
        print(json.dumps(s, indent=2))
        return 0
    t = s["total"]
    print("%s  %s tokens  (%d messages, subagents %s, cache hit %.0f%%)" % (
        fmt_usd(t["cost"]), fmt_tokens(t["tokens"]), t["messages"],
        fmt_usd(t["subagent_cost"]), 100 * t["cache_hit_ratio"]))
    for model, m in sorted(s["per_model"].items(), key=lambda kv: -kv[1]["cost"]):
        print("  %-28s %9s  in %s  out %s  cw %s  cr %s" % (
            model, fmt_usd(m["cost"]), fmt_tokens(m["input"]), fmt_tokens(m["output"]),
            fmt_tokens(m["cache_write"]), fmt_tokens(m["cache_read"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
