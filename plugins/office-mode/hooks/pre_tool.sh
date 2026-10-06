#!/bin/sh
# Hook entry point: runs the Python hook beside this plugin.
exec python3 "$(dirname "$0")/../scripts/pre_tool.py"
