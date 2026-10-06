#!/usr/bin/env python3
"""handoff note store CLI.

  note.py add --kind decision|dead_end|open|gotcha --text "..." [--files a b] [--session ID] [--ttl-days N]
  note.py skip [--session ID]          record "nothing worth keeping" for this session
  note.py list [--all] [--kind K]      live notes (or everything with --all)
  note.py close <id>                   close an open item
  note.py prune                        drop expired notes

Store: $HANDOFF_HOME/notes.jsonl, default <cwd>/.claude/handoff/notes.jsonl.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handoff_lib as hl  # noqa: E402


def _session(args):
    return args.session or os.environ.get("HANDOFF_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID") or ""


def _mark_session(home, session_id, key):
    if session_id:
        st = hl.load_state(home, session_id)
        st[key] = True
        hl.save_state(home, session_id, st)


def cmd_add(args, home):
    files = []
    for f in args.files or []:
        files.extend(x for x in f.split(",") if x.strip())
    sid = _session(args)
    note = hl.make_note(args.kind, args.text, files, session_id=sid,
                        ttl_days=args.ttl_days, cwd=os.getcwd())
    notes = hl.load_notes(home)
    notes.append(note)
    hl.save_notes(home, notes)
    _mark_session(home, sid, "noted")
    print("added %s %s (expires %s)" % (note["kind"], note["id"], note["expires"][:10]))


def cmd_skip(args, home):
    _mark_session(home, _session(args), "skipped")
    print("skipped: no handoff notes for this session")


def cmd_list(args, home):
    notes = hl.load_notes(home)
    if not args.all:
        notes = [n for n in notes if hl.is_live(n)]
    if args.kind:
        notes = [n for n in notes if n.get("kind") == args.kind]
    notes.sort(key=lambda n: n.get("created", ""), reverse=True)
    if not notes:
        print("no notes")
        return
    for n in notes:
        tag = ""
        if args.all:
            tag = " [expired]" if hl.is_expired(n) else (" [closed]" if n.get("status") == "closed" else "")
        print(hl.format_note(n) + tag)


def cmd_close(args, home):
    notes = hl.load_notes(home)
    for n in notes:
        if n["id"] == args.id:
            n["status"] = "closed"
            n["closed"] = hl.iso(hl.now())
            hl.save_notes(home, notes)
            print("closed %s" % args.id)
            return 0
    print("no note with id %s" % args.id, file=sys.stderr)
    return 1


def cmd_prune(args, home):
    notes = hl.load_notes(home)
    keep = [n for n in notes if not hl.is_expired(n)]
    hl.save_notes(home, keep)
    print("pruned %d expired note(s), %d kept" % (len(notes) - len(keep), len(keep)))


def main(argv=None):
    p = argparse.ArgumentParser(prog="note.py", description="handoff note store")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--kind", required=True, choices=hl.KINDS)
    a.add_argument("--text", required=True)
    a.add_argument("--files", nargs="*", default=[])
    a.add_argument("--session")
    a.add_argument("--ttl-days", type=int)
    s = sub.add_parser("skip")
    s.add_argument("--session")
    l = sub.add_parser("list")
    l.add_argument("--all", action="store_true")
    l.add_argument("--kind", choices=hl.KINDS)
    c = sub.add_parser("close")
    c.add_argument("id")
    sub.add_parser("prune")
    args = p.parse_args(argv)
    home = hl.home_dir()
    try:
        rc = {"add": cmd_add, "skip": cmd_skip, "list": cmd_list,
              "close": cmd_close, "prune": cmd_prune}[args.cmd](args, home)
    except ValueError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    return rc or 0


if __name__ == "__main__":
    sys.exit(main())
