#!/usr/bin/env python3
"""Record whether a candidate command works.

  verify.py mark "<cmd>" ok|fail "<note>"
  verify.py list
State: $ONBOARDER_HOME/verified.json (default <cwd>/.claude/onboarder/verified.json).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import onboarder_lib as ol  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description="Mark candidate commands verified")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mark")
    m.add_argument("command")
    m.add_argument("status", choices=["ok", "fail"])
    m.add_argument("note", nargs="?", default="")
    sub.add_parser("list")
    args = p.parse_args(argv)
    home = ol.home_dir()
    if args.cmd == "mark":
        ol.mark(home, args.command, args.status, args.note)
        print("marked `%s` %s" % (ol.norm_cmd(args.command), args.status))
    else:
        v = ol.load_verified(home)
        if not v:
            print("nothing verified yet")
        for k, e in sorted(v.items()):
            print("%-4s %s  %s  %s" % (e.get("status"), k, (e.get("at") or "")[:10], e.get("note", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
