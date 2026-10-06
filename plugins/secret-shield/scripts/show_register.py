#!/usr/bin/env python3
"""Print the secret-shield rotation register with a rotation checklist.

Usage:
  show_register.py                    list everything not yet rotated
  show_register.py --all              include rotated / ignored entries
  show_register.py --rotated FP       mark a fingerprint (prefix, >= 6 chars) as rotated
  show_register.py --ignore FP        mark as a false positive (hooks will skip it)
  show_register.py --unignore FP
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import register  # noqa: E402

CHECKLIST = [
    "Create a replacement credential at the link above.",
    "Update every place that uses it: env vars, .env files, CI secrets, secret managers, teammates.",
    "Revoke / delete the old credential.",
    "Check the provider's logs or usage page for activity you do not recognise.",
    "Mark it done: show_register.py --rotated <fingerprint>  (or /secret-shield:register --rotated <fp>)",
]


def main(argv=None):
    ap = argparse.ArgumentParser(description="secret-shield rotation register")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--rotated", metavar="FP")
    ap.add_argument("--ignore", metavar="FP")
    ap.add_argument("--unignore", metavar="FP")
    args = ap.parse_args(argv)

    for flag, fn, verb in ((args.rotated, register.mark_rotated, "rotated"),
                           (args.ignore, register.mark_ignored, "ignored (false positive)"),
                           (args.unignore, lambda p: register.mark_ignored(p, False), "un-ignored")):
        if flag:
            hits = fn(flag)
            print("Marked %d entr%s as %s." % (len(hits), "y" if len(hits) == 1 else "ies", verb)
                  if hits else "No entry with fingerprint prefix %r (need at least 6 characters)." % flag)
            return 0 if hits else 1

    entries = register.load()["secrets"]
    shown = {fp: e for fp, e in entries.items()
             if args.all or not (e.get("rotated") or e.get("ignored"))}
    print("secret-shield rotation register: %s" % register.path())
    print("Only fingerprints and masked values are stored - never the secrets themselves.\n")
    if not shown:
        print("Nothing to rotate. %d entr%s on file%s." % (
            len(entries), "y" if len(entries) == 1 else "ies",
            "" if args.all or not entries else " (use --all to see rotated/ignored)"))
        return 0
    order = sorted(shown.items(), key=lambda kv: (kv[1].get("action") != "exposed-in-context",
                                                  kv[1].get("first_seen", "")))
    for fp, e in order:
        title, url = register.ROTATION.get(e.get("provider"), register.ROTATION["generic"])
        status = "ROTATED %s" % e["rotated"] if e.get("rotated") else "IGNORED" if e.get("ignored") else \
            ("ROTATE NOW (exposed)" if any(ev.get("action") == "exposed-in-context" for ev in e.get("events", []))
             else "review (blocked before it left)")
        print("- %s  %s  [%s]" % (e.get("label", e.get("kind")), e.get("masked"), status))
        print("    fingerprint: %s" % fp[:16])
        print("    first seen:  %s   last seen: %s   times: %s" % (e.get("first_seen"), e.get("last_seen"), e.get("count", 1)))
        for ev in e.get("events", [])[-3:]:
            print("    %s  %-18s %s" % (ev.get("ts"), ev.get("action"), ev.get("where")))
        print("    rotate at:   %s - %s" % (title, url))
        print()
    print("Rotation checklist (for each credential above):")
    for i, step in enumerate(CHECKLIST, 1):
        print("  %d. %s" % (i, step))
    print("\nBlocked-only entries never left the machine; rotate them if they were also pasted elsewhere.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
