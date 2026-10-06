#!/usr/bin/env python3
"""Print the latest receipt (or the one for a given session id)."""
import glob
import os
import sys

import receipts_lib as lib


def main(argv):
    directory = lib.state_dir(os.getcwd())
    if argv and argv[0].strip():
        path = lib.receipt_path(os.getcwd(), argv[0].strip())
        if not os.path.exists(path):
            print("No receipt for session %s in %s" % (argv[0], directory))
            return 1
    else:
        found = glob.glob(os.path.join(directory, "receipt-*.md"))
        if not found:
            print("No receipts yet in %s. One is written each time Claude finishes a reply." % directory)
            return 0
        path = max(found, key=os.path.getmtime)
    with open(path, encoding="utf-8") as fh:
        sys.stdout.write(fh.read())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
