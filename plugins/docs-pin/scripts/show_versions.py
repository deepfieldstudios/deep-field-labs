#!/usr/bin/env python3
"""Print the pinned-versions table for the current directory (all direct deps)."""
import os
import sys

import versions as V


def main(argv):
    cwd = argv[0] if argv and argv[0].strip() else os.getcwd()
    info = V.detect(cwd)
    table = V.render_table(info, cap=10000)
    if not table:
        print("docs-pin: no manifests or lockfiles found in %s" % cwd)
        return 0
    print("Pinned versions in %s" % cwd)
    print("Detected from: %s" % ", ".join(dict.fromkeys(info["files"])))
    print()
    print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
