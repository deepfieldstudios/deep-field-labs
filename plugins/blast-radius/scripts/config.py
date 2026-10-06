"""Config and state locations for blast-radius.

Config: <cwd>/.claude/blast-radius.json (optional). Example:
{
  "thresholds": {"low": 30, "high": 70},
  "allow": ["^make dev$"],          # regexes: matching commands are allowed (score 0)
  "ask":   ["\\bnpm install\\b"],   # regexes: matching commands always ask
  "deny":  ["terraform destroy"],   # regexes: matching commands are denied
  "auto_allow": true,               # false = never emit "allow", only ask/defer
  "snapshot": {"enabled": true, "max_mb": 50},
  "preview_max_files": 5000,
  "show_medium": true               # show a one-line note for medium-risk commands
}
State: $BLAST_RADIUS_HOME, else <cwd>/.claude/blast-radius/
"""
import json
import os

DEFAULTS = {
    "thresholds": {"low": 30, "high": 70},
    "allow": [],
    "ask": [],
    "deny": [],
    "auto_allow": True,
    "snapshot": {"enabled": True, "max_mb": 50},
    "preview_max_files": 5000,
    "show_medium": True,
}


def load(cwd):
    cfg = json.loads(json.dumps(DEFAULTS))
    path = os.path.join(cwd, ".claude", "blast-radius.json")
    try:
        with open(path) as fh:
            user = json.load(fh)
    except (OSError, ValueError):
        return cfg
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def state_dir(cwd):
    home = os.environ.get("BLAST_RADIUS_HOME")
    return home if home else os.path.join(cwd, ".claude", "blast-radius")
